FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

FROM base AS runtime
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/demo.py scripts/benchmark.py ./scripts/
RUN mkdir -p /app/.local /app/output && chown -R app:app /app
USER app
EXPOSE 8001
CMD ["uvicorn", "orderflow.main:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]

FROM base AS development
RUN apt-get update && apt-get install -y --no-install-recommends redis-server && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.lock .
RUN pip install --require-hashes -r requirements-dev.lock
COPY . .
RUN pip install --no-deps -e . && mkdir -p /app/.local /app/output && chown -R app:app /app
USER app
CMD ["pytest", "-q"]
