"""A full fake runtime for job-runner tests: fake Gmail, LLM, images, store, mail, queue."""

from __future__ import annotations

import base64
import copy
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from PIL import Image

from autolist.agent_runtime.classifier import ClassifiedReply
from autolist.content.categories import ChosenCategories
from autolist.content.writer import DescriptionOut, TitleOut
from autolist.core.blob_store import InMemoryBlobStore
from autolist.core.config import parse_store_config
from autolist.core.cost_ledger import CostLedger, InMemoryLedgerStore
from autolist.core.job_store import InMemoryJobStore
from autolist.core.models import GeneratedImage, ImageRequest, OutboundMessage, SentMessage
from autolist.intake.request_parser import ExtractedRequest
from autolist.llm.client import LLMRequest, LLMResult
from autolist.orchestrator.runtime import Runtime
from tests.channels.conftest import FakeGmail
from tests.core.conftest import VALID_STORE
from tests.publish.conftest import make_client

T0 = datetime(2026, 11, 15, 10, tzinfo=UTC)
AR = (
    "mx.google.com; dkim=pass header.i=@example.com header.s=s1 header.b=abc; "
    "dmarc=pass (p=NONE) header.from=example.com"
)
SECTIONS: list[dict[str, Any]] = [
    {"id": "headline", "render": "<p><strong>{headline}</strong></p>"},
    {"id": "intro", "sentences": 2},
    {"id": "specs", "heading": "מפרט:"},
    {"id": "benefits", "heading": "יתרונות:"},
]
PLAYBOOK: dict[str, Any] = {
    "listing": {"required": ["name", "price", "quantity", "model_or_item_code", "photo"]},
    "stock": {"subtract": True},
    "categories": {"never_auto": [9], "max_per_product": 2},
    "title": {"pattern": "x"},
    "description": {"sections": SECTIONS},
}
LABELS = {
    "name": "שם המוצר",
    "price": "מחיר",
    "sale_price": "מחיר מבצע",
    "quantity": "כמות במלאי",
    "model": "דגם",
    "barcode": "ברקוד",
    "keywords": "מילות חיפוש",
    "category_hint": "קטגוריה",
    "photo": "תמונה",
    "sku": "sku-label",
    "item_code": "item-code-label",
    "model_or_item_code": "model-or-item-code-label",
    "detail": "detail-label",
    "_none": "אין",
}
STATES = {
    "RECEIVED": "s-received",
    "NEEDS_INFO": "s-needs-info",
    "GENERATING": "s-generating",
    "AWAITING_APPROVAL": "s-awaiting",
    "PUBLISHING": "s-publishing",
    "SAVED_HIDDEN": "s-saved",
    "CANCELLED": "s-cancelled",
    "FAILED": "s-failed",
    "EXPIRED": "s-expired",
    "_product_id": "pid",
}
CATEGORIES = [
    {"category_id": 11, "name": "ריהוט  >  כיסאות", "status": 1, "hidden": 0},
    {"category_id": 12, "name": "ציוד משרדי", "status": 1, "hidden": 0},
    {"category_id": 9, "name": "מבצעים", "status": 1, "hidden": 0},
    {"category_id": 13, "name": "כבויה", "status": 0, "hidden": 0},
]
COMMANDS = {"approve": "מאשר", "cancel": "בטל", "image_1": "תמונה 1", "image_2": "תמונה 2"}


