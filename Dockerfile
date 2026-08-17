# The API, with the slice baked in so a fresh deployment can build its own
# graph. The slice is 9.1 MB of gzipped CSV; the object store it becomes is
# 1.7 GB, so shipping the source and loading on first boot is far cheaper than
# shipping the store.
#
# The UI build stage is skipped gracefully when web/ has no package.json yet,
# so this Dockerfile works before and after the frontend exists.

FROM node:22-alpine AS web
WORKDIR /build
COPY web/ ./web/
RUN if [ -f web/package.json ]; then \
      cd web && npm ci && npm run build; \
    else \
      mkdir -p web/dist; \
    fi

FROM python:3.13-slim
WORKDIR /app

# uv gives us the same resolution as local development.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY api/ ./api/
COPY ingest/ ./ingest/
COPY eval/ ./eval/
COPY data/slice/ ./data/slice/
COPY data/incident/ ./data/incident/
COPY data/fixtures/ ./data/fixtures/
COPY --from=web /build/web/dist ./web/dist

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Railway supplies $PORT. Bind to :: so the service is reachable over both
# stacks -- private networking resolves to IPv6 in older environments.
CMD ["sh", "-c", "uvicorn api.main:app --host :: --port ${PORT:-8000}"]
