"""The Phase 1 MCP surface exposes exactly the expected tools."""

from __future__ import annotations

from jobmatch_mcp.server.app import mcp


async def test_registers_phase1_tools() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(tools) == {"parse_resume", "search_jobs", "get_job_details"}


async def test_tools_have_input_schemas() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    search_schema = tools["search_jobs"].parameters
    assert "query" in search_schema["properties"]
    assert "locations" in search_schema["properties"]
    assert "limit" in search_schema["properties"]
