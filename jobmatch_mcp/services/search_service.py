"""Search orchestration: fan out, upsert, score, rank, persist."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.errors import (
    ConfigurationError,
    ConnectorError,
    InvalidInputError,
    JobMatchError,
)
from jobmatch_mcp.core.logging import get_logger
from jobmatch_mcp.db.repositories import (
    create_matches,
    create_search,
    get_latest_resume,
    get_or_create_default_user,
    upsert_jobs,
)
from jobmatch_mcp.schemas import (
    RankedJob,
    RawJob,
    ResumeProfile,
    SearchCriteria,
    SearchJobsOutput,
)
from jobmatch_mcp.services.match_service import MatchService

logger = get_logger(__name__)


class SearchService:
    def __init__(
        self,
        registry: ConnectorRegistry,
        match_service: MatchService,
    ) -> None:
        self._registry = registry
        self._match_service = match_service

    async def search(
        self,
        session: AsyncSession,
        criteria: SearchCriteria,
        sources: list[str] | None = None,
    ) -> SearchJobsOutput:
        user = await get_or_create_default_user(session)
        resume = await get_latest_resume(session, user.id)
        if resume is None:
            raise InvalidInputError("No resume loaded. Call parse_resume first.")
        profile = ResumeProfile.model_validate(resume.parsed_profile)

        connectors = self._registry.select(sources)
        if not connectors:
            raise ConfigurationError(
                "No job sources are enabled. Set JOBMATCH_JSEARCH_API_KEY to enable "
                "the JSearch connector."
            )

        results = await asyncio.gather(
            *(connector.search(criteria) for connector in connectors),
            return_exceptions=True,
        )

        jobs: list[RawJob] = []
        warnings: list[str] = []
        sources_used: list[str] = []
        for connector, result in zip(connectors, results, strict=True):
            if isinstance(result, BaseException):
                detail = str(result) if isinstance(result, JobMatchError) else type(result).__name__
                warnings.append(f"{connector.name} failed: {detail}")
                logger.warning(
                    "connector.failed",
                    extra={"fields": {"source": connector.name, "error": detail}},
                )
                continue
            sources_used.append(connector.name)
            jobs.extend(result)

        if not sources_used:
            raise ConnectorError("All job sources failed. " + " ".join(warnings))

        unique_jobs = self._dedupe_raw(jobs)
        warnings.extend(self._filter_warnings(criteria, unique_jobs))

        if not unique_jobs:
            warnings.append("No postings were returned for these criteria.")

        stored = await upsert_jobs(session, unique_jobs)
        ranked = self._match_service.rank(profile, unique_jobs, criteria)[: criteria.limit]

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
                    url=scored.job.url,
                    explanation=scored.explanation,
                    breakdown=scored.breakdown,
                )
            )

        await create_matches(session, search.id, match_rows)

        return SearchJobsOutput(
            search_id=search.id,
            resume_id=resume.id,
            count=len(output_rows),
            sources_used=sources_used,
            warnings=warnings,
            results=output_rows,
        )

    @staticmethod
    def _dedupe_raw(jobs: list[RawJob]) -> list[RawJob]:
        seen: set[tuple[str, str]] = set()
        unique: list[RawJob] = []
        for job in jobs:
            key = (job.source, job.external_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(job)
        return unique

    @staticmethod
    def _filter_warnings(criteria: SearchCriteria, jobs: list[RawJob]) -> list[str]:
        warnings: list[str] = []
        if criteria.remote is True and not any(job.remote for job in jobs):
            warnings.append("No remote postings matched the request.")
        return warnings
