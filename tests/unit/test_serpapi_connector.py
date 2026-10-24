"""SerpAPI Google Jobs connector parsing and errors."""

from __future__ import annotations

import httpx
import pytest

from jobmatch_mcp.connectors.serpapi import SerpApiConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.schemas import SearchCriteria

PAYLOAD = {
    "jobs_results": [
        {
            "title": "Senior Python Engineer",
            "company_name": "Acme",
            "location": "Bengaluru, Karnataka, India",
            "description": "Remote Python, FastAPI and PostgreSQL role.",
            "job_id": "serp-1",
            "apply_options": [{"title": "Apply", "link": "https://example.com/apply/serp-1"}],
            "share_link": "https://google.com/jobs/serp-1",
        },
        {
            "title": "Python Developer",
            "company_name": "Beta",
            "location": "Pune, India",
            "description": "On-site Python role.",
            "job_id": "serp-2",
            "share_link": "https://google.com/jobs/serp-2",
        },
    ]
}


def configured_settings() -> Settings:
    return Settings(_env_file=None, serpapi_api_key="serp-key", serpapi_country="in")


async def test_parses_and_filters_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search.json"
        assert request.url.params["engine"] == "google_jobs"
        assert request.url.params["q"] == "python developer"
        assert request.url.params["gl"] == "in"
        assert request.url.params["location"] == "Bengaluru"
        assert request.url.params["api_key"] == "serp-key"
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = SerpApiConnector(configured_settings(), client=client)
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
    assert job.external_id == "serp-1"
    assert job.source == "serpapi"
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme"
    assert job.remote is True
    assert job.url == "https://example.com/apply/serp-1"


async def test_falls_back_to_share_link() -> None:
    payload = {"jobs_results": [PAYLOAD["jobs_results"][1]]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = SerpApiConnector(configured_settings(), client=client)
    jobs = await connector.search(SearchCriteria(query="python", limit=10))
    await connector.close()

    assert len(jobs) == 1
    assert jobs[0].url == "https://google.com/jobs/serp-2"


async def test_requires_api_key() -> None:
    connector = SerpApiConnector(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        await connector.search(SearchCriteria(query="python", limit=5))
    health = await connector.health()
    assert health.ok is False


async def test_api_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "Invalid API key"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = SerpApiConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="Invalid API key"):
        await connector.search(SearchCriteria(query="python", limit=5))
    await connector.close()
