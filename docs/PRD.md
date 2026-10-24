# PRD: Job-Match MCP Server

| | |
|---|---|
| **Working title** | JobMatch MCP |
| **Status** | Draft v0.1 |
| **Date** | 2026-10-21 |
| **Owner** | Project author (single contributor) |
| **Purpose** | Production-grade portfolio project demonstrating deep MCP knowledge for senior engineering roles |

---

## 1. Overview

### 1.1 Problem
Job seekers search several platforms separately, re-read near-identical postings, and have no consistent way to judge how well a posting fits their resume. Results are ranked by recency or paid placement, not by fit.

### 1.2 Solution
An MCP server that takes a candidate's resume, searches multiple job sources through pluggable connectors, deduplicates the results, scores each job against the resume with a hybrid (structured + semantic + LLM) pipeline, and returns a list ranked by match score with a short explanation for each score.

### 1.3 Goals
1. Return a ranked list of relevant jobs, highest match first, each with a 0-100 score and an explanation.
2. Cover multiple sources behind one connector interface (LinkedIn, Naukri, Indeed and others, subject to the compliance rules in section 7).
3. Show senior-level MCP depth: tools, resources, prompts, elicitation, progress notifications, structured outputs, multiple transports, versioned schemas.
4. Run locally first (Ollama / Qwen) with automatic fallback to hosted models (OpenAI, then Claude).
5. Meet production standards: observability, resilience, security, tests, evaluation, CI/CD, documentation.

### 1.4 Non-goals (v1)
- Application tracking, alerts for new matches, resume tailoring (future enhancements).
- Multi-user accounts and OAuth (planned for a later phase; v1 is single-user).
- A custom web UI. The MCP client is the interface.
- Auto-applying to jobs.

---

## 2. Users and Use Cases

**Primary user (v1):** one job seeker (the author) using an MCP client.

| # | Use case | Example |
|---|---|---|
| UC1 | Load a resume | "Use my resume at ~/resume.pdf" |
| UC2 | Find matching jobs | "Find senior Python/AI roles in Delhi or remote" |
| UC3 | Understand a score | "Why is job #3 a 72? What am I missing?" |
| UC4 | Compare top jobs | "Compare my top 3 matches" |
| UC5 | Refine search | Server asks for missing location or salary via elicitation |
| UC6 | Check system health | "Which LLM provider is serving requests?" |

**MCP clients (v1):** Claude Desktop (free plan, local stdio servers), MCP Inspector for development and testing, Claude Code / Cursor as optional extras.

---

## 3. Decisions Summary

| Area | Decision |
|---|---|
| Language / framework | Python, FastMCP, Pydantic v2 |
| Matching | Hybrid: structured scoring + pgvector semantic similarity + LLM re-rank with explanations |
| Storage | PostgreSQL with pgvector |
| Job source (Phase 1) | JSearch via RapidAPI; additional connectors optional |
| Result filtering | Exclude jobs scoring below 60; return at most 30 ranked jobs per search |
| LLM routing | Ollama (Qwen) first, then OpenAI, then Claude; automatic fallback on failure |
| Deployment | Docker Compose |
| Users | Single-user v1; `user_id` present in the schema from day one |
| Transports | stdio (local) and Streamable HTTP (hosted-ready) |
| Future scope | Application tracking, alerts, resume tailoring, multi-user auth |

---

## 4. Functional Requirements

### 4.1 MCP surface

**Tools**

| Tool | Purpose | Key inputs | Output (structured) |
|---|---|---|---|
| `parse_resume` | Parse a resume file or text into a structured profile and store embeddings | `path` or `text` | Profile: skills, years of experience, seniority, roles, locations, education |
| `search_jobs` | Search all enabled sources, dedupe, score, rank | `query?`, `locations?`, `remote?`, `min_salary?`, `sources?`, `limit?` (max 30) | Ranked list (score 60-100, max 30): job, score, score breakdown, short reason |
| `get_job_details` | Full details of one job | `job_id` | Job record plus source link |
| `explain_match` | Detailed explanation of one score | `job_id` | Matched skills, missing skills, seniority fit, suggestions |
| `list_sources` | Show enabled connectors and their health | none | Source name, status, last success, rate-limit state |
| `get_provider_status` | Show LLM provider health and recent routing | none | Provider, status, circuit state, recent latency and cost |