def png(color: str = "red", size: tuple[int, int] = (40, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(out, format="JPEG")
    return out.getvalue()


def _b(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def email(
    text: str,
    *,
    thread: str = "t0",
    photos: int = 1,
    subject: str = "מוצר חדש",
    sender: str = "owner@example.com",
    in_reply_to: str | None = None,
) -> dict[str, Any]:
    headers = [
        {"name": "Authentication-Results", "value": AR},
        {"name": "From", "value": f"Owner <{sender}>"},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{thread}-{len(text)}@example.com>"},
    ]
    if in_reply_to:
        headers.append({"name": "In-Reply-To", "value": in_reply_to})
    parts: list[dict[str, Any]] = [
        {"mimeType": "text/plain", "filename": "", "body": {"data": _b(text.encode())}}
    ]
    for i in range(photos):
        data = png()
        parts.append(
            {
                "mimeType": "image/png",
                "filename": f"p{i + 1}.png",
                "body": {"size": len(data), "data": _b(data)},
            }
        )
    return {
        "threadId": thread,
        "internalDate": "1794730000000",
        "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
    }


def q(value: Any = None, quote: str | None = None) -> dict[str, Any]:
    return {"value": value, "quote": quote}


def item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": q("כיסא משרדי", "כיסא משרדי"),
        "price": q("450", "450"),
        "sale_price": q(),
        "quantity": q(3, "3 יחידות"),
        "barcode": q(),
        "model": q("C1", "C1"),
        "keywords": q(),
        "category_hint": q(),
        "item_code": q(),
        "details": [],
        "photo_indexes": [1],
        "printed_text": False,
    }
    base.update(over)
    return base


EMAIL_TEXT = "כיסא משרדי, דגם C1, 450, 3 יחידות"


def extraction(items: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "items": items,
        "products_clearly_separated": True,
        "unassigned_photos": [],
        "questions": [],
    }
    base.update(over)
    return base


def classified(intent: str, question: str = "", also: bool = False, **facts: Any) -> dict[str, Any]:
    base = {k: q() for k in ("name", "price", "sale_price", "quantity", "model", "barcode")}
    base["keywords"] = q()
    base["item_code"] = q()
    base["category_hint"] = q()
    base["details"] = []
    base.update(facts)
    return {"intent": intent, "facts": base, "also_wording_or_image": also, "question": question}


TITLE = TitleOut(title="כיסא משרדי C1", fact_ids=["F1", "F2"], open_points=[])
DESC = DescriptionOut.model_validate(
    {
        "headline": "כיסא משרדי לעבודה",
        "intro": ["א.", "ב."],
        "specs": [{"label": "דגם", "value": "C1", "fact_ids": ["F2"]}],
        "benefits": [
            {"title": "נוחות", "sentence": "נוח", "fact_ids": ["F1"]},
            {"title": "סדר", "sentence": "מסודר", "fact_ids": ["F1"]},
            {"title": "פשטות", "sentence": "פשוט", "fact_ids": ["F1"]},
        ],
        "open_points": [],
    }
)


class FakeLLM:
    def __init__(self, extract: dict[str, Any], *, title: TitleOut = TITLE) -> None:
        self.extract, self.title = extract, title
        self.classify: dict[str, Any] = classified("unclear")
        self.category: dict[str, Any] = {
            "primary": 11,
            "secondary": None,
            "clear": True,
            "question": "",
        }
        self.prompts: list[str] = []
        self.requests: list[LLMRequest] = []
        self.error: Exception | None = None

    def complete(self, req: LLMRequest) -> LLMResult:
        self.prompts.append(req.prompt)
        self.requests.append(req)
        if self.error and req.prompt != "extract_request":
            raise self.error
        parsed: Any = {
            "extract_request": lambda: ExtractedRequest.model_validate_json(
                json.dumps(self.extract)
            ),
            "write_title": lambda: self.title,
            "write_description": lambda: DESC,
            "classify_reply": lambda: ClassifiedReply.model_validate_json(
                json.dumps(self.classify)
            ),
            "choose_category": lambda: ChosenCategories.model_validate(self.category),
        }[req.prompt]()
        return LLMResult(
            parsed=parsed, text=None, model="m", input_tokens=1, output_tokens=1, usd=Decimal(0)
        )


class FakeImages:
    def __init__(self) -> None:
        self.requests: list[ImageRequest] = []
        self.error: Exception | None = None

    def generate(self, req: ImageRequest) -> GeneratedImage:
        self.requests.append(req)
        if self.error:
            raise self.error
        return GeneratedImage(
            model=req.model,
            role=req.role,
            mime_type="image/jpeg",
            data=jpeg(),
            size_px=(64, 64),
            text_parts=[],
            finish_reason="STOP",
            usd=Decimal("0.05"),
            estimated_cost=False,
        )


class FakeMailer:
    def __init__(self, clock: Clock | None = None) -> None:
        self.sent: list[OutboundMessage] = []
        self.meta: list[tuple[SentMessage, int]] = []  # with the send time in ms
        self.fail_on: str | None = None
        self.clock = clock

    def deliver(self, msg: OutboundMessage) -> SentMessage:
        if msg.template == self.fail_on:
            raise RuntimeError("send failed")
        self.sent.append(msg)
        n = len(self.sent)
        sent = SentMessage(
            gmail_id=f"s{n}",
            thread_id=msg.thread_id or f"jt{n}",
            rfc_message_id=f"<s{n}@example.com>",
        )
        ms = int(self.clock.now.timestamp() * 1000) + n if self.clock else n
        self.meta.append((sent, ms))
        return sent

    def templates(self) -> list[str]:
        return [m.template for m in self.sent]


class FakeQueue:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, path: str, payload: dict[str, Any]) -> str:
        self.tasks.append((path, payload))
        return f"task-{len(self.tasks)}"


@dataclass
class Clock:
    now: datetime = T0

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@dataclass
class Rig:
    extract: dict[str, Any]
    catalog: list[dict[str, Any]] = field(default_factory=list)
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.posted: dict[str, Any] | None = None
        self.store_overrides: dict[str, Any] = {}
        data = copy.deepcopy(VALID_STORE)
        data["istores"]["catalog_page_size"] = 100
        data["istores"]["required_name_languages"] = [3]
        data["images"]["model"] = "image-model-a"
        self.config = parse_store_config(data)
        self.clock = Clock()
        self.llm = FakeLLM(self.extract)
        self.images = FakeImages()
        self.mail = FakeMailer(self.clock)
        self.queue = FakeQueue()
        self.jobs = InMemoryJobStore()
        self.blobs = InMemoryBlobStore()
        self.ledger = CostLedger(
            self.config.costs, self.config.store.timezone, InMemoryLedgerStore()
        )
        self.rt = Runtime(
            config=self.config,
            playbook=PLAYBOOK,
            glossary={"terms": []},
            voice="ענייני",
            labels=LABELS,
            states=STATES,
            commands=COMMANDS,
            jobs=self.jobs,
            gmail=self._gmail,
            mail=self.mail,
            llm=self.llm,
            images=self.images,
            istores=self._istores,
            blobs=self.blobs,
            ledger=self.ledger,
            queue=self.queue,
            fragment=lambda name, _v: f"<{name}>",
            now=lambda: self.clock.now,
        )

    def _gmail(self) -> FakeGmail:
        sent = {
            meta.gmail_id: _as_sent(m, meta, ms, self.config.channels.email.agent_mailbox)
            for m, (meta, ms) in zip(self.mail.sent, self.mail.meta, strict=True)
        }
        return _MessagesById({**self.messages, **sent})

    def _store(self, r: httpx.Request) -> httpx.Response:
        path = r.url.path
        if r.method == "GET" and path == "/categories":
            return httpx.Response(200, json={"response": CATEGORIES})
        if r.method == "GET" and path.count("/") == 3 and path.startswith("/products/"):
            return httpx.Response(200, json={"products": self.catalog})
        if r.method == "POST" and path == "/products":
            self.posted = json.loads(r.content)
            return httpx.Response(200, text='"productID: 970"')
        if r.method == "GET" and path == "/products/970" and self.posted is not None:
            product = {
                **self.posted,
                "image": "https://cdn.istores.co.il/image/upload/clients/1/a.jpg",
                "product_id": 970,
                "category": {
                    str(i): [{"category_id": i}] for i in self.posted.get("product_category", [])
                },
                **self.store_overrides,
            }
            return httpx.Response(200, json={"product": product})
        return httpx.Response(500)

    def _istores(self) -> Any:
        client, _, _, _ = make_client(self._store, self.config)
        return client


def _as_sent(msg: OutboundMessage, meta: SentMessage, ms: int, mailbox: str) -> dict[str, Any]:
    parts = [
        {
            "mimeType": a.mime_type,
            "filename": a.filename,
            "body": {"size": a.size_bytes, "data": _b(a.data or b"")},
        }
        for a in msg.attachments
    ]
    return {
        "id": meta.gmail_id,
        "threadId": meta.thread_id,
        "internalDate": str(ms),
        "rfc": meta.rfc_message_id,
        "labelIds": ["SENT"],
        "payload": {"headers": [{"name": "From", "value": mailbox}], "parts": parts},
    }


class _MessagesById(FakeGmail):
    """``messages().get(id=...)`` returns the stored message with that id."""

    def __init__(self, messages: dict[str, dict[str, Any]]) -> None:
        super().__init__({})
        self._messages = messages

    def get(self, **kw: Any) -> Any:
        from tests.channels.conftest import Call

        return Call(self._messages[kw["id"]], self.calls, "get")

    def list(self, **kw: Any) -> Any:
        from tests.channels.conftest import Call

        rfc = "<" + str(kw["q"]).split("rfc822msgid:")[1].split(" ")[0] + ">"
        found = [{"id": k} for k, m in self._messages.items() if m.get("rfc") == rfc]
        return Call({"messages": found}, self.calls, "list")

    def threads(self) -> _Threads:
        return _Threads(self._messages)


class _Threads:
    """``threads().get(id=...)``: the stored messages of that thread, in insertion order."""

    def __init__(self, messages: dict[str, dict[str, Any]]) -> None:
        self._messages = messages

    def get(self, **kw: Any) -> Any:
        from tests.channels.conftest import Call

        found = [
            {"id": k, "labelIds": m.get("labelIds", ["INBOX"])}
            for k, m in self._messages.items()
            if m.get("threadId") == kw["id"]
        ]
        return Call({"messages": found}, [], "threads.get")
