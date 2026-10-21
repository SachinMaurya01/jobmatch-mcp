"""Resume text extraction and rule-based profile parsing."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from jobmatch_mcp.core.errors import InvalidInputError
from jobmatch_mcp.matching.seniority import infer_seniority
from jobmatch_mcp.matching.skills import extract_skills
from jobmatch_mcp.schemas import ResumeProfile

_SUPPORTED_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}

_EXPLICIT_YEARS = re.compile(
    r"(?<!\d)(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of\s+experience)?",
    re.IGNORECASE,
)
_YEAR_RANGE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:\u2013|\u2014|-|to)\s*((?:19|20)\d{2}|present|current|now)(?!\d)",
    re.IGNORECASE,
)

_ROLE_KEYWORDS = re.compile(
    r"\b(engineer|developer|scientist|architect|manager|analyst|consultant|designer|"
    r"administrator|specialist|researcher|programmer)\b",
    re.IGNORECASE,
)

_LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "Ahmedabad": ("ahmedabad",),
    "Bengaluru": ("bengaluru", "bangalore"),
    "Chennai": ("chennai", "madras"),
    "Delhi": ("delhi", "new delhi"),
    "Dubai": ("dubai",),
    "Gurugram": ("gurugram", "gurgaon"),
    "Hyderabad": ("hyderabad",),
    "India": ("india",),
    "Kolkata": ("kolkata", "calcutta"),
    "London": ("london",),
    "Mumbai": ("mumbai", "bombay"),
    "Noida": ("noida",),
    "Pune": ("pune",),
    "Remote": ("remote", "work from home", "wfh"),
    "Singapore": ("singapore",),
    "Toronto": ("toronto",),
    "United Kingdom": ("united kingdom", "uk"),
    "United States": ("united states", "usa", "u.s."),
}

_EDUCATION_PATTERN = re.compile(
    r"\b(b\.tech|b\.e\.?|m\.tech|m\.e\.?|b\.sc|m\.sc|mba|ph\.?d|doctorate|"
    r"bachelor|master|bca|mca)\b",
    re.IGNORECASE,
)

_MAX_TEXT_LINES = 400


def read_resume_text(path: Path, max_bytes: int) -> str:
    """Read a plain-text, Markdown, or PDF resume from disk."""
    expanded = path.expanduser()
    if not expanded.exists():
        raise InvalidInputError(f"Resume file not found: {expanded}")
    if not expanded.is_file():
        raise InvalidInputError(f"Resume path is not a file: {expanded}")

    size = expanded.stat().st_size
    if size > max_bytes:
        raise InvalidInputError(
            f"Resume file is {size} bytes; maximum allowed is {max_bytes} bytes"
        )

    suffix = expanded.suffix.lower()
    if suffix == ".pdf":
        try:
            reader = PdfReader(str(expanded))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise InvalidInputError(f"Could not read PDF resume: {exc}") from exc
    elif suffix in _SUPPORTED_TEXT_SUFFIXES:
        try:
            text = expanded.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise InvalidInputError(f"Could not read resume file: {exc}") from exc
    else:
        supported = ", ".join(sorted({".pdf", *_SUPPORTED_TEXT_SUFFIXES}))
        raise InvalidInputError(
            f"Unsupported resume format '{suffix or '(none)'}'. Supported: {supported}"
        )

    if not text.strip():
        raise InvalidInputError("Resume contains no extractable text")
    return text


def _extract_years_experience(text: str) -> float | None:
    explicit = [
        float(match.group(1))
        for match in _EXPLICIT_YEARS.finditer(text)
        if 0 < float(match.group(1)) <= 50
    ]

    current_year = date.today().year
    intervals: list[tuple[int, int]] = []
    for start_raw, end_raw in _YEAR_RANGE.findall(text):
        start = int(start_raw)
        end = current_year if end_raw.lower() in {"present", "current", "now"} else int(end_raw)
        if 1980 <= start <= end <= current_year + 1:
            intervals.append((start, end))

    range_years = 0.0
    if intervals:
        merged: list[list[int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        range_years = min(40.0, float(sum(end - start for start, end in merged)))

    candidates = [*explicit]
    if intervals:
        candidates.append(range_years)
    return max(candidates) if candidates else None


def _extract_roles(text: str) -> list[str]:
    roles: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip(" \t-*•|")
        if not cleaned or len(cleaned) > 120 or not _ROLE_KEYWORDS.search(cleaned):
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            roles.append(cleaned)
        if len(roles) >= 5:
            break
    return roles


def _extract_locations(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for canonical, aliases in _LOCATION_ALIASES.items():
        if any(re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered) for alias in aliases):
            found.append(canonical)
    return found[:10]


def _extract_education(text: str) -> list[str]:
    education: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        cleaned = line.strip(" \t-*•|")
        if not cleaned or not _EDUCATION_PATTERN.search(cleaned):
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            education.append(cleaned)
        if len(education) >= 5:
            break
    return education


def parse_resume_text(text: str) -> ResumeProfile:
    """Parse raw resume text into a deterministic structured profile."""
    normalized = "\n".join(text.splitlines()[:_MAX_TEXT_LINES])
    years = _extract_years_experience(normalized)
    return ResumeProfile(
        skills=extract_skills(normalized),
        years_experience=years,
        seniority=infer_seniority(normalized, years),
        roles=_extract_roles(normalized),
        locations=_extract_locations(normalized),
        education=_extract_education(normalized),
    )
