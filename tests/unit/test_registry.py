"""Connector registry configuration and status reporting."""

from __future__ import annotations

import pytest

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import InvalidInputError
from jobmatch_mcp.schemas import RawJob, SearchCriteria


class StubConnector(JobConnector):
    name = "stub"
    rate_limit_per_minute = 10

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        return []

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="ready")


def test_from_settings_registers_only_enabled_sources() -> None:
    settings = Settings(
        _env_file=None,
        greenhouse_boards="acme",
        lever_companies="globex",
    )
    registry = ConnectorRegistry.from_settings(settings)
    assert registry.names == ["greenhouse", "lever"]


def test_select_unknown_source_raises() -> None:
    registry = ConnectorRegistry([StubConnector()])
    with pytest.raises(InvalidInputError, match="Unknown or disabled"):
        registry.select(["missing"])


async def test_statuses_report_health_and_limits() -> None:
    registry = ConnectorRegistry([StubConnector()], rate_limits={"stub": 7})
    statuses = await registry.statuses()
    assert len(statuses) == 1
    status = statuses[0]
    assert status.name == "stub"
    assert status.enabled is True
    assert status.status == "ok"
    assert status.rate_limit_per_minute == 7
    assert status.rate_limit_remaining == 7
    await registry.close()
