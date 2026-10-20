# Production image for the Observa backend (Railway or any container host).
# Build:  docker build -t observa-backend .
# Run:    docker run --env-file .env -p 8000:8000 observa-backend

# --- Build stage: resolve dependencies with uv into /app/.venv ---------------------------
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so this layer is reused while only the code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# --- Runtime stage: only Python, the virtualenv and the code ------------------------------
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --home-dir /app observa

WORKDIR /app
COPY --from=build --chown=observa:observa /app /app

USER observa

# Railway sets PORT; 8000 is the local fallback.
EXPOSE 8000

# Migrations first, then the API. A single process on purpose: APScheduler runs inside it,
# so more workers or replicas would run every scan and the daily report more than once.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
