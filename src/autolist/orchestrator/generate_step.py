"""Step 2: a job in GENERATING gets its text and images, and the owner gets the preview.

The duplicate check already ran before GENERATING. Order (fast-track doc): text -> image 1
(the owner's photo, postprocessed, free) -> image 2 (generated; any image failure falls back
to image 1) -> preview -> AWAITING_APPROVAL. A paid call that fails is not repeated: the job fails.
"""

from __future__ import annotations

from autolist.content.writer import write_listing
from autolist.core.logging import get_logger
from autolist.core.models import (
    ImageRequest,
    Job,
    JobState,
    JobVersion,
    ListingText,
    ReferenceImage,
)
from autolist.core.state_machine import transition
from autolist.images.errors import BillingRequired, ImageError
from autolist.images.postprocess import postprocess
from autolist.intake.attachment_guard import guard
from autolist.orchestrator.intake_step import read
from autolist.orchestrator.runtime import Runtime, alert_developer, send
from autolist.review.preview import build_preview

log = get_logger(__name__)
MAX_REFERENCES = 4  # ImageRequest limit


def job_photos(rt: Runtime, job: Job) -> list[ReferenceImage]:
    """The job's photos, re-read from the owner's email and guarded again (nothing is stored)."""
    msg = read(rt, job.source_gmail_id)
    accepted = guard(msg.attachments, rt.config.intake).accepted
    photos = [accepted[i - 1] for i in job.photo_indexes if 0 < i <= len(accepted)]
    if not photos or len(photos) != len(job.photo_indexes):
        raise RuntimeError(f"{job.job_id}: the job's photos are no longer in the source email")
    return photos


def _image_2(rt: Runtime, job: Job, photos: list[ReferenceImage]) -> bytes | None:
    name, model = job.fact("name"), job.fact("model")
    facts = "\n".join(f"{f.key}: {f.value}" for f in (name, model) if f is not None)
    try:
        img = rt.images.generate(
            ImageRequest(
                job_id=job.job_id,
                model=rt.config.require("images.model"),
                role="main",
                prompt_name="image_main",
                variables={"product_facts": facts, "reference_notes": "-"},
                references=photos[:MAX_REFERENCES],
            )
        )
    except BillingRequired as exc:
        alert_developer(rt, job.job_id, "gemini_billing", job.state, str(exc))
        return None
    except ImageError as exc:
        log.warning("image generation failed; using image 1", extra={"job_id": job.job_id})
        alert_developer(rt, job.job_id, "image_fallback", job.state, str(exc))
        return None
    px: int = rt.config.require("images.output_px")
    return postprocess(img.data, px)


def generate(rt: Runtime, job: Job) -> Job:
    text: ListingText = write_listing(
        job.facts,
        llm=rt.llm,
        playbook=rt.playbook,
        glossary=rt.glossary,
        voice=rt.voice,
        job_id=job.job_id,
    )
    if text.problems:
        raise RuntimeError(f"text checks failed: {text.problems}")

    photos = job_photos(rt, job)
    px: int = rt.config.require("images.output_px")
    image_1 = postprocess(photos[0].data, px)
    image_2 = _image_2(rt, job, photos)

    version = len(job.versions) + 1
    preview = build_preview(
        job,
        version=version,
        text=text,
        image_1=image_1,
        image_2=image_2,
        owner_choice=None,
        labels=rt.labels,
        to=rt.owner,
        in_reply_to=None,
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
    job = job.model_copy(
        update={
            "versions": [
                *job.versions,
                JobVersion(
                    number=version,
                    created_at=rt.now(),
                    title=text.title,
                    description_html=text.description_html,
                    image_choice=preview.image_choice,
                    image_options=preview.image_options,
                    preview_gmail_id=sent.gmail_id,
                    preview_rfc_message_id=sent.rfc_message_id,
                ),
            ]
        }
    )
    job = transition(job, JobState.AWAITING_APPROVAL, rt.now())
    rt.jobs.save(job)
    return job
