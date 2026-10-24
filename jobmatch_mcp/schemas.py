"""Pydantic domain models and versioned MCP tool outputs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["1.0"] = "1.0"


class Seniority(StrEnum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"


class ResumeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    seniority: Seniority = Seniority.UNKNOWN
    roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)


class RawJob(BaseModel):
    """A normalized job returned by a connector before persistence."""

    model_config = ConfigDict(extra="forbid")

    source: str
    external_id: str
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    salary_min: float | None = None
    salary_max: float | None = None
    description: str
    url: str
    posted_at: datetime | None = None
    dedupe_key: str
    sources: list[str] = Field(default_factory=list)


class SearchCriteria(BaseModel):
    query: str | None = None
    locations: list[str] = Field(default_factory=list)
    remote: bool | None = None
    min_salary: float | None = None
    limit: int = Field(default=30, ge=1, le=30)


class ComponentScore(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    weight: float = Field(ge=0.0)
    included: bool
    detail: str


class ScoreBreakdown(BaseModel):
    components: dict[str, ComponentScore]
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    weights_used: dict[str, float] = Field(default_factory=dict)
    total: float = Field(ge=0.0, le=100.0)


class RankedJob(BaseModel):
    job_id: UUID
    rank: int
    score: float = Field(ge=60.0, le=100.0)
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    salary_min: float | None = None
    salary_max: float | None = None
    source: str
    sources: list[str] = Field(default_factory=list)
    url: str
    explanation: str
    breakdown: ScoreBreakdown


class JobRecord(BaseModel):
    id: UUID
    source: str
    external_id: str
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    salary_min: float | None = None
    salary_max: float | None = None
    description: str
    url: str
    posted_at: datetime | None = None
    fetched_at: datetime
    dedupe_key: str


class SourceStatus(BaseModel):
    name: str
    enabled: bool = True
    status: Literal["ok", "degraded", "unavailable"] = "ok"
    detail: str = ""
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    rate_limit_per_minute: int | None = None
    rate_limit_remaining: int | None = None
    cache_ttl_seconds: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0


class ParseResumeOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    resume_id: UUID
    profile: ResumeProfile


class SearchJobsOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    search_id: UUID
    resume_id: UUID
    count: int
    sources_used: list[str] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    results: list[RankedJob] = Field(default_factory=list)


class JobDetailsOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job: JobRecord


class ListSourcesOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    sources: list[SourceStatus] = Field(default_factory=list)
