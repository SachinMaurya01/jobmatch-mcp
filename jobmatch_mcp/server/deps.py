"""Process-wide dependencies shared by MCP tools."""

from __future__ import annotations

from functools import lru_cache

from jobmatch_mcp.connectors.registry import ConnectorRegistry
from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.db.session import dispose_engine


@lru_cache(maxsize=1)
def get_registry() -> ConnectorRegistry:
    return ConnectorRegistry.from_settings(get_settings())


async def close_dependencies() -> None:
    registry = get_registry()
    await registry.close()
    get_registry.cache_clear()
    await dispose_engine()
