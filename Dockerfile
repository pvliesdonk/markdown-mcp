FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git git-lfs \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install --system

COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (cache layer).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --extra all

# Copy source and install project.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra all

# Create non-root user with configurable UID/GID for bind-mount compatibility.
ARG APP_UID=1000
ARG APP_GID=1000
RUN if [ "$APP_UID" -eq 0 ] || [ "$APP_GID" -eq 0 ]; then \
        echo "ERROR: APP_UID and APP_GID must be non-zero" >&2; exit 1; \
    fi \
    && groupadd -r --gid $APP_GID --non-unique appuser \
    && useradd -r --uid $APP_UID --gid $APP_GID --no-log-init -d /app appuser \
    && mkdir -p /data/vault /data/index /data/embeddings /data/fastembed \
    && chown -R appuser:appuser /app /data
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

VOLUME ["/data/vault", "/data/index", "/data/embeddings", "/data/fastembed"]

ENTRYPOINT ["markdown-vault-mcp"]
CMD ["serve", "--transport", "http"]
