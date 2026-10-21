"""Log redaction must mask PII but not scores or years."""

from __future__ import annotations

from jobmatch_mcp.core.redaction import redact, redact_value


def test_redacts_email_and_phone() -> None:
    text = "Contact jane.doe+job@example.com or +91 98765 43210 for details."
    result = redact(text)
    assert "jane.doe" not in result
    assert "98765" not in result
    assert "[redacted-email]" in result
    assert "[redacted-phone]" in result


def test_leaves_scores_and_years_untouched() -> None:
    text = "Score 72.5 out of 100, 5 years of experience, 2018 - 2021."
    assert redact(text) == text


def test_redacts_nested_structures() -> None:
    value = {"email": "a@b.com", "items": ["call +1 415 555 0199"]}
    redacted = redact_value(value)
    assert redacted["email"] == "[redacted-email]"
    assert "555" not in redacted["items"][0]
