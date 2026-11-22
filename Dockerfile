# Cloud Run image of the autolist service (infra/SPEC.md).
# Build context (made by the store repo's deploy workflow):
#   engine/  this repository at a pinned commit
#   store/   only store.yaml, playbook/, agent/ and docs/voice.md (no fixtures, no live runs)
# base images pinned by digest (security review 22.11.2026, L8); a tag can be moved, a digest cannot
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
WORKDIR /app

COPY engine/pyproject.toml engine/uv.lock engine/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY engine/src ./src
RUN uv sync --frozen --no-dev

# the store files and the code are root's and read-only: the service user cannot change its
# own rules, prompts or code at runtime (L8). The service writes only to Firestore.
COPY store/ /app/store/
ENV AUTOLIST_STORE_DIR=/app/store PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 autolist \
    && chown -R root:root /app \
    && chmod -R a-w /app
USER autolist
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn autolist.orchestrator.main:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
