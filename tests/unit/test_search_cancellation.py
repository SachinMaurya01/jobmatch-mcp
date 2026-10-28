"""Search cancellation must propagate instead of being converted to a warning."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.config import ScoringWeights, Settings
from jobmatch_mcp.schemas import ResumeProfile, SearchCriteria
from jobmatch_mcp.server import tools
from jobmatch_mcp.services import search_service as search_service_module
from jobmatch_mcp.services.match_service import MatchService
from jobmatch_mcp.services.search_service import SearchService


class BlockingConnector(JobConnector):
    name = "blocking"

    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    async def search(self, criteria: SearchCriteria) -> list[Any]:
        del criteria
        self._started.set()
        await asyncio.Event().wait()
        return []

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="ready")


async def test_search_service_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=uuid4())

    async def fake_resume(session: Any, user_id: Any) -> Any:
        del session, user_id
        return SimpleNamespace(
            id=uuid4(),
            parsed_profile=ResumeProfile(skills=["Python"]).model_dump(),
            raw_text="Python",
            embedding=None,
            embedding_model=None,
        )

    monkeypatch.setattr(search_service_module, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(search_service_module, "get_latest_resume", fake_resume)

    registry = ConnectorRegistry([BlockingConnector(started)], timeout_seconds=60)
    service = SearchService(registry, MatchService(ScoringWeights(), 60))

    task = asyncio.create_task(
        service.search(object(), SearchCriteria(query="python"))  # type: ignore[arg-type]
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await registry.close()


async def test_tool_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    class CancellingSearchService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise asyncio.CancelledError

    class DummySessionScope:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

    monkeypatch.setattr(tools, "SearchService", CancellingSearchService)
    monkeypatch.setattr(tools, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(tools, "get_registry", lambda: object())
    monkeypatch.setattr(
        tools,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            enable_semantic=False,
            enable_llm_rerank=False,
            enable_elicitation=False,
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await tools.search_jobs(query="python")
