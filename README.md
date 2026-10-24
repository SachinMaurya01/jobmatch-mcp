Built
- FastMCP stdio server with parse_resume, search_jobs, get_job_details, list_sources
- Pluggable connectors for JSearch (RapidAPI), Greenhouse public boards, Lever public postings, and Google Jobs via SerpAPI, all behind one interface and registry
- Per-source sliding-window rate limiting, TTL/LRU response caching, per-source timeouts, and secret-safe errors
- Cross-source deduplication that merges duplicate postings by normalized title, company, and location; each result records all contributing sources
- Partial-result handling: a failing source produces warnings and a degraded status while healthy sources still return ranked results
- MCP progress notifications during multi-source searches when the client supports them
- list_sources reports connector health, last success/failure, rate-limit state, and cache statistics
- Resume parsing for .txt, .md, .pdf; structured profile extraction (skills, years, seniority, roles, locations, education)
- Structured scoring with configurable weights, missing-signal redistribution (e.g. no salary), deterministic explanations, and ranking
- Jobs scoring below 60 are never ranked, persisted, or returned; each search is capped at 30 results
- PostgreSQL + pgvector models and Alembic migration for users, resumes, jobs, searches, matches
- JSON logging with email/phone redaction, config via JOBMATCH_*, Dockerfile, Compose, .env.example, GitHub Actions
- Unit, connector contract, and PostgreSQL integration tests

Verification
- ruff check / ruff format --check: passed
- mypy strict: passed
- pytest: 56 passed, 3 skipped without an integration database
- pip-audit: no known vulnerabilities

## Run

- cp .env.example .env          # fill at least one source
- docker compose up -d postgres
- uv run alembic upgrade head
- uv run python -m jobmatch_mcp

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
