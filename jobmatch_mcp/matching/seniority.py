"""Seniority inference shared by resume parsing and job scoring."""

from __future__ import annotations

import re

from jobmatch_mcp.schemas import Seniority

_LEAD_PATTERNS = ("principal", "staff engineer", "head of", "director", "vp of", "lead architect")
_SENIOR_PATTERNS = ("senior", "sr.", "sr ", "lead ", "lead\n")
_MID_PATTERNS = ("mid-level", "mid level", "intermediate")
_JUNIOR_PATTERNS = ("junior", "jr.", "jr ", "intern", "graduate", "entry level", "fresher")


def _contains(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def infer_seniority(text: str, years_experience: float | None = None) -> Seniority:
    """Infer seniority from explicit keywords, falling back to experience."""
    lowered = f" {text.lower()} "
    if _contains(lowered, _LEAD_PATTERNS):
        return Seniority.LEAD
    if _contains(lowered, _SENIOR_PATTERNS):
        return Seniority.SENIOR
    if _contains(lowered, _MID_PATTERNS):
        return Seniority.MID
    if _contains(lowered, _JUNIOR_PATTERNS):
        return Seniority.JUNIOR

    if years_experience is not None:
        if years_experience >= 8:
            return Seniority.SENIOR
        if years_experience >= 4:
            return Seniority.MID
        if years_experience > 0:
            return Seniority.JUNIOR
    return Seniority.UNKNOWN


def infer_job_seniority(title: str, description: str = "") -> Seniority:
    """Infer the seniority a posting asks for; title takes precedence."""
    from_title = infer_seniority(title)
    if from_title is not Seniority.UNKNOWN:
        return from_title
    return infer_seniority(description[:400])


SENIORITY_ORDER: dict[Seniority, int] = {
    Seniority.JUNIOR: 1,
    Seniority.MID: 2,
    Seniority.SENIOR: 3,
    Seniority.LEAD: 4,
}


def seniority_distance(left: Seniority, right: Seniority) -> int | None:
    if left is Seniority.UNKNOWN or right is Seniority.UNKNOWN:
        return None
    return abs(SENIORITY_ORDER[left] - SENIORITY_ORDER[right])


_REQUIRED_YEARS_RANGE = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:\u2013|\u2014|-|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)
_REQUIRED_YEARS = re.compile(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)


def extract_required_years(description: str) -> float | None:
    """Extract the minimum experience a posting asks for."""
    range_match = _REQUIRED_YEARS_RANGE.search(description)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        if 0 < low <= high <= 50:
            return low

    candidates = [
        float(match.group(1))
        for match in _REQUIRED_YEARS.finditer(description)
        if 0 < float(match.group(1)) <= 50
    ]
    return max(candidates) if candidates else None
