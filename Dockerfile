# Cloud Run image of the autolist service (infra/SPEC.md).
# Build context (made by the store repo's deploy workflow):
#   engine/  this repository at a pinned commit
#   store/   only store.yaml, playbook/, agent/ and docs/voice.md (no fixtures, no live runs)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app

COPY engine/pyproject.toml engine/uv.lock engine/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY engine/src ./src
RUN uv sync --frozen --no-dev

COPY store/ /app/store/
ENV AUTOLIST_STORE_DIR=/app/store PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 autolist && chown -R autolist /app/store
USER autolist
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn autolist.orchestrator.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
