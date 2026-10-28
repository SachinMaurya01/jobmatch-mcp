"""Streamable HTTP transport authentication."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.server.app import build_app

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "jobmatch-test", "version": "1.0"},
    },
}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_http_transport_requires_bearer_token() -> None:
    app = build_app(Settings(_env_file=None, http_bearer_token="secret"))
    assert app.auth is not None

    with TestClient(app.http_app(path="/mcp")) as client:
        headers = {"Accept": "application/json, text/event-stream"}
        missing = client.post("/mcp/", json=INITIALIZE, headers=headers)
        wrong = client.post(
            "/mcp/",
            json=INITIALIZE,
            headers={**headers, "Authorization": "Bearer wrong"},
        )
        accepted = client.post(
            "/mcp/",
            json=INITIALIZE,
            headers={**headers, "Authorization": "Bearer secret"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert "result" in accepted.text


def test_stdio_app_has_no_http_auth() -> None:
    app = build_app(Settings(_env_file=None))
    assert app.auth is None
