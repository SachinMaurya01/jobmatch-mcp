"""Configuration parsing and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobmatch_mcp.core.config import ScoringWeights, Settings


def test_defaults_and_jsearch_flag() -> None:
    settings = Settings(_env_file=None)
    assert settings.search_default_limit == 30
    assert settings.search_max_limit == 30
    assert settings.min_match_score == 60.0
    assert settings.disable_hosted_fallback is True
    assert settings.jsearch_configured is False


def test_jsearch_configured_requires_api_key() -> None:
    assert Settings(_env_file=None, jsearch_api_key="key").jsearch_configured is True
    assert Settings(_env_file=None, jsearch_api_key="").jsearch_configured is False


def test_rejects_default_limit_above_max() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_default_limit=9, search_max_limit=5)


def test_rejects_search_max_limit_above_30() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_max_limit=31)


def test_rejects_minimum_match_score_below_60() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, min_match_score=59)


def test_rejects_all_zero_structured_weights() -> None:
    with pytest.raises(ValidationError):
        ScoringWeights(skills=0, experience=0, seniority=0, location=0, salary=0)
