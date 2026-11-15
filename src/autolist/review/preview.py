"""Build the preview email for one job version (fast track, docs/fast-track-pipeline.md).

Pure: no I/O. The orchestrator gives the written text, the owner's facts and the two image
options as JPEG bytes; this module picks which image is preselected, which images are attached,
and which image-note fragment goes into the ``ready_for_approval`` template.

Image rule (DEV, 14.11.2026):
- no generated image (failed, blocked, no credit): image 1 only, fallback note;
- ``printed_text`` true: both images, image 1 preselected, printed-text warning;
- ``printed_text`` false: image 2 only, image 2 preselected;
- the owner chose an image ("תמונה 1"/"תמונה 2"): both images, his choice, owner-choice note.
"""

from __future__ import annotations

from typing import Literal

from autolist.core.models import Attachment, Job, ListingText, OutboundMessage, Preview

TEMPLATE = "ready_for_approval"
NOTE_GENERATED = "image_note_generated"
NOTE_PRINTED_TEXT = "image_note_printed_text"
NOTE_FALLBACK = "image_note_fallback"
NOTE_OWNER_CHOICE = "image_note_owner_choice"
# optional details that the fast-track listing actually uses; shown once if missing
SHOWN_OPTIONAL = ("barcode", "keywords")
IMAGE_NAMES: dict[Literal[1, 2], str] = {1: "image-1.jpg", 2: "image-2.jpg"}


class PreviewError(ValueError):
    """The job is not in a shape that can be previewed (a bug upstream, never guessed)."""


def _attachment(n: Literal[1, 2], data: bytes) -> Attachment:
    return Attachment(
        filename=IMAGE_NAMES[n], mime_type="image/jpeg", size_bytes=len(data), data=data
    )


def build_preview(
    job: Job,
    *,
    version: int,
    text: ListingText,
    image_1: bytes,
    image_2: bytes | None,
    owner_choice: Literal[1, 2] | None,
    labels: dict[str, str],
    to: str,
    in_reply_to: str | None,
) -> Preview:
    """``job.thread_id`` None opens the job's own thread (first agent email for the job)."""
    if text.problems:
        raise PreviewError(f"text has failed checks: {text.problems}")
    facts: dict[str, str] = {}
    for key in ("price", "quantity", "model"):
        fact = job.fact(key)
        if fact is None:
            raise PreviewError(f"required fact missing: {key}")
        facts[key] = fact.value

    choice: Literal[1, 2]
    options: list[Literal[1, 2]]
    if image_2 is None:
        if owner_choice == 2:
            raise PreviewError("image 2 chosen but there is no generated image")
        choice, note, options = 1, NOTE_FALLBACK, [1]
    elif owner_choice is not None:
        choice, note, options = owner_choice, NOTE_OWNER_CHOICE, [1, 2]
    elif job.printed_text is None:
        raise PreviewError("printed_text unknown for a job with a photo")
    elif job.printed_text:
        choice, note, options = 1, NOTE_PRINTED_TEXT, [1, 2]
    else:
        choice, note, options = 2, NOTE_GENERATED, [2]

    data: dict[Literal[1, 2], bytes] = {1: image_1}
    if image_2 is not None:
        data[2] = image_2

    # the writer's open points are not shown: the owner cannot answer them in a way the fast
    # track can use, which made a loop in P1-08 (DEV 15.11.2026). They stay on the version.
    missing = [labels[k] for k in SHOWN_OPTIONAL if job.fact(k) is None]
    message = OutboundMessage(
        channel="email",
        to=to,
        template=TEMPLATE,
        thread_id=job.thread_id,
        in_reply_to=in_reply_to,
        variables={
            "job_id": job.job_id,
            "product_name": text.title,
            "version": str(version),
            "title": text.title,
            "price": facts["price"],
            "quantity": facts["quantity"],
            "model": facts["model"],
            "description_text": text.description_text,
            "optional_missing": ", ".join(missing) if missing else labels["_none"],
        },
        attachments=[_attachment(n, data[n]) for n in options],
    )
    return Preview(
        message=message,
        image_note_template=note,
        image_choice=choice,
        image_options=options,
    )
