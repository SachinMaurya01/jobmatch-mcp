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
    create_llm_calls,
    create_matches,
    create_resume,
    create_search,
    get_job,
    get_latest_resume,
    get_or_create_default_user,
    get_recent_llm_calls,
    get_search_for_user,
    get_search_results,
    to_job_record,
    to_llm_call_record,
    update_resume_embedding,
    upsert_jobs,
)
from jobmatch_mcp.db.session import dispose_engine, get_session_factory
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.schemas import (
    LLMCallRecord,
    RawJob,
    ResumeProfile,
    SearchCriteria,
    Seniority,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    url = os.environ.get("JOBMATCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JOBMATCH_TEST_DATABASE_URL is not set")

    os.environ["JOBMATCH_DATABASE_URL"] = url
    os.environ["JOBMATCH_SEARCH_DEFAULT_LIMIT"] = "30"
    os.environ["JOBMATCH_SEARCH_MAX_LIMIT"] = "30"
    os.environ["JOBMATCH_MIN_MATCH_SCORE"] = "60"
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
        await session.execute(
            text("TRUNCATE llm_calls, matches, searches, jobs, resumes, users CASCADE")
        )

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

        saved = await get_search_for_user(session, user.id, search.id)
        assert saved is not None
        assert saved.resume_id == resume.id
        rows = await get_search_results(session, search.id)
        assert len(rows) == 1
        assert rows[0][0].score == 92.5
        assert rows[0][1].id == row.id


async def test_embeddings_and_llm_calls_roundtrip(database_url: str) -> None:
    assert database_url
    async with get_session_factory()() as session:
        await session.execute(
            text("TRUNCATE llm_calls, matches, searches, jobs, resumes, users CASCADE")
        )

        user = await get_or_create_default_user(session)
        profile = ResumeProfile(skills=["Python"], years_experience=5)
        resume = await create_resume(
            session,
            user.id,
            "Python",
            profile,
            embedding=[0.1] * 768,
            embedding_model="nomic-embed-text",
        )
        assert resume.embedding_model == "nomic-embed-text"
        assert resume.embedding is not None

        await update_resume_embedding(session, resume.id, [0.2] * 768, "nomic-embed-text-v2")
        assert resume.embedding_model == "nomic-embed-text-v2"

        await create_llm_calls(
            session,
            [
                LLMCallRecord(
                    provider="ollama",
                    model="qwen",
                    latency_ms=120.0,
                    tokens_in=100,
                    tokens_out=50,
                    cost_estimate=0.0,
                    status="success",
                )
            ],
        )
        await session.commit()

        recent = await get_recent_llm_calls(session, 5)
        assert len(recent) == 1
        record = to_llm_call_record(recent[0])
        assert record.provider == "ollama"
        assert record.tokens_in == 100
