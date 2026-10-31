"""HTTP health endpoint reports database reachability."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient

from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.server import app as app_module


class _Session:
    async def execute(self, statement: Any) -> None:
        del statement


@asynccontextmanager
async def _healthy_session() -> AsyncIterator[_Session]:
    yield _Session()


@asynccontextmanager
async def _broken_session() -> AsyncIterator[_Session]:
    raise RuntimeError("database down")
    yield _Session()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_health_reports_ok_when_database_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "session_scope", _healthy_session)
    app = app_module.build_app(Settings(_env_file=None))

    with TestClient(app.http_app(path="/mcp")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_health_reports_degraded_when_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "session_scope", _broken_session)
    app = app_module.build_app(Settings(_env_file=None))

    with TestClient(app.http_app(path="/mcp")) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
