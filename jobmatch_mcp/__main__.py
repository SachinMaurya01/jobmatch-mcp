"""Entry point for the stdio and Streamable HTTP MCP server."""

from __future__ import annotations

import argparse
import asyncio

from jobmatch_mcp.core.config import get_settings
from jobmatch_mcp.core.errors import ConfigurationError
from jobmatch_mcp.core.logging import configure_logging
from jobmatch_mcp.core.telemetry import setup_telemetry, shutdown_telemetry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the JobMatch MCP server.")
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--path", default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    setup_telemetry(settings)
    transport = args.transport or settings.transport

    from jobmatch_mcp.server.app import mcp
    from jobmatch_mcp.server.deps import close_dependencies

    try:
        if transport == "http":
            if not (settings.http_bearer_token or "").strip():
                raise ConfigurationError(
                    "HTTP transport requires JOBMATCH_HTTP_BEARER_TOKEN to be set."
                )
            mcp.run(
                transport="http",
                host=args.host if args.host is not None else settings.http_host,
                port=args.port if args.port is not None else settings.http_port,
                path=args.path if args.path is not None else settings.http_path,
                show_banner=False,
                json_response=settings.http_json_response,
                stateless_http=settings.http_stateless,
            )
        else:
            mcp.run(transport="stdio", show_banner=False)
    finally:
        try:
            asyncio.run(close_dependencies())
        finally:
            shutdown_telemetry()


if __name__ == "__main__":
    main()
