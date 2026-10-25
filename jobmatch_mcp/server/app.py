"""FastMCP application wiring (stdio transport)."""

from __future__ import annotations

from fastmcp import FastMCP

from jobmatch_mcp.server.tools import (
    explain_match,
    get_job_details,
    get_provider_status,
    list_sources,
    parse_resume,
    search_jobs,
)

mcp = FastMCP(
    name="JobMatch MCP",
    instructions=(
        "JobMatch ranks job postings against a locally stored resume. "
        "Call parse_resume before search_jobs. Searches fan out across every "
        "enabled source, merge duplicate postings, and keep returning partial "
        "results when one source fails. Scoring combines structured signals with "
        "local embeddings, then a bounded LLM re-rank over the strongest matches. "
        "Each result includes a 0-100 score, a breakdown, source attribution, and "
        "a link to the original posting. Jobs below a 60 match score are excluded, "
        "and each search returns at most 30 jobs. Use explain_match for a detailed "
        "per-job explanation, list_sources for connector health, and "
        "get_provider_status for LLM routing, circuit state, latency, and cost."
    ),
)

mcp.tool(parse_resume)
mcp.tool(search_jobs)
mcp.tool(get_job_details)
mcp.tool(list_sources)
mcp.tool(explain_match)
mcp.tool(get_provider_status)
