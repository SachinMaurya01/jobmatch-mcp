"""Search orchestration: fan out, dedupe, score, rank, persist."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from jobmatch_mcp.connectors.controls import ConnectorRuntime
from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.errors import (
    ConfigurationError,
    ConnectorError,
    InvalidInputError,
    describe_error,
)
from jobmatch_mcp.core.logging import get_logger
from jobmatch_mcp.db.repositories import (
    create_matches,
    create_search,
    get_latest_resume,
    get_or_create_default_user,
    upsert_jobs,
)
from jobmatch_mcp.matching.dedupe import dedupe_jobs
from jobmatch_mcp.schemas import (
    RankedJob,
    RawJob,
    ResumeProfile,
    SearchCriteria,
    SearchJobsOutput,
)
from jobmatch_mcp.services.match_service import MatchService

logger = get_logger(__name__)

ProgressCallback = Callable[[float, float, str], Awaitable[None]]


class SearchService:
    def __init__(
        self,
        registry: ConnectorRegistry,
        match_service: MatchService,
        max_results: int = 30,
    ) -> None:
        self._registry = registry
        self._match_service = match_service
        self._max_results = max_results

    async def search(
        self,
        session: AsyncSession,
        criteria: SearchCriteria,
        sources: list[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> SearchJobsOutput:
        await self._notify(progress, 0, 100, "Loading resume")
        user = await get_or_create_default_user(session)
        resume = await get_latest_resume(session, user.id)
        if resume is None:
            raise InvalidInputError("No resume loaded. Call parse_resume first.")
        profile = ResumeProfile.model_validate(resume.parsed_profile)

        runtimes = self._registry.select(sources)
        if not runtimes:
            raise ConfigurationError(
                "No job sources are enabled. Configure at least one of JSearch, "
                "Greenhouse, Lever, or SerpAPI."
            )

        await self._notify(
            progress,
            5,
            100,
            f"Searching {len(runtimes)} source(s)",
        )
        completed = 0
        progress_lock = asyncio.Lock()

        async def run_source(runtime: ConnectorRuntime) -> list[RawJob]:
            nonlocal completed
            try:
                return await runtime.search(criteria)
            finally:
                async with progress_lock:
                    completed += 1
                    await self._notify(
                        progress,
                        5 + (completed / len(runtimes)) * 55,
                        100,
                        f"{runtime.name} finished",
                    )

        results = await asyncio.gather(
            *(run_source(runtime) for runtime in runtimes),
            return_exceptions=True,
        )

        jobs: list[RawJob] = []
        warnings: list[str] = []
        sources_used: list[str] = []
        for runtime, result in zip(runtimes, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                detail = describe_error(result)
                warnings.append(f"{runtime.name} failed: {detail}")
                logger.warning(
                    "connector.failed",
                    extra={"fields": {"source": runtime.name, "error": detail}},
                )
                continue
            sources_used.append(runtime.name)
            jobs.extend(result)

        if not sources_used:
            raise ConnectorError("All job sources failed. " + " ".join(warnings))

        await self._notify(progress, 70, 100, "Deduplicating results")
        unique_jobs = dedupe_jobs(jobs)
        warnings.extend(self._filter_warnings(criteria, unique_jobs))

        if not unique_jobs:
            warnings.append("No postings were returned for these criteria.")

        await self._notify(progress, 80, 100, "Scoring and ranking matches")
        eligible = self._match_service.rank(profile, unique_jobs, criteria)
        if unique_jobs and not eligible:
            warnings.append(
                f"No postings met the {self._match_service.min_score:g} minimum match score."
            )
        elif len(eligible) < len(unique_jobs):
            warnings.append(
                f"Excluded {len(unique_jobs) - len(eligible)} postings below the "
                f"{self._match_service.min_score:g} minimum match score."
            )

        ranked = eligible[: min(criteria.limit, self._max_results)]
        await self._notify(progress, 90, 100, "Saving ranked matches")
        stored = await upsert_jobs(session, [item.job for item in ranked])

        search = await create_search(session, user.id, resume.id, criteria)
        match_rows: list[tuple[UUID, float, dict[str, Any], str, int]] = []
        output_rows: list[RankedJob] = []

        for rank, scored in enumerate(ranked, start=1):
            stored_job = stored[(scored.job.source, scored.job.external_id)]
            breakdown = scored.breakdown.model_dump(mode="json")
            match_rows.append((stored_job.id, scored.score, breakdown, scored.explanation, rank))
            output_rows.append(
                RankedJob(
                    job_id=stored_job.id,
                    rank=rank,
                    score=scored.score,
                    title=scored.job.title,
                    company=scored.job.company,
                    location=scored.job.location,
                    remote=scored.job.remote,
                    salary_min=scored.job.salary_min,
                    salary_max=scored.job.salary_max,
                    source=scored.job.source,
                    sources=scored.job.sources,
                    url=scored.job.url,
                    explanation=scored.explanation,
                    breakdown=scored.breakdown,
                )
            )

        await create_matches(session, search.id, match_rows)
        await self._notify(progress, 100, 100, "Search complete")

        return SearchJobsOutput(
            search_id=search.id,
            resume_id=resume.id,
            count=len(output_rows),
            sources_used=sources_used,
            source_statuses=await self._registry.statuses(),
            warnings=warnings,
            results=output_rows,
        )

    @staticmethod
    async def _notify(
        progress: ProgressCallback | None,
        current: float,
        total: float,
        message: str,
    ) -> None:
        if progress is None:
            return
        try:
            await progress(current, total, message)
        except Exception as exc:
            logger.warning(
                "search.progress_failed",
                extra={"fields": {"error": type(exc).__name__}},
            )

    @staticmethod
    def _filter_warnings(criteria: SearchCriteria, jobs: list[RawJob]) -> list[str]:
        warnings: list[str] = []
        if criteria.remote is True and not any(job.remote for job in jobs):
            warnings.append("No remote postings matched the request.")
        return warnings
