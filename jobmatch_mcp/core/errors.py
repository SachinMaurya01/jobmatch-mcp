"""Domain exceptions surfaced by services and tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobmatch_mcp.schemas import LLMCallRecord


class JobMatchError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(JobMatchError):
    """Raised when required configuration is missing or invalid."""


class ConnectorError(JobMatchError):
    """Raised when a job source cannot satisfy a request."""


class NotFoundError(JobMatchError):
    """Raised when a requested record does not exist."""


class InvalidInputError(JobMatchError):
    """Raised when user-supplied input fails validation."""


class LLMError(JobMatchError):
    """Base class for LLM routing and provider failures."""


class LLMUnavailableError(LLMError):
    """Raised when no LLM provider can serve a request."""

    def __init__(self, message: str, *, calls: list[LLMCallRecord] | None = None) -> None:
        super().__init__(message)
        self.calls: list[LLMCallRecord] = calls or []


class LLMOutputError(LLMError):
    """Raised when a provider response cannot be parsed or validated."""


class EmbeddingError(JobMatchError):
    """Raised when an embedding request fails."""


def describe_error(exc: BaseException) -> str:
    """Return a secret-safe description for logs and tool output."""
    if isinstance(exc, JobMatchError):
        return str(exc)
    return type(exc).__name__
