"""Async engine and session management."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from opentelemetry import trace
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.core.telemetry import record_db_query

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_tracer = trace.get_tracer(__name__)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a session that commits on success and rolls back on error."""
    started = time.perf_counter()
    with _tracer.start_as_current_span("db.session") as span:
        try:
            async with get_session_factory()() as session:
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            record_db_query("session", (time.perf_counter() - started) * 1000, False)
            raise
        else:
            span.set_status(trace.Status(trace.StatusCode.OK))
            record_db_query("session", (time.perf_counter() - started) * 1000, True)
