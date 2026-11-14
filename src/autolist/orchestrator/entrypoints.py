"""HTTP entrypoints of the service (infra/SPEC.md). Wiring only.

Authentication is done by Cloud Run: the service is not public, and only the invoker service
account may call it. Handlers therefore trust the caller and validate only the payload shape.
`/tasks/job` and `/cron/watchdog` are added with the job runner (P1-04).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from autolist.channels.email.inbox import poll_accepted, renew_watch
from autolist.core.logging import get_logger
from autolist.core.models import StoreConfig
from autolist.core.state_store import ChannelStateStore, ProcessedMessages
from autolist.orchestrator.tasks_queue import TaskQueue

log = get_logger(__name__)
JOB_TASK_PATH = "/tasks/job"


@dataclass
class Deps:
    config: StoreConfig
    gmail: Callable[[], Any]  # builds the Gmail API service (reads secrets on first use)
    state: ChannelStateStore
    processed: ProcessedMessages
    queue: TaskQueue
    health: Callable[[], dict[str, Any]]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def create_app(deps: Deps) -> Starlette:
    email = deps.config.channels.email

    async def healthz(_: Request) -> JSONResponse:
        report = deps.health()
        return JSONResponse(report, status_code=200 if report.get("ok") else 503)

    async def gmail_push(request: Request) -> JSONResponse:
        # The push body only says "something changed"; the mailbox history is the source of truth.
        try:
            envelope = await request.json()
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict) or "message" not in envelope:
            return JSONResponse({"error": "not a pub/sub push"}, status_code=400)
        accepted = poll_accepted(deps.gmail(), email, deps.state, deps.processed, deps.now())
        for msg in accepted:
            task = deps.queue.enqueue(
                JOB_TASK_PATH,
                {
                    "event": "email_received",
                    "gmail_id": msg.message_id,
                    "thread_id": msg.thread_id,
                    "rfc_message_id": msg.rfc_message_id,
                },
            )
            deps.processed.set_outcome(msg.message_id, "queued", task=task)
        return JSONResponse({"accepted": len(accepted)})

    async def cron_renew_watch(_: Request) -> JSONResponse:
        topic = f"projects/{deps.config.gcp.project_id}/topics/{email.pubsub_topic}"
        expires_ms = renew_watch(deps.gmail(), topic, deps.state)
        expires = datetime.fromtimestamp(expires_ms / 1000, tz=UTC).isoformat()
        log.info("gmail watch renewed", extra={"expires": expires})
        return JSONResponse({"watch_expires": expires})

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/events/gmail", gmail_push, methods=["POST"]),
            Route("/cron/renew-watch", cron_renew_watch, methods=["POST"]),
        ]
    )
