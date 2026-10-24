"""Connector registry with per-source runtime controls."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from jobmatch_mcp.connectors.base import JobConnector
from jobmatch_mcp.connectors.controls import ConnectorRuntime
from jobmatch_mcp.connectors.greenhouse import GreenhouseConnector
from jobmatch_mcp.connectors.jsearch import JSearchConnector
from jobmatch_mcp.connectors.lever import LeverConnector
from jobmatch_mcp.connectors.serpapi import SerpApiConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import InvalidInputError
from jobmatch_mcp.schemas import SourceStatus


class ConnectorRegistry:
    def __init__(
        self,
        connectors: Iterable[JobConnector],
        *,
        rate_limits: Mapping[str, int] | None = None,
        cache_ttl_seconds: float = 0.0,
        cache_max_entries: int = 256,
        timeout_seconds: float = 20.0,
    ) -> None:
        limits = dict(rate_limits or {})
        self._runtimes = [
            ConnectorRuntime(
                connector,
                rate_limit_per_minute=limits.get(connector.name),
                cache_ttl_seconds=cache_ttl_seconds,
                cache_max_entries=cache_max_entries,
                timeout_seconds=timeout_seconds,
            )
            for connector in connectors
        ]
        self._by_name = {runtime.name: runtime for runtime in self._runtimes}

    @classmethod
    def from_settings(cls, settings: Settings) -> ConnectorRegistry:
        connectors: list[JobConnector] = []
        rate_limits: dict[str, int] = {}

        if settings.jsearch_configured:
            connectors.append(JSearchConnector(settings))
            rate_limits["jsearch"] = settings.jsearch_rate_limit_per_minute
        if settings.greenhouse_configured:
            connectors.append(GreenhouseConnector(settings))
            rate_limits["greenhouse"] = settings.greenhouse_rate_limit_per_minute
        if settings.lever_configured:
            connectors.append(LeverConnector(settings))
            rate_limits["lever"] = settings.lever_rate_limit_per_minute
        if settings.serpapi_configured:
            connectors.append(SerpApiConnector(settings))
            rate_limits["serpapi"] = settings.serpapi_rate_limit_per_minute

        return cls(
            connectors,
            rate_limits=rate_limits,
            cache_ttl_seconds=settings.connector_cache_ttl_seconds,
            cache_max_entries=settings.connector_cache_max_entries,
            timeout_seconds=settings.source_timeout_seconds,
        )

    @property
    def names(self) -> list[str]:
        return sorted(self._by_name)

    def select(self, requested: list[str] | None = None) -> list[ConnectorRuntime]:
        if not requested:
            return list(self._runtimes)

        unknown = sorted(set(requested) - set(self._by_name))
        if unknown:
            available = ", ".join(self.names) or "(none configured)"
            raise InvalidInputError(
                f"Unknown or disabled source(s): {', '.join(unknown)}. Available: {available}"
            )
        return [self._by_name[name] for name in requested]

    async def statuses(self) -> list[SourceStatus]:
        results = await asyncio.gather(
            *(runtime.status() for runtime in self._runtimes),
            return_exceptions=True,
        )
        statuses: list[SourceStatus] = []
        for runtime, result in zip(self._runtimes, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                statuses.append(
                    SourceStatus(
                        name=runtime.name,
                        enabled=True,
                        status="unavailable",
                        detail=f"status check failed ({type(result).__name__})",
                    )
                )
            else:
                statuses.append(result)
        return statuses

    async def close(self) -> None:
        for runtime in self._runtimes:
            await runtime.close()
