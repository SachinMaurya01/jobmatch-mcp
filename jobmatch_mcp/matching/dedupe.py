"""Deduplication key helpers (cross-source dedupe lands in Phase 2)."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_dedupe_key(title: str, company: str, location: str | None) -> str:
    raw = f"{title} {company} {location or ''}".lower()
    return _NON_ALNUM.sub(" ", raw).strip()[:512]
