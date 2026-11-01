# Changelog

All notable changes to JobMatch MCP are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-01

First public release. This version delivers the complete local-first job matching
server: MCP tools, resources, and prompts; multi-source search; hybrid ranking;
bounded LLM re-ranking; both stdio and HTTP transports; and production
observability.

**Release highlights:** multi-source search with deduplication and partial
results, hybrid structured + pgvector semantic ranking, a bounded Ollama → OpenAI
→ Claude re-rank router with circuit breakers and a privacy switch, the full MCP
surface over stdio and Streamable HTTP, and a provisioned Prometheus + Tempo +
Grafana observability stack.

**Requirements:** Python 3.12+ (container uses 3.13), PostgreSQL 17 with
pgvector, and optionally Ollama for local embeddings and re-ranking.

### Added

#### MCP surface

- Six MCP tools: `parse_resume`, `search_jobs`, `get_job_details`,
  `explain_match`, `list_sources`, and `get_provider_status`.
- Three MCP resources: `resume://current`, `job://{id}`, and `search://{id}`.
- Two MCP prompts: `find_jobs_for_resume` and `compare_top_matches`.
- Elicitation for missing search preferences (location, remote, salary) with
  graceful degradation when the client does not support it.
- Per-source progress notifications during searches.
- Cancellation and per-tool/per-source timeouts.
- Versioned structured tool outputs (`schema_version`).

#### Resume processing

- Resume parsing from `.txt`, `.md`, and `.pdf` files or raw text.
- Structured extraction of skills, years of experience, seniority, roles,
  locations, and education.
- Resume persistence with local embeddings for semantic matching.
- Configurable maximum resume size and safety checks for untrusted files.

#### Job search and connectors

- JSearch (RapidAPI) connector.
- Greenhouse public job board connector.
- Lever public postings connector.
- SerpAPI Google Jobs connector.
- Connector interface and registry with per-connector capabilities.
- Cross-source deduplication by normalized title, company, and location, merging
  source attribution into one posting.
- Partial-result handling: a failing source produces a warning and degraded
  status while healthy sources still return results.
- Per-source sliding-window rate limiting.
- Per-source TTL/LRU response cache with hit/miss statistics.
- Remote and minimum-salary filtering.
- Result cap of 30 jobs per search and a configurable minimum match score
  (default 60).

#### Ranking

- Rule-based structured scoring for skills overlap, experience, seniority,
  location/remote fit, and salary fit.
- Automatic weight redistribution when a signal (for example salary) is missing.
- Local embeddings through Ollama with pgvector storage and HNSW indexes.
- Hybrid structured plus semantic scoring with configurable weights.
- Bounded LLM re-ranking over the top 20 candidates with a maximum adjustment of
  ±10 points.
- Per-job explanations, matched/missing skills, seniority fit, and suggestions.
- Ranking evaluation using NDCG@10.

#### LLM routing

- Fixed provider order: Ollama (primary), OpenAI (fallback), Claude (fallback).
- Provider adapter interface with structured output support.
- Retry on invalid output, then fallback to the next provider.
- Per-provider circuit breakers with cooldown and half-open probes.
- Per-call accounting for provider, model, latency, input/output tokens,
  estimated cost, and fallback reason.
- Privacy switch that disables hosted fallbacks so resume data stays local.
- Provider status reporting with circuit state and recent calls.

#### Persistence and data

- Async SQLAlchemy engine and session management.
- Alembic migrations for resumes, jobs, searches, matches, and LLM calls.
- Stored searches and ranked matches exposed through MCP resources.
- Docker Compose with PostgreSQL 17 and pgvector, plus a local pgAdmin service.

#### Transports and deployment

- stdio transport for desktop clients such as Claude Desktop.
- Streamable HTTP transport with bearer-token authentication.
- Configurable HTTP path, host, port, JSON response mode, and stateless mode.
- `GET /health` probe that checks database reachability.
- Dockerfile and Docker Compose support for the MCP server.

#### Observability

- Structured JSON logs on stderr with `request_id`, `trace_id`, and `span_id`.
- OpenTelemetry traces for tool calls, connector searches, LLM attempts, and
  database sessions.
- OpenTelemetry metrics for tool latency/counts, connector latency, cache
  events, LLM latency/tokens/cost/fallbacks, and database session latency.
- Opt-in exporters: `none`, `console`, and OTLP over gRPC or HTTP.
- Standard `OTEL_*` environment variable support.
- Self-contained Prometheus + Tempo + Grafana stack in `deploy/observability/`.
- Provisioned Grafana dashboard (`jobmatch-mcp-live`) covering live traces and
  metrics, with 10-second refresh.

#### Security and privacy

- Redaction of emails, phone numbers, bearer tokens, and credential-like log
  fields.
- Secret-safe error messages that do not echo API keys or request headers.
- Untrusted job text delimited and labelled as data in LLM prompts.
- Strict schema validation and clamping of LLM score adjustments.
- Prompt-injection test coverage for the re-ranking flow.
- No credential-based scraping of LinkedIn, Naukri, or Indeed; search uses
  official APIs and public board feeds.
- Source attribution and original posting links on every result.

#### Tooling, tests, and documentation

- Ruff linting and formatting, plus strict mypy checking.
- 113 unit and evaluation tests, including connector, protocol, transport,
  ranking, redaction, telemetry, prompt-injection, and resilience coverage.
- Integration tests against PostgreSQL with pgvector.
- Load and failure tests for concurrent timeouts, degraded sources, cache
  behavior, and concurrent LLM failures.
- `pip-audit` dependency scanning.
- README with quick start, configuration, MCP surface, and deployment guidance.
- Operations runbook with health checks, telemetry, failure drills, and security
  scans.
- Architecture decision records for OpenTelemetry and untrusted job input.
- `.env.example` covering application, database, sources, ranking, LLM, privacy,
  and telemetry settings.

### Changed

- Upgraded `pypdf` from 6.21.2 to 6.21.4 to address known vulnerabilities.
- Consolidated OpenTelemetry setup into `jobmatch_mcp.core.telemetry` with
  idempotent initialization and clean shutdown.
- Reworked JSON logging so caller-supplied structured fields, trace context, and
  request ids are merged rather than dropped.
- Instrumented connector runtime, LLM router, and database sessions with spans
  and metrics.

### Fixed

- Corrected OpenTelemetry span usage so tool calls, connectors, and LLM attempts
  record attributes, status, and exceptions on real spans.
- Added missing `opentelemetry.trace` imports in connectors.
- Removed duplicated unreachable error-handling code in the JSearch connector.
- Fixed log records overriding the active request id with the span id.

### Security

- Added key-based redaction for `token`, `api_key`, `authorization`, `password`,
  `secret`, and related credential fields.
- Added bearer-token masking in free-form log messages and errors.
- Added prompt-injection containment tests to guard future prompt changes.

### Known limitations

- Single-user local deployment only; OAuth 2.1 and tenant isolation are not part
  of this release.
- Hosted LLM fallbacks are disabled by default and require explicit opt-in.
- Image scanning is documented but is not yet part of the automated pipeline.
- Job sources remain subject to their own API plans, quotas, and terms.
