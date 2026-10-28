"""MCP protocol tests for tools, resources, prompts, and elicitation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastmcp import Client

from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.schemas import (
    ResumeProfile,
    SearchCriteria,
    SearchJobsOutput,
    SearchPreferences,
    Seniority,
)
from jobmatch_mcp.server import resources, tools
from jobmatch_mcp.server.app import build_app


class DummySessionScope:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


async def test_protocol_lists_and_reads_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_app(Settings(_env_file=None))
    resume_id = uuid4()
    profile = ResumeProfile(
        skills=["Python"],
        years_experience=6,
        seniority=Seniority.SENIOR,
    )
    resume = SimpleNamespace(
        id=resume_id,
        parsed_profile=profile.model_dump(),
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
    )

    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=uuid4())

    async def fake_resume(session: Any, user_id: Any) -> Any:
        del session, user_id
        return resume

    monkeypatch.setattr(resources, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(resources, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(resources, "get_latest_resume", fake_resume)

    async with Client(app) as client:
        tools_list = {tool.name for tool in await client.list_tools()}
        resources_list = {str(resource.uri) for resource in await client.list_resources()}
        templates = {template.uriTemplate for template in await client.list_resource_templates()}
        prompts = {prompt.name for prompt in await client.list_prompts()}
        contents = await client.read_resource("resume://current")
        prompt = await client.get_prompt("find_jobs_for_resume", {"query": "python"})
        comparison = await client.get_prompt(
            "compare_top_matches", {"search_id": str(uuid4()), "count": 3}
        )

    payload = json.loads(contents[0].text)
    assert payload["schema_version"] == "1.0"
    assert payload["resume_id"] == str(resume_id)
    assert payload["profile"]["skills"] == ["Python"]
    assert "resume://current" in resources_list
    assert {"job://{id}", "search://{id}"} == templates
    assert {"find_jobs_for_resume", "compare_top_matches"} == prompts
    assert "parse_resume" in tools_list
    assert "search_jobs" in prompt.messages[0].content.text
    assert "top 3" in comparison.messages[0].content.text


async def test_elicitation_fills_missing_search_preferences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_app(Settings(_env_file=None))
    captured: list[SearchCriteria] = []

    class FakeSearchService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def search(
            self,
            session: Any,
            criteria: SearchCriteria,
            sources: list[str] | None,
            progress: Any,
        ) -> SearchJobsOutput:
            del session, sources, progress
            captured.append(criteria)
            return SearchJobsOutput(
                search_id=uuid4(),
                resume_id=uuid4(),
                count=0,
            )

    async def handler(
        message: str,
        response_type: Any,
        params: Any,
        context: Any,
    ) -> SearchPreferences:
        del response_type, params, context
        assert "location" in message
        return SearchPreferences(
            locations="Berlin, Remote",
            remote="remote",
            min_salary=90_000,
        )

    monkeypatch.setattr(
        tools,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            enable_semantic=False,
            enable_llm_rerank=False,
            enable_elicitation=True,
        ),
    )
    monkeypatch.setattr(tools, "SearchService", FakeSearchService)
    monkeypatch.setattr(tools, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(tools, "get_registry", lambda: object())

    async with Client(app, elicitation_handler=handler) as client:
        await client.call_tool("search_jobs", {"query": "python"})

    assert captured[0].locations == ["Berlin", "Remote"]
    assert captured[0].remote is True
    assert captured[0].min_salary == 90_000
