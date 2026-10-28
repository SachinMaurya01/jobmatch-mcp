"""Read-only MCP resources for the current resume, jobs, and saved searches."""

from __future__ import annotations

from uuid import UUID

from jobmatch_mcp.core.errors import InvalidInputError, NotFoundError
from jobmatch_mcp.core.logging import get_logger, request_context
from jobmatch_mcp.db.models import Job, Match
from jobmatch_mcp.db.repositories import (
    get_job,
    get_latest_resume,
    get_or_create_default_user,
    get_search_for_user,
    get_search_results,
    to_job_record,
)
from jobmatch_mcp.db.session import session_scope
from jobmatch_mcp.schemas import (
    JobDetailsOutput,
    RankedJob,
    ResumeProfile,
    ResumeResourceOutput,
    SavedSearchOutput,
    ScoreBreakdown,
    SearchCriteria,
)

logger = get_logger(__name__)


async def current_resume() -> str:
    """Return the latest parsed resume profile for the default user."""
    with request_context() as request_id:
        logger.info("resource.resume_current.start", extra={"fields": {"request_id": request_id}})
        async with session_scope() as session:
            user = await get_or_create_default_user(session)
            resume = await get_latest_resume(session, user.id)
            if resume is None:
                raise NotFoundError("No resume loaded. Call parse_resume first.")
            result = ResumeResourceOutput(
                resume_id=resume.id,
                profile=ResumeProfile.model_validate(resume.parsed_profile),
                created_at=resume.created_at,
            )
        logger.info(
            "resource.resume_current.done",
            extra={
                "fields": {
                    "resume_id": str(result.resume_id),
                    "request_id": request_id,
                }
            },
        )
        return result.model_dump_json()


async def job(id: str) -> str:
    """Return one stored job by id, including its source link."""
    job_id = _parse_uuid(id, "job id")
    with request_context() as request_id:
        logger.info(
            "resource.job.start",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        async with session_scope() as session:
            record = await get_job(session, job_id)
            if record is None:
                raise NotFoundError(f"No job found with id {job_id}")
            result = JobDetailsOutput(job=to_job_record(record))
        logger.info(
            "resource.job.done",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        return result.model_dump_json()


async def search(id: str) -> str:
    """Return a persisted search with its ranked results."""
    search_id = _parse_uuid(id, "search id")
    with request_context() as request_id:
        logger.info(
            "resource.search.start",
            extra={"fields": {"search_id": str(search_id), "request_id": request_id}},
        )
        async with session_scope() as session:
            user = await get_or_create_default_user(session)
            record = await get_search_for_user(session, user.id, search_id)
            if record is None:
                raise NotFoundError(f"No saved search found with id {search_id}")
            rows = await get_search_results(session, search_id)
            results = [_to_ranked_job(match, stored_job) for match, stored_job in rows]
            result = SavedSearchOutput(
                search_id=record.id,
                resume_id=record.resume_id,
                criteria=SearchCriteria.model_validate(record.criteria),
                created_at=record.created_at,
                result_count=len(results),
                results=results,
            )
        logger.info(
            "resource.search.done",
            extra={
                "fields": {
                    "search_id": str(search_id),
                    "count": result.result_count,
                    "request_id": request_id,
                }
            },
        )
        return result.model_dump_json()


def _to_ranked_job(match: Match, stored_job: Job) -> RankedJob:
    return RankedJob(
        job_id=stored_job.id,
        rank=match.rank,
        score=match.score,
        title=stored_job.title,
        company=stored_job.company,
        location=stored_job.location,
        remote=stored_job.remote,
        salary_min=stored_job.salary_min,
        salary_max=stored_job.salary_max,
        source=stored_job.source,
        sources=[stored_job.source],
        url=stored_job.url,
        explanation=match.explanation,
        breakdown=ScoreBreakdown.model_validate(match.breakdown),
    )


def _parse_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidInputError(f"Invalid {label}: {value}") from exc
