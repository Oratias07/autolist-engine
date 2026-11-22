"""Reusing a generated image instead of paying for it again (DEV 22.11.2026).

Nothing is stored for this. A generated image already lives in the agent's own preview email
(``image-2.jpg``, ``image-3.jpg``), and the owner's photos in his own email. When a product
comes again with the same photos (for example after "בטל"), the earlier job's preview email
gives the image back:
- candidates: earlier jobs with the same store model, newest first, at most ``MAX_CANDIDATES``;
- a match: the same role, prompt, image model and photos (compared by hash); the product facts
  are not compared, because the image is made from the photos;
- a mail error never stops the job: it only costs a new image.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal

from autolist.channels.email.mail_sender import MAX_OUT_ATTACHMENT_BYTES
from autolist.channels.email.own_attachment import fetch_own_attachment
from autolist.core.facts import store_model
from autolist.core.logging import get_logger
from autolist.core.models import Job, JobVersion, ReferenceImage
from autolist.orchestrator.runtime import Runtime
from autolist.review.preview import IMAGE_NAMES, LIFESTYLE_NAME

log = get_logger(__name__)
MAX_CANDIDATES = 5
Role = Literal["main", "lifestyle"]
_PROMPT: dict[Role, str] = {"main": "image_main", "lifestyle": "image_lifestyle"}
_FILE: dict[Role, str] = {"main": IMAGE_NAMES[2], "lifestyle": LIFESTYLE_NAME}


def reuse_key(rt: Runtime, role: Role, photos: list[ReferenceImage]) -> str:
    h = hashlib.sha256()
    for part in (role, _PROMPT[role], str(rt.config.require("images.model"))):
        h.update(part.encode() + b"\0")
    for photo in photos:
        h.update(hashlib.sha256(photo.data).digest())
    return h.hexdigest()


def _has(version: JobVersion, role: Role) -> bool:
    if version.preview_gmail_id is None:
        return False
    return 2 in version.image_options if role == "main" else version.lifestyle


def reused_image(
    rt: Runtime,
    job: Job,
    role: Role,
    photos: list[ReferenceImage],
    photos_of: Callable[[Job], list[ReferenceImage]],
    max_refs: int,
) -> bytes | None:
    """The same image from an earlier job's preview email, or None."""
    model = store_model(job)
    if model is None:
        return None
    try:
        key = reuse_key(rt, role, photos[:max_refs])
        candidates = sorted(
            (
                c
                for c in rt.jobs.all_jobs()
                if c.job_id != job.job_id
                and store_model(c) == model
                and any(_has(v, role) for v in c.versions)
            ),
            key=lambda c: c.number,
            reverse=True,
        )[:MAX_CANDIDATES]
        for cand in candidates:
            if reuse_key(rt, role, photos_of(cand)[:max_refs]) != key:
                continue
            preview_id = next(v.preview_gmail_id for v in reversed(cand.versions) if _has(v, role))
            if preview_id is None:  # _has checked it; never fetched without an id
                continue
            data = fetch_own_attachment(
                rt.gmail(),
                preview_id,
                _FILE[role],
                config=rt.config.channels.email,
                max_bytes=MAX_OUT_ATTACHMENT_BYTES,
            )
            log.info(
                "generated image reused",
                extra={"job_id": job.job_id, "from_job": cand.job_id, "role": role},
            )
            return data
    except Exception:  # never stops the job: it only costs a new image
        log.warning("image reuse failed", extra={"job_id": job.job_id})
    return None