**Resources**
- `resume://current`: the parsed profile.
- `job://{id}`: a job record.
- `search://{id}`: a saved search with its ranked results.

**Prompts**
- `find_jobs_for_resume`: guided workflow (load resume, search, summarize top matches).
- `compare_top_matches`: side-by-side comparison of the top N jobs.

**Protocol features**
- **Elicitation:** ask the user for missing details (location, salary floor, remote preference) instead of guessing.
- **Progress notifications:** report per-source progress during long searches.
- **Structured tool output:** typed output schemas, versioned (`schema_version` field).
- **Cancellation and timeouts:** honor client cancellation; per-tool and per-source timeouts.
- **Sampling (optional, later phase):** use client-side LLM when the client supports it.
- **Capability negotiation:** degrade gracefully when the client lacks elicitation or progress support.

### 4.2 Matching pipeline

1. **Resume profile.** Parse the resume into structured fields; chunk and embed it.
2. **Candidate retrieval.** Query connectors; normalize each job to a common schema; dedupe across sources (normalized title + company + location, plus embedding similarity as a tiebreaker).
3. **Structured score.** Rule-based comparison on skills overlap, years of experience, seniority, location, salary.
4. **Semantic score.** Cosine similarity between resume and job embeddings (pgvector).
5. **Combine.** Weighted sum to a 0-100 score (initial weights below, tunable via config and the evaluation set).
6. **LLM re-rank.** Top N (default 20) are re-ranked by the LLM, which returns a short explanation and adjusted score within a bounded range (for example plus or minus 10 points) so the LLM cannot override the structured signal entirely.
7. **Filter and output.** Drop every posting scoring below 60. Sort the remainder descending by final score, include the score breakdown, and return at most 30 jobs per search.

Initial weights (to be tuned by evaluation):

| Component | Weight |
|---|---|
| Skills overlap | 40% |
| Semantic similarity | 25% |
| Experience match | 15% |
| Seniority match | 10% |
| Location / remote fit | 5% |
| Salary fit | 5% |

If salary is missing from a posting, its weight is redistributed rather than scored as zero.

The minimum match score is 60 and may only be raised. Jobs below it are not ranked, persisted, included in saved searches or matches, or returned anywhere. A search fetches and returns at most 30 jobs at a time; jobs above the threshold but beyond the 30-result cap are discarded for that request.

### 4.3 LLM routing

- **Order:** Ollama (Qwen), then OpenAI, then Claude.
- **Fallback triggers:** timeout, connection error, rate limit, 5xx, invalid or unparseable output after retry.
- **Provider abstraction:** a single interface (`generate`, `generate_structured`) with one adapter per provider.
- **Circuit breaker per provider:** open after consecutive failures, half-open probe after cooldown.
- **Observability per call:** provider, model, latency, input/output tokens, estimated cost, fallback reason.
- **Privacy switch:** config flag to disable hosted fallback so resume data never leaves the machine.
- **Embeddings:** one fixed embedding model per index (to keep the vector space consistent); changing the model requires a re-embed job. Embedding model choice is an open question (section 12).

---

## 5. Architecture

### 5.1 Components

```
MCP Client (Claude Desktop / Inspector)
        |  stdio or Streamable HTTP
        v
+---------------------------------------------+
| MCP Server (FastMCP)                        |
|  tools / resources / prompts / elicitation  |
+---------------------------------------------+
        |
        v
+---------------------------------------------+
| Application layer                           |
|  ResumeService  SearchService  MatchService |
+---------------------------------------------+
   |            |               |
   v            v               v
Connectors   Matching        LLM Router
(per source) pipeline        Ollama -> OpenAI -> Claude
   |            |               |
   v            v               v
 External    PostgreSQL      Providers
 job APIs    + pgvector      (circuit breakers)
```

### 5.2 Connector interface
Each source implements one interface: `search(criteria) -> list[RawJob]`, `health() -> Status`, plus declared rate limits and capabilities. A registry enables or disables connectors by config. Connectors never contain scoring logic.

### 5.3 Suggested package layout
```
jobmatch_mcp/
  server/        # FastMCP app, tools, resources, prompts
  services/      # resume, search, match orchestration
  connectors/    # base.py, one module per source
  matching/      # structured scoring, semantic scoring, re-rank, dedupe
  llm/           # router, providers, circuit breaker, cost tracking
  db/            # models, migrations (Alembic), repositories
  core/          # config, logging, telemetry, errors
tests/
  unit/ integration/ contract/ eval/
```

