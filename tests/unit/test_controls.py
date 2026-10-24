"""Rate limiter, TTL cache, and connector runtime controls."""

from __future__ import annotations

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.controls import AsyncRateLimiter, ConnectorRuntime, TTLCache
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class CountingConnector(JobConnector):
    name = "counting"
    rate_limit_per_minute = 2

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return [
            RawJob(
                source=self.name,
                external_id="1",
                title="Engineer",
                company="Acme",
                description="Python",
                url="https://example.com/1",
                dedupe_key=normalize_dedupe_key("Engineer", "Acme", None),
                sources=[self.name],
            )
        ]

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="healthy")


async def test_rate_limiter_waits_for_the_window() -> None:
    clock = FakeClock()
    limiter = AsyncRateLimiter(2, clock=clock, sleep=clock.sleep)

    await limiter.acquire()
    await limiter.acquire()
    assert limiter.remaining == 0

    await limiter.acquire()
    assert clock.now == 60.0
    assert limiter.remaining == 1


async def test_ttl_cache_expires_and_evicts() -> None:
    clock = FakeClock()
    cache: TTLCache[str] = TTLCache(10, max_entries=2, clock=clock)
    cache.set("a", "one")
    cache.set("b", "two")
    assert cache.get("a") == "one"

    cache.set("c", "three")
    assert cache.get("b") is None

    clock.now = 11.0
    assert cache.get("a") is None
    assert cache.get("c") is None


async def test_runtime_caches_repeat_searches() -> None:
    connector = CountingConnector()
    runtime = ConnectorRuntime(
        connector,
        rate_limit_per_minute=10,
        cache_ttl_seconds=60,
        timeout_seconds=5,
    )
    criteria = SearchCriteria(query="python", limit=5)

    first = await runtime.search(criteria)
    second = await runtime.search(criteria)

    assert connector.calls == 1
    assert len(first) == len(second) == 1
    status = await runtime.status()
    assert status.cache_hits == 1
    assert status.cache_misses == 1
    assert status.rate_limit_remaining == 9
    assert status.status == "ok"


async def test_runtime_marks_source_degraded_after_failure() -> None:
    connector = CountingConnector()
    connector.fail = True
    runtime = ConnectorRuntime(connector, cache_ttl_seconds=0, timeout_seconds=5)

    try:
        await runtime.search(SearchCriteria(query="python"))
    except RuntimeError:
        pass

    status = await runtime.status()
    assert status.status == "degraded"
    assert status.last_error == "RuntimeError"
