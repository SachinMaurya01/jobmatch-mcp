"""PostgreSQL + pgvector persistence tests (run with Docker Compose)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.db.repositories import (
    create_matches,
    create_resume,
    create_search,
    get_job,
    get_latest_resume,
    get_or_create_default_user,
    to_job_record,
    upsert_jobs,
)
from jobmatch_mcp.db.session import dispose_engine, get_session_factory
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria, Seniority

pytestmark = pytest.mark.integration


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


async def test_resume_job_search_roundtrip(database_url: str) -> None:
    assert database_url
    async with get_session_factory()() as session:
        await session.execute(text("TRUNCATE matches, searches, jobs, resumes, users CASCADE"))

        user = await get_or_create_default_user(session)
        profile = ResumeProfile(
            skills=["Python", "PostgreSQL"],
            years_experience=6,
            seniority=Seniority.SENIOR,
            locations=["Bengaluru"],
        )
        resume = await create_resume(session, user.id, "Python PostgreSQL", profile)
        latest = await get_latest_resume(session, user.id)
        assert latest is not None
        assert latest.id == resume.id
        assert latest.parsed_profile["skills"] == ["Python", "PostgreSQL"]

        job = RawJob(
            source="jsearch",
            external_id="42",
            title="Senior Python Engineer",
            company="Acme",
            location="Bengaluru, India",
            remote=True,
            salary_min=100_000,
            salary_max=150_000,
            description="Python and PostgreSQL. 5+ years.",
            url="https://example.com/jobs/42",
            posted_at=datetime(2026, 10, 1, tzinfo=UTC),
            dedupe_key=normalize_dedupe_key("Senior Python Engineer", "Acme", "Bengaluru, India"),
        )
        stored = await upsert_jobs(session, [job])
        row = stored[("jsearch", "42")]
        assert row.title == "Senior Python Engineer"

        updated = job.model_copy(update={"title": "Staff Python Engineer"})
        stored_again = await upsert_jobs(session, [updated])
        assert stored_again[("jsearch", "42")].id == row.id
        assert stored_again[("jsearch", "42")].title == "Staff Python Engineer"

        fetched = await get_job(session, row.id)
        assert fetched is not None
        record = to_job_record(fetched)
        assert record.url == job.url
        assert record.fetched_at is not None

        criteria = SearchCriteria(query="python", locations=["Bengaluru"], limit=5)
        search = await create_search(session, user.id, resume.id, criteria)
        await create_matches(
            session,
            search.id,
            [(row.id, 92.5, {"total": 92.5}, "Strong Python overlap.", 1)],
        )
        await session.commit()

        count = await session.scalar(text("SELECT count(*) FROM matches"))
        assert count == 1
