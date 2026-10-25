"""Tool wiring for progress notifications, source listing, and explanations."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.schemas import (
    LLMCallRecord,
    ProviderStatus,
    RawJob,
    SearchCriteria,
    SearchJobsOutput,
)
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


async def test_explain_match_uses_latest_stored_match(monkeypatch: Any) -> None:
    job_id = uuid4()
    search_id = uuid4()
    user_id = uuid4()
    job = SimpleNamespace(id=job_id, title="Python Engineer")
    search = SimpleNamespace(id=search_id, created_at=datetime(2026, 10, 24, tzinfo=UTC))
    match = SimpleNamespace(
        score=88.0,
        rank=1,
        explanation="Strong skills overlap.",
        breakdown={
            "components": {},
            "matched_skills": ["Python"],
            "missing_skills": ["Go"],
            "weights_used": {},
            "total": 88.0,
        },
    )

    async def fake_get_job(session: Any, requested_id: Any) -> Any:
        del session, requested_id
        return job

    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=user_id)

    async def fake_latest(session: Any, requested_user: Any, requested_job: Any) -> Any:
        del session, requested_user, requested_job
        return match, search

    monkeypatch.setattr(tools, "get_job", fake_get_job)
    monkeypatch.setattr(tools, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(tools, "get_latest_match_for_job", fake_latest)
    monkeypatch.setattr(tools, "session_scope", lambda: DummySessionScope())

    result = await tools.explain_match(job_id)

    assert result.job_id == job_id
    assert result.search_id == search_id
    assert result.score == 88.0
    assert result.matched_skills == ["Python"]
    assert any("Go" in suggestion for suggestion in result.suggestions)


async def test_get_provider_status_aggregates_recent_calls(monkeypatch: Any) -> None:
    call = LLMCallRecord(
        provider="ollama",
        model="qwen",
        latency_ms=120.0,
        tokens_in=100,
        tokens_out=50,
        cost_estimate=0.002,
        status="success",
    )

    class FakeRouter:
        async def statuses(self) -> list[ProviderStatus]:
            return [
                ProviderStatus(
                    name="ollama",
                    role="primary",
                    configured=True,
                    available=True,
                    circuit_state="closed",
                )
            ]

    async def fake_recent(session: Any, limit: int) -> list[Any]:
        del session, limit
        return [call]

    monkeypatch.setattr(tools, "get_llm_router", lambda: FakeRouter())
    monkeypatch.setattr(tools, "get_recent_llm_calls", fake_recent)
    monkeypatch.setattr(tools, "to_llm_call_record", lambda value: value)
    monkeypatch.setattr(tools, "session_scope", lambda: DummySessionScope())

    result = await tools.get_provider_status()

    assert result.providers[0].recent_calls == 1
    assert result.providers[0].recent_cost_estimate == 0.002
    assert result.total_cost_estimate == 0.002
    assert result.recent_calls == [call]
