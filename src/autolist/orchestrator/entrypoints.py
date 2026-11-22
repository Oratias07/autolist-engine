"""HTTP entrypoints of the service (infra/SPEC.md). Wiring only.

Authentication is done by Cloud Run: the service is not public, and only the invoker service
account may call it. Handlers therefore trust the caller and validate only the payload shape.
`/tasks/job` runs one job step (``job_runner.run_task``); `/cron/watchdog` runs the daily
watchdog. A step error is logged and answered with 500; Cloud Tasks does not retry (max 1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from autolist.channels.email.inbox import poll_accepted, renew_watch
from autolist.core.logging import get_logger
from autolist.core.models import StoreConfig
from autolist.core.state_store import ChannelStateStore, ProcessedMessages
from autolist.orchestrator.job_runner import run_task
from autolist.orchestrator.runtime import JOB_TASK_PATH, Runtime
from autolist.orchestrator.tasks_queue import TaskQueue
from autolist.orchestrator.watchdog import run_watchdog

log = get_logger(__name__)


@dataclass
class Deps:
    config: StoreConfig
    gmail: Callable[[], Any]  # builds the Gmail API service (reads secrets on first use)
    state: ChannelStateStore
    processed: ProcessedMessages
    queue: TaskQueue
    health: Callable[[], dict[str, Any]]
    runtime: Callable[[], Runtime]  # built on first use (reads secrets)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def create_app(deps: Deps) -> Starlette:
    email = deps.config.channels.email

    async def healthz(_: Request) -> JSONResponse:
        report = await run_in_threadpool(deps.health)
        return JSONResponse(report, status_code=200 if report.get("ok") else 503)

    async def gmail_push(request: Request) -> JSONResponse:
        # The push body only says "something changed"; the mailbox history is the source of truth.
        try:
            envelope = await request.json()
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict) or "message" not in envelope:
            return JSONResponse({"error": "not a pub/sub push"}, status_code=400)
        # blocking Gmail and Firestore calls run off the event loop, so a long job step in
        # another request never delays the push acknowledgement
        accepted, latest = await run_in_threadpool(
            poll_accepted, deps.gmail(), email, deps.state, deps.processed, deps.now()
        )
        queued = 0
        try:
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
                queued += 1
        except Exception:
            # not queued: released, and the cursor stays, so Pub/Sub's retry handles them
            for msg in accepted[queued:]:
                deps.processed.release(msg.message_id)
            raise
        if latest is not None:
            deps.state.set_history_id(latest)
        return JSONResponse({"accepted": len(accepted)})

    async def cron_renew_watch(_: Request) -> JSONResponse:
        topic = f"projects/{deps.config.gcp.project_id}/topics/{email.pubsub_topic}"
        expires_ms = renew_watch(deps.gmail(), topic, deps.state)
        expires = datetime.fromtimestamp(expires_ms / 1000, tz=UTC).isoformat()
        log.info("gmail watch renewed", extra={"expires": expires})
        return JSONResponse({"watch_expires": expires})

    async def task_job(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
            return JSONResponse({"error": "not a job task"}, status_code=400)
        try:
            # a job step makes blocking calls for minutes: its own thread and event loop
            result = await run_in_threadpool(lambda: asyncio.run(run_task(deps.runtime(), payload)))
        except Exception:
            log.exception("job task failed", extra={"event": payload.get("event")})
            return JSONResponse({"error": "step failed"}, status_code=500)
        return JSONResponse(result)

    async def cron_watchdog(_: Request) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(run_watchdog, deps.runtime()))

    return Starlette(
        routes=[
            Route("/health", healthz, methods=["GET"]),
            Route("/events/gmail", gmail_push, methods=["POST"]),
            Route("/cron/renew-watch", cron_renew_watch, methods=["POST"]),
            Route(JOB_TASK_PATH, task_job, methods=["POST"]),
            Route("/cron/watchdog", cron_watchdog, methods=["POST"]),
        ]
    )
