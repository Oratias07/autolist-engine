"""Owner email -> products with quoted facts (extract_request v2, fast-track design).

The model reads the text and sees the photos. Its output is not trusted:
- every value must quote the email text exactly, or the value is dropped and asked about;
- photo indexes outside the accepted list are dropped;
- required details come from the playbook; a missing one is listed, never filled.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from autolist.core.models import (
    ChannelMessage,
    IntakeConfig,
    JobFact,
    ParsedRequest,
    ProductRequest,
)
from autolist.intake.attachment_guard import guard
from autolist.llm.client import LLMClient, LLMRequest

MAX_OUTPUT_TOKENS = 3000


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quoted[T](_M):
    value: T | None = Field(description="null when the email text does not state it explicitly")
    quote: str | None = Field(description="exact substring of the email text that states it")


class ExtractedItem(_M):
    name: Quoted[str]
    price: Quoted[Decimal]
    sale_price: Quoted[Decimal]
    quantity: Quoted[int]
    barcode: Quoted[str]
    model: Quoted[str]
    keywords: Quoted[list[str]]
    category_hint: Quoted[str]
    photo_indexes: list[int] = Field(description="1-based positions in the attachments list")
    printed_text: bool | None


class ExtractedRequest(_M):
    items: list[ExtractedItem]
    products_clearly_separated: bool
    unassigned_photos: list[int]
    questions: list[str] = Field(description="questions for the owner, in Hebrew")


_FIELDS = (
    "name",
    "price",
    "sale_price",
    "quantity",
    "barcode",
    "model",
    "keywords",
    "category_hint",
)


def _value_text(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def parse_request(
    message: ChannelMessage,
    *,
    llm: LLMClient,
    intake: IntakeConfig,
    required: list[str],
    job_id: str | None,
) -> ParsedRequest:
    guarded = guard(message.attachments, intake)
    listing = "\n".join(f"{i}. {img.name}" for i, img in enumerate(guarded.accepted, 1))
    result = llm.complete(
        LLMRequest(
            prompt="extract_request",
            variables={"email_text": message.text, "attachments_list": listing or "(none)"},
            output_schema=ExtractedRequest,
            model_role="extract",
            job_id=job_id,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            images=guarded.accepted,
        )
    )
    parsed = result.parsed
    if not isinstance(parsed, ExtractedRequest):
        raise TypeError("extract_request returned no parsed result")

    valid_photos = set(range(1, len(guarded.accepted) + 1))
    questions = list(parsed.questions)
    items: list[ProductRequest] = []
    for index, item in enumerate(parsed.items):
        facts: list[JobFact] = []
        unverified: list[str] = []
        for field in _FIELDS:
            q: Quoted[object] = getattr(item, field)
            if q.value is None or q.value == []:
                continue
            if not q.quote or q.quote not in message.text:
                unverified.append(field)  # the reply template asks about it; never used
                continue
            facts.append(
                JobFact(
                    key=field,
                    value=_value_text(q.value),
                    quote=q.quote,
                    source="email",
                    gmail_id=message.message_id,
                )
            )
        photos = sorted({p for p in item.photo_indexes if p in valid_photos})
        have = {f.key for f in facts} | ({"photo"} if photos else set())
        items.append(
            ProductRequest(
                index=index,
                facts=facts,
                photo_indexes=photos,
                printed_text=item.printed_text if photos else None,
                missing_required=[r for r in required if r not in have],
                unverified_fields=unverified,
            )
        )
    return ParsedRequest(
        items=items,
        clearly_separated=parsed.products_clearly_separated,
        unassigned_photos=sorted({p for p in parsed.unassigned_photos if p in valid_photos}),
        questions=questions,
        rejected_attachments=guarded.rejected,
        accepted_images=guarded.accepted,
    )
