FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY jobmatch_mcp ./jobmatch_mcp
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

CMD ["python", "-m", "jobmatch_mcp"]
