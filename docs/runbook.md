# Operations runbook

This runbook covers observability, health checks, failure drills, and the
security scans for the JobMatch MCP server.

## Health check

When the server runs over Streamable HTTP, probe `/health`:

```bash
curl -sS http://127.0.0.1:8000/health
```

- `200 {"status":"ok","database":"ok"}` — process is up and PostgreSQL answers
  `SELECT 1`.
- `503 {"status":"degraded","database":"unavailable"}` — the process is up but
  the database is unreachable. Check `JOBMATCH_DATABASE_URL`, container health,
  and network access.

The stdio transport does not expose HTTP endpoints. Use the MCP client's tool
list and `list_sources` for a functional check instead.

## Logs

All logs are single-line JSON on stderr so they never corrupt the stdio
protocol stream. Every record includes:

| Field | Meaning |
|---|---|
| `timestamp` | UTC ISO-8601 timestamp |
| `level` | Log level |
| `logger` | Logger name |
| `message` | Rendered message, with PII redacted |
| `request_id` | Correlation id for the current tool call |
| `trace_id` | W3C trace id when a span is active |
| `span_id` | Current span id when a span is active |

Emails, phone numbers, `Bearer` tokens, and credential-like fields (`token`,
`api_key`, `authorization`, `password`, `secret`, ...) are redacted before
serialization. Resume text is never logged.

## Traces and metrics

Tracing and metrics are no-ops by default. Enable exporters with environment
variables:

```bash
JOBMATCH_OTEL_ENABLED=true
JOBMATCH_OTEL_SERVICE_NAME=jobmatch-mcp
JOBMATCH_OTEL_EXPORTER=otlp          # none | console | otlp
JOBMATCH_OTEL_ENDPOINT=http://127.0.0.1:4317
JOBMATCH_OTEL_METRICS_INTERVAL_SECONDS=30
```

Standard OpenTelemetry variables also work and take precedence for endpoints:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4317
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4317
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc    # use http/protobuf for HTTP exporters
```

Set `JOBMATCH_OTEL_EXPORTER=console` to print spans and metrics locally while
debugging.

### Span names

| Span | Location |
|---|---|
| `parse_resume`, `search_jobs`, `get_job_details`, `list_sources`, `explain_match`, `get_provider_status` | MCP tools |
| `connector.search` | Per-source runtime wrapper (cache, rate limit, timeout) |
| `connector.<source>.search` / `.health` | JSearch and Greenhouse adapters |
| `llm.generate_structured` | Every LLM provider attempt |
| `db.session` | Database session scope |

### Metrics

| Metric | Type | Attributes |
|---|---|---|
| `mcp.tool.calls` / `mcp.tool.duration` | counter / histogram (ms) | `tool`, `status` |
| `connector.search.calls` / `connector.search.duration` | counter / histogram (ms) | `source`, `status` |
| `connector.cache.events` | counter | `source`, `result` (`hit`/`miss`) |
| `llm.calls` / `llm.call.duration` | counter / histogram (ms) | `provider`, `model`, `status` |
| `llm.tokens.input` / `llm.tokens.output` | counter | `provider`, `model` |
| `llm.cost.estimated` | counter (USD) | `provider`, `model` |
| `llm.fallbacks` | counter | `provider`, `reason` |
| `db.query.duration` | histogram (ms) | `operation`, `status` |

## Grafana stack

`deploy/observability/` contains a local Prometheus + Tempo + Grafana stack with
provisioned datasources and the **JobMatch MCP — Live Traces & Metrics**
dashboard (`uid: jobmatch-mcp-live`).

```bash
docker compose -f deploy/observability/docker-compose.yml up -d
```

| Service | URL | Purpose |
|---|---|---|
| Grafana | http://localhost:3000 | Dashboards (anonymous admin in this local stack) |
| Prometheus | http://localhost:9090 | Metric storage and ad-hoc queries |
| Tempo | http://localhost:3200 | Trace storage |
| OTel Collector | `localhost:4317` (gRPC), `localhost:4318` (HTTP) | OTLP ingest |

Point the MCP server at the collector and restart it:

```bash
JOBMATCH_OTEL_EXPORTER=otlp
JOBMATCH_OTEL_ENDPOINT=http://127.0.0.1:4317
```

When the server and collector share a Compose network, use
`http://otel-collector:4317` instead.

