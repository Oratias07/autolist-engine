"""A new preview version after a reply: same images, new facts and/or a new image choice.

Images are never generated again here: image 1 comes from the owner's email, image 2 from the
previous preview in the agent mailbox (decision #36). The text is the previous version's, unless
the caller passes a newly written one (a fact the writer uses changed).
"""

from __future__ import annotations

from typing import Literal

from autolist.channels.email.mail_sender import MAX_OUT_ATTACHMENT_BYTES
from autolist.channels.email.own_attachment import fetch_own_attachment
from autolist.core.facts import TITLE_OVERRIDE
from autolist.core.models import CategoryChoice, ChannelMessage, Job, JobState, ListingText
from autolist.core.state_machine import transition
from autolist.images.postprocess import postprocess
from autolist.orchestrator.generate_step import job_photos
from autolist.orchestrator.runtime import (
    Runtime,
    alert_developer,
    category_text,
    lifestyle_text,
    product_name,
    sale_text,
    send,
    to_owner,
)
from autolist.review.preview import IMAGE_NAMES, LIFESTYLE_NAME, build_preview


def limit_reached(rt: Runtime, job: Job, msg: ChannelMessage) -> bool:
    """More than ``agent.max_revision_rounds`` new versions: the developer takes over."""
    limit: int = rt.config.require("agent.max_revision_rounds")
    if len(job.versions) <= limit:
        return False
    alert_developer(rt, job.job_id, "revision_limit", job.state, f"{len(job.versions)}")
    _, _ = send(
        rt,
        to_owner(
            "change_not_supported",
            {"job_id": job.job_id, "product_name": product_name(job)},
            to=rt.owner,
            thread_id=job.thread_id,
            in_reply_to=msg.rfc_message_id,
        ),
        job,
    )
    return True


def new_version(
    rt: Runtime,
    job: Job,
    msg: ChannelMessage,
    *,
    owner_choice: Literal[1, 2] | None,
    text: ListingText | None = None,
    category: CategoryChoice | None = None,
    lifestyle: bytes | None = None,
    drop_lifestyle: bool = False,
) -> Job:
    """``lifestyle``: a new lifestyle image; else the previous one is kept, unless
    ``drop_lifestyle`` ("בלי תמונת אווירה")."""
    latest = job.versions[-1]
    px: int = rt.config.require("images.output_px")
    image_1 = postprocess(job_photos(rt, job)[0].data, px)
    image_2 = None
    if 2 in latest.image_options:
        if latest.preview_gmail_id is None:
            raise RuntimeError(
                f"{job.job_id}: the preview id of version {latest.number} is missing"
            )
        image_2 = fetch_own_attachment(
            rt.gmail(),
            latest.preview_gmail_id,
            IMAGE_NAMES[2],
            config=rt.config.channels.email,
            max_bytes=MAX_OUT_ATTACHMENT_BYTES,
        )
    if lifestyle is None and latest.lifestyle and not drop_lifestyle:
        if latest.preview_gmail_id is None:
            raise RuntimeError(
                f"{job.job_id}: the preview id of version {latest.number} is missing"
            )
        lifestyle = fetch_own_attachment(
            rt.gmail(),
            latest.preview_gmail_id,
            LIFESTYLE_NAME,
            config=rt.config.channels.email,
            max_bytes=MAX_OUT_ATTACHMENT_BYTES,
        )
    text = text or ListingText(
        title=latest.title,
        description_html=latest.description_html,
        description_text=latest.description_text,
        open_points=latest.open_points,
        problems=[],
    )
    override = job.fact(TITLE_OVERRIDE)
    if override is not None:  # the owner's own name wins over any written title
        text = text.model_copy(update={"title": override.value})
    number = latest.number + 1
    preview = build_preview(
        job,
        version=number,
        text=text,
        image_1=image_1,
        image_2=image_2,
        owner_choice=owner_choice,
        labels=rt.labels,
        to=rt.owner,
        in_reply_to=msg.rfc_message_id,
        category=category_text(rt, category.names if category else latest.category_names),
        sale=sale_text(rt, job),
        lifestyle=lifestyle,
        lifestyle_note=lifestyle_text(rt, lifestyle is not None),
    )
    message = preview.message.model_copy(
        update={
            "variables": {
                **preview.message.variables,
                "image_note": rt.fragment(preview.image_note_template, {}),
            }
        }
    )
    sent, saved = send(rt, message, job)
    job = saved or job
    version = latest.model_copy(
        update={
            "number": number,
            "created_at": rt.now(),
            "title": text.title,
            "description_html": text.description_html,
            "description_text": text.description_text,
            "open_points": text.open_points,
            "category_ids": category.category_ids if category else latest.category_ids,
            "category_names": category.names if category else latest.category_names,
            "image_choice": preview.image_choice,
            "owner_choice": owner_choice,
            "image_options": preview.image_options,
            "lifestyle": preview.lifestyle,
            "preview_gmail_id": sent.gmail_id,
            "preview_rfc_message_id": sent.rfc_message_id,
        }
    )
    job = job.model_copy(
        update={"versions": [*job.versions, version], "revision_count": job.revision_count + 1}
    )
    job = transition(job, JobState.AWAITING_APPROVAL, rt.now())
    rt.jobs.save(job)
    return job
