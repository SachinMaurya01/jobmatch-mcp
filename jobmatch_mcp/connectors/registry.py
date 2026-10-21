"""Connector registry and config-driven enablement (PRD section 5.2)."""

from __future__ import annotations

from collections.abc import Iterable

from jobmatch_mcp.connectors.adzuna import AdzunaConnector
from jobmatch_mcp.connectors.base import JobConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import InvalidInputError


class ConnectorRegistry:
    def __init__(self, connectors: Iterable[JobConnector]) -> None:
        self._connectors = {connector.name: connector for connector in connectors}

    @classmethod
    def from_settings(cls, settings: Settings) -> ConnectorRegistry:
        connectors: list[JobConnector] = []
        if settings.adzuna_configured:
            connectors.append(AdzunaConnector(settings))
        return cls(connectors)

    @property
    def names(self) -> list[str]:
        return sorted(self._connectors)

    def select(self, requested: list[str] | None = None) -> list[JobConnector]:
        if not requested:
            return list(self._connectors.values())

        unknown = sorted(set(requested) - set(self._connectors))
        if unknown:
            available = ", ".join(self.names) or "(none configured)"
            raise InvalidInputError(
                f"Unknown or disabled source(s): {', '.join(unknown)}. Available: {available}"
            )
        return [self._connectors[name] for name in requested]

    async def close(self) -> None:
        for connector in self._connectors.values():
            await connector.close()
