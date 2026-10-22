"""JSearch connector parsing and error handling."""

from __future__ import annotations

import httpx
import pytest

from jobmatch_mcp.connectors.jsearch import JSearchConnector
from jobmatch_mcp.core.config import Settings
from jobmatch_mcp.core.errors import ConfigurationError, ConnectorError
from jobmatch_mcp.schemas import SearchCriteria

PAYLOAD = {
    "status": "OK",
    "data": [
        {
            "job_id": "js-1",
            "job_title": "Senior <b>Python</b> Engineer",
            "employer_name": "Acme",
            "job_location": "Bengaluru, India",
            "job_description": "<p>Remote role. Python, Docker, AWS. 5+ years.</p>",
            "job_apply_link": "https://example.com/jobs/js-1",
            "job_is_remote": True,
            "job_posted_at_datetime_utc": "2026-10-01T10:00:00Z",
            "job_min_salary": 10_000,
            "job_max_salary": 12_500,
            "job_salary_period": "MONTH",
        },
        {"job_id": "broken", "job_title": "Missing link"},
    ],
}


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        jsearch_api_key="rapid-key",
        jsearch_api_host="jsearch.p.rapidapi.com",
        jsearch_country="in",
    )


async def test_parses_and_normalizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["query"] == "python developer in Bengaluru, Delhi"
        assert request.url.params["country"] == "in"
        assert request.url.params["work_from_home"] == "true"
        assert request.url.params["num_pages"] == "1"
        assert request.headers["X-RapidAPI-Key"] == "rapid-key"
        assert request.headers["X-RapidAPI-Host"] == "jsearch.p.rapidapi.com"
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = JSearchConnector(configured_settings(), client=client)
    criteria = SearchCriteria(
        query="python developer",
        locations=["Bengaluru", "Delhi"],
        remote=True,
        limit=10,
    )

    jobs = await connector.search(criteria)
    await connector.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "jsearch"
    assert job.external_id == "js-1"
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme"
    assert job.location == "Bengaluru, India"
    assert job.remote is True
    assert job.salary_min == 120_000
    assert job.salary_max == 150_000
    assert "Remote role" in job.description
    assert "<p>" not in job.description
    assert job.posted_at is not None


async def test_requires_api_key() -> None:
    connector = JSearchConnector(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        await connector.search(SearchCriteria(query="python"))
    health = await connector.health()
    assert health.ok is False


async def test_authentication_failure_is_reported_without_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = JSearchConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="authentication"):
        await connector.search(SearchCriteria(query="python"))
    await connector.close()


async def test_rate_limit_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = JSearchConnector(configured_settings(), client=client)
    with pytest.raises(ConnectorError, match="rate limit"):
        await connector.search(SearchCriteria(query="python"))
    await connector.close()


async def test_remote_filter_removes_onsite_results() -> None:
    onsite_payload = {
        "status": "OK",
        "data": [
            {
                "job_id": "onsite-1",
                "job_title": "Python Engineer",
                "employer_name": "Acme",
                "job_location": "Delhi",
                "job_description": "On-site role. Python. 5+ years.",
                "job_apply_link": "https://example.com/1",
                "job_is_remote": False,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=onsite_payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = JSearchConnector(configured_settings(), client=client)
    jobs = await connector.search(SearchCriteria(query="python", remote=True, limit=5))
    await connector.close()
    assert jobs == []


async def test_salary_floor_filters_low_pay_but_keeps_unknown() -> None:
    payload = {
        "status": "OK",
        "data": [
            {
                "job_id": "low",
                "job_title": "Junior Python Developer",
                "employer_name": "Acme",
                "job_location": "Delhi",
                "job_description": "Python. 1 year.",
                "job_apply_link": "https://example.com/low",
                "job_is_remote": False,
                "job_min_salary": 500,
                "job_max_salary": 600,
                "job_salary_period": "MONTH",
            },
            {
                "job_id": "unknown",
                "job_title": "Senior Python Engineer",
                "employer_name": "Beta",
                "job_location": "Delhi",
                "job_description": "Python. 5+ years.",
                "job_apply_link": "https://example.com/unknown",
                "job_is_remote": False,
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    connector = JSearchConnector(configured_settings(), client=client)
    jobs = await connector.search(SearchCriteria(query="python", min_salary=100_000, limit=5))
    await connector.close()
    assert [job.external_id for job in jobs] == ["unknown"]
