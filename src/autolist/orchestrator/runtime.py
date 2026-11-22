"""What every job step needs, wired once per process (orchestrator only).

``Runtime`` holds the dependencies; the small helpers here only format and send. Wording comes
from the store repo's reply templates; this file has none.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from autolist.channels.templates import render_fragment
from autolist.core.blob_store import BlobStore
from autolist.core.cost_ledger import CostLedger
from autolist.core.job_store import JobStore
from autolist.core.logging import get_logger, redact_text
from autolist.core.models import Job, OutboundMessage, SentMessage, StoreConfig
from autolist.core.sale import sale_spec
from autolist.images.generator import ImageGenerator
from autolist.llm.client import LLMClient
from autolist.orchestrator.tasks_queue import TaskQueue
from autolist.publish.istores_client import IStoresClient

JOB_TASK_PATH = "/tasks/job"


class Mailer(Protocol):
    def deliver(self, msg: OutboundMessage) -> SentMessage: ...


log = get_logger(__name__)


@dataclass
class Runtime:
    config: StoreConfig
    playbook: dict[str, Any]
    glossary: dict[str, Any]
    voice: str
    labels: dict[str, str]
    states: dict[str, str]  # status lines per job state (agent/state_labels.yaml)
    commands: dict[str, str]  # exact reply commands (agent/commands.yaml)
    jobs: JobStore
    gmail: Callable[[], Any]  # Gmail API service of the agent mailbox
    mail: Mailer
    llm: LLMClient
    images: ImageGenerator
    istores: Callable[[], IStoresClient]  # used as ``async with rt.istores() as client``
    blobs: BlobStore
    ledger: CostLedger
    queue: TaskQueue
    fragment: Callable[[str, dict[str, str]], str] = field(default=render_fragment)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    @property
    def owner(self) -> str:
        return self.config.channels.email.owner_address

    @property
    def developer(self) -> str:
        contact: str = self.config.require("agent.developer_contact")
        return contact

    def none_label(self) -> str:
        return self.labels["_none"]

    def today(self) -> date:
        """Today in the store's timezone (the sale's start day)."""
        return self.now().astimezone(ZoneInfo(self.config.store.timezone)).date()


_REPLY_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)


def original_subject(subject: str | None) -> str:
    """The subject without "Re:"/"Fwd:" prefixes: reply templates add their own "Re: "."""
    return _REPLY_PREFIX.sub("", subject or "").strip()


def product_name(job: Job) -> str:
    """The name the owner wrote; the written title once a version exists."""
    if job.versions:
        return job.versions[-1].title
    fact = job.fact("name")
    return fact.value if fact else job.job_id


CATEGORY_NONE = "_category_none"


def category_text(rt: Runtime, names: list[str]) -> str:
    """The preview's category line: the names, or the "none chosen" fragment."""
    return " · ".join(names) if names else rt.fragment(CATEGORY_NONE, {})


SALE_NONE = "_sale_none"
SALE_VALUE = "_sale_value"
LIFESTYLE_NONE = "_lifestyle_none"
LIFESTYLE_ATTACHED = "_lifestyle_attached"


def sale_text(rt: Runtime, job: Job) -> str:
    """The preview's sale line: price and end date (d.m.yyyy), or the "none" fragment."""
    spec = sale_spec(job, rt.today())
    if spec is None:
        return rt.fragment(SALE_NONE, {})
    sale = job.fact("sale_price")
    end = spec.date_end
    return rt.fragment(
        SALE_VALUE,
        {
            "sale_price": sale.value if sale else str(spec.price),
            "sale_end": f"{end.day}.{end.month}.{end.year}",
        },
    )


def lifestyle_text(rt: Runtime, attached: bool) -> str:
    return rt.fragment(LIFESTYLE_ATTACHED if attached else LIFESTYLE_NONE, {})


def numbered(lines: list[str]) -> str:
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def jobs_lines(jobs: list[Job]) -> str:
    return "\n".join(f"{j.job_id} \N{EN DASH} {product_name(j)}" for j in jobs)


def send(
    rt: Runtime, msg: OutboundMessage, job: Job | None = None
) -> tuple[SentMessage, Job | None]:
    """Deliver, and record the sent id (and the job's own thread, if it had none) on the job.

    A job's emails form one thread in the owner's mailbox too (DEV 22.11.2026): each one goes
    in the job's thread, with the thread's fixed subject, answering the newest message."""
    if job is None:
        return rt.mail.deliver(msg), None
    subject = job.thread_subject or one_line(
        rt.fragment(THREAD_SUBJECT, {"job_id": job.job_id, "product_name": product_name(job)})
    )
    msg = msg.model_copy(
        update={
            "thread_id": msg.thread_id or job.thread_id,
            "in_reply_to": msg.in_reply_to or job.last_rfc_message_id,
            "subject": subject,
        }
    )
    sent = rt.mail.deliver(msg)
    update: dict[str, Any] = {
        "sent_messages": [*job.sent_messages, sent.gmail_id],
        "thread_subject": subject,
        "last_rfc_message_id": sent.rfc_message_id,
    }
    if job.thread_id is None:
        update["thread_id"] = sent.thread_id
    job = job.model_copy(update=update)
    rt.jobs.save(job)
    return sent, job


THREAD_SUBJECT = "_thread_subject"


def one_line(text: str) -> str:
    """A header-safe single line: no line breaks, single spaces."""
    return " ".join(text.split())


def reply_subject(subject: str) -> str:
    """The subject of an answer in the owner's own thread: "Re: " once."""
    s = one_line(subject)
    return s if s.lower().startswith("re:") else f"Re: {s}"


def to_owner(
    template: str,
    variables: dict[str, str],
    *,
    to: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        channel="email",
        to=to,
        template=template,
        variables=variables,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )


def alert_developer(rt: Runtime, job_id: str, kind: str, state: str, detail: str) -> None:
    """The detail is an error text: redacted like a log line (registered secrets, tokens).

    Never raises: an alert that cannot be sent is logged as an error with the same detail, and
    the step that called it goes on (live 22.11.2026: a timed-out alert hid the real failure)."""
    try:
        rt.mail.deliver(
            to_owner(
                "developer_alert",
                {
                    "job_id": job_id,
                    "kind": kind,
                    "state": state,
                    "detail": redact_text(detail)[:2000],
                },
                to=rt.developer,
            )
        )
    except Exception as exc:
        log.error(
            "developer alert not sent",
            extra={
                "job_id": job_id,
                "kind": kind,
                "detail": redact_text(detail)[:2000],
                "error": repr(exc)[:300],
            },
        )
