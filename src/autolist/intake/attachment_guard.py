"""Decide which email attachments may be used as product photos, and prepare them.

Everything here is defensive: an attachment is untrusted bytes with an untrusted name.
- only the configured image types, checked by decoding, not by the declared MIME type;
- size and count limits from store.yaml;
- the pixel count is read from the header and checked before any decode (decompression bombs);
- the accepted photo is re-encoded (drops EXIF and anything appended to the file) and
  downscaled for the LLM.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from autolist.core.models import Attachment, IntakeConfig, ReferenceImage

_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_SAFE_NAME = re.compile(r"[^\w.\-() ]+", re.UNICODE)


@dataclass(frozen=True)
class GuardResult:
    accepted: list[ReferenceImage]  # in attachment order; index i+1 is what the prompt shows
    rejected: list[str]  # "name: reason", for the owner


def safe_name(name: str) -> str:
    """A display-only file name: no path, no control or markup characters, bounded length."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME.sub("_", base).strip() or "attachment"
    return cleaned[:80]


def _prepare(data: bytes, cfg: IntakeConfig) -> tuple[bytes, str]:
    with Image.open(io.BytesIO(data)) as probe:
        fmt = probe.format or ""
        if probe.width * probe.height > cfg.max_image_pixels:
            raise ValueError("image is too large")
    mime = _FORMATS.get(fmt)
    if mime is None or mime not in cfg.image_types:
        raise ValueError("not a supported image type")
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        rgb = im.convert("RGBA").convert("RGB") if im.mode in ("P", "LA", "RGBA") else im
        rgb = rgb.convert("RGB")
        rgb.thumbnail((cfg.llm_image_max_edge_px, cfg.llm_image_max_edge_px))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=90)  # no exif passed: stripped
    return out.getvalue(), "image/jpeg"


def guard(attachments: list[Attachment], cfg: IntakeConfig) -> GuardResult:
    accepted: list[ReferenceImage] = []
    rejected: list[str] = []
    for att in attachments:
        name = safe_name(att.filename)
        if len(accepted) >= cfg.max_attachments:
            rejected.append(f"{name}: too many attachments")
            continue
        if att.data is None or att.size_bytes > cfg.max_attachment_bytes:
            rejected.append(f"{name}: file is too large")
            continue
        if len(att.data) > cfg.max_attachment_bytes:
            rejected.append(f"{name}: file is too large")
            continue
        try:
            data, mime = _prepare(att.data, cfg)
        except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "not a readable image"
            rejected.append(f"{name}: {reason}")
            continue
        accepted.append(ReferenceImage(name=name, mime_type=mime, data=data))
    return GuardResult(accepted=accepted, rejected=rejected)
