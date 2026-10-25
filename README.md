Built
- FastMCP stdio server with parse_resume, search_jobs, get_job_details, list_sources, explain_match, get_provider_status
- Pluggable connectors for JSearch (RapidAPI), Greenhouse public boards, Lever public postings, and Google Jobs via SerpAPI, all behind one interface and registry
- Per-source sliding-window rate limiting, TTL/LRU response caching, per-source timeouts, and secret-safe errors
- Cross-source deduplication that merges duplicate postings by normalized title, company, and location; each result records all contributing sources
- Partial-result handling: a failing source produces warnings and a degraded status while healthy sources still return ranked results
- MCP progress notifications during multi-source searches when the client supports them
- list_sources reports connector health, last success/failure, rate-limit state, and cache statistics
- Resume parsing for .txt, .md, .pdf; structured profile extraction (skills, years, seniority, roles, locations, education)
- Hybrid scoring: configurable structured weights plus local Ollama embeddings stored in pgvector, with missing-signal redistribution
- Bounded LLM re-rank over the strongest matches (top 20, ±10 points) with per-job explanations and suggestions
- Provider router with fixed Ollama → OpenAI → Claude fallback, per-provider circuit breakers, call accounting, and a local-only privacy switch
- explain_match returns the stored breakdown, matched/missing skills, seniority fit, and suggestions; get_provider_status reports circuit state, latency, and cost
- Jobs scoring below 60 are never ranked, persisted, or returned; each search is capped at 30 results
- PostgreSQL + pgvector models and Alembic migrations for users, resumes, jobs, searches, matches, llm_calls, and HNSW embedding indexes
- JSON logging with email/phone redaction, config via JOBMATCH_*, Dockerfile, Compose, .env.example, GitHub Actions
- Unit, connector contract, ranking-quality evaluation, router failure-injection, and PostgreSQL integration tests

Verification
- ruff check / ruff format --check: passed
- mypy strict: passed
- pytest: 83 passed, 4 skipped without an integration database; 87 passed with pgvector (temporary container)
- pip-audit: no known vulnerabilities

## Run

- cp .env.example .env          # fill at least one source
- docker compose up -d postgres
- ollama pull nomic-embed-text  # local embeddings
- ollama pull qwen2.5:7b        # local re-rank provider
- uv run alembic upgrade head
- uv run python -m jobmatch_mcp

Hosted fallback stays disabled by default. To allow OpenAI or Claude, set the
matching API key and `JOBMATCH_DISABLE_HOSTED_FALLBACK=false`. If local models
are unavailable, searches still return structured-only results with warnings.

## Hybrid ranking and LLM routing

- `JOBMATCH_EMBEDDING_MODEL` must stay fixed for an index; changing it triggers
  re-embedding on the next resume/search. Embeddings are local via Ollama and
  must produce 768-dimensional vectors (the default `nomic-embed-text` does).
- The router always tries Ollama first, then OpenAI, then Claude. A provider is
  skipped while its circuit is open and probed again after the cooldown.
- Re-rank adjusts at most the top `JOBMATCH_RERANK_TOP_N` results by
  `±JOBMATCH_RERANK_MAX_ADJUSTMENT` points; output is schema-validated and
  clamped. Every attempt is recorded in `llm_calls`.
- `get_provider_status` shows configuration, circuit state, recent latency, and
  estimated cost. `explain_match` explains a job from the latest stored search.

## Source configuration

Enable any combination of sources; searches fan out across all enabled sources.

- JSearch: `JOBMATCH_JSEARCH_API_KEY` (RapidAPI key)
- Greenhouse: `JOBMATCH_GREENHOUSE_BOARDS=stripe,figma` (public board tokens)
- Lever: `JOBMATCH_LEVER_COMPANIES=netflix,spotify` (public company tokens)
- Google Jobs: `JOBMATCH_SERPAPI_API_KEY` (SerpAPI key)

Cache and rate limits are configurable per source in `.env.example`.

## Data sources and terms

- JSearch: licensed API via RapidAPI; requests are subject to the RapidAPI plan and JSearch terms.
- Greenhouse and Lever: public job-board feeds for publicly listed postings; source attribution and the original posting link are always returned.
- SerpAPI: licensed Google Jobs aggregator; requests are subject to SerpAPI terms.
- No credential-based scraping is performed for LinkedIn, Naukri, or Indeed.

- psql -h <REMOTE_HOST> -p 5432 -U <USERNAME> -d <DATABASE_NAME>
