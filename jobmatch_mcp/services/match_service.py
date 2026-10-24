"""Match orchestration around the structured scoring pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.structured import ScoredJob, rank_jobs
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria


class MatchService:
    def __init__(self, weights: ScoringWeights, min_score: float = 60.0) -> None:
        self._weights = weights
        self._min_score = min_score

    @property
    def min_score(self) -> float:
        return self._min_score

    def rank(
        self,
        profile: ResumeProfile,
        jobs: list[RawJob],
        criteria: SearchCriteria,
        *,
        resume_embedding: Sequence[float] | None = None,
        resume_embedding_model: str | None = None,
        job_embeddings: Mapping[str, Sequence[float]] | None = None,
        embedding_model: str | None = None,
    ) -> list[ScoredJob]:
        return rank_jobs(
            profile,
            jobs,
            criteria,
            self._weights,
            self._min_score,
            resume_embedding=resume_embedding,
            resume_embedding_model=resume_embedding_model,
            job_embeddings=job_embeddings,
            embedding_model=embedding_model,
        )
