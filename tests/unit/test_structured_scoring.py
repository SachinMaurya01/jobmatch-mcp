"""Structured scoring and ranking behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.dedupe import normalize_dedupe_key
from jobmatch_mcp.matching.structured import rank_jobs, score_job
from jobmatch_mcp.schemas import RawJob, ResumeProfile, SearchCriteria, Seniority

PROFILE = ResumeProfile(
    skills=["AWS", "Docker", "FastAPI", "PostgreSQL", "Python"],
    years_experience=6,
    seniority=Seniority.SENIOR,
    locations=["Bengaluru"],
)

DESCRIPTION = (
    "We need a senior engineer with 5+ years of experience building Python, FastAPI, "
    "PostgreSQL, Docker and AWS services. This is a remote role."
)


def make_job(
    *,
    external_id: str = "1",
    title: str = "Senior Python Engineer",
    location: str | None = "Bengaluru, India",
    remote: bool = True,
    salary_min: float | None = 120_000,
    salary_max: float | None = 150_000,
    description: str = DESCRIPTION,
) -> RawJob:
    return RawJob(
        source="adzuna",
        external_id=external_id,
        title=title,
        company="Acme",
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        description=description,
        url=f"https://example.com/jobs/{external_id}",
        posted_at=datetime(2026, 10, 1, tzinfo=UTC),
        dedupe_key=normalize_dedupe_key(title, "Acme", location),
    )


def full_criteria() -> SearchCriteria:
    return SearchCriteria(
        query="python",
        locations=["Bengaluru"],
        remote=None,
        min_salary=100_000,
        limit=10,
    )


def test_perfect_match_scores_100() -> None:
    scored = score_job(PROFILE, make_job(), full_criteria(), ScoringWeights())
    assert scored.score == 100
    assert set(scored.breakdown.weights_used) == {
        "skills",
        "experience",
        "seniority",
        "location",
        "salary",
    }
    assert abs(sum(scored.breakdown.weights_used.values()) - 1.0) < 1e-6
    assert "PostgreSQL" in scored.explanation


def test_missing_salary_redistributes_weight() -> None:
    job = make_job(salary_min=None, salary_max=None)
    scored = score_job(PROFILE, job, full_criteria(), ScoringWeights())
    assert "salary" not in scored.breakdown.weights_used
    assert scored.breakdown.components["salary"].included is False
    assert scored.score == 100
    assert abs(sum(scored.breakdown.weights_used.values()) - 1.0) < 1e-6


def test_skills_missing_reduces_score() -> None:
    job = make_job(
        description="Requires Kubernetes, Terraform, Go and Python. 5+ years.",
    )
    scored = score_job(PROFILE, job, full_criteria(), ScoringWeights())
    assert 0 < scored.score < 100
    assert "missing" in scored.breakdown.components["skills"].detail


def test_location_mismatch_lowers_score() -> None:
    job = make_job(location="Pune, India", remote=False)
    scored = score_job(PROFILE, job, full_criteria(), ScoringWeights())
    assert scored.breakdown.components["location"].score == 0
    assert scored.score < 100


def test_scores_stay_in_range() -> None:
    empty_profile = ResumeProfile()
    scored = score_job(empty_profile, make_job(), full_criteria(), ScoringWeights())
    assert 0 <= scored.score <= 100


def test_ranking_is_descending() -> None:
    strong = make_job(external_id="strong")
    weak = make_job(
        external_id="weak",
        title="Junior PHP Developer",
        location="Pune, India",
        remote=False,
        salary_min=20_000,
        salary_max=30_000,
        description="PHP and MySQL. 1 year of experience.",
    )
    ranked = rank_jobs(PROFILE, [weak, strong], full_criteria(), ScoringWeights())
    assert [item.job.external_id for item in ranked] == ["strong", "weak"]
    assert ranked[0].score >= ranked[1].score
