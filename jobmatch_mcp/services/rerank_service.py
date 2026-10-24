"""Bounded LLM re-ranking over the strongest deterministic matches."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from jobmatch_mcp.core.errors import LLMUnavailableError
from jobmatch_mcp.core.logging import get_logger
from jobmatch_mcp.llm.prompts import (
    RERANK_SCHEMA_NAME,
    build_rerank_user_prompt,
    rerank_schema,
    rerank_system_prompt,
)
from jobmatch_mcp.llm.router import LLMRouter
from jobmatch_mcp.matching.structured import ScoredJob, scored_sort_key
from jobmatch_mcp.schemas import LLMCallRecord, RerankResponse, ResumeProfile

logger = get_logger(__name__)


@dataclass(frozen=True)
class RerankOutcome:
    jobs: list[ScoredJob]
    calls: list[LLMCallRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class RerankService:
    def __init__(
        self,
        router: LLMRouter,
        *,
        top_n: int = 20,
        max_adjustment: float = 10.0,
        min_score: float = 60.0,
        max_description_chars: int = 1500,
    ) -> None:
        self._router = router
        self._top_n = top_n
        self._max_adjustment = max_adjustment
        self._min_score = min_score
        self._max_description_chars = max_description_chars

    async def rerank(
        self, profile: ResumeProfile, scored_jobs: Sequence[ScoredJob]
    ) -> RerankOutcome:
        jobs = list(scored_jobs)
        if not jobs or self._top_n <= 0:
            return RerankOutcome(jobs=jobs)

        top = jobs[: self._top_n]
        payloads = [
            self._job_payload(index, item) for index, item in enumerate(top)
        ]
        try:
            route = await self._router.generate_structured(
                system=rerank_system_prompt(self._max_adjustment),
                user=build_rerank_user_prompt(profile, payloads),
                schema=rerank_schema(self._max_adjustment),
                schema_name=RERANK_SCHEMA_NAME,
            )
        except asyncio.CancelledError:
            raise
        except LLMUnavailableError as exc:
            logger.warning(
                "rerank.unavailable",
                extra={"fields": {"error": str(exc)}},
            )
            return RerankOutcome(
                jobs=jobs,
                calls=exc.calls,
                warnings=[f"LLM re-rank unavailable ({exc}); using deterministic ranking."],
            )

        try:
            response = RerankResponse.model_validate(route.data)
        except ValidationError as exc:
            logger.warning(
                "rerank.invalid_output",
                extra={"fields": {"error": type(exc).__name__}},
            )
            return RerankOutcome(
                jobs=jobs,
                calls=route.calls,
                warnings=["LLM re-rank output failed validation; using deterministic ranking."],
            )

        decisions = {
            decision.index: decision
            for decision in response.decisions
            if 0 <= decision.index < len(top)
        }
        reranked_top: list[ScoredJob] = []
        for index, item in enumerate(top):
            decision = decisions.get(index)
            if decision is None:
                reranked_top.append(item)
                continue
            adjustment = max(
                -self._max_adjustment, min(self._max_adjustment, decision.adjustment)
            )
            pre_total = (
                item.breakdown.pre_rerank_total
                if item.breakdown.pre_rerank_total is not None
                else item.score
            )
            final_score = max(0.0, min(100.0, pre_total + adjustment))
            breakdown = item.breakdown.model_copy(
                update={
                    "total": round(final_score, 2),
                    "pre_rerank_total": round(pre_total, 2),
                    "llm_adjustment": round(adjustment, 2),
                    "llm_provider": route.provider,
                    "llm_model": route.model,
                    "seniority_fit": decision.seniority_fit or item.breakdown.seniority_fit,
                    "suggestions": decision.suggestions or item.breakdown.suggestions,
                }
            )
            reranked_top.append(
                ScoredJob(
                    job=item.job,
                    score=round(final_score, 2),
                    breakdown=breakdown,
                    explanation=decision.explanation,
                )
            )

        combined = reranked_top + jobs[len(top) :]
        combined.sort(key=scored_sort_key)
        filtered = [item for item in combined if item.score >= self._min_score]
        warnings: list[str] = []
        if len(filtered) < len(combined):
            warnings.append(
                f"Excluded {len(combined) - len(filtered)} postings after LLM re-rank "
                f"below the {self._min_score:g} minimum match score."
            )
        return RerankOutcome(
            jobs=filtered,
            calls=route.calls,
            warnings=warnings,
            provider=route.provider,
            model=route.model,
        )

    def _job_payload(self, index: int, item: ScoredJob) -> dict[str, Any]:
        job = item.job
        return {
            "index": index,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "remote": job.remote,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "description": job.description[: self._max_description_chars],
            "pre_rank_score": round(item.score, 2),
            "matched_skills": item.breakdown.matched_skills,
            "missing_skills": item.breakdown.missing_skills,
            "components": {
                name: component.score
                for name, component in item.breakdown.components.items()
                if component.included
            },
        }
