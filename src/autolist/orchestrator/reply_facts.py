"""A reply that corrects or supplies product details (classify_reply intent ``facts``).

- NEEDS_INFO: merge, then ask again for what is still missing, or run the duplicate check and
  move to GENERATING.
- AWAITING_APPROVAL: merge and send a new version. The text is written again only when a fact
  the writer uses changed (name, model, barcode, keywords: the title shows the model, so the
  doc's "only if the name changed" is extended to every writer fact). Images are not generated
  again. Price and quantity never enter the text.
"""

from __future__ import annotations

from autolist.content.writer import write_listing, writer_facts
from autolist.core.facts import as_title_override, present_keys
from autolist.core.models import ChannelMessage, Job, JobState, ReplyClassification
from autolist.core.playbook import require
from autolist.core.state_machine import transition
from autolist.orchestrator.category_step import choose_for
from autolist.orchestrator.duplicate_check import cancel_if_duplicate
from autolist.orchestrator.new_version import limit_reached, new_version
from autolist.orchestrator.runtime import (
    JOB_TASK_PATH,
    Runtime,
    numbered,
    product_name,
    send,
    to_owner,
)
from autolist.orchestrator.sale_step import sale_blocked, sale_not_below, sale_questions


def missing_required(rt: Runtime, job: Job) -> list[str]:
    """Required fields the job still lacks (``photo`` counts when it has photo indexes)."""
    required: list[str] = require(rt.playbook, "listing.required")
    have = present_keys({f.key for f in job.facts}, bool(job.photo_indexes))
    return [k for k in required if k not in have]


def _ask(rt: Runtime, job: Job, msg: ChannelMessage, questions: list[str]) -> Job:
    _, saved = send(
        rt,
        to_owner(
            "needs_info",
            {
                "job_id": job.job_id,
                "product_name": product_name(job),
                "questions_list": numbered(questions),
            },
            to=rt.owner,
            thread_id=job.thread_id,
            in_reply_to=msg.rfc_message_id,
        ),
        job,
    )
    return saved or job


async def apply_facts(
    rt: Runtime,
    job: Job,
    msg: ChannelMessage,
    c: ReplyClassification,
    *,
    lifestyle: bytes | None = None,
) -> dict[str, str]:
    """``lifestyle``: a lifestyle image made for this reply (AWAITING_APPROVAL only)."""
    unverified = [rt.labels[k] for k in c.unverified_fields]
    extra = [c.question] if c.question else []
    merged = job.model_copy(update={"facts": [*job.facts, *c.facts]})

    if job.state == JobState.NEEDS_INFO:
        missing = [rt.labels[k] for k in missing_required(rt, merged)]
        questions = [*missing, *unverified, *sale_questions(rt, merged), *extra]
        if sale_not_below(rt, merged, msg.rfc_message_id):
            merged = transition(rt.jobs.get(job.job_id) or merged, JobState.NEEDS_INFO, rt.now())
            merged = merged.model_copy(update={"facts": [*job.facts, *c.facts]})
            rt.jobs.save(merged)
            if questions:
                _ask(rt, merged, msg, questions)
            return {"job_id": job.job_id, "reply": "sale_not_below"}
        if questions:
            merged = transition(merged, JobState.NEEDS_INFO, rt.now())
            rt.jobs.save(merged)
            _ask(rt, merged, msg, questions)
            return {"job_id": job.job_id, "reply": "needs_info"}
        rt.jobs.save(merged)
        if await cancel_if_duplicate(rt, merged) is not None:
            return {"job_id": job.job_id, "reply": "duplicate"}
        merged = transition(merged, JobState.GENERATING, rt.now())
        rt.jobs.save(merged)
        rt.queue.enqueue(JOB_TASK_PATH, {"event": "generate", "job_id": job.job_id})
        return {"job_id": job.job_id, "reply": "generating"}

    # AWAITING_APPROVAL: a name is the owner's exact title from now on (DEV 22.11.2026)
    c = c.model_copy(update={"facts": as_title_override(c.facts)})
    merged = job.model_copy(update={"facts": [*job.facts, *c.facts]})
    if not c.facts and lifestyle is None:
        _ask(rt, job, msg, [*unverified, *extra])
        return {"job_id": job.job_id, "reply": "needs_info"}
    if sale_blocked(rt, merged):
        # the facts are kept, no version is made, and approval waits until the sale is fixed
        # (or "בלי מבצע"): a version never shows a sale that would not be sent
        rt.jobs.save(merged)
        if not sale_not_below(rt, merged, msg.rfc_message_id):
            _ask(rt, merged, msg, [*sale_questions(rt, merged), *unverified, *extra])
        return {"job_id": job.job_id, "reply": "sale_pending"}
    if limit_reached(rt, job, msg):
        return {"job_id": job.job_id, "reply": "revision_limit"}
    text = None
    if writer_facts(merged.facts) != writer_facts(job.facts):
        text = write_listing(
            merged.facts,
            llm=rt.llm,
            playbook=rt.playbook,
            glossary=rt.glossary,
            voice=rt.voice,
            job_id=job.job_id,
        )
        if text.problems:
            raise RuntimeError(f"text checks failed: {text.problems}")
    category = None
    if any(f.key == "category_hint" for f in c.facts):
        category = await choose_for(rt, merged)  # the owner named a category: choose again
    rt.jobs.save(merged)
    new_version(
        rt,
        merged,
        msg,
        owner_choice=merged.versions[-1].owner_choice,
        text=text,
        category=category,
        lifestyle=lifestyle,
    )
    if unverified or extra:
        current = rt.jobs.get(job.job_id) or merged
        _ask(rt, current, msg, [*unverified, *extra])
    return {"job_id": job.job_id, "reply": "new_version"}
