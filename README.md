Built
- FastMCP stdio server with parse_resume, search_jobs, get_job_details (jobmatch_mcp/server/app.py)
- JSearch (RapidAPI) connector behind the connector interface + registry, with normalization, source links, and secret-safe errors
- Resume parsing for .txt, .md, .pdf; structured profile extraction (skills, years, seniority, roles, locations, education)
- Structured scoring with PRD weights, missing-signal redistribution (e.g. no salary), deterministic explanations, and ranking (jobmatch_mcp/matching/structured.py)
- Jobs scoring below 60 are never ranked, persisted, or returned; each search is capped at 30 results
- PostgreSQL + pgvector models and Alembic migration for users, resumes, jobs, searches, matches
- JSON logging with email/phone redaction, config via JOBMATCH_*, Dockerfile, Compose, .env.example, GitHub Actions
- Unit and PostgreSQL integration tests


Verification
- ruff check / ruff format --check: passed
- mypy strict: passed
- pytest: 32 passed, 2 skipped without DB; 34 passed against an isolated PostgreSQL 18 + pgvector cluster
- pip-audit: no known vulnerabilities (FastMCP upgraded to 3.4.x to clear advisories)
- Docker daemon was unavailable, so Compose build was not run; the Alembic migration was validated online and offline

Run Phase 1:

cp .env.example .env          # fill JSearch RapidAPI key
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m jobmatch_mcp