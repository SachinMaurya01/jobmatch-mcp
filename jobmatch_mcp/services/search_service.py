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
    create_llm_calls,
    create_matches,
    create_search,
    get_latest_resume,
    get_or_create_default_user,
    update_resume_embedding,
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
from jobmatch_mcp.services.embedding_service import EmbeddingService
from jobmatch_mcp.services.match_service import MatchService
from jobmatch_mcp.services.rerank_service import RerankService

logger = get_logger(__name__)

ProgressCallback = Callable[[float, float, str], Awaitable[None]]


class SearchService:
    def __init__(
        self,
        registry: ConnectorRegistry,
        match_service: MatchService,
        max_results: int = 30,
        *,
        embedding_service: EmbeddingService | None = None,
        rerank_service: RerankService | None = None,
    ) -> None:
        self._registry = registry
        self._match_service = match_service
        self._max_results = max_results
        self._embedding_service = embedding_service
        self._rerank_service = rerank_service

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

        resume_embedding = resume.embedding
        resume_embedding_model = resume.embedding_model
        job_embeddings: dict[str, list[float]] | None = None
        embedding_model: str | None = None

        if self._embedding_service is not None and unique_jobs:
            expected_model = self._embedding_service.model
            if resume_embedding is None or resume_embedding_model != expected_model:
                await self._notify(progress, 74, 100, "Embedding resume")
                vector, embedding_warnings = await self._embedding_service.embed_resume(
                    resume.raw_text
                )
                warnings.extend(embedding_warnings)
                if vector is not None:
                    resume_embedding = vector
                    resume_embedding_model = expected_model
                    await update_resume_embedding(session, resume.id, vector, expected_model)

            if resume_embedding is not None and resume_embedding_model == expected_model:
                await self._notify(progress, 78, 100, "Embedding postings")
                outcome = await self._embedding_service.embed_jobs(unique_jobs)
                warnings.extend(outcome.warnings)
                job_embeddings = outcome.embeddings
                embedding_model = expected_model

        await self._notify(progress, 82, 100, "Scoring and ranking matches")
        eligible = self._match_service.rank(
            profile,
            unique_jobs,
            criteria,
            resume_embedding=resume_embedding,
            resume_embedding_model=resume_embedding_model,
            job_embeddings=job_embeddings,
            embedding_model=embedding_model,
        )
        if unique_jobs and not eligible:
            warnings.append(
                f"No postings met the {self._match_service.min_score:g} minimum match score."
            )
        elif len(eligible) < len(unique_jobs):
            warnings.append(
                f"Excluded {len(unique_jobs) - len(eligible)} postings below the "
                f"{self._match_service.min_score:g} minimum match score."
            )

        if self._rerank_service is not None and eligible:
            await self._notify(progress, 88, 100, "Re-ranking top matches")
            rerank_outcome = await self._rerank_service.rerank(profile, eligible)
            warnings.extend(rerank_outcome.warnings)
            await create_llm_calls(session, rerank_outcome.calls)
            eligible = rerank_outcome.jobs

        ranked = eligible[: min(criteria.limit, self._max_results)]
        await self._notify(progress, 92, 100, "Saving ranked matches")
        jobs_to_store = [
            item.job.model_copy(
                update={
                    "embedding": job_embeddings[item.job.dedupe_key],
                    "embedding_model": embedding_model,
                }
            )
            if job_embeddings is not None
            and item.job.dedupe_key in job_embeddings
            and embedding_model is not None
            else item.job
            for item in ranked
        ]
        stored = await upsert_jobs(session, jobs_to_store)

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
