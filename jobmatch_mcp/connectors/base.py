"""Connector interface for job sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel

from opentelemetry import trace
from jobmatch_mcp.schemas import RawJob, SearchCriteria


class ConnectorHealth(BaseModel):
    name: str
    ok: bool
    detail: str


class JobConnector(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[str]] = frozenset()
    rate_limit_per_minute: ClassVar[int | None] = None

    _tracer = trace.get_tracer(__name__)

    @abstractmethod
    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        """Return normalized postings matching the criteria."""

    @abstractmethod
    async def health(self) -> ConnectorHealth:
        """Report whether the connector is configured and reachable."""

    async def close(self) -> None:
        """Release connector-owned resources."""
        return None
