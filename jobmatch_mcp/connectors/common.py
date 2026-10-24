"""Shared normalization helpers for job-source connectors."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.IGNORECASE)

_PERIOD_MULTIPLIERS: dict[str, float] = {
    "HOUR": 2080.0,
    "DAY": 260.0,
    "WEEK": 52.0,
    "MONTH": 12.0,
    "YEAR": 1.0,
}

_LEVER_INTERVAL_MULTIPLIERS: dict[str, float] = {
    "per-hour-wage": 2080.0,
    "per-day-wage": 260.0,
    "per-week-wage": 52.0,
    "per-month-salary": 12.0,
    "per-year-salary": 1.0,
}


def clean_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_epoch(value: Any, *, milliseconds: bool = False) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    seconds = value / 1000.0 if milliseconds else float(value)
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def detect_remote(*parts: str | None) -> bool:
    return bool(_REMOTE_RE.search(" ".join(part for part in parts if part)))


def matches_query(query: str | None, *parts: str | None) -> bool:
    if not query or not query.strip():
        return True
    haystack = " ".join(part for part in parts if part).lower()
    return all(token in haystack for token in query.lower().split())


def matches_location(locations: list[str] | None, location: str | None, remote: bool) -> bool:
    requested = [item.strip() for item in (locations or []) if item.strip()]
    if not requested:
        return True
    job_location = (location or "").lower()
    if any(item.lower() in job_location for item in requested):
        return True
    return remote


def annual_salary(value: Any, period: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    multiplier = _PERIOD_MULTIPLIERS.get(str(period or "YEAR").upper(), 1.0)
    return float(value) * multiplier


def lever_annual_salary(value: Any, interval: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    multiplier = _LEVER_INTERVAL_MULTIPLIERS.get(str(interval or "").lower(), 1.0)
    return float(value) * multiplier
