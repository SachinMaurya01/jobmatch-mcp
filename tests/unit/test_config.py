"""Configuration parsing and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobmatch_mcp.core.config import ScoringWeights, Settings


def test_defaults_and_adzuna_flag() -> None:
    settings = Settings(_env_file=None)
    assert settings.search_default_limit == 10
    assert settings.search_max_limit == 50
    assert settings.disable_hosted_fallback is True
    assert settings.adzuna_configured is False


def test_adzuna_configured_requires_both_values() -> None:
    assert Settings(_env_file=None, adzuna_app_id="id").adzuna_configured is False
    assert (
        Settings(_env_file=None, adzuna_app_id="id", adzuna_app_key="key").adzuna_configured is True
    )


def test_rejects_default_limit_above_max() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_default_limit=9, search_max_limit=5)


def test_rejects_all_zero_structured_weights() -> None:
    with pytest.raises(ValidationError):
        ScoringWeights(skills=0, experience=0, seniority=0, location=0, salary=0)
