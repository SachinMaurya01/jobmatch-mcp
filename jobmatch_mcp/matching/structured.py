"""Structured (non-semantic) scoring and deterministic ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from jobmatch_mcp.core.config import ScoringWeights
from jobmatch_mcp.matching.semantic import semantic_component
from jobmatch_mcp.matching.seniority import (
    extract_required_years,
    infer_job_seniority,
    seniority_distance,
)
from jobmatch_mcp.matching.skills import extract_skills
from jobmatch_mcp.schemas import (
    ComponentScore,
    RawJob,
    ResumeProfile,
    ScoreBreakdown,
    SearchCriteria,
)


@dataclass(frozen=True)
class ScoredJob:
    job: RawJob
    score: float
    breakdown: ScoreBreakdown
    explanation: str


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _skills_component(
    profile: ResumeProfile, job_skills: list[str]
) -> tuple[float, str, list[str], list[str]]:
    resume_skills = set(profile.skills)
    posting_skills = set(job_skills)
    matched = sorted(resume_skills & posting_skills)
    missing = sorted(posting_skills - resume_skills)

    if not posting_skills:
        return 0.5, "No recognized skills found in the posting; neutral score.", matched, missing
    if not resume_skills:
        return 0.0, "No recognized skills found in the resume.", matched, missing

    f1 = 2 * len(matched) / (len(resume_skills) + len(posting_skills))
    detail = (
        f"Matched {len(matched)} of {len(posting_skills)} posting skills"
        + (f" ({', '.join(matched)})" if matched else "")
        + (f"; missing {', '.join(missing)}" if missing else "")
        + "."
    )
    return f1, detail, matched, missing


def _experience_component(profile: ResumeProfile, job: RawJob) -> tuple[float | None, str]:
    required = extract_required_years(job.description)
    if required is None or profile.years_experience is None:
        return None, "Experience requirement could not be compared."
    if profile.years_experience >= required:
        return 1.0, (f"{profile.years_experience:g} years meets the {required:g} year requirement.")
    return _ratio(profile.years_experience, required), (
        f"{profile.years_experience:g} of {required:g} required years."
    )


def _seniority_component(profile: ResumeProfile, job: RawJob) -> tuple[float | None, str]:
    job_seniority = infer_job_seniority(job.title, job.description)
    distance = seniority_distance(profile.seniority, job_seniority)
    if distance is None:
        return None, "Seniority could not be compared."
    score = {0: 1.0, 1: 0.6}.get(distance, 0.2)
    return score, (f"Seniority {profile.seniority.value} vs posting {job_seniority.value}.")


def _location_component(job: RawJob, criteria: SearchCriteria) -> tuple[float | None, str]:
    if criteria.remote is True:
        return (1.0 if job.remote else 0.0), (
            "Remote requested; posting is remote."
            if job.remote
            else "Remote requested; posting is not remote."
        )
    if criteria.remote is False:
        return (0.0 if job.remote else 1.0), (
            "On-site requested; posting is remote."
            if job.remote
            else "On-site requested; posting is not remote."
        )
    if criteria.locations:
        job_location = (job.location or "").lower()
        matched = any(
            location.lower() in job_location for location in criteria.locations if location.strip()
        )
        if matched:
            return 1.0, f"Location matches requested area ({job.location or 'unknown'})."
        if job.remote:
            return 0.8, "Requested location not listed, but posting is remote."
        return 0.0, f"Location {job.location or 'unknown'} does not match the request."
    return None, "No location preference supplied."


def _salary_component(job: RawJob, criteria: SearchCriteria) -> tuple[float | None, str]:
    if criteria.min_salary is None or criteria.min_salary <= 0:
        return None, "No salary floor supplied."
    best_known = job.salary_max or job.salary_min
    if best_known is None:
        return None, "Posting has no salary data; weight redistributed."
    score = _ratio(best_known, criteria.min_salary)
    return score, (f"Salary ceiling {best_known:g} vs requested floor {criteria.min_salary:g}.")


def score_job(
    profile: ResumeProfile,
    job: RawJob,
    criteria: SearchCriteria,
    weights: ScoringWeights,
    *,
    resume_embedding: Sequence[float] | None = None,
    resume_embedding_model: str | None = None,
    job_embedding: Sequence[float] | None = None,
    job_embedding_model: str | None = None,
    embedding_model: str | None = None,
) -> ScoredJob:
    """Score one posting with the hybrid pipeline."""
    job_skills = extract_skills(f"{job.title}\n{job.description}")

    skills_score, skills_detail, matched, missing = _skills_component(profile, job_skills)
    semantic_score, semantic_detail = semantic_component(
        resume_embedding,
        job_embedding,
        resume_model=resume_embedding_model,
        job_model=job_embedding_model,
        expected_model=embedding_model,
    )
    raw_components: dict[str, tuple[float | None, str]] = {
        "skills": (skills_score, skills_detail),
        "semantic": (semantic_score, semantic_detail),
        "experience": _experience_component(profile, job),
        "seniority": _seniority_component(profile, job),
        "location": _location_component(job, criteria),
        "salary": _salary_component(job, criteria),
    }

    configured: dict[str, float] = {
        "skills": weights.skills,
        "semantic": weights.semantic,
        "experience": weights.experience,
        "seniority": weights.seniority,
        "location": weights.location,
        "salary": weights.salary,
    }

    components: dict[str, ComponentScore] = {}
    used: dict[str, float] = {}
    weighted_total = 0.0
    for name, (component_score, detail) in raw_components.items():
        weight = configured[name]
        included = component_score is not None and weight > 0
        components[name] = ComponentScore(
            score=round((component_score or 0.0) * 100, 2),
            weight=weight,
            included=included,
            detail=detail,
        )
        if included and component_score is not None:
            used[name] = weight
            weighted_total += component_score * weight

    weight_sum = sum(used.values())
    total = round((weighted_total / weight_sum) * 100, 2) if weight_sum > 0 else 0.0
    normalized = (
        {name: round(weight / weight_sum, 4) for name, weight in used.items()}
        if weight_sum > 0
        else {}
    )

    breakdown = ScoreBreakdown(
        components=components,
        matched_skills=matched,
        missing_skills=missing,
        weights_used=normalized,
        total=max(0.0, min(100.0, total)),
    )
    explanation = " ".join(
        detail for name, (_, detail) in raw_components.items() if configured[name] > 0
    )
    return ScoredJob(
        job=job,
        score=breakdown.total,
        breakdown=breakdown,
        explanation=explanation,
    )


def _posted_sort_value(posted_at: datetime | None) -> float:
    return posted_at.timestamp() if posted_at is not None else 0.0


def rank_jobs(
    profile: ResumeProfile,
    jobs: list[RawJob],
    criteria: SearchCriteria,
    weights: ScoringWeights,
    min_score: float = 60.0,
    *,
    resume_embedding: Sequence[float] | None = None,
    resume_embedding_model: str | None = None,
    job_embeddings: Mapping[str, Sequence[float]] | None = None,
    embedding_model: str | None = None,
) -> list[ScoredJob]:
    scored: list[ScoredJob] = []
    for job in jobs:
        from_service = job_embeddings is not None and job.dedupe_key in job_embeddings
        job_embedding = (
            job_embeddings[job.dedupe_key]
            if from_service and job_embeddings is not None
            else job.embedding
        )
        job_embedding_model = embedding_model if from_service else job.embedding_model
        item = score_job(
            profile,
            job,
            criteria,
            weights,
            resume_embedding=resume_embedding,
            resume_embedding_model=resume_embedding_model,
            job_embedding=job_embedding,
            job_embedding_model=job_embedding_model,
            embedding_model=embedding_model,
        )
        if item.score >= min_score:
            scored.append(item)

    scored.sort(key=scored_sort_key)
    return scored


def scored_sort_key(item: ScoredJob) -> tuple[float, float, str, str]:
    return (
        -item.score,
        -_posted_sort_value(item.job.posted_at),
        item.job.title.lower(),
        item.job.company.lower(),
    )
