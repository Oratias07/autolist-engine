"""Classify an owner reply that is not an exact command (classify_reply v1, DEV 15.11.2026).

The model's output is not trusted:
- there is no approve or cancel intent in the schema, so a model can never approve;
- every fact value must quote the reply text exactly, or it is dropped and asked about.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autolist.core.models import ChannelMessage, JobFact, ReplyClassification, ReplyIntent
from autolist.llm.client import LLMClient, LLMRequest

MAX_OUTPUT_TOKENS = 1500


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quoted[T](_M):
    value: T | None = Field(description="null when the reply does not state it explicitly")
    quote: str | None = Field(description="exact substring of the reply that states it")


class ReplyFacts(_M):
    name: Quoted[str]
    price: Quoted[Decimal]
    sale_price: Quoted[Decimal]
    quantity: Quoted[int]
    model: Quoted[str]
    barcode: Quoted[str]
    keywords: Quoted[list[str]]


class ClassifiedReply(_M):
    intent: Literal["facts", "wording_or_image", "status", "other", "unclear"]
    facts: ReplyFacts
    also_wording_or_image: bool
    question: str = Field(description="one short question in Hebrew, or an empty string")


FIELDS = ("name", "price", "sale_price", "quantity", "model", "barcode", "keywords")


def _text(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def classify_reply(
    message: ChannelMessage, *, llm: LLMClient, job_context: str, job_id: str
) -> ReplyClassification:
    result = llm.complete(
        LLMRequest(
            prompt="classify_reply",
            variables={"reply_text": message.text, "job_context": job_context},
            output_schema=ClassifiedReply,
            model_role="classify",
            job_id=job_id,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    )
    parsed = result.parsed
    if not isinstance(parsed, ClassifiedReply):
        raise TypeError("classify_reply returned no parsed result")
    facts: list[JobFact] = []
    unverified: list[str] = []
    for field in FIELDS:
        q: Quoted[object] = getattr(parsed.facts, field)
        if q.value is None or q.value == []:
            continue
        if not q.quote or q.quote not in message.text:
            unverified.append(field)
            continue
        facts.append(
            JobFact(
                key=field,
                value=_text(q.value),
                quote=q.quote,
                source="reply",
                gmail_id=message.message_id,
            )
        )
    intent = ReplyIntent(parsed.intent)
    if intent == ReplyIntent.FACTS and not facts and not unverified:
        intent = ReplyIntent.UNCLEAR  # "facts" with nothing stated: never act on it
    return ReplyClassification(
        intent=intent,
        facts=facts,
        unverified_fields=unverified,
        also_wording_or_image=parsed.also_wording_or_image,
        question=parsed.question.strip(),
    )
