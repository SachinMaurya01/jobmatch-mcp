"""MCP tools: resume parsing, hybrid search, explanations, and provider health."""

from __future__ import annotations

from uuid import UUID

from fastmcp import Context

from jobmatch_mcp.core.config import Settings, get_settings
from jobmatch_mcp.core.errors import InvalidInputError, NotFoundError
from jobmatch_mcp.core.logging import get_logger, request_context
from jobmatch_mcp.db.repositories import (
    get_job,
    get_latest_match_for_job,
    get_or_create_default_user,
    get_recent_llm_calls,
    to_job_record,
    to_llm_call_record,
)
from jobmatch_mcp.db.session import session_scope
from jobmatch_mcp.schemas import (
    ExplainMatchOutput,
    JobDetailsOutput,
    ListSourcesOutput,
    ParseResumeOutput,
    ProviderStatus,
    ProviderStatusOutput,
    ScoreBreakdown,
    SearchCriteria,
    SearchJobsOutput,
    SearchPreferences,
)
from jobmatch_mcp.server.deps import (
    get_embedding_service,
    get_llm_router,
    get_registry,
    get_rerank_service,
)
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
            service = ResumeService(
                settings.max_resume_bytes,
                embedding_service=(get_embedding_service() if settings.enable_semantic else None),
            )
            resume, profile, warnings = await service.parse_and_store(session, path=path, text=text)
            result = ParseResumeOutput(resume_id=resume.id, profile=profile, warnings=warnings)
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
    criteria = await _elicit_search_preferences(ctx, settings, criteria)

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
            embedding_service=(get_embedding_service() if settings.enable_semantic else None),
            rerank_service=(get_rerank_service() if settings.enable_llm_rerank else None),
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


async def explain_match(job_id: UUID) -> ExplainMatchOutput:
    """Explain the most recent stored match for one job.

    Returns matched and missing skills, seniority fit, suggestions, and the full
    score breakdown. Run `search_jobs` first so a match exists.
    """
    with request_context() as request_id:
        logger.info(
            "tool.explain_match.start",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        async with session_scope() as session:
            job = await get_job(session, job_id)
            if job is None:
                raise NotFoundError(f"No job found with id {job_id}")
            user = await get_or_create_default_user(session)
            latest = await get_latest_match_for_job(session, user.id, job_id)
            if latest is None:
                raise NotFoundError(f"No stored match for job {job_id}. Run search_jobs first.")
            match, search = latest
            breakdown = ScoreBreakdown.model_validate(match.breakdown)
            result = ExplainMatchOutput(
                job_id=job.id,
                search_id=search.id,
                score=match.score,
                rank=match.rank,
                explanation=match.explanation,
                matched_skills=breakdown.matched_skills,
                missing_skills=breakdown.missing_skills,
                seniority_fit=breakdown.seniority_fit or _seniority_fit(breakdown),
                suggestions=breakdown.suggestions or _suggestions(breakdown),
                breakdown=breakdown,
                created_at=search.created_at,
            )
        logger.info(
            "tool.explain_match.done",
            extra={"fields": {"job_id": str(job_id), "request_id": request_id}},
        )
        return result


async def get_provider_status() -> ProviderStatusOutput:
    """Show LLM provider configuration, circuit state, and recent call costs."""
    settings = get_settings()
    with request_context() as request_id:
        logger.info(
            "tool.get_provider_status.start",
            extra={"fields": {"request_id": request_id}},
        )
        router = get_llm_router()
        statuses = await router.statuses()
        async with session_scope() as session:
            calls = await get_recent_llm_calls(session, settings.llm_recent_calls_limit)
        records = [to_llm_call_record(call) for call in calls]

        counts: dict[str, int] = {}
        costs: dict[str, float] = {}
        for record in records:
            counts[record.provider] = counts.get(record.provider, 0) + 1
            costs[record.provider] = costs.get(record.provider, 0.0) + record.cost_estimate

        providers: list[ProviderStatus] = [
            status.model_copy(
                update={
                    "recent_calls": counts.get(status.name, 0),
                    "recent_cost_estimate": round(costs.get(status.name, 0.0), 6),
                }
            )
            for status in statuses
        ]
        result = ProviderStatusOutput(
            providers=providers,
            recent_calls=records,
            total_cost_estimate=round(sum(costs.values()), 6),
        )
        logger.info(
            "tool.get_provider_status.done",
            extra={
                "fields": {
                    "providers": len(providers),
                    "recent_calls": len(records),
                    "request_id": request_id,
                }
            },
        )
        return result


async def _elicit_search_preferences(
    ctx: Context | None,
    settings: Settings,
    criteria: SearchCriteria,
) -> SearchCriteria:
    if not settings.enable_elicitation or ctx is None:
        return criteria
    if criteria.locations or criteria.remote is not None or criteria.min_salary is not None:
        return criteria
    elicit = getattr(ctx, "elicit", None)
    if not callable(elicit):
        return criteria

    try:
        response = await elicit(
            "Optionally add a location, remote preference, or salary floor for this search.",
            SearchPreferences,
        )
    except Exception as exc:
        logger.info(
            "tool.search_jobs.elicitation_unavailable",
            extra={"fields": {"error": type(exc).__name__}},
        )
        return criteria

    if getattr(response, "action", None) != "accept":
        return criteria
    data = getattr(response, "data", None)
    if data is None:
        return criteria
    try:
        preferences = (
            data if isinstance(data, SearchPreferences) else SearchPreferences.model_validate(data)
        )
    except Exception as exc:
        logger.info(
            "tool.search_jobs.elicitation_invalid",
            extra={"fields": {"error": type(exc).__name__}},
        )
        return criteria

    locations = criteria.locations
    if preferences.locations:
        locations = [item.strip() for item in preferences.locations.split(",") if item.strip()]
    remote = criteria.remote
    if preferences.remote == "remote":
        remote = True
    elif preferences.remote == "onsite":
        remote = False
    min_salary = criteria.min_salary
    if preferences.min_salary > 0:
        min_salary = preferences.min_salary
    return criteria.model_copy(
        update={
            "locations": locations,
            "remote": remote,
            "min_salary": min_salary,
        }
    )


def _seniority_fit(breakdown: ScoreBreakdown) -> str:
    component = breakdown.components.get("seniority")
    return component.detail if component is not None else ""


def _suggestions(breakdown: ScoreBreakdown) -> list[str]:
    suggestions = [
        f"Add concrete evidence for {skill} to the resume."
        for skill in breakdown.missing_skills[:5]
    ]
    if not suggestions and breakdown.matched_skills:
        suggestions.append("Keep the matched skills prominent in the resume summary.")
    if not suggestions:
        suggestions.append(
            "Add more detail about relevant tools, projects, and outcomes to improve the match."
        )
    return suggestions
