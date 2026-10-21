"""End-to-end Phase 1 tool flow against PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from jobmatch_mcp.connectors.base import ConnectorHealth, JobConnector
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.db.session import dispose_engine, get_session_factory
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, SearchCriteria
from jobmatch_mcp.server import tools

pytestmark = pytest.mark.integration

RESUME_TEXT = """
Senior Python Engineer
Skills: Python, FastAPI, PostgreSQL, Docker, AWS
6 years of experience
Location: Bengaluru, India
"""


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    url = os.environ.get("JOBMATCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JOBMATCH_TEST_DATABASE_URL is not set")

    os.environ["JOBMATCH_DATABASE_URL"] = url
    get_settings.cache_clear()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")

    yield url

    asyncio.run(dispose_engine())
    get_settings.cache_clear()


class FakeConnector(JobConnector):
    name = "fake"

    async def search(self, criteria: SearchCriteria) -> list[RawJob]:
        return [
            RawJob(
                source=self.name,
                external_id="job-1",
                title="Senior Python Engineer",
                company="Acme",
                location="Bengaluru, India",
                remote=True,
                salary_min=120_000,
                salary_max=150_000,
                description="Python, FastAPI, PostgreSQL, Docker, AWS. 5+ years.",
                url="https://example.com/jobs/job-1",
                posted_at=None,
                dedupe_key=normalize_dedupe_key(
                    "Senior Python Engineer", "Acme", "Bengaluru, India"
                ),
            )
        ]

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, ok=True, detail="test connector")


async def test_parse_search_and_details(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert database_url
    async with get_session_factory()() as session:
        await session.execute(text("TRUNCATE matches, searches, jobs, resumes, users CASCADE"))
        await session.commit()

    registry = ConnectorRegistry([FakeConnector()])
    monkeypatch.setattr(tools, "get_registry", lambda: registry)

    parsed = await tools.parse_resume(text=RESUME_TEXT)
    assert parsed.profile.years_experience == 6
    assert "Python" in parsed.profile.skills

    search = await tools.search_jobs(query="python", locations=["Bengaluru"], limit=5)
    assert search.count == 1
    assert search.results[0].score == 100
    assert search.results[0].rank == 1
    assert search.results[0].url == "https://example.com/jobs/job-1"

    details = await tools.get_job_details(search.results[0].job_id)
    assert details.job.title == "Senior Python Engineer"
    assert details.job.source == "fake"