---

## 6. Data Model (initial)

| Table | Key fields |
|---|---|
| `users` | `id`, created_at (single row in v1, kept for multi-user later) |
| `resumes` | `id`, `user_id`, raw_text, parsed_profile (JSONB), embedding (vector), created_at |
| `jobs` | `id`, source, external_id, title, company, location, remote, salary_min/max, description, url, posted_at, dedupe_key, embedding (vector), fetched_at |
| `searches` | `id`, `user_id`, `resume_id`, criteria (JSONB), created_at |
| `matches` | `id`, `search_id`, `job_id`, score, breakdown (JSONB), explanation, rank |
| `llm_calls` | `id`, provider, model, latency_ms, tokens_in/out, cost_estimate, status, fallback_reason, created_at |

Indexing: HNSW (or IVFFlat) index on embeddings; unique constraint on (`source`, `external_id`); index on `dedupe_key`. Migrations managed with Alembic.

---

## 7. Data Sources and Compliance

LinkedIn, Naukri and Indeed do not offer open public job-search APIs, and scraping them violates their terms and is brittle. The design therefore treats them as optional connectors and starts with legitimate sources.

| Tier | Sources (examples) | Approach |
|---|---|---|
| Phase 1-2 default | JSearch (RapidAPI) first, plus Google Jobs via SerpAPI, Greenhouse and Lever public job feeds | Official or licensed APIs and public feeds |
| Optional | LinkedIn, Naukri, Indeed | User-provided exports or email/RSS job alerts parsed into the common schema; no credential-based scraping |

Requirements:
- Each connector documents its terms-of-service position in its own README.
- Respect robots.txt and published rate limits; per-source rate limiter and cache.
- Job descriptions are untrusted input (see prompt-injection controls in section 8).
- Source attribution and a link to the original posting are always returned.

---

## 8. Non-Functional Requirements

### 8.1 Security and privacy
- Resume data is PII: stored locally, never logged in full; log redaction for emails and phone numbers.
- Secrets via environment variables / Docker secrets; none in code or images.
- **Prompt-injection defenses:** treat job text as data; delimit it in prompts; strict output schemas; LLM output cannot trigger tool calls or change scoring weights.
- HTTP transport in v1 binds to localhost with a bearer token; OAuth 2.1 (per the MCP authorization spec) is a later phase.
- Dependency and image scanning in CI; pinned dependencies.
- Config flag to disable hosted LLM fallback.

### 8.2 Reliability
- Timeouts, retries with exponential backoff and jitter, circuit breakers per source and per LLM provider.
- Partial results: if one source fails, return results from the others with a warning and per-source status.
- Idempotent ingestion (upsert on source + external_id).

### 8.3 Performance (initial targets, to be validated)
- First ranked results for a typical search: under 30 seconds with local Qwen, with progress notifications throughout.
- Cached repeat searches: under 3 seconds.
- Re-rank limited to top N to bound cost and latency.

### 8.4 Observability
- Structured JSON logs with request and trace IDs.
- OpenTelemetry traces across tool call, connectors, DB and LLM calls.
- Metrics: tool latency, per-source success rate, cache hit rate, LLM latency/tokens/cost by provider, fallback count.
- Health endpoint for the HTTP transport.

### 8.5 Maintainability
- Typed code (mypy strict on core packages), ruff, pre-commit.
- Versioned tool schemas with a changelog; backward-compatible changes within a minor version.
- Architecture Decision Records for key choices (hybrid scoring, routing, connector approach).

---

## 9. Testing and Evaluation

| Layer | Approach |
|---|---|
| Unit | Scoring functions, dedupe, router and circuit breaker logic |
| Integration | Postgres + pgvector via Docker; connector tests against recorded responses |
| Contract | Each connector validated against the common job schema |
| MCP protocol | End-to-end tests using an MCP client harness (tool listing, calls, elicitation, progress, cancellation) |
| LLM | Provider adapters tested with mocks and failure injection (timeouts, malformed output, rate limits) |
| Evaluation | Labelled set of resume-job pairs; track precision@k and NDCG for ranking quality; compare structured-only vs hybrid vs hybrid + LLM re-rank |

CI runs lint, type checks, tests and security scans on every push; the evaluation suite runs on demand and before releases.

---

## 10. Deployment (Docker Compose, v1)