The dashboard refreshes every 10 seconds and covers tool calls/errors/latency,
connector calls/latency/cache, LLM calls/latency/tokens/cost/fallbacks, database
session latency, and a Tempo trace list filtered to `service.name=jobmatch-mcp`.
Use Grafana Explore to open any trace and jump to related metrics through the
provisioned Tempo → Prometheus link.

Metric naming in Prometheus replaces dots with underscores. The collector sets
`add_metric_suffixes: false`, so counters keep their OTel names (for example
`mcp_tool_calls`) and histograms use `_bucket`, `_sum`, and `_count`. The
dashboard queries accept both this form and the default Prometheus `_total` /
unit suffixes, so it also works if you point it at a collector with suffix
translation enabled.

### No data in Grafana

1. Confirm the app exports OTLP: `JOBMATCH_OTEL_EXPORTER=otlp` and a reachable
   `JOBMATCH_OTEL_ENDPOINT`, then restart the server.
2. Check the collector logs:
   `docker compose -f deploy/observability/docker-compose.yml logs -f otel-collector`.
3. Query Prometheus directly: `curl -s localhost:8889/metrics | grep mcp_tool`.
4. In Prometheus, run `count({__name__=~"mcp_.*"})`. An empty result means no
   metrics arrived; a non-empty result means the datasource URL or dashboard
   time range is wrong.
5. For empty traces, generate traffic, then in Tempo/Grafana Explore search
   `{ resource.service.name = "jobmatch-mcp" }` with a `now-1h` range.

### Reset the local stack

```bash
docker compose -f deploy/observability/docker-compose.yml down -v
```

This deletes the Prometheus, Tempo, and Grafana volumes. Do not use `-v` against
a shared or production stack.

## Failure drills

These run without external services:

```bash
uv run pytest tests/unit/test_resilience.py -q
```

They cover per-source timeouts, degraded source status, concurrent timeout
storms, cache behavior under load, and concurrent LLM failures. The bounded
re-rank and prompt-injection cases live in:

```bash
uv run pytest tests/unit/test_rerank_service.py tests/unit/test_prompt_injection.py -q
```

For an end-to-end failure drill, stop a source or the database and confirm:

1. `search_jobs` returns partial results with a warning instead of failing.
2. `list_sources` marks the affected source `degraded` or `unavailable`.
3. `/health` returns `503` when PostgreSQL is down.
4. Logs still contain `request_id` and `trace_id` for every call.

## Quality and security gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/unit tests/eval -q
uv run pip-audit
```

Integration tests need PostgreSQL with pgvector:

```bash
docker compose up -d postgres
export JOBMATCH_TEST_DATABASE_URL=postgresql+psycopg://jobmatch:jobmatch@localhost:5432/jobmatch
uv run alembic upgrade head
uv run pytest tests/integration -q
```

Ranking evaluation:

```bash
uv run pytest tests/eval -q
```

### Image scan

Build the container and scan it locally:

```bash
docker build -t jobmatch-mcp:local .
trivy image --severity HIGH,CRITICAL jobmatch-mcp:local
```

`pip-audit` scans the Python dependency graph; `trivy` covers OS packages and
the image filesystem. Both must be clean before a production release.

## Source troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| JSearch `401` | Missing or invalid RapidAPI key | Set `JOBMATCH_JSEARCH_API_KEY`; restart the server |
| JSearch `403` | Plan does not grant the endpoint | Check the RapidAPI subscription for `/search` |
| JSearch `404` | Wrong host/path or endpoint not in plan | Verify `JOBMATCH_JSEARCH_API_HOST` and the subscribed API |
| JSearch `429` | Rate limit reached | Lower `JOBMATCH_JSEARCH_RATE_LIMIT_PER_MINUTE` or wait |
| Greenhouse/Lever empty | No boards configured or board token renamed | Update `JOBMATCH_GREENHOUSE_BOARDS` / `JOBMATCH_LEVER_COMPANIES` |
| All LLM calls fail | Ollama not running and hosted fallback disabled | Start Ollama or enable hosted fallback after approval |

Search warnings are returned in tool output and mirrored in logs; the search is
not failed when one source is unavailable.

## Rollback

The server is stateless apart from PostgreSQL. To roll back an application
change, redeploy the previous image and run `uv run alembic downgrade -1` only
when the release included a migration. Never downgrade the database unless the
migration is reversible and the data impact is understood.
