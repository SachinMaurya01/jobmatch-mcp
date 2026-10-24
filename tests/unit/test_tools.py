"""Tool wiring for progress notifications and source listing."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.schemas import RawJob, SearchCriteria, SearchJobsOutput
from jobmatch_mcp.server import tools


class StubConnector(JobConnector):
    name = "stub"

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        return []

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="ready")


class FakeContext:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float, str]] = []

    async def report_progress(self, current: float, total: float, message: str) -> None:
        self.calls.append((current, total, message))


class DummySessionScope:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


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
        assert progress is not None
        await progress(50, 100, "Halfway")
        return SearchJobsOutput(
            search_id=uuid4(),
            resume_id=uuid4(),
            count=0,
        )


async def test_search_jobs_forwards_progress_notifications(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(tools, "SearchService", FakeSearchService)
    monkeypatch.setattr(tools, "session_scope", lambda: DummySessionScope())
    context: Any = FakeContext()

    await tools.search_jobs(query="python", ctx=context)

    assert context.calls == [(50, 100, "Halfway")]


async def test_list_sources_reports_registry_status(monkeypatch: Any) -> None:
    registry = ConnectorRegistry([StubConnector()])
    monkeypatch.setattr(tools, "get_registry", lambda: registry)

    result = await tools.list_sources()

    assert [status.name for status in result.sources] == ["stub"]
    assert result.sources[0].status == "ok"
    await registry.close()
