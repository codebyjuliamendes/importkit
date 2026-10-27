# syntax=docker/dockerfile:1.7

# ---- builder: resolve and install locked dependencies with uv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
# Dependencies first for layer caching, then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: slim image, non-root, data on a volume ----
FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IMPORTKIT_ENVIRONMENT=production \
    IMPORTKIT_HOST=0.0.0.0 \
    IMPORTKIT_PORT=3205 \
    IMPORTKIT_DATABASE_URL=sqlite:////data/importkit.sqlite \
    IMPORTKIT_STATIC_DIR=/app/public
RUN groupadd --system --gid 10001 importkit \
    && useradd --system --uid 10001 --gid importkit --home-dir /app --shell /usr/sbin/nologin importkit \
    && mkdir -p /data && chown importkit:importkit /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY public ./public
USER importkit
VOLUME ["/data"]
EXPOSE 3205
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('IMPORTKIT_PORT', '3205')}/api/health\", timeout=2)"]
# exec form + sh -c so uvicorn becomes PID 1 and receives SIGTERM for graceful shutdown.
CMD ["sh", "-c", "alembic upgrade head && exec importkit serve"]
