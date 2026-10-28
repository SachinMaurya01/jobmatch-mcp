"""The MCP surface exposes the expected tools, resources, and prompts."""

from __future__ import annotations

from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.schemas import SearchJobsOutput
from jobmatch_mcp.server.app import build_app


async def test_registers_expected_tools() -> None:
    app = build_app(Settings(_env_file=None))
    tools = {tool.name: tool for tool in await app.list_tools()}
    assert set(tools) == {
        "parse_resume",
        "search_jobs",
        "get_job_details",
        "list_sources",
        "explain_match",
        "get_provider_status",
    }


async def test_tools_have_input_schemas() -> None:
    app = build_app(Settings(_env_file=None))
    tools = {tool.name: tool for tool in await app.list_tools()}
    search_schema = tools["search_jobs"].parameters
    assert "query" in search_schema["properties"]
    assert "locations" in search_schema["properties"]
    assert "limit" in search_schema["properties"]
    assert "ctx" not in search_schema["properties"]


async def test_registers_resources_and_prompts() -> None:
    app = build_app(Settings(_env_file=None))
    resources = {str(resource.uri) for resource in await app.list_resources()}
    templates = {template.uri_template for template in await app.list_resource_templates()}
    prompts = {prompt.name for prompt in await app.list_prompts()}

    assert "resume://current" in resources
    assert {"job://{id}", "search://{id}"} == templates
    assert {"find_jobs_for_resume", "compare_top_matches"} == prompts


def test_tool_outputs_are_versioned() -> None:
    assert SearchJobsOutput.model_fields["schema_version"].default == "1.0"
