"""Synthetic evaluation: hybrid semantic ranking beats structured-only."""

from __future__ import annotations

import math

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.matching.structured import ScoredJob, rank_jobs
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria, Seniority

PROFILE = ResumeProfile(
    skills=["Python"],
    years_experience=5,
    seniority=Seniority.UNKNOWN,
)

DESCRIPTION = "Python role. 5 years."


def make_job(index: int, *, relevant: bool) -> RawJob:
    title = f"{'Zeta' if relevant else 'Alpha'} {index}"
    return RawJob(
        source="eval",
        external_id=f"job-{index}",
        title=title,
        company="Acme",
        location="Remote",
        remote=True,
        salary_min=100_000,
        salary_max=120_000,
        description=DESCRIPTION,
        url=f"https://example.com/jobs/{index}",
        dedupe_key=normalize_dedupe_key(title, "Acme", "Remote"),
    )


def ndcg_at_10(ranked: list[ScoredJob], relevance: dict[str, int]) -> float:
    gains = [relevance.get(item.job.external_id, 0) for item in ranked[:10]]
    dcg = sum(gain / math.log2(position + 2) for position, gain in enumerate(gains))
    ideal = sorted(relevance.values(), reverse=True)[:10]
    idcg = sum(gain / math.log2(position + 2) for position, gain in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def test_hybrid_ranking_improves_ndcg_over_structured_only() -> None:
    jobs: list[RawJob] = []
    relevance: dict[str, int] = {}
    for index in range(10):
        relevant = index % 2 == 0
        job = make_job(index, relevant=relevant)
        jobs.append(job)
        relevance[job.external_id] = 1 if relevant else 0

    embeddings = {
        job.dedupe_key: ([1.0, 0.0] if relevance[job.external_id] else [0.0, 1.0]) for job in jobs
    }
    criteria = SearchCriteria(limit=10)

    structured = rank_jobs(PROFILE, jobs, criteria, ScoringWeights(), min_score=0)
    hybrid = rank_jobs(
        PROFILE,
        jobs,
        criteria,
        ScoringWeights(),
        min_score=0,
        resume_embedding=[1.0, 0.0],
        resume_embedding_model="eval-model",
        job_embeddings=embeddings,
        embedding_model="eval-model",
    )

    structured_ndcg = ndcg_at_10(structured, relevance)
    hybrid_ndcg = ndcg_at_10(hybrid, relevance)

    assert hybrid_ndcg == 1.0
    assert hybrid_ndcg > structured_ndcg + 0.2
