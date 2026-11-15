"""The sale price rules (playbook ``price.special``, DEV 15.11.2026). Pure.

- A sale exists only when the owner stated a sale price (fact ``sale_price``).
- It starts on the day the product is saved in the store.
- Its end comes from the owner (fact ``sale_end``), in his words. The code reads one explicit
  date from them (30.11, 30.11.26, 30/11/2026). Anything else, a past date or no end at all
  means the end is missing, and the owner is asked. A date is never worked out or invented.
- The sale price must be below the regular price. Otherwise it is never sent, and the owner
  is asked.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from autolist.core.models import Job, SaleSpec

SALE_KEYS = ("sale_price", "sale_end")
_DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2}))?(?!\d)")

SaleState = Literal["none", "ok", "missing_end", "not_below"]


def parse_sale_end(text: str, today: date) -> date | None:
    """Exactly one explicit day.month[.year] in the owner's words; no year means this year.

    Two dates, an impossible date or a date before today give None (the owner is asked)."""
    found = _DATE.findall(text)
    if len(found) != 1:
        return None
    day, month, year = found[0]
    y = today.year if not year else int(year) + (2000 if len(year) == 2 else 0)
    try:
        end = date(y, int(month), int(day))
    except ValueError:
        return None
    return end if end >= today else None


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def sale_state(job: Job, today: date) -> SaleState:
    sale = job.fact("sale_price")
    if sale is None:
        return "none"
    price = job.fact("price")
    sale_price, regular = _decimal(sale.value), _decimal(price.value) if price else None
    if sale_price is not None and regular is not None and not sale_price < regular:
        return "not_below"
    end = job.fact("sale_end")
    if end is None or parse_sale_end(end.value, today) is None:
        return "missing_end"
    return "ok"


def sale_spec(job: Job, today: date) -> SaleSpec | None:
    """The sale to send on create (``today`` = the publish day), or None when there is none.

    Raises ValueError when a sale was stated but is not valid any more (a bug or a sale end
    that passed while waiting): never sent half-checked."""
    state = sale_state(job, today)
    if state == "none":
        return None
    if state != "ok":
        raise ValueError(f"sale is {state}")
    sale, end = job.fact("sale_price"), job.fact("sale_end")
    end_date = parse_sale_end(end.value, today) if end else None
    if sale is None or end_date is None:  # "ok" guarantees both; checked, never assumed
        raise ValueError("sale is ok but its facts are missing")
    return SaleSpec(price=Decimal(sale.value), date_start=today, date_end=end_date)


def without_sale(job: Job) -> Job:
    """The owner replied "בלי מבצע": every sale fact is removed."""
    return job.model_copy(update={"facts": [f for f in job.facts if f.key not in SALE_KEYS]})
