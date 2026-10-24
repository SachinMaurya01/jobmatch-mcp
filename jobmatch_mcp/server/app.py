"""FastMCP application wiring (stdio transport)."""

from __future__ import annotations

from fastmcp import FastMCP

from jobmatch_mcp.server.tools import (
    get_job_details,
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
        "results when one source fails. Each result includes a 0-100 score, a "
        "breakdown, source attribution, and a link to the original posting. "
        "Jobs below a 60 match score are excluded, and each search returns at "
        "most 30 jobs. Use list_sources to inspect source health and rate limits."
    ),
)

mcp.tool(parse_resume)
mcp.tool(search_jobs)
mcp.tool(get_job_details)
mcp.tool(list_sources)
