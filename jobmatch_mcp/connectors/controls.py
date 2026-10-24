"""Per-source rate limiting, response caching, and runtime state."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.core.errors import ConnectorError, describe_error
from jobmatch_mcp.schemas import RawJob, SearchCriteria, SourceStatus


class AsyncRateLimiter:
    """Sliding-window limiter shared by all calls to one source."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._max_per_minute = max(1, max_per_minute)
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._prune(now)
                if len(self._events) < self._max_per_minute:
                    self._events.append(now)
                    return
                wait = 60.0 - (now - self._events[0])
                await self._sleep(max(0.0, wait))

    @property
    def max_per_minute(self) -> int:
        return self._max_per_minute

    @property
    def remaining(self) -> int:
        self._prune(self._clock())
        return max(0, self._max_per_minute - len(self._events))

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] >= 60.0:
            self._events.popleft()


class TTLCache[T]:
    """Small in-memory LRU cache with per-entry expiry."""

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 256,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        if self._ttl_seconds <= 0:
            return
        if key in self._entries:
            del self._entries[key]
        elif len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)
        self._entries[key] = (self._clock() + self._ttl_seconds, value)

    def clear(self) -> None:
        self._entries.clear()


class ConnectorRuntime:
    """Wraps one connector with caching, rate limiting, timeouts, and status."""

    def __init__(
        self,
        connector: JobConnector,
        *,
        rate_limit_per_minute: int | None = None,
        cache_ttl_seconds: float = 0.0,
        cache_max_entries: int = 256,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._connector = connector
        self._limiter = AsyncRateLimiter(
            rate_limit_per_minute or connector.rate_limit_per_minute or 60
        )
        self._cache: TTLCache[list[RawJob]] = TTLCache(cache_ttl_seconds, cache_max_entries)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._last_success_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def name(self) -> str:
        return self._connector.name

    @property
    def capabilities(self) -> frozenset[str]:
        return self._connector.capabilities

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        cache_key = criteria.model_dump_json()
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache_hits += 1
            return list(cached)

        self._cache_misses += 1
        await self._limiter.acquire()
        try:
            jobs = await asyncio.wait_for(
                self._connector.search(criteria),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            message = f"request timed out after {self._timeout_seconds:g}s"
            self._record_failure(message)
            raise ConnectorError(f"{self.name} {message}") from exc
        except Exception as exc:
            self._record_failure(describe_error(exc))
            raise

        self._record_success()
        self._cache.set(cache_key, jobs)
        return jobs

    async def health(self) -> ConnectorHealth:
        try:
            return await self._connector.health()
        except Exception as exc:
            return ConnectorHealth(
                name=self.name,
                ok=False,
                detail=f"health check failed ({describe_error(exc)})",
            )

    async def status(self) -> SourceStatus:
        health = await self.health()
        if not health.ok:
            state = "unavailable"
        elif self._last_failure_at is not None and (
            self._last_success_at is None or self._last_failure_at > self._last_success_at
        ):
            state = "degraded"
        else:
            state = "ok"

        return SourceStatus(
            name=self.name,
            enabled=True,
            status=state,
            detail=health.detail,
            last_success_at=self._last_success_at,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
            rate_limit_per_minute=self._limiter.max_per_minute,
            rate_limit_remaining=self._limiter.remaining,
            cache_ttl_seconds=self._cache_ttl_seconds,
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
        )

    async def close(self) -> None:
        await self._connector.close()

    def _record_success(self) -> None:
        self._last_success_at = datetime.now(UTC)
        self._last_error = None

    def _record_failure(self, detail: str) -> None:
        self._last_failure_at = datetime.now(UTC)
        self._last_error = detail
