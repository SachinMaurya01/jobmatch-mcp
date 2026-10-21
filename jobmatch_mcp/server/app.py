"""FastMCP application wiring (stdio transport for Phase 1)."""

from __future__ import annotations

from fastmcp import FastMCP

from jobmatch_mcp.server.tools import get_job_details, parse_resume, search_jobs

mcp = FastMCP(
    name="JobMatch MCP",
    instructions=(
        "JobMatch ranks job postings against a locally stored resume. "
        "Call parse_resume before search_jobs. Scores are deterministic and "
        "structured in Phase 1; each result includes a 0-100 score, a "
        "breakdown, and a link to the original posting."
    ),
)

mcp.tool(parse_resume)
mcp.tool(search_jobs)
mcp.tool(get_job_details)
