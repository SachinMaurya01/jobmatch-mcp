# JobMatch MCP

JobMatch MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that ranks job postings against your resume. It searches multiple job sources, removes duplicate postings, scores each job with a hybrid pipeline (structured rules, local embeddings, and a bounded LLM re-rank), and returns only the matches that clear a minimum score, each with an explanation and a link to the original posting.

It is built with Python, [FastMCP](https://gofastmcp.com), PostgreSQL with pgvector, and Ollama for local inference. It runs over stdio for desktop clients such as Claude Desktop, and over Streamable HTTP for tools such as MCP Inspector.

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Connecting an MCP client](#connecting-an-mcp-client)
- [Configuration](#configuration)
- [Job sources](#job-sources)
- [MCP surface](#mcp-surface)
- [LLM routing and privacy](#llm-routing-and-privacy)
- [Security](#security)
- [Observability](#observability)
- [Development](#development)
- [Testing and quality gates](#testing-and-quality-gates)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Data model](#data-model)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Resume parsing** from `.txt`, `.md`, and `.pdf` files or raw text, with structured extraction of skills, years of experience, seniority, roles, locations, and education.
- **Multi-source search** through pluggable connectors for JSearch (RapidAPI), Greenhouse, Lever, and Google Jobs (SerpAPI), behind one interface and registry.
- **Cross-source deduplication** that merges postings by normalized title, company, and location. Each result records every source that contributed to it.
- **Hybrid scoring** that combines configurable structured weights with local semantic similarity from Ollama embeddings stored in pgvector. When a signal is missing, its weight is redistributed rather than scored as zero.
- **Bounded LLM re-rank** over the strongest matches (top 20 by default, adjusted by at most ±10 points), with per-job explanations and suggestions.
- **Provider routing** with a fixed Ollama → OpenAI → Claude order, per-provider circuit breakers, and call accounting.
- **Privacy switch** that keeps resume data on the local machine by disabling all hosted LLM calls.
- **Partial-result handling.** A failing source produces a warning and a degraded status while healthy sources still return ranked results.
- **Rate limiting and caching** per source, with sliding-window limits and a TTL/LRU response cache.
- **Persistence** of resumes, jobs, searches, matches, and LLM calls, exposed through MCP resources.
- **Full MCP surface:** tools, resources, prompts, elicitation, progress notifications, cancellation, and versioned structured outputs.
- **Secret-safe errors and log redaction** for emails, phone numbers, and tokens.

## How it works

```
MCP client (Claude Desktop, MCP Inspector, ...)
        │  stdio  or  Streamable HTTP (bearer token)
        ▼
MCP server (FastMCP): tools · resources · prompts · elicitation
        │
        ▼
Application services: ResumeService · SearchService · MatchService · RerankService
        │                 │                    │
        ▼                 ▼                    ▼
   Connectors         Matching pipeline     LLM router
   (per source)       structured · semantic  Ollama → OpenAI → Claude
        │             dedupe · re-rank       (circuit breakers, call log)
        ▼                 ▼                    ▼
  Job APIs / feeds   PostgreSQL + pgvector   Model providers
```

A search runs in this order:

1. **Load the resume.** `parse_resume` extracts a structured profile and stores it with an embedding.
2. **Retrieve candidates.** Enabled connectors are queried in parallel, each under its own timeout and rate limit. Results are normalized to a common schema.
3. **Deduplicate.** Postings are merged across sources.
4. **Score.** Structured scoring covers skills, experience, seniority, location, and salary. Semantic scoring compares embeddings. The two are combined into a 0–100 score.
5. **Re-rank.** The top N results are sent to the LLM router, which returns an explanation and a bounded score adjustment. Output is schema-validated and clamped.
6. **Filter and return.** Jobs below the minimum score (60) are dropped, the rest are sorted by score, and at most 30 are returned. The search and its matches are saved.

Default scoring weights (override all of them with a JSON object in `JOBMATCH_SCORING_WEIGHTS`, for example `{"skills": 0.4, "semantic": 0.25, "experience": 0.15, "seniority": 0.1, "location": 0.05, "salary": 0.05}`):

| Component | Default weight |
|---|---|
| Skills overlap | 40% |
| Semantic similarity | 25% |
| Experience | 15% |
| Seniority | 10% |
| Location / remote fit | 5% |
| Salary fit | 5% |

## Requirements

- Python 3.12 or newer (Docker images use 3.13)
- [uv](https://docs.astral.sh/uv/) for dependency management
- PostgreSQL 16+ with the [pgvector](https://github.com/pgvector/pgvector) extension (the Compose file provides one)
- [Ollama](https://ollama.com) for local embeddings and re-ranking
- At least one job source API key (JSearch or SerpAPI), or Greenhouse / Lever board tokens for keyless public feeds
- Optional: OpenAI or Anthropic API keys, only if you enable hosted fallback

## Quick start

### 1. Clone and install

```bash
git clone <your-fork-or-repo-url> jobmatch-mcp
cd jobmatch-mcp
uv sync --group dev
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and add at least one job source. Keep `.env` out of version control; it is already listed in `.gitignore`.

### 3. Start PostgreSQL

```bash
docker compose up -d postgres
```

This starts `pgvector/pgvector:pg17` on port `5432` with the default credentials from `.env.example`. Change them before any shared or production use.

### 4. Pull local models

```bash
ollama pull nomic-embed-text   # embeddings (768 dimensions)
ollama pull qwen2.5:7b         # local re-rank provider
```

### 5. Apply database migrations

```bash
uv run alembic upgrade head
```

### 6. Run the server

```bash
uv run python -m jobmatch_mcp              # stdio (default)
uv run python -m jobmatch_mcp --transport http
```

You can also run the installed script with `uv run jobmatch-mcp`.

## Connecting an MCP client

### Claude Desktop (stdio)

Add the server to `claude_desktop_config.json`, replacing the path with your checkout location:

```json
{
  "mcpServers": {
    "jobmatch": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/jobmatch-mcp",
        "python",
        "-m",
        "jobmatch_mcp"
      ]
    }
  }
}
```

Restart Claude Desktop, then ask it to parse your resume and find matching jobs.

### MCP Inspector or another HTTP client

HTTP transport requires a bearer token. The server refuses to start without one, and the token is never logged.

```bash
JOBMATCH_HTTP_BEARER_TOKEN=change-me uv run python -m jobmatch_mcp --transport http
```

- Endpoint: `http://127.0.0.1:8000/mcp/`
- Override host, port, and path with `--host`, `--port`, and `--path`, or with `JOBMATCH_HTTP_HOST`, `JOBMATCH_HTTP_PORT`, and `JOBMATCH_HTTP_PATH`.
- In MCP Inspector, choose **Streamable HTTP**, enter the endpoint, and add the header `Authorization: Bearer change-me`.

Use a long random token (for example, from `openssl rand -hex 32`). The HTTP transport is designed to bind to localhost in v1. Do not expose it to a network without a reverse proxy that terminates TLS.

## Configuration

All settings are read from environment variables prefixed with `JOBMATCH_`, and from a `.env` file in the working directory. `.env.example` lists every setting with its default value. The most important settings are below.

### Core

| Variable | Default | Description |
|---|---|---|
| `JOBMATCH_APP_ENV` | `local` | `local`, `test`, or `prod` |
| `JOBMATCH_LOG_LEVEL` | `INFO` | Log level |
| `JOBMATCH_DATABASE_URL` | `postgresql+psycopg://jobmatch:jobmatch@localhost:5432/jobmatch` | PostgreSQL connection string |
| `JOBMATCH_SEARCH_DEFAULT_LIMIT` | `30` | Default results per search (must be ≤ max) |
| `JOBMATCH_SEARCH_MAX_LIMIT` | `30` | Hard cap on results per search |
| `JOBMATCH_MIN_MATCH_SCORE` | `60` | Minimum score to return or persist a job (may only be raised) |
| `JOBMATCH_HTTP_TIMEOUT_SECONDS` | `10` | Timeout for source HTTP requests |
| `JOBMATCH_SOURCE_TIMEOUT_SECONDS` | `20` | Per-source timeout during a search |
| `JOBMATCH_ENABLE_ELICITATION` | `true` | Ask for location, remote, or salary when the client supports it |

### Transport

| Variable | Default | Description |
|---|---|---|
| `JOBMATCH_TRANSPORT` | `stdio` | `stdio` or `http` |
| `JOBMATCH_HTTP_HOST` | `127.0.0.1` | Bind address for HTTP |
| `JOBMATCH_HTTP_PORT` | `8000` | Port for HTTP |
| `JOBMATCH_HTTP_PATH` | `/mcp` | Endpoint path |
| `JOBMATCH_HTTP_BEARER_TOKEN` | *(empty)* | Required for HTTP transport |
| `JOBMATCH_HTTP_JSON_RESPONSE` | `false` | Pass-through to the FastMCP HTTP transport's JSON response mode |
| `JOBMATCH_HTTP_STATELESS` | `false` | Run without per-session state |

### Caching and rate limits

| Variable | Default | Description |
|---|---|---|
| `JOBMATCH_CONNECTOR_CACHE_TTL_SECONDS` | `300` | Response cache lifetime |
| `JOBMATCH_CONNECTOR_CACHE_MAX_ENTRIES` | `256` | Cache size per connector |
| `JOBMATCH_JSEARCH_RATE_LIMIT_PER_MINUTE` | `30` | JSearch requests per minute |
| `JOBMATCH_GREENHOUSE_RATE_LIMIT_PER_MINUTE` | `60` | Greenhouse requests per minute |
| `JOBMATCH_LEVER_RATE_LIMIT_PER_MINUTE` | `60` | Lever requests per minute |
| `JOBMATCH_SERPAPI_RATE_LIMIT_PER_MINUTE` | `30` | SerpAPI requests per minute |

### Ranking and LLMs

| Variable | Default | Description |
|---|---|---|
| `JOBMATCH_ENABLE_SEMANTIC` | `true` | Use embeddings for semantic scoring |
| `JOBMATCH_EMBEDDING_MODEL` | `nomic-embed-text` | Fixed per index. Changing it requires re-embedding |
| `JOBMATCH_EMBEDDING_BASE_URL` | `http://localhost:11434` | Ollama endpoint for embeddings |
| `JOBMATCH_EMBEDDING_MAX_JOBS` | `100` | Maximum jobs embedded per search |
| `JOBMATCH_ENABLE_LLM_RERANK` | `true` | Enable the LLM re-rank step |
| `JOBMATCH_RERANK_TOP_N` | `20` | Candidates re-ranked per search (max 30) |
| `JOBMATCH_RERANK_MAX_ADJUSTMENT` | `10` | Maximum score change from re-ranking (max 10) |
| `JOBMATCH_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint for re-ranking |
| `JOBMATCH_OLLAMA_MODEL` | `qwen2.5:7b` | Local re-rank model |
| `JOBMATCH_LLM_MAX_CONSECUTIVE_FAILURES` | `3` | Failures before a provider circuit opens |
| `JOBMATCH_LLM_CIRCUIT_COOLDOWN_SECONDS` | `60` | Time before a half-open probe |
| `JOBMATCH_LLM_MAX_OUTPUT_TOKENS` | `1500` | Output token cap per call |
| `JOBMATCH_DISABLE_HOSTED_FALLBACK` | `true` | When `true`, OpenAI and Claude are never called |
| `JOBMATCH_OPENAI_API_KEY` / `JOBMATCH_OPENAI_MODEL` | *(empty)* / `gpt-4o-mini` | Optional OpenAI fallback |
| `JOBMATCH_ANTHROPIC_API_KEY` / `JOBMATCH_ANTHROPIC_MODEL` | *(empty)* / `claude-3-5-haiku-latest` | Optional Claude fallback |

## Job sources

Enable any combination of sources. A search fans out across all enabled sources, and a failing source does not fail the search.

| Source | Configuration | Access |
|---|---|---|
| JSearch (RapidAPI) | `JOBMATCH_JSEARCH_API_KEY`, `JOBMATCH_JSEARCH_COUNTRY` | Licensed API. Subject to your RapidAPI plan and JSearch terms |
| Google Jobs (SerpAPI) | `JOBMATCH_SERPAPI_API_KEY`, `JOBMATCH_SERPAPI_COUNTRY` | Licensed aggregator. Subject to SerpAPI terms |
| Greenhouse | `JOBMATCH_GREENHOUSE_BOARDS=stripe,figma` (board tokens) | Public job-board feed |
| Lever | `JOBMATCH_LEVER_COMPANIES=netflix,spotify` (company tokens) | Public postings feed |

Every result includes source attribution and a link to the original posting.

**Not supported:** credential-based scraping of LinkedIn, Naukri, or Indeed. These platforms do not offer open job-search APIs, and scraping them violates their terms. Optional connectors for these sites may only use user-provided exports or job-alert emails.

Check connector health at runtime with the `list_sources` tool.

## MCP surface

### Tools

| Tool | Purpose | Key inputs |
|---|---|---|
| `parse_resume` | Parse and store a resume | `path` or `text` |
| `search_jobs` | Search, score, and rank jobs against the latest resume | `query`, `locations`, `remote`, `min_salary`, `sources`, `limit` (max 30) |
| `get_job_details` | Full stored record for one job, including the source link | `job_id` |
| `explain_match` | Matched and missing skills, seniority fit, suggestions, and score breakdown for the latest match | `job_id` |
| `list_sources` | Connector health, last success or failure, rate-limit state, and cache statistics | none |
| `get_provider_status` | LLM provider configuration, circuit state, recent latency, and estimated cost | none |

Tool outputs are typed and carry a `schema_version` field.

### Resources

| URI | Returns |
|---|---|
| `resume://current` | The latest parsed resume profile and its ID |
| `job://{id}` | One stored job with its source link |
| `search://{id}` | A saved search, its criteria, and its ranked results |

### Prompts

- `find_jobs_for_resume`: loads the resume, runs a search, and summarizes the top matches.
- `compare_top_matches`: compares the top results of a saved search side by side.

### Elicitation

When `JOBMATCH_ENABLE_ELICITATION=true` and the client supports it, `search_jobs` asks for a location, remote preference, or salary floor if none were supplied. Declining proceeds with the original criteria. Client cancellation is propagated to the caller and is not converted into a warning.

### Progress notifications

Multi-source searches send progress updates to clients that support them.

## LLM routing and privacy

- **Order:** Ollama (local) → OpenAI → Claude. A provider is skipped while its circuit is open and is probed again after the cooldown.
- **Fallback triggers:** timeouts, connection errors, rate limits, server errors, and invalid output after one retry.
- **Privacy switch:** with `JOBMATCH_DISABLE_HOSTED_FALLBACK=true` (the default), resume data is never sent to OpenAI or Claude. If local models are unavailable, searches still return structured-only results with warnings. The server never fabricates a score or explanation.
- **Re-rank bounds:** the LLM can adjust at most the top N results by ±10 points. Output must match a Pydantic schema, and out-of-range adjustments are clamped.
- **Prompt-injection controls:** job descriptions are treated as untrusted data. They are delimited in prompts, and model output is never interpreted as instructions or tool calls. The LLM cannot change scoring weights.
- **Accounting:** every provider call is recorded in `llm_calls` with provider, model, latency, token counts, estimated cost, and fallback reason. `get_provider_status` summarizes these records.
- **Embeddings:** one fixed embedding model per index, computed locally through Ollama. Changing the model requires re-embedding, because mixing vector spaces produces meaningless scores.

## Security

- **Resume data is PII.** It is stored only in your local PostgreSQL database. Logs never contain full resume text, and emails and phone numbers are redacted.
- **Secrets** are read from environment variables. They are not stored in code, images, or logs.
- **HTTP authentication:** bearer token required. The server refuses to start without one.
- **Hosted LLMs are opt-in.** Disable them with the privacy switch to keep all processing local.
- **Pin and scan dependencies.** `uv.lock` pins versions, and CI runs `pip-audit`.
- **Do not commit `.env`** or any file containing API keys.

## Observability

- Structured JSON logs with request IDs on every tool call.
- Redaction of emails, phone numbers, tokens, and resume text.
- Per-source health and cache statistics via `list_sources`.
- Per-provider latency, circuit state, and cost via `get_provider_status`.

## Development

### Common commands

```bash
uv sync --group dev                 # install runtime and dev dependencies
uv run ruff check .                 # lint
uv run ruff format --check .        # formatting check (use `ruff format .` to fix)
uv run mypy .                       # type checking (strict)
uv run pytest -q                    # tests
uv run pip-audit                    # dependency vulnerability scan
uv run alembic revision -m "msg"    # create a migration
```

### Environment for tests

Unit tests run without external services. Integration tests need PostgreSQL with pgvector. Set a test database URL and they run automatically:

```bash
docker compose up -d postgres
export JOBMATCH_TEST_DATABASE_URL=postgresql+psycopg://jobmatch:jobmatch@localhost:5432/jobmatch
uv run alembic upgrade head
uv run pytest -q
```

Without a test database, integration tests are skipped.

### Code style

- Line length 100, double quotes, Ruff rules: pycodestyle, pyflakes, isort, pyupgrade, bugbear, async, bandit, and ruff-specific rules.
- Mypy runs in strict mode for the application packages. Database and migration modules are excluded from strict checking.

## Testing and quality gates

| Layer | Location | Coverage |
|---|---|---|
| Unit | `tests/unit/` | Scoring, dedupe, circuit breaker, router, connectors, config, redaction, resume parsing, transport |
| Integration | `tests/integration/` | Repositories and end-to-end tool flow against PostgreSQL with pgvector |
| Ranking evaluation | `tests/eval/` | Ranking quality checks |
| MCP protocol | `tests/unit/test_protocol.py` | Tool listing and calls over the MCP protocol |

CI (`.github/workflows/ci.yml`) runs on every push and pull request:

1. `ruff check` and `ruff format --check`
2. `mypy .`
3. `pytest` against a pgvector service container
4. `pip-audit`

## Deployment

### Docker Compose

```bash
docker compose up -d postgres                       # database only
docker compose --profile app run --rm mcp-server    # MCP server container (stdio)
```

The `mcp-server` service is behind the `app` profile. It reads its database URL and source settings from the Compose file and your shell environment, so set API keys in `.env` or export them before running.

The image uses `uv sync --frozen`, so builds are reproducible from `uv.lock`. Ollama is not included in Compose. Run it on the host, or add it as a service if you need containerized local models.

### Production checklist

- Replace the default PostgreSQL credentials and restrict port `5432`.
- Use a strong `JOBMATCH_HTTP_BEARER_TOKEN` and place HTTP behind TLS.
- Keep `JOBMATCH_DISABLE_HOSTED_FALLBACK=true` unless you have approved sending resume data to a hosted provider.
- Run migrations (`alembic upgrade head`) before starting a new version.
- Set `JOBMATCH_APP_ENV=prod` and `JOBMATCH_LOG_LEVEL=INFO`.
- Ensure the Ollama host has enough memory for `qwen2.5:7b` and `nomic-embed-text`. CPU-only hosts work but are slower.

## Project structure

```
jobmatch_mcp/
  __main__.py        Entry point (stdio / HTTP)
  schemas.py         Versioned Pydantic input and output models
  core/              Config, errors, logging, redaction
  server/            FastMCP app, tools, resources, prompts, dependencies
  services/          Resume, search, match, and re-rank orchestration
  connectors/        Base interface, registry, rate limits, caching, one module per source
  matching/          Structured scoring, semantic scoring, skills, seniority, dedupe
  llm/               Router, providers, circuit breaker, prompts, embeddings
  db/                SQLAlchemy models, repositories, sessions
migrations/          Alembic migrations
tests/               Unit, integration, protocol, and evaluation tests
docs/                Product requirements and contributor rules
```

## Data model

| Table | Purpose |
|---|---|
| `users` | Single default user in v1. Kept for future multi-user support |
| `resumes` | Raw text, parsed profile (JSONB), embedding (vector) |
| `jobs` | Normalized postings with source, external ID, dedupe key, and embedding. Unique on (`source`, `external_id`) |
| `searches` | Criteria (JSONB) and the resume used |
| `matches` | Score, breakdown (JSONB), explanation, and rank per search and job |
| `llm_calls` | Provider, model, latency, tokens, cost estimate, status, and fallback reason |

Embeddings use HNSW indexes for approximate nearest-neighbor search.

## Limitations

- Single-user in v1. There is no authentication beyond a bearer token for HTTP.
- Search quality depends on source coverage. Some companies and regions are underrepresented in public feeds.
- Local model quality varies. Explanations from smaller models are less reliable, which is why output is schema-validated and clamped.
- Scores are heuristic. They are useful for ranking, not as a hiring prediction.
- Resume parsing is rule-based and may miss unusual layouts, especially scanned PDFs.

## Contributing

1. Read the repository rules in `docs/AGENTS.md` before making changes.
2. Keep changes small and focused. Follow the existing patterns, libraries, and pinned versions.
3. Add or update tests for behavior changes. Prompt changes need prompt-injection test cases.
4. Before opening a pull request, run the full set of quality gates in [Development](#development).

## License

No license has been specified for this repository yet. Until one is added, all rights are reserved by the author.
