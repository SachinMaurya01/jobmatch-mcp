"""Lever connector parsing, salary normalization, and filtering."""

from __future__ import annotations

import httpx
import pytest

from jobmatch_mcp.connectors.lever import LeverConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.schemas import SearchCriteria

PAYLOAD = [
    {
        "id": "lever-1",
        "text": "Senior Python Engineer",
        "categories": {
            "location": "Bengaluru",
            "allLocations": ["Bengaluru", "Remote"],
        },
        "descriptionPlain": "Build Python and FastAPI services.",
        "lists": [{"text": "Requirements", "content": "<ul><li>5+ years Python</li></ul>"}],
        "createdAt": 1_790_000_000_000,
        "hostedUrl": "https://jobs.lever.co/acme/lever-1",
        "workplaceType": "remote",
        "salaryRange": {
            "min": 10_000,
            "max": 12_500,
            "currency": "USD",
            "interval": "per-month-salary",
        },
    },
    {
        "id": "lever-2",
        "text": "Account Executive",
        "categories": {"location": "Delhi"},
        "descriptionPlain": "Sales role.",
        "createdAt": 1_790_000_000_000,
        "hostedUrl": "https://jobs.lever.co/acme/lever-2",
        "workplaceType": "onsite",
    },
]


def configured_settings() -> Settings:
    return Settings(_env_file=None, lever_companies="acme")


async def test_parses_and_filters_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/acme"
        assert request.url.params["mode"] == "json"
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = LeverConnector(configured_settings(), client=client)
    criteria = SearchCriteria(query="python engineer", remote=True, limit=10)

    jobs = await connector.search(criteria)
    await connector.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "acme:lever-1"
    assert job.source == "lever"
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme"
    assert job.location == "Bengaluru, Remote"
    assert job.remote is True
    assert job.salary_min == 120_000
    assert job.salary_max == 150_000
    assert "Requirements" in job.description
    assert "5+ years Python" in job.description


async def test_salary_floor_filters_low_pay() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = LeverConnector(configured_settings(), client=client)
    jobs = await connector.search(
        SearchCriteria(query="python engineer", min_salary=200_000, limit=10)
    )
    await connector.close()
    assert jobs == []


async def test_requires_company_configuration() -> None:
    connector = LeverConnector(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        await connector.search(SearchCriteria(query="python", limit=5))
    health = await connector.health()
    assert health.ok is False


async def test_rate_limit_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = LeverConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="rate limit"):
        await connector.search(SearchCriteria(query="python", limit=5))
    await connector.close()
