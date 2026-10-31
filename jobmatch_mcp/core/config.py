"""Application settings loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringWeights(BaseModel):
    """Weights for the hybrid scoring pipeline."""

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

    otel_enabled: bool = True
    otel_service_name: str = "jobmatch-mcp"
    otel_exporter: Literal["none", "console", "otlp"] = "none"
    otel_endpoint: str | None = None
    otel_metrics_interval_seconds: float = Field(default=30.0, gt=0.0)

    database_url: str = "postgresql+psycopg://jobmatch:jobmatch@localhost:5432/jobmatch"

    jsearch_api_key: str | None = None
    jsearch_api_host: str = "jsearch.p.rapidapi.com"
    jsearch_base_url: str = "https://jsearch.p.rapidapi.com"
    jsearch_country: str = "in"

    greenhouse_boards: str = ""
    greenhouse_base_url: str = "https://boards-api.greenhouse.io/v1"

    lever_companies: str = ""
    lever_base_url: str = "https://api.lever.co/v0"

    serpapi_api_key: str | None = None
    serpapi_base_url: str = "https://serpapi.com"
    serpapi_country: str = "in"

    http_timeout_seconds: float = Field(default=10.0, gt=0.0)
    source_timeout_seconds: float = Field(default=20.0, gt=0.0)
    search_default_limit: int = Field(default=30, ge=1, le=30)
    search_max_limit: int = Field(default=30, ge=1, le=30)
    min_match_score: float = Field(default=60.0, ge=60.0, le=100.0)
    max_resume_bytes: int = Field(default=5_000_000, ge=1)
    max_job_description_chars: int = Field(default=5_000, ge=100)
    enable_elicitation: bool = True

    transport: Literal["stdio", "http"] = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)
    http_path: str = Field(default="/mcp", pattern=r"^/")
    http_bearer_token: str | None = None
    http_json_response: bool = False
    http_stateless: bool = False

    connector_cache_ttl_seconds: float = Field(default=300.0, ge=0.0)
    connector_cache_max_entries: int = Field(default=256, ge=1)

    jsearch_rate_limit_per_minute: int = Field(default=30, ge=1)
    greenhouse_rate_limit_per_minute: int = Field(default=60, ge=1)
    lever_rate_limit_per_minute: int = Field(default=60, ge=1)
    serpapi_rate_limit_per_minute: int = Field(default=30, ge=1)

    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)

    enable_semantic: bool = True
    embedding_model: str = "nomic-embed-text"
    embedding_base_url: str = "http://localhost:11434"
    embedding_timeout_seconds: float = Field(default=30.0, gt=0.0)
    embedding_max_jobs: int = Field(default=100, ge=1)

    enable_llm_rerank: bool = True
    rerank_top_n: int = Field(default=20, ge=0, le=30)
    rerank_max_adjustment: float = Field(default=10.0, ge=0.0, le=10.0)
    llm_max_consecutive_failures: int = Field(default=3, ge=1)
    llm_circuit_cooldown_seconds: float = Field(default=60.0, gt=0.0)
    llm_max_output_tokens: int = Field(default=1500, ge=1)
    llm_recent_calls_limit: int = Field(default=20, ge=1, le=100)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: float = Field(default=30.0, gt=0.0)
    ollama_cost_per_million_tokens: float = Field(default=0.0, ge=0.0)

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = Field(default=30.0, gt=0.0)
    openai_input_cost_per_million: float = Field(default=0.15, ge=0.0)
    openai_output_cost_per_million: float = Field(default=0.60, ge=0.0)

    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-3-5-haiku-latest"
    anthropic_timeout_seconds: float = Field(default=30.0, gt=0.0)
    anthropic_input_cost_per_million: float = Field(default=0.80, ge=0.0)
    anthropic_output_cost_per_million: float = Field(default=4.00, ge=0.0)

    disable_hosted_fallback: bool = True

    @model_validator(mode="after")
    def _validate_limits(self) -> Settings:
        if self.search_default_limit > self.search_max_limit:
            raise ValueError("search_default_limit must be <= search_max_limit")
        return self

    @property
    def jsearch_configured(self) -> bool:
        return bool(self.jsearch_api_key)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def hosted_fallback_enabled(self) -> bool:
        return not self.disable_hosted_fallback

    @property
    def greenhouse_board_tokens(self) -> list[str]:
        return _split_csv(self.greenhouse_boards)

    @property
    def lever_company_tokens(self) -> list[str]:
        return _split_csv(self.lever_companies)

    @property
    def greenhouse_configured(self) -> bool:
        return bool(self.greenhouse_board_tokens)

    @property
    def lever_configured(self) -> bool:
        return bool(self.lever_company_tokens)

    @property
    def serpapi_configured(self) -> bool:
        return bool(self.serpapi_api_key)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
