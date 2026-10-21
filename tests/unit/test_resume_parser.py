"""Resume text parsing."""

from __future__ import annotations

from datetime import date

from jobmatch_mcp.schemas import Seniority
from jobmatch_mcp.services.resume_parser import parse_resume_text

RESUME = """Jane Doe
jane@example.com | +91 98765 43210

Senior Python Engineer
2018 - 2021 Acme Corp
2021 - Present Beta Ltd

Skills: Python, FastAPI, PostgreSQL, Docker, AWS, Kubernetes
B.Tech in Computer Science
Location: Bengaluru, India
"""


def test_parses_structured_profile() -> None:
    profile = parse_resume_text(RESUME)
    assert {"Python", "FastAPI", "PostgreSQL", "Docker", "AWS", "Kubernetes"} <= set(profile.skills)
    assert profile.years_experience == date.today().year - 2018
    assert profile.seniority is Seniority.SENIOR
    assert any("Senior Python Engineer" in role for role in profile.roles)
    assert "Bengaluru" in profile.locations
    assert "India" in profile.locations
    assert any("B.Tech" in item for item in profile.education)


def test_explicit_years_are_used() -> None:
    profile = parse_resume_text("Software Engineer with 5+ years of experience in Python.")
    assert profile.years_experience == 5
    assert profile.seniority is Seniority.MID


def test_unknown_experience_is_none() -> None:
    profile = parse_resume_text("Recent graduate interested in backend development.")
    assert profile.years_experience is None
    assert profile.seniority is Seniority.JUNIOR


def test_does_not_treat_common_words_as_degrees() -> None:
    profile = parse_resume_text("I will be responsible for the team.")
    assert profile.education == []