| Service | Purpose |
|---|---|
| `mcp-server` | The FastMCP application (HTTP transport; stdio run locally) |
| `postgres` | PostgreSQL with pgvector extension |
| `ollama` | Local model host (Qwen and the embedding model) |
| `otel-collector` (optional) | Telemetry export |

Requirements: health checks, named volumes for Postgres and model files, `.env.example`, one-command start, documented GPU/CPU notes for Ollama.

---

## 11. Phases

### Phase 1: Core server (foundation)
- Project scaffolding, config, logging, CI, Docker Compose with Postgres + pgvector.
- FastMCP server over stdio; tools `parse_resume`, `search_jobs`, `get_job_details`.
- One connector, basic structured scoring, ranked output.
- **Exit:** a user can load a resume in Claude Desktop and get a ranked list with scores.

### Phase 2: Multi-source and deduplication
- Connector interface and registry; 2-3 additional sources; per-source rate limiting and caching.
- Cross-source deduplication; `list_sources`; partial-result handling.
- Progress notifications.
- **Exit:** results from multiple sources merge into one deduplicated list; one failing source does not fail the search.

### Phase 3: Hybrid ranking and LLM router
- Embeddings and pgvector similarity; hybrid score; LLM re-rank with explanations.
- LLM router: Ollama, OpenAI, Claude with fallback, circuit breakers, cost and latency tracking.
- `explain_match`, `get_provider_status`.
- **Exit:** ranking quality measurably beats structured-only on the evaluation set; fallback demonstrated by failure injection.

### Phase 4: Full MCP surface and hosted-ready transport
- Resources and prompts; elicitation; cancellation; versioned structured outputs.
- Streamable HTTP transport with bearer token; persistence of searches and matches.
- **Exit:** works in Claude Desktop (stdio) and MCP Inspector (HTTP); protocol tests pass.

### Phase 5: Production hardening
- OpenTelemetry traces and metrics; log redaction; prompt-injection test cases.
- Evaluation suite and report; load and failure tests; dependency and image scanning.
- Documentation: README, architecture diagram, ADRs, runbook, demo script.
- **Exit:** all non-functional targets checked; public-ready repository.

### Phase 6: Future enhancements (out of v1)
- Multi-user support with OAuth 2.1 and tenant isolation.
- Application tracking, new-match alerts, resume tailoring.
- MCP sampling support; Kubernetes and Terraform deployment.

---

## 12. Open Questions

1. **Embedding model:** which model (local via Ollama vs hosted)? It must stay fixed per index; fallback for embeddings may be limited to local only.
2. **Resume formats:** PDF and DOCX at launch, or plain text first?
3. **Re-rank size (N) and score adjustment bounds:** defaults of 20 and plus or minus 10 are placeholders for the evaluation to tune.
4. **Region focus:** India-first search defaults, or location-agnostic?
5. **Evaluation data:** how to build the labelled resume-job set without exposing real personal data.

---

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| No official APIs for LinkedIn / Naukri / Indeed | Core promise of coverage weakened | Pluggable connectors; start with licensed sources; optional export/alert-based connectors; transparent documentation |
| Local model quality (Qwen) varies | Poor explanations or invalid output | Strict output schemas, retry, fall back to OpenAI/Claude, evaluation per provider |
| Prompt injection via job text | Manipulated scores or leaked data | Data delimiting, schema-constrained output, no tool access from LLM, test cases |
| Resume PII exposure to hosted LLMs | Privacy harm | Hosted fallback toggle, redaction option, local-first default |
| API rate limits and cost | Failed or expensive searches | Caching, rate limiters, bounded re-rank size, cost tracking |
| Scope creep | Project never reaches a showable state | Phased exit criteria; future features explicitly deferred |

---

## 14. Success Metrics

- Ranking quality: precision@10 and NDCG@10 on the evaluation set, with hybrid + re-rank above structured-only.
- Relevance floor: no returned or persisted job scores below 60; each search returns at most 30 jobs.
- Reliability: search succeeds with partial results when any single source or provider is down (verified by failure-injection tests).
- Fallback: demonstrated Ollama to OpenAI to Claude chain with recorded routing in `llm_calls`.
- Protocol coverage: tools, resources, prompts, elicitation, progress and cancellation all exercised by automated tests.
- Portfolio readiness: one-command start, architecture diagram, ADRs, runbook and a recorded demo.