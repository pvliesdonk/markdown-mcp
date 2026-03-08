FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cache layer).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --extra mcp

# Copy source and install project.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra mcp

ENV PATH="/app/.venv/bin:$PATH"

# Run as non-root user.
RUN useradd --system --create-home --home-dir /app appuser \
    && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["markdown-mcp"]
CMD ["serve"]
