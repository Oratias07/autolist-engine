"""The duplicate check that runs before a job enters GENERATING (fast-track doc).

It runs right after extraction, and again after a NEEDS_INFO answer, so no paid text or image
call is made for a product the store already has. ``publish.create_hidden`` checks once more
before it creates. The SKU is not known in the fast track (decision 15.11.2026): model only.
"""

from __future__ import annotations

from autolist.core.models import Job, JobState
from autolist.core.state_machine import transition
from autolist.orchestrator.runtime import Runtime, product_name, send, to_owner
from autolist.publish.publisher import find_duplicate


async def cancel_if_duplicate(rt: Runtime, job: Job) -> Job | None:
    """Returns the cancelled job when the store already has the model, else None."""
    model = job.fact("model")
    if model is None:
        raise RuntimeError(f"{job.job_id}: duplicate check needs the model")
    page_size: int = rt.config.require("istores.catalog_page_size")
    async with rt.istores() as client:
        dup = await find_duplicate(client, page_size, None, model.value)
    if dup is None:
        return None
    field, value, product_id = dup
    _, saved = send(
        rt,
        to_owner(
            "duplicate_found",
            {
                "original_subject": job.source_subject or "",
                "product_name": product_name(job),
                "match_field": rt.labels[field],
                "match_value": value,
                "existing_product_id": str(product_id),
            },
            to=rt.owner,
            thread_id=job.source_thread_id,
            in_reply_to=job.source_rfc_message_id,
        ),
        job,
    )
    job = transition(saved or job, JobState.CANCELLED, rt.now(), error=f"duplicate {field}")
    rt.jobs.save(job)
    return job
