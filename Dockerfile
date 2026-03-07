FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first (cache layer).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --extra mcp

# Copy source and install project.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra mcp

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["markdown-mcp"]
CMD ["serve"]
