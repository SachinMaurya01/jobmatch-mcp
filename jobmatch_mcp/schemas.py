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
    embedding: list[float] | None = None
    embedding_model: str | None = None


class SearchCriteria(BaseModel):
    query: str | None = None
    locations: list[str] = Field(default_factory=list)
    remote: bool | None = None
    min_salary: float | None = None
    limit: int = Field(default=30, ge=1, le=30)


class SearchPreferences(BaseModel):
    """Optional details elicited from the user before a search."""

    model_config = ConfigDict(extra="forbid")

    locations: str = Field(
        default="",
        description="Comma-separated locations, for example 'Bengaluru, Remote'.",
    )
    remote: Literal["any", "remote", "onsite"] = Field(
        default="any",
        description="Choose remote-only, on-site-only, or no preference.",
    )
    min_salary: float = Field(
        default=0.0,
        ge=0.0,
        description="Minimum annual salary floor; 0 means no floor.",
    )


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
    pre_rerank_total: float | None = Field(default=None, ge=0.0, le=100.0)
    llm_adjustment: float | None = Field(default=None, ge=-10.0, le=10.0)
    llm_provider: str | None = None
    llm_model: str | None = None
    seniority_fit: str | None = None
    suggestions: list[str] = Field(default_factory=list)


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
    warnings: list[str] = Field(default_factory=list)


class ResumeResourceOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    resume_id: UUID
    profile: ResumeProfile
    created_at: datetime


class SearchJobsOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    search_id: UUID
    resume_id: UUID
    count: int
    sources_used: list[str] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    results: list[RankedJob] = Field(default_factory=list)


class SavedSearchOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    search_id: UUID
    resume_id: UUID
    criteria: SearchCriteria
    created_at: datetime
    result_count: int = Field(ge=0)
    results: list[RankedJob] = Field(default_factory=list)


class JobDetailsOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job: JobRecord


class ListSourcesOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    sources: list[SourceStatus] = Field(default_factory=list)


class ExplainMatchOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job_id: UUID
    search_id: UUID
    score: float = Field(ge=0.0, le=100.0)
    rank: int = Field(ge=1)
    explanation: str
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    seniority_fit: str = ""
    suggestions: list[str] = Field(default_factory=list)
    breakdown: ScoreBreakdown
    created_at: datetime


class LLMCallRecord(BaseModel):
    id: UUID | None = None
    provider: str
    model: str
    latency_ms: float = Field(ge=0.0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_estimate: float = Field(ge=0.0)
    status: Literal["success", "error", "skipped"]
    fallback_reason: str | None = None
    created_at: datetime | None = None


class ProviderStatus(BaseModel):
    name: str
    role: Literal["primary", "fallback"]
    configured: bool
    available: bool
    circuit_state: Literal["closed", "open", "half_open"]
    detail: str = ""
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    last_latency_ms: float | None = None
    recent_calls: int = 0
    recent_cost_estimate: float = 0.0


class ProviderStatusOutput(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    providers: list[ProviderStatus] = Field(default_factory=list)
    recent_calls: list[LLMCallRecord] = Field(default_factory=list)
    total_cost_estimate: float = Field(default=0.0, ge=0.0)


class RerankDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    adjustment: float
    explanation: str = Field(min_length=1, max_length=600)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    seniority_fit: str = Field(default="", max_length=300)
    suggestions: list[str] = Field(default_factory=list)


class RerankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[RerankDecision] = Field(default_factory=list)
