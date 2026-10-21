"""Deterministic skill extraction."""

from __future__ import annotations

from jobmatch_mcp.matching.skills import extract_skills


def test_extracts_canonical_skills() -> None:
    text = "Built FastAPI services with Python, Postgres, Docker and AWS."
    skills = extract_skills(text)
    assert {"Python", "FastAPI", "PostgreSQL", "Docker", "AWS"} <= set(skills)


def test_distinguishes_java_from_javascript() -> None:
    skills = extract_skills("Java and JavaScript, plus Node.js")
    assert "Java" in skills
    assert "JavaScript" in skills
    assert "Node.js" in skills


def test_handles_special_characters() -> None:
    skills = extract_skills("C++ and C# with gRPC")
    assert {"C++", "C#", "gRPC"} <= set(skills)


def test_does_not_match_bare_go_word() -> None:
    assert "Go" not in extract_skills("We go to market quickly.")
    assert "Go" in extract_skills("Experience with Golang")
