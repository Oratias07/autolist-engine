"""Make a generated image store-ready: square on white (never cropped), resized, re-encoded."""

from __future__ import annotations

import io

from PIL import Image

_WHITE = (255, 255, 255)


def postprocess(image: bytes, output_px: int, quality: int = 90) -> bytes:
    """Pad to a white square, resize to ``output_px``, strip metadata, encode as JPEG."""
    with Image.open(io.BytesIO(image)) as src:
        src.load()
        rgb = _flatten(src)
    side = max(rgb.size)
    canvas = Image.new("RGB", (side, side), _WHITE)
    canvas.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
    if side != output_px:
        canvas = canvas.resize((output_px, output_px), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=quality, optimize=True)  # no exif passed: stripped
    return out.getvalue()


def _flatten(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, _WHITE)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")
