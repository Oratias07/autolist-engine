"""Owner-stated product facts that have no field of their own (key ``detail``). Pure.

Shared by intake (the first email) and agent_runtime (a reply). Each detail must quote the
message text exactly; an unquoted one is dropped, not asked about (it is optional data).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from autolist.core.models import ChannelMessage, Job, JobFact

MAX_DETAILS = 12  # an owner email states a handful; more is not read
DETAIL = "detail"


class QuotedText(Protocol):
    @property
    def value(self) -> str | None: ...
    @property
    def quote(self) -> str | None: ...


def owner_details(
    details: Sequence[QuotedText], message: ChannelMessage, source: Literal["email", "reply"]
) -> list[JobFact]:
    out: list[JobFact] = []
    seen: set[str] = set()
    for d in details[:MAX_DETAILS]:
        value = (d.value or "").strip()
        if not value or not d.quote or d.quote not in message.text or value in seen:
            continue
        seen.add(value)
        out.append(
            JobFact(
                key=DETAIL,
                value=value,
                quote=d.quote,
                source=source,
                gmail_id=message.message_id,
            )
        )
    return out


MODEL_OR_ITEM_CODE = "model_or_item_code"  # playbook required key: one of the two is enough


def present_keys(fact_keys: set[str], has_photo: bool) -> set[str]:
    """The keys a job has, for the playbook's required list (DEV 15.11.2026: a model or an
    item code is enough)."""
    keys = set(fact_keys)
    if has_photo:
        keys.add("photo")
    if keys & {"model", "item_code"}:
        keys.add(MODEL_OR_ITEM_CODE)
    return keys


def store_model(job: Job) -> str | None:
    """What goes in the store's model field: the model, else the item code (DEV 15.11.2026)."""
    fact = job.fact("model") or job.fact("item_code")
    return fact.value if fact else None


TITLE_OVERRIDE = "title_override"
_QUOTES = "\"'״׳“”‘’ "  # noqa: RUF001


def owner_title(value: str) -> str:
    """A name the owner gave in a reply, used exactly as he wrote it (DEV 22.11.2026): only
    surrounding quotes and spaces are removed, and line breaks become single spaces."""
    return " ".join(value.split()).strip(_QUOTES).strip()


def as_title_override(facts: list[JobFact]) -> list[JobFact]:
    """In a reply to a preview, a name replaces the written title as is (never rewritten)."""
    return [
        f.model_copy(update={"key": TITLE_OVERRIDE, "value": owner_title(f.value)})
        if f.key == "name" and owner_title(f.value)
        else f
        for f in facts
    ]
