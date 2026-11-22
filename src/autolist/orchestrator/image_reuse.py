"""Reusing a generated image instead of paying for it again (DEV 22.11.2026).

An image made for the same photos, role, image model and prompt is reused for up to one day
(the bucket deletes objects after a day). The product facts are not part of the key: the
image is made from the photos, so an email sent again with the same photos gets the same
image. A cancelled job keeps the images of its latest preview the same way, so the product
can be sent again without paying twice.
"""

from __future__ import annotations

import hashlib

from autolist.core.logging import get_logger
from autolist.core.models import Job, ReferenceImage
from autolist.orchestrator.runtime import Runtime

log = get_logger(__name__)
PREFIX = "image-reuse/v1"


def reuse_key(rt: Runtime, role: str, prompt_name: str, photos: list[ReferenceImage]) -> str:
    h = hashlib.sha256()
    for part in (role, prompt_name, str(rt.config.require("images.model"))):
        h.update(part.encode() + b"\\0")
    for photo in photos:
        h.update(hashlib.sha256(photo.data).digest())
    return f"{PREFIX}/{h.hexdigest()}.jpg"


def reused(rt: Runtime, key: str, job: Job) -> bytes | None:
    try:
        data = rt.blobs.get(key)
    except Exception:  # a storage error never stops the job: it only costs a new image
        log.warning("image reuse read failed", extra={"job_id": job.job_id})
        return None
    if data is not None:
        log.info("generated image reused", extra={"job_id": job.job_id})
    return data


def keep(rt: Runtime, key: str, data: bytes, job: Job) -> None:
    try:
        rt.blobs.put(key, data, "image/jpeg")
    except Exception:
        log.warning("image reuse write failed", extra={"job_id": job.job_id})
