"""Greenhouse connector parsing, filtering, and errors."""

from __future__ import annotations

import httpx
import pytest

from jobmatch_mcp.connectors.greenhouse import GreenhouseConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.schemas import SearchCriteria

PAYLOAD = {
    "jobs": [
        {
            "id": 101,
            "title": "Senior Python Developer",
            "company_name": "Acme",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
            "location": {"name": "Bengaluru, India"},
            "content": "<p>Remote Python, FastAPI and PostgreSQL role.</p>",
            "first_published": "2026-10-01T10:00:00Z",
        },
        {
            "id": 102,
            "title": "Sales Manager",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
            "location": {"name": "Delhi, India"},
            "content": "<p>Enterprise sales.</p>",
        },
        {
            "id": 103,
            "title": "Python Developer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/103",
            "location": {"name": "Pune, India"},
            "content": "<p>On-site Python role.</p>",
        },
    ]
}


def configured_settings() -> Settings:
    return Settings(_env_file=None, greenhouse_boards="acme")


async def test_parses_and_filters_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/boards/acme/jobs"
        assert request.url.params["content"] == "true"
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = GreenhouseConnector(configured_settings(), client=client)
    criteria = SearchCriteria(
        query="python developer",
        locations=["Bengaluru"],
        remote=True,
        limit=10,
    )

    jobs = await connector.search(criteria)
    await connector.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "acme:101"
    assert job.source == "greenhouse"
    assert job.title == "Senior Python Developer"
    assert job.company == "Acme"
    assert job.location == "Bengaluru, India"
    assert job.remote is True
    assert job.description == "Remote Python, FastAPI and PostgreSQL role."
    assert job.posted_at is not None
    assert job.sources == ["greenhouse"]


async def test_requires_board_configuration() -> None:
    connector = GreenhouseConnector(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        await connector.search(SearchCriteria(query="python", limit=5))
    health = await connector.health()
    assert health.ok is False


async def test_missing_board_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = GreenhouseConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="not found"):
        await connector.search(SearchCriteria(query="python", limit=5))
    await connector.close()
