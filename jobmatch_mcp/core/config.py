"""Application settings loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringWeights(BaseModel):
    """Initial structured-scoring weights from PRD section 4.2.

    Semantic similarity is part of the hybrid pipeline (Phase 3). It is kept
    here so the configured weights remain stable when Phase 3 lands.
    """

    skills: float = Field(default=0.40, ge=0.0)
    semantic: float = Field(default=0.25, ge=0.0)
    experience: float = Field(default=0.15, ge=0.0)
    seniority: float = Field(default=0.10, ge=0.0)
    location: float = Field(default=0.05, ge=0.0)
    salary: float = Field(default=0.05, ge=0.0)

    @model_validator(mode="after")
    def _positive_total(self) -> ScoringWeights:
        if self.skills + self.experience + self.seniority + self.location + self.salary <= 0:
            raise ValueError("at least one structured scoring weight must be positive")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBMATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "prod"] = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://jobmatch:jobmatch@localhost:5432/jobmatch"

    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    adzuna_country: str = "in"
    adzuna_base_url: str = "https://api.adzuna.com/v1/api/jobs"

    http_timeout_seconds: float = Field(default=10.0, gt=0.0)
    search_default_limit: int = Field(default=10, ge=1, le=50)
    search_max_limit: int = Field(default=50, ge=1, le=100)
    max_resume_bytes: int = Field(default=5_000_000, ge=1)
    max_job_description_chars: int = Field(default=5_000, ge=100)

    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)

    disable_hosted_fallback: bool = True

    @model_validator(mode="after")
    def _validate_limits(self) -> Settings:
        if self.search_default_limit > self.search_max_limit:
            raise ValueError("search_default_limit must be <= search_max_limit")
        return self

    @property
    def adzuna_configured(self) -> bool:
        return bool(self.adzuna_app_id and self.adzuna_app_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
