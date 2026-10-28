"""FastMCP application wiring for stdio and Streamable HTTP transports."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier

from jobmatch_mcp.core.config import Settings, get_settings
from jobmatch_mcp.server.prompts import compare_top_matches, find_jobs_for_resume
from jobmatch_mcp.server.resources import current_resume, job, search
from jobmatch_mcp.server.tools import (
    explain_match,
    get_job_details,
    get_provider_status,
    list_sources,
    parse_resume,
    search_jobs,
)

_INSTRUCTIONS = (
    "JobMatch ranks job postings against a locally stored resume. "
    "Call parse_resume before search_jobs. Searches fan out across every "
    "enabled source, merge duplicate postings, and keep returning partial "
    "results when one source fails. Scoring combines structured signals with "
    "local embeddings, then a bounded LLM re-rank over the strongest matches. "
    "Each result includes a 0-100 score, a breakdown, source attribution, and "
    "a link to the original posting. Jobs below a 60 match score are excluded, "
    "and each search returns at most 30 jobs. Use explain_match for a detailed "
    "per-job explanation, list_sources for connector health, and "
    "get_provider_status for LLM routing, circuit state, latency, and cost. "
    "Read resume://current, job://{id}, and search://{id} for stored data. "
    "Use the find_jobs_for_resume and compare_top_matches prompts for guided "
    "workflows. Ask for missing location, remote, or salary preferences with "
    "elicitation when the client supports it."
)


def build_app(settings: Settings | None = None) -> FastMCP:
    """Create the MCP application, including optional HTTP bearer auth."""
    settings = settings or get_settings()
    mcp = FastMCP(
        name="JobMatch MCP",
        instructions=_INSTRUCTIONS,
        auth=_auth_provider(settings),
    )

    mcp.tool(parse_resume)
    mcp.tool(search_jobs)
    mcp.tool(get_job_details)
    mcp.tool(list_sources)
    mcp.tool(explain_match)
    mcp.tool(get_provider_status)

    mcp.resource(
        "resume://current",
        name="current_resume",
        description="The latest parsed resume profile.",
        mime_type="application/json",
    )(current_resume)
    mcp.resource(
        "job://{id}",
        name="job",
        description="A stored job record with its source link.",
        mime_type="application/json",
    )(job)
    mcp.resource(
        "search://{id}",
        name="search",
        description="A saved search with its ranked results.",
        mime_type="application/json",
    )(search)

    mcp.prompt(find_jobs_for_resume)
    mcp.prompt(compare_top_matches)

    return mcp


def _auth_provider(settings: Settings) -> StaticTokenVerifier | None:
    token = (settings.http_bearer_token or "").strip()
    if not token:
        return None
    return StaticTokenVerifier(
        tokens={token: {"client_id": "jobmatch", "scopes": ["jobmatch"]}},
        required_scopes=["jobmatch"],
    )


mcp = build_app()
