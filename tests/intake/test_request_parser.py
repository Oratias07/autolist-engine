"""intake.request_parser: model output is checked, never trusted."""

from __future__ import annotations

import copy
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from PIL import Image

from autolist.core.config import parse_store_config
from autolist.core.models import Attachment, ChannelMessage
from autolist.intake.request_parser import ExtractedRequest, parse_request
from autolist.llm.client import LLMRequest, LLMResult
from tests.core.conftest import VALID_STORE

REQUIRED = ["name", "price", "quantity", "model", "photo"]
TEXT = 'כיסא מזכירה T8103 שחור, 449 ש"ח, מלאי 3'


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (1, 1, 1)).save(buf, format="PNG")
    return buf.getvalue()


def _msg(text: str = TEXT, photos: int = 1) -> ChannelMessage:
    atts = [
        Attachment(filename=f"p{i}.png", mime_type="image/png", size_bytes=10, data=_png())
        for i in range(1, photos + 1)
    ]
    return ChannelMessage(
        channel="email",
        message_id="g1",
        thread_id="t1",
        sender="owner@example.com",
        authenticated=True,
        received_at=datetime(2026, 11, 15, tzinfo=UTC),
        subject=None,
        text=text,
        attachments=atts,
    )


def _q(value: Any = None, quote: str | None = None) -> dict[str, Any]:
    return {"value": value, "quote": quote}


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": _q("כיסא מזכירה T8103 שחור", "כיסא מזכירה T8103 שחור"),
        "price": _q("449", '449 ש"ח'),
        "sale_price": _q(),
        "quantity": _q(3, "מלאי 3"),
        "barcode": _q(),
        "model": _q("T8103", "T8103"),
        "keywords": _q(),
        "category_hint": _q(),
        "item_code": _q(),
        "sale_end": _q(),
        "details": [],
        "lifestyle_requested": False,
        "photo_indexes": [1],
        "printed_text": False,
    }
    base.update(over)
    return base


class FakeLLM:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        parsed = ExtractedRequest.model_validate_json(json.dumps(self.output))
        return LLMResult(
            parsed=parsed, text=None, model="m", input_tokens=1, output_tokens=1, usd=Decimal(0)
        )


def _run(output: dict[str, Any], msg: ChannelMessage | None = None) -> tuple[Any, FakeLLM]:
    llm = FakeLLM(output)
    intake = parse_store_config(copy.deepcopy(VALID_STORE)).intake
    res = parse_request(msg or _msg(), llm=llm, intake=intake, required=REQUIRED, job_id=None)
    return res, llm


def _out(items: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "items": items,
        "products_clearly_separated": True,
        "unassigned_photos": [],
        "questions": [],
    }
    base.update(over)
    return base


def test_complete_request_has_quoted_facts_and_nothing_missing() -> None:
    res, llm = _run(_out([_item()]))
    item = res.items[0]
    assert {f.key: f.value for f in item.facts} == {
        "name": "כיסא מזכירה T8103 שחור",
        "price": "449",
        "quantity": "3",
        "model": "T8103",
    }
    assert all(f.quote in TEXT and f.gmail_id == "g1" for f in item.facts)
    assert item.missing_required == []
    assert item.photo_indexes == [1]
    req = llm.requests[0]
    assert req.model_role == "extract"
    assert len(req.images) == 1
    assert req.variables["attachments_list"] == "1. p1.png"


def test_value_without_exact_quote_is_dropped_and_listed() -> None:
    res, _ = _run(_out([_item(price=_q("399", "399 שקל"))]))
    item = res.items[0]
    assert "price" not in {f.key for f in item.facts}
    assert item.unverified_fields == ["price"]
    assert item.missing_required == ["price"]


def test_missing_required_and_no_photo() -> None:
    res, _ = _run(_out([_item(quantity=_q(), photo_indexes=[])]), _msg(photos=0))
    item = res.items[0]
    assert item.missing_required == ["quantity", "photo"]
    assert item.printed_text is None


def test_photo_indexes_outside_the_accepted_list_are_dropped() -> None:
    res, _ = _run(_out([_item(photo_indexes=[1, 7])], unassigned_photos=[9]))
    assert res.items[0].photo_indexes == [1]
    assert res.unassigned_photos == []


def test_not_clearly_separated_is_passed_through_with_questions() -> None:
    out = _out(
        [_item(), _item(name=_q("כיסא", "כיסא"))],
        products_clearly_separated=False,
        questions=["לאיזה מוצר שייך המחיר 449?"],
    )
    res, _ = _run(out)
    assert res.clearly_separated is False
    assert res.questions == ["לאיזה מוצר שייך המחיר 449?"]


def test_rejected_attachment_is_reported_and_not_sent() -> None:
    msg = _msg()
    bad = Attachment(filename="x.pdf", mime_type="image/png", size_bytes=5, data=b"%PDF-")
    msg = msg.model_copy(update={"attachments": [bad, *msg.attachments]})
    res, llm = _run(_out([_item()]), msg)
    assert res.rejected_attachments == ["x.pdf: not a readable image"]
    assert len(llm.requests[0].images) == 1
    assert llm.requests[0].variables["attachments_list"] == "1. p1.png"


def test_item_code_and_details_with_exact_quotes() -> None:
    text = TEXT + '\nמקט: 1571\nאורך 40 מ"מ, מיועדת לכל סוגי המשטחים'  # noqa: RUF001
    out = _out(
        [
            _item(
                item_code=_q("1571", "מקט: 1571"),
                details=[
                    _q('אורך 40 מ"מ', 'אורך 40 מ"מ'),
                    _q("מיועדת לכל סוגי המשטחים", "מיועדת לכל סוגי המשטחים"),
                    _q("עמיד במים", "עמיד במים"),  # not in the text: dropped, not asked
                    _q('אורך 40 מ"מ', 'אורך 40 מ"מ'),  # duplicate: once
                ],
            )
        ]
    )
    res, _ = _run(out, _msg(text))
    facts = [(f.key, f.value) for f in res.items[0].facts]
    assert ("item_code", "1571") in facts
    assert [v for k, v in facts if k == "detail"] == ['אורך 40 מ"מ', "מיועדת לכל סוגי המשטחים"]
    assert res.items[0].unverified_fields == []


def test_sale_end_and_lifestyle_request_pass_through() -> None:
    text = TEXT + ", מבצע 399 עד 30.11, תעשה גם תמונת אווירה"
    res, _ = _run(
        _out(
            [
                _item(
                    sale_price=_q("399", "מבצע 399"),
                    sale_end=_q("30.11", "עד 30.11"),
                    lifestyle_requested=True,
                )
            ]
        ),
        _msg(text),
    )
    item = res.items[0]
    facts = {f.key: f.value for f in item.facts}
    assert (facts["sale_price"], facts["sale_end"]) == ("399", "30.11")
    assert item.lifestyle_requested


def test_sale_end_without_exact_quote_is_dropped() -> None:
    res, _ = _run(_out([_item(sale_end=_q("2026-11-30", "סוף החודש"))]))
    assert "sale_end" in res.items[0].unverified_fields
    assert all(f.key != "sale_end" for f in res.items[0].facts)
