"""Entry point for the stdio MCP server."""

from __future__ import annotations

import asyncio

from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.core.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    from jobmatch_mcp.server.app import mcp
    from jobmatch_mcp.server.deps import close_dependencies

    try:
        mcp.run(transport="stdio", show_banner=False)
    finally:
        asyncio.run(close_dependencies())


if __name__ == "__main__":
    main()
