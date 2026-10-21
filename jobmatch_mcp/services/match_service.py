"""Match orchestration around the structured scoring pipeline."""

from __future__ import annotations

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.structured import ScoredJob, rank_jobs
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria


class MatchService:
    def __init__(self, weights: ScoringWeights) -> None:
        self._weights = weights

    def rank(
        self,
        profile: ResumeProfile,
        jobs: list[RawJob],
        criteria: SearchCriteria,
    ) -> list[ScoredJob]:
        return rank_jobs(profile, jobs, criteria, self._weights)
