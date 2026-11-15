"""The sale price checks of a job (``core.sale``), and the owner emails they lead to.

- end missing (or not a clear future date): the owner is asked for "תאריך סיום המבצע";
- sale price not below the price: ``sale_price_not_below``;
- either one blocks generating and approving until the owner fixes it or replies "בלי מבצע".
"""

from __future__ import annotations

from autolist.core.models import Job
from autolist.core.sale import sale_state
from autolist.orchestrator.runtime import Runtime, product_name, send, to_owner

SALE_END = "sale_end"


def sale_questions(rt: Runtime, job: Job) -> list[str]:
    return [rt.labels[SALE_END]] if sale_state(job, rt.today()) == "missing_end" else []


def sale_not_below(rt: Runtime, job: Job, in_reply_to: str | None) -> bool:
    """Sends ``sale_price_not_below`` and returns True when the sale price is not below."""
    if sale_state(job, rt.today()) != "not_below":
        return False
    price, sale = job.fact("price"), job.fact("sale_price")
    send(
        rt,
        to_owner(
            "sale_price_not_below",
            {
                "job_id": job.job_id,
                "product_name": product_name(job),
                "price": price.value if price else "-",
                "sale_price": sale.value if sale else "-",
            },
            to=rt.owner,
            thread_id=job.thread_id,
            in_reply_to=in_reply_to,
        ),
        job,
    )
    return True


def sale_blocked(rt: Runtime, job: Job) -> bool:
    return sale_state(job, rt.today()) in ("missing_end", "not_below")
