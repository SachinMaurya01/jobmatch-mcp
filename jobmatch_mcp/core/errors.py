"""Domain exceptions surfaced by services and tools."""

from __future__ import annotations


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


def describe_error(exc: BaseException) -> str:
    """Return a secret-safe description for logs and tool output."""
    if isinstance(exc, JobMatchError):
        return str(exc)
    return type(exc).__name__
