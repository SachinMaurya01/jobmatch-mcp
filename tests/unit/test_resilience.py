"""Load and failure behavior under concurrent tool work."""

from __future__ import annotations

import asyncio

import pytest

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.controls import ConnectorRuntime
from jobmatch_mcp.core.errors import ConnectorError, LLMUnavailableError
from jobmatch_mcp.llm.base import LLMResponse
from jobmatch_mcp.llm.router import LLMRouter, LLMRouteResult
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria


def make_job(source: str = "fake") -> RawJob:
    title = "Python Engineer"
    return RawJob(
        source=source,
        external_id="1",
        title=title,
        company="Acme",
        description="Python role.",
        url="https://example.com/job",
        dedupe_key=normalize_dedupe_key(title, "Acme", None),
    )


class SlowConnector(JobConnector):
    name = "slow"

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        del criteria
        self.calls += 1
        await asyncio.sleep(self.delay)
        return [make_job(self.name)]

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="ok")


async def test_timeout_is_reported_as_connector_error_and_degraded_status() -> None:
    runtime = ConnectorRuntime(
        SlowConnector(0.2),
        rate_limit_per_minute=1000,
        timeout_seconds=0.01,
    )

    with pytest.raises(ConnectorError, match="timed out"):
        await runtime.search(SearchCriteria(query="python", limit=1))

    status = await runtime.status()
    assert status.status == "degraded"
    assert status.last_error is not None
    assert "timed out" in status.last_error


async def test_many_concurrent_searches_fail_cleanly() -> None:
    runtime = ConnectorRuntime(
        SlowConnector(0.2),
        rate_limit_per_minute=1000,
        timeout_seconds=0.01,
    )

    results = await asyncio.gather(
        *(runtime.search(SearchCriteria(query="python", limit=1)) for _ in range(20)),
        return_exceptions=True,
    )

    assert all(isinstance(result, ConnectorError) for result in results)


async def test_many_concurrent_successful_searches_stay_consistent() -> None:
    connector = SlowConnector(0.001)
    runtime = ConnectorRuntime(
        connector,
        rate_limit_per_minute=1000,
        cache_ttl_seconds=60,
        timeout_seconds=5,
    )

    results = await asyncio.gather(
        *(runtime.search(SearchCriteria(query="python", limit=1)) for _ in range(20))
    )

    assert len(results) == 20
    assert all(len(jobs) == 1 for jobs in results)
    assert all(jobs[0].source == "slow" for jobs in results)

    await runtime.search(SearchCriteria(query="python", limit=1))
    status = await runtime.status()
    assert status.status == "ok"
    assert status.cache_hits >= 1


class AlternatingProvider:
    name = "alternating"
    model = "alternating-1"
    hosted = False

    def __init__(self) -> None:
        self.calls = 0

    @property
    def configured(self) -> bool:
        return True

    async def generate_structured(
        self, *, system: str, user: str, schema: dict[str, object], schema_name: str
    ) -> LLMResponse:
        del system, user, schema, schema_name
        self.calls += 1
        if self.calls % 2:
            raise RuntimeError("upstream down")
        return LLMResponse(
            data={"decisions": []},
            tokens_in=1,
            tokens_out=1,
            latency_ms=1.0,
            cost_estimate=0.0,
        )

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


async def test_concurrent_llm_failures_do_not_leak_unexpected_errors() -> None:
    router = LLMRouter(
        [AlternatingProvider()],
        hosted_fallback_enabled=False,
        failure_threshold=1000,
        cooldown_seconds=60,
    )

    results = await asyncio.gather(
        *(
            router.generate_structured(
                system="s", user="u", schema={"type": "object"}, schema_name="load"
            )
            for _ in range(20)
        ),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, LLMUnavailableError):
            continue
        assert isinstance(result, LLMRouteResult)
        assert result.provider == "alternating"
