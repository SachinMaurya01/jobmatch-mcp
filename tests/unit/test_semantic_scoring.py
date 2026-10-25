"""Semantic similarity and hybrid scoring behavior."""

from __future__ import annotations

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.matching.semantic import cosine_similarity, semantic_component
from jobmatch_mcp.matching.structured import score_job
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria, Seniority

PROFILE = ResumeProfile(
    skills=["Python", "PostgreSQL"],
    years_experience=6,
    seniority=Seniority.SENIOR,
    locations=["Bengaluru"],
)

DESCRIPTION = "Senior Python and PostgreSQL role. 5+ years. Remote."


def make_job(external_id: str = "1") -> RawJob:
    return RawJob(
        source="test",
        external_id=external_id,
        title="Senior Python Engineer",
        company="Acme",
        location="Bengaluru, India",
        remote=True,
        salary_min=100_000,
        salary_max=150_000,
        description=DESCRIPTION,
        url=f"https://example.com/{external_id}",
        dedupe_key=normalize_dedupe_key("Senior Python Engineer", "Acme", "Bengaluru, India"),
    )


def criteria() -> SearchCriteria:
    return SearchCriteria(query="python", locations=["Bengaluru"], min_salary=90_000)


def test_cosine_similarity_bounds() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([-1.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_semantic_component_requires_matching_models() -> None:
    score, detail = semantic_component(
        [1.0, 0.0],
        [1.0, 0.0],
        resume_model="a",
        job_model="b",
        expected_model="a",
    )
    assert score is None
    assert "differ" in detail


def test_hybrid_score_includes_semantic_component() -> None:
    job = make_job()
    structured_only = score_job(PROFILE, job, criteria(), ScoringWeights())
    assert "semantic" not in structured_only.breakdown.weights_used

    hybrid = score_job(
        PROFILE,
        job,
        criteria(),
        ScoringWeights(),
        resume_embedding=[1.0, 0.0],
        resume_embedding_model="test-model",
        job_embedding=[1.0, 0.0],
        job_embedding_model="test-model",
        embedding_model="test-model",
    )
    assert "semantic" in hybrid.breakdown.weights_used
    assert hybrid.breakdown.components["semantic"].included is True
    assert hybrid.breakdown.components["semantic"].score == 100.0


def test_weak_semantic_similarity_lowers_hybrid_score() -> None:
    job = make_job()
    strong = score_job(
        PROFILE,
        job,
        criteria(),
        ScoringWeights(),
        resume_embedding=[1.0, 0.0],
        resume_embedding_model="test-model",
        job_embedding=[1.0, 0.0],
        job_embedding_model="test-model",
        embedding_model="test-model",
    )
    weak = score_job(
        PROFILE,
        job,
        criteria(),
        ScoringWeights(),
        resume_embedding=[1.0, 0.0],
        resume_embedding_model="test-model",
        job_embedding=[0.0, 1.0],
        job_embedding_model="test-model",
        embedding_model="test-model",
    )
    assert weak.score < strong.score
