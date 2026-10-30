from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from autolist.core.errors import MissingProvenance
from autolist.core.models import Provenance, SourcedValue
from autolist.core.provenance import find_unsourced, require_sources, sourced, unknown

PROV = Provenance(
    source="client_email",
    ref="<msg-1@example.com>",
    confidence="certain",
    observed_at=datetime(2026, 10, 30, tzinfo=UTC),
)


class Stock(BaseModel):
    quantity: SourcedValue[int]


class Draft(BaseModel):
    name: SourcedValue[str]
    price: SourcedValue[float]
    stock: Stock
    tags: list[SourcedValue[str]]
    extra: dict[str, SourcedValue[str]]


def _draft(**over: object) -> Draft:
    base: dict[str, object] = {
        "name": sourced("Chair", PROV),
        "price": unknown(),
        "stock": Stock(quantity=sourced(10, PROV)),
        "tags": [sourced("office", PROV)],
        "extra": {"color": sourced("black", PROV)},
    }
    base.update(over)
    return Draft.model_validate(base)


def test_none_passes_and_sourced_passes() -> None:
    require_sources(_draft())


def test_nested_value_without_source_is_flagged() -> None:
    draft = _draft(
        stock=Stock(quantity=SourcedValue[int](value=12, provenance=[])),
        tags=[sourced("a", PROV), SourcedValue[str](value="b")],
        extra={"color": SourcedValue[str](value="red")},
    )
    with pytest.raises(MissingProvenance) as exc:
        require_sources(draft)
    assert exc.value.paths == ["stock.quantity", "tags[1]", "extra.color"]
    assert exc.value.code == "missing_provenance"


def test_find_unsourced_root_and_sourced_requires_provenance() -> None:
    assert find_unsourced(SourcedValue[str](value="x")) == ["<root>"]
    with pytest.raises(MissingProvenance):
        sourced("x")
    assert unknown().value is None
