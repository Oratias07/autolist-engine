"""Cloud Run entrypoint: builds the real dependencies and the app (wiring only).

Run as ``uvicorn autolist.orchestrator.main:app``. Every client-specific value comes from the
store repo copied into the image (``AUTOLIST_STORE_DIR``) and from Secret Manager by name.
The only environment value is ``AUTOLIST_SERVICE_URL`` (known after the first deploy; the deploy
workflow sets it). Secrets and clients are built on first use, so a missing secret fails the
request that needs it, and ``/health`` reports it, instead of a crash loop at start.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from functools import cache
from typing import Any

from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.logging import configure_logging, get_logger
from autolist.core.models import StoreConfig
from autolist.core.state_store import ChannelStateStore, ProcessedMessages
from autolist.core.text_status import ALLOW_DRAFTS_ENV, drafts_allowed
from autolist.orchestrator.entrypoints import Deps, create_app
from autolist.orchestrator.runtime import Runtime
from autolist.orchestrator.tasks_queue import CloudTasksQueue, QueueTarget

log = get_logger(__name__)
SERVICE_URL_ENV = "AUTOLIST_SERVICE_URL"
# every store-repo text the service uses: /health loads each one, so a refused text (draft,
# missing) fails the deploy check instead of the first owner email that needs it
PROMPTS_IN_USE = (
    "extract_request",
    "write_title",
    "write_description",
    "classify_reply",
    "resolve_cancel",
    "choose_category",
    "image_main",
    "image_lifestyle",
    "system_json_output",
    "system_json_repair",
)
REPLIES_IN_USE = (
    "_category_none",
    "cancel_which",
    "cancelled_list",
    "_thread_subject",
    "_lifestyle_attached",
    "_lifestyle_none",
    "_sale_none",
    "_sale_value",
    "lifestyle_unavailable",
    "sale_price_not_below",
    "approve_old_version",
    "approve_reply_to_preview",
    "approve_with_text",
    "budget_cap_reached",
    "cancelled",
    "change_not_supported",
    "clarify_products",
    "cost_alert",
    "developer_alert",
    "duplicate_found",
    "expired",
    "failed",
    "failed_budget",
    "image_2_unavailable",
    "image_note_fallback",
    "image_note_generated",
    "image_note_owner_choice",
    "image_note_printed_text",
    "needs_info",
    "no_product_found",
    "out_of_scope",
    "ready_for_approval",
    "received",
    "reply_in_product_thread",
    "reply_unclear",
    "saved_hidden",
    "status_reply",
    "too_many_products",
)


def text_problems() -> list[str]:
    """Load every text in use; a refused or broken one is a health problem."""
    from autolist.channels.templates import load_reply, load_signature
    from autolist.llm.prompt_loader import load_prompt

    problems: list[str] = []
    for name in PROMPTS_IN_USE:
        try:
            load_prompt(name)
        except Exception as exc:
            problems.append(f"prompt {name}: {type(exc).__name__}")
    try:
        load_signature()
    except Exception as exc:
        problems.append(f"reply _signature: {type(exc).__name__}")
    for name in REPLIES_IN_USE:
        try:
            load_reply(name)
        except Exception as exc:
            problems.append(f"reply {name}: {type(exc).__name__}")
    return problems


TASK_DEADLINE_MARGIN_S = 60  # the task waits a bit longer than the step limit


@cache
def config() -> StoreConfig:
    return load_store_config()


@cache
def firestore() -> Any:  # pragma: no cover - live only
    from google.cloud import firestore as fs

    return fs.Client(project=config().gcp.project_id)


_THREAD = threading.local()


def gmail() -> Any:  # pragma: no cover - live only
    """One Gmail client per thread: httplib2 is not thread-safe, and the push handler and the
    job steps run in parallel threads (suspected cause of the 22.11 Gmail timeouts)."""
    service = getattr(_THREAD, "gmail", None)
    if service is None:
        from autolist.channels.email.gmail_client import build_service

        cfg = config()
        service = build_service(
            get_secret(cfg, "gmail_oauth_client"),
            get_secret(cfg, "gmail_refresh_token"),
            cfg.require("channels.email.api_timeout_s"),  # the service never uses the default
        )
        _THREAD.gmail = service
    return service


@cache
def queue() -> CloudTasksQueue:  # pragma: no cover - live only
    from google.cloud import tasks_v2

    cfg = config()
    url = os.environ.get(SERVICE_URL_ENV, "")
    timeout: int = cfg.require("agent.job_step_timeout_s")
    return CloudTasksQueue(
        tasks_v2.CloudTasksClient(),
        QueueTarget(
            project=cfg.gcp.project_id,
            region=cfg.require("gcp.region"),
            queue=cfg.require("gcp.tasks_queue"),
            service_url=url,
            invoker_email=cfg.require("gcp.invoker_service_account"),
            dispatch_deadline_s=timeout + TASK_DEADLINE_MARGIN_S,
        ),
    )


class _LazyQueue:
    """The real queue is built on the first enqueue: the service URL is unset before the first
    deploy finishes, and the app must still start (``/health``)."""

    def enqueue(self, path: str, payload: dict[str, Any]) -> str:  # pragma: no cover
        return queue().enqueue(path, payload)


@cache
def runtime() -> Runtime:  # pragma: no cover - live only
    from autolist.channels.email.mail_sender import GmailSender
    from autolist.core.blob_store import GcsBlobStore
    from autolist.core.cost_ledger import CostLedger
    from autolist.core.firestore_ledger import FirestoreLedgerStore
    from autolist.core.job_store import FirestoreJobStore
    from autolist.core.playbook import (
        load_commands,
        load_field_labels,
        load_glossary,
        load_playbook,
        load_state_labels,
        load_voice_principles,
    )
    from autolist.images.generator import GeminiImageGenerator
    from autolist.llm.client import AnthropicClient
    from autolist.publish.firestore_registry import FirestoreRegistry
    from autolist.publish.istores_client import IStoresClient

    cfg = config()
    db = firestore()
    ledger = CostLedger(cfg.costs, cfg.store.timezone, FirestoreLedgerStore(db, cfg.store.timezone))
    registry = FirestoreRegistry(db)
    istores_token = get_secret(cfg, "istores_token")
    developer: str = cfg.require("agent.developer_contact")
    return Runtime(
        config=cfg,
        playbook=load_playbook(),
        glossary=load_glossary(),
        voice=load_voice_principles(),
        labels=load_field_labels(),
        states=load_state_labels(),
        commands=load_commands(),
        jobs=FirestoreJobStore(db),
        gmail=gmail,
        mail=GmailSender(gmail(), cfg.channels.email, extra_recipients=frozenset({developer})),
        llm=AnthropicClient(cfg, get_secret(cfg, "anthropic_api_key"), ledger),
        images=GeminiImageGenerator(cfg, get_secret(cfg, "gemini_api_key"), ledger),
        istores=lambda: IStoresClient(cfg, istores_token, registry),
        blobs=GcsBlobStore(cfg),
        ledger=ledger,
        queue=_LazyQueue(),
    )


def health() -> dict[str, Any]:  # pragma: no cover - live only
    """Config, store files, Firestore and the Gmail token. The watch is reported, not required
    (it is created in setup part 2, after the first deploy)."""
    from autolist.channels.email.health import check

    problems: list[str] = []
    report: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat()}
    try:
        cfg = config()
        report["stage"] = cfg.require("deploy.stage")
        report["store_dir"] = str(store_dir())
        state = ChannelStateStore(firestore(), "email")
        status = check(gmail(), cfg.channels.email, state.watch_expires_ms())
        report["mailbox"] = status.details
        report["mailbox_problems"] = status.problems
        problems += [p for p in status.problems if "watch" not in p]
        problems += text_problems()
    except Exception as exc:
        problems.append(type(exc).__name__)
    report["problems"] = problems
    report["ok"] = not problems
    return report


def build_app() -> Any:  # pragma: no cover - live only
    configure_logging()
    if drafts_allowed():
        raise RuntimeError(f"{ALLOW_DRAFTS_ENV} is set: the service never uses draft texts")
    db = firestore()
    return create_app(
        Deps(
            config=config(),
            gmail=gmail,
            state=ChannelStateStore(db, "email"),
            processed=ProcessedMessages(db),
            queue=_LazyQueue(),
            health=health,
            runtime=runtime,
        )
    )


def __getattr__(name: str) -> Any:  # pragma: no cover - live only
    # ``uvicorn autolist.orchestrator.main:app`` builds the app on import of the attribute
    if name == "app":
        return build_app()
    raise AttributeError(name)
