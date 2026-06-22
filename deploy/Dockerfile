# Shared image for worker / api / dashboard. Uses uv for fast, reproducible installs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# App code
COPY pipeline ./pipeline
COPY db ./db
COPY api ./api
COPY scheduler ./scheduler
COPY dashboard ./dashboard
COPY data ./data
COPY alembic.ini ./
COPY main.py ./

# Playwright chromium for the CloakBrowser tier (optional at runtime).
RUN uv run playwright install --with-deps chromium || true

EXPOSE 8000 8501

CMD ["uv", "run", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
