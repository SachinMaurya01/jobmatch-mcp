"""Resource payloads for resumes, jobs, and saved searches."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from jobmatch_mcp.core.errors import InvalidInputError, NotFoundError
from jobmatch_mcp.schemas import ResumeProfile, Seniority
from jobmatch_mcp.server import resources


class DummySessionScope:
    async def __aenter__(self) -> Any:
        return object()

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


async def test_current_resume_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    resume_id = uuid4()
    created_at = datetime(2026, 10, 25, tzinfo=UTC)
    profile = ResumeProfile(
        skills=["Python"],
        years_experience=6,
        seniority=Seniority.SENIOR,
    )
    resume = SimpleNamespace(
        id=resume_id, parsed_profile=profile.model_dump(), created_at=created_at
    )

    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=uuid4())

    async def fake_resume(session: Any, user_id: Any) -> Any:
        del session, user_id
        return resume

    monkeypatch.setattr(resources, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(resources, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(resources, "get_latest_resume", fake_resume)

    result = json.loads(await resources.current_resume())

    assert result["resume_id"] == str(resume_id)
    assert result["profile"]["skills"] == ["Python"]
    assert result["created_at"] == created_at.isoformat().replace("+00:00", "Z")


async def test_current_resume_resource_requires_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=uuid4())

    async def fake_resume(session: Any, user_id: Any) -> None:
        del session, user_id
        return None

    monkeypatch.setattr(resources, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(resources, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(resources, "get_latest_resume", fake_resume)

    with pytest.raises(NotFoundError):
        await resources.current_resume()


async def test_job_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = uuid4()
    fetched_at = datetime(2026, 10, 25, tzinfo=UTC)
    stored = SimpleNamespace(
        id=job_id,
        source="fake",
        external_id="1",
        title="Senior Python Engineer",
        company="Acme",
        location="Berlin",
        remote=True,
        salary_min=90_000,
        salary_max=120_000,
        description="Python and PostgreSQL.",
        url="https://example.com/jobs/1",
        posted_at=None,
        fetched_at=fetched_at,
        dedupe_key="acme-senior-python-engineer-berlin",
    )

    async def fake_get_job(session: Any, requested_id: Any) -> Any:
        del session
        assert requested_id == job_id
        return stored

    monkeypatch.setattr(resources, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(resources, "get_job", fake_get_job)

    result = json.loads(await resources.job(str(job_id)))

    assert result["job"]["id"] == str(job_id)
    assert result["job"]["title"] == "Senior Python Engineer"
    assert result["job"]["url"] == "https://example.com/jobs/1"


async def test_saved_search_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    search_id = uuid4()
    resume_id = uuid4()
    job_id = uuid4()
    created_at = datetime(2026, 10, 25, tzinfo=UTC)
    search = SimpleNamespace(
        id=search_id,
        resume_id=resume_id,
        criteria={
            "query": "python",
            "locations": ["Berlin"],
            "remote": True,
            "min_salary": 90_000,
            "limit": 5,
        },
        created_at=created_at,
    )
    match = SimpleNamespace(
        rank=1,
        score=88.0,
        explanation="Strong Python overlap.",
        breakdown={
            "components": {},
            "matched_skills": ["Python"],
            "missing_skills": ["Go"],
            "weights_used": {},
            "total": 88.0,
        },
    )
    stored_job = SimpleNamespace(
        id=job_id,
        source="fake",
        external_id="1",
        title="Senior Python Engineer",
        company="Acme",
        location="Berlin",
        remote=True,
        salary_min=90_000,
        salary_max=120_000,
        description="Python and PostgreSQL.",
        url="https://example.com/jobs/1",
        posted_at=None,
        fetched_at=created_at,
        dedupe_key="acme-senior-python-engineer-berlin",
    )

    async def fake_user(session: Any) -> Any:
        del session
        return SimpleNamespace(id=uuid4())

    async def fake_search(session: Any, user_id: Any, requested_id: Any) -> Any:
        del session, user_id
        assert requested_id == search_id
        return search

    async def fake_results(session: Any, requested_id: Any) -> Any:
        del session
        assert requested_id == search_id
        return [(match, stored_job)]

    monkeypatch.setattr(resources, "session_scope", lambda: DummySessionScope())
    monkeypatch.setattr(resources, "get_or_create_default_user", fake_user)
    monkeypatch.setattr(resources, "get_search_for_user", fake_search)
    monkeypatch.setattr(resources, "get_search_results", fake_results)

    result = json.loads(await resources.search(str(search_id)))

    assert result["search_id"] == str(search_id)
    assert result["resume_id"] == str(resume_id)
    assert result["criteria"]["locations"] == ["Berlin"]
    assert result["result_count"] == 1
    assert result["results"][0]["job_id"] == str(job_id)
    assert result["results"][0]["breakdown"]["missing_skills"] == ["Go"]


async def test_resources_reject_invalid_ids() -> None:
    with pytest.raises(InvalidInputError):
        await resources.job("not-a-uuid")
    with pytest.raises(InvalidInputError):
        await resources.search("not-a-uuid")
