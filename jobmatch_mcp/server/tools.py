"""MCP tools: resume parsing, multi-source search, job details, and source health."""

from __future__ import annotations

from uuid import UUID

from fastmcp import Context

from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.core.errors import InvalidInputError, NotFoundError
from jobmatch_mcp.core.logging import get_logger, request_context
from jobmatch_mcp.db.repositories import get_job, to_job_record
from jobmatch_mcp.db.session import session_scope
from jobmatch_mcp.schemas import (
    JobDetailsOutput,
    ListSourcesOutput,
    ParseResumeOutput,
    SearchCriteria,
    SearchJobsOutput,
)
from jobmatch_mcp.server.deps import get_registry
from jobmatch_mcp.services.match_service import MatchService
from jobmatch_mcp.services.resume_service import ResumeService
from jobmatch_mcp.services.search_service import ProgressCallback, SearchService

logger = get_logger(__name__)


async def parse_resume(
    path: str | None = None,
    text: str | None = None,
) -> ParseResumeOutput:
    """Parse a resume from a file path (txt, md, pdf) or raw text and store it.

    Provide exactly one of `path` or `text`. Returns a structured profile and
    the stored resume id. Resume content is never logged.
    """
    settings = get_settings()
    with request_context() as request_id:
        logger.info(
            "tool.parse_resume.start",
            extra={"fields": {"has_path": path is not None, "text_chars": len(text or "")}},
        )
        async with session_scope() as session:
            service = ResumeService(settings.max_resume_bytes)
            resume, profile = await service.parse_and_store(session, path=path, text=text)
            result = ParseResumeOutput(resume_id=resume.id, profile=profile)
        logger.info(
            "tool.parse_resume.done",
            extra={
                "fields": {
                    "resume_id": str(result.resume_id),
                    "skills": len(profile.skills),
                    "request_id": request_id,
                }
            },
        )
        return result


async def search_jobs(
    query: str | None = None,
    locations: list[str] | None = None,
    remote: bool | None = None,
    min_salary: float | None = None,
    sources: list[str] | None = None,
    limit: int | None = None,
    ctx: Context | None = None,
) -> SearchJobsOutput:
    """Search enabled job sources, score each posting against the latest resume,
    and return a ranked list with a score (0-100) and score breakdown.

    Load a resume with `parse_resume` first. Jobs scoring below the configured
    minimum (default 60) are excluded, and at most 30 jobs are returned. If
    `limit` exceeds the configured maximum it is clamped. Duplicate postings
    across sources are merged, and a failing source does not fail the search.
    Progress notifications are sent when the client supports them.
    """
    settings = get_settings()
    effective_limit = limit if limit is not None else settings.search_default_limit
    if effective_limit < 1:
        raise InvalidInputError("limit must be at least 1")
    effective_limit = min(effective_limit, settings.search_max_limit)

    if min_salary is not None and min_salary < 0:
        raise InvalidInputError("min_salary must be non-negative")

    criteria = SearchCriteria(
        query=query.strip() if query and query.strip() else None,
        locations=[item.strip() for item in (locations or []) if item.strip()],
        remote=remote,
        min_salary=min_salary,
        limit=effective_limit,
    )

    with request_context() as request_id:
        logger.info(
            "tool.search_jobs.start",
            extra={
                "fields": {
                    "has_query": criteria.query is not None,
                    "locations": len(criteria.locations),
                    "remote": criteria.remote,
                    "min_salary": criteria.min_salary,
                    "limit": criteria.limit,
                }
            },
        )
        service = SearchService(
            registry=get_registry(),
            match_service=MatchService(settings.scoring_weights, settings.min_match_score),
            max_results=settings.search_max_limit,
        )
        progress: ProgressCallback | None = None
        if ctx is not None:

            async def report(current: float, total: float, message: str) -> None:
                await ctx.report_progress(current, total, message)

            progress = report

        async with session_scope() as session:
            result = await service.search(session, criteria, sources, progress)
        logger.info(
            "tool.search_jobs.done",
            extra={
                "fields": {
                    "search_id": str(result.search_id),
                    "count": result.count,
                    "sources": result.sources_used,
                    "warnings": len(result.warnings),
                    "request_id": request_id,
                }
            },
        )
        return result


async def list_sources() -> ListSourcesOutput:
    """List enabled job sources with health, last success, and rate-limit state."""
    with request_context() as request_id:
        logger.info(
            "tool.list_sources.start",
            extra={"fields": {"request_id": request_id}},
        )
        statuses = await get_registry().statuses()
        logger.info(
            "tool.list_sources.done",
            extra={
                "fields": {
                    "count": len(statuses),
                    "request_id": request_id,
                }
            },
        )
        return ListSourcesOutput(sources=statuses)


async def get_job_details(job_id: UUID) -> JobDetailsOutput:
    """Return the full stored record for one job, including the source link."""
    with request_context() as request_id:
        logger.info(
            "tool.get_job_details.start",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        async with session_scope() as session:
            job = await get_job(session, job_id)
            if job is None:
                raise NotFoundError(f"No job found with id {job_id}")
            result = JobDetailsOutput(job=to_job_record(job))
        logger.info(
            "tool.get_job_details.done",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        return result
