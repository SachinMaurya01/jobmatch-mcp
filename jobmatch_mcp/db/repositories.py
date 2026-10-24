"""Async repositories for persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobmatch_mcp.db.models import Job, LLMCall, Match, Resume, Search, User
from jobmatch_mcp.schemas import (
    JobRecord,
    LLMCallRecord,
    RawJob,
    ResumeProfile,
    SearchCriteria,
)


async def get_or_create_default_user(session: AsyncSession) -> User:
    user = await session.scalar(select(User).order_by(User.created_at).limit(1))
    if user is not None:
        return user
    user = User()
    session.add(user)
    await session.flush()
    return user


async def create_resume(
    session: AsyncSession,
    user_id: UUID,
    raw_text: str,
    profile: ResumeProfile,
    *,
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
) -> Resume:
    resume = Resume(
        user_id=user_id,
        raw_text=raw_text,
        parsed_profile=profile.model_dump(mode="json"),
        embedding=embedding,
        embedding_model=embedding_model,
    )
    session.add(resume)
    await session.flush()
    return resume


async def get_latest_resume(session: AsyncSession, user_id: UUID) -> Resume | None:
    return await session.scalar(
        select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc()).limit(1)
    )


async def update_resume_embedding(
    session: AsyncSession,
    resume_id: UUID,
    embedding: list[float],
    embedding_model: str,
) -> None:
    resume = await session.get(Resume, resume_id)
    if resume is None:
        return
    resume.embedding = embedding
    resume.embedding_model = embedding_model
    await session.flush()


async def upsert_jobs(session: AsyncSession, jobs: Sequence[RawJob]) -> dict[tuple[str, str], Job]:
    """Insert or refresh postings keyed by (source, external_id)."""
    ids: list[UUID] = []
    for job in jobs:
        update_values: dict[str, Any] = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "remote": job.remote,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "description": job.description,
            "url": job.url,
            "posted_at": job.posted_at,
            "dedupe_key": job.dedupe_key,
        }
        if job.embedding is not None:
            update_values["embedding"] = job.embedding
            update_values["embedding_model"] = job.embedding_model

        stmt = (
            insert(Job)
            .values(
                source=job.source,
                external_id=job.external_id,
                title=job.title,
                company=job.company,
                location=job.location,
                remote=job.remote,
                salary_min=job.salary_min,
                salary_max=job.salary_max,
                description=job.description,
                url=job.url,
                posted_at=job.posted_at,
                dedupe_key=job.dedupe_key,
                embedding=job.embedding,
                embedding_model=job.embedding_model,
            )
            .on_conflict_do_update(
                index_elements=["source", "external_id"],
                set_=update_values,
            )
            .returning(Job.id)
        )
        job_id = await session.scalar(stmt)
        if job_id is None:
            raise RuntimeError("Failed to upsert job")
        ids.append(job_id)

    if not ids:
        return {}

    rows = (
        await session.scalars(
            select(Job).where(Job.id.in_(ids)).execution_options(populate_existing=True)
        )
    ).all()
    return {(row.source, row.external_id): row for row in rows}


async def get_job(session: AsyncSession, job_id: UUID) -> Job | None:
    return await session.get(Job, job_id)


async def create_search(
    session: AsyncSession,
    user_id: UUID,
    resume_id: UUID,
    criteria: SearchCriteria,
) -> Search:
    search = Search(
        user_id=user_id,
        resume_id=resume_id,
        criteria=criteria.model_dump(mode="json"),
    )
    session.add(search)
    await session.flush()
    return search


async def create_matches(
    session: AsyncSession,
    search_id: UUID,
    rows: Sequence[tuple[UUID, float, dict[str, Any], str, int]],
) -> None:
    session.add_all(
        [
            Match(
                search_id=search_id,
                job_id=job_id,
                score=score,
                breakdown=breakdown,
                explanation=explanation,
                rank=rank,
            )
            for job_id, score, breakdown, explanation, rank in rows
        ]
    )
    await session.flush()


async def get_latest_match_for_job(
    session: AsyncSession, user_id: UUID, job_id: UUID
) -> tuple[Match, Search] | None:
    row = (
        await session.execute(
            select(Match, Search)
            .join(Search, Match.search_id == Search.id)
            .where(Search.user_id == user_id, Match.job_id == job_id)
            .order_by(Search.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


async def create_llm_calls(session: AsyncSession, calls: Sequence[LLMCallRecord]) -> None:
    session.add_all(
        [
            LLMCall(
                provider=call.provider,
                model=call.model,
                latency_ms=call.latency_ms,
                tokens_in=call.tokens_in,
                tokens_out=call.tokens_out,
                cost_estimate=call.cost_estimate,
                status=call.status,
                fallback_reason=call.fallback_reason,
            )
            for call in calls
        ]
    )
    await session.flush()


async def get_recent_llm_calls(session: AsyncSession, limit: int = 20) -> list[LLMCall]:
    return list(
        (
            await session.scalars(
                select(LLMCall).order_by(LLMCall.created_at.desc()).limit(limit)
            )
        ).all()
    )


def to_llm_call_record(call: LLMCall) -> LLMCallRecord:
    return LLMCallRecord(
        id=call.id,
        provider=call.provider,
        model=call.model,
        latency_ms=call.latency_ms,
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        cost_estimate=call.cost_estimate,
        status=call.status,
        fallback_reason=call.fallback_reason,
        created_at=call.created_at,
    )


def to_job_record(job: Job) -> JobRecord:
    return JobRecord(
        id=job.id,
        source=job.source,
        external_id=job.external_id,
        title=job.title,
        company=job.company,
        location=job.location,
        remote=job.remote,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        description=job.description,
        url=job.url,
        posted_at=job.posted_at,
        fetched_at=job.fetched_at,
        dedupe_key=job.dedupe_key,
    )
