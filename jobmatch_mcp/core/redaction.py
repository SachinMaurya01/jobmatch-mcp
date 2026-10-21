"""PII redaction helpers used by logs and error messages (PRD section 8.1)."""

from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Requires at least 8 digits so scores, years, and salaries are not masked.
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{6,}\d)(?!\d)")

REDACTED_EMAIL = "[redacted-email]"
REDACTED_PHONE = "[redacted-phone]"


def _redact_phone_match(match: re.Match[str]) -> str:
    # Require at least 10 digits so year ranges (e.g. "2018 - 2021") survive.
    if sum(character.isdigit() for character in match.group(0)) >= 10:
        return REDACTED_PHONE
    return match.group(0)


def redact(text: str) -> str:
    """Mask email addresses and phone numbers in free-form text."""
    redacted = EMAIL_RE.sub(REDACTED_EMAIL, text)
    return PHONE_RE.sub(_redact_phone_match, redacted)


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside mappings and sequences."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value
