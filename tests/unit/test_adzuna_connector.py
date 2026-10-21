"""Adzuna connector parsing and error handling."""

from __future__ import annotations

import httpx
import pytest

from jobmatch_mcp.connectors.adzuna import AdzunaConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.schemas import SearchCriteria

PAYLOAD = {
    "results": [
        {
            "id": 123,
            "title": "Senior <b>Python</b> Engineer",
            "company": {"display_name": "Acme"},
            "location": {"display_name": "Bengaluru, India"},
            "description": "<p>Remote role. Python, Docker, AWS. 5+ years.</p>",
            "redirect_url": "https://example.com/jobs/123",
            "created": "2026-10-01T10:00:00Z",
            "salary_min": 100000,
            "salary_max": 150000,
        },
        {"id": None, "title": "Broken"},
    ]
}


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        adzuna_app_id="app-id",
        adzuna_app_key="app-key",
        adzuna_country="in",
    )


async def test_parses_and_normalizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["app_id"] == "app-id"
        assert request.url.params["what"] == "python developer"
        assert request.url.params["where"] == "Bengaluru, Delhi"
        assert request.url.params["salary_min"] == "100000"
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = AdzunaConnector(configured_settings(), client=client)
    criteria = SearchCriteria(
        query="python developer",
        locations=["Bengaluru", "Delhi"],
        min_salary=100_000,
        limit=10,
    )

    jobs = await connector.search(criteria)
    await connector.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "adzuna"
    assert job.external_id == "123"
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme"
    assert job.location == "Bengaluru, India"
    assert job.remote is True
    assert job.salary_min == 100000
    assert job.salary_max == 150000
    assert "Remote role" in job.description
    assert "<p>" not in job.description
    assert job.posted_at is not None


async def test_requires_credentials() -> None:
    connector = AdzunaConnector(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        await connector.search(SearchCriteria(query="python"))
    health = await connector.health()
    assert health.ok is False


async def test_rate_limit_is_reported_without_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = AdzunaConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="rate limit"):
        await connector.search(SearchCriteria(query="python"))
    await connector.close()


async def test_remote_filter_removes_onsite_results() -> None:
    onsite_payload = {
        "results": [
            {
                "id": 1,
                "title": "Python Engineer",
                "company": {"display_name": "Acme"},
                "location": {"display_name": "Delhi"},
                "description": "On-site role. Python. 5+ years.",
                "redirect_url": "https://example.com/1",
                "created": "2026-10-01T10:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=onsite_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = AdzunaConnector(configured_settings(), client=client)
    jobs = await connector.search(SearchCriteria(query="python", remote=True, limit=5))
    await connector.close()
    assert jobs == []
