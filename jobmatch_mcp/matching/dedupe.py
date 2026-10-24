"""Deduplication keys and cross-source merging."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from jobmatch_mcp.schemas import RawJob

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMPANY_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "private",
        "pvt",
        "sa",
    }
)


def _normalize_company(company: str) -> str:
    tokens = _NON_ALNUM.sub(" ", company.lower()).split()
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_dedupe_key(title: str, company: str, location: str | None) -> str:
    raw = f"{title} {_normalize_company(company)} {location or ''}".lower()
    return _NON_ALNUM.sub(" ", raw).strip()[:512]


def _completeness(job: RawJob) -> tuple[int, bool, bool, bool]:
    return (
        len(job.description),
        job.salary_min is not None or job.salary_max is not None,
        job.posted_at is not None,
        job.location is not None,
    )


def _newest(values: list[datetime | None]) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _first_known(values: list[float | None]) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def dedupe_jobs(jobs: Sequence[RawJob]) -> list[RawJob]:
    """Merge postings sharing a normalized title/company/location key."""
    groups: dict[str, list[RawJob]] = {}
    order: list[str] = []
    for job in jobs:
        key = job.dedupe_key or normalize_dedupe_key(job.title, job.company, job.location)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(job)

    merged: list[RawJob] = []
    for key in order:
        group = groups[key]
        representative = max(group, key=_completeness)
        sources = sorted({job.source for job in group} | set(representative.sources))
        merged.append(
            representative.model_copy(
                update={
                    "sources": sources,
                    "salary_min": _first_known([job.salary_min for job in group]),
                    "salary_max": _first_known([job.salary_max for job in group]),
                    "location": representative.location
                    or next((job.location for job in group if job.location), None),
                    "remote": any(job.remote for job in group),
                    "posted_at": representative.posted_at
                    or _newest([job.posted_at for job in group]),
                    "dedupe_key": key,
                }
            )
        )
    return merged
