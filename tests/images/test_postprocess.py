"""images.postprocess: square on white, never cropped, metadata stripped."""

from __future__ import annotations

import io

from PIL import Image

from autolist.images.postprocess import postprocess


def _img(w: int, h: int, mode: str = "RGB", fmt: str = "PNG", exif: bool = False) -> bytes:
    color: tuple[int, ...] = (200, 0, 0) if mode == "RGB" else (200, 0, 0, 255)
    im = Image.new(mode, (w, h), color)
    buf = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "CameraMaker"
        im.save(buf, format=fmt, exif=ex)
    else:
        im.save(buf, format=fmt)
    return buf.getvalue()


def test_portrait_is_padded_not_cropped() -> None:
    out = Image.open(io.BytesIO(postprocess(_img(900, 1600), 1200)))
    assert out.size == (1200, 1200)
    assert out.format == "JPEG"
    # left and right edges are white padding, centre is the product colour
    assert out.getpixel((5, 600))[0] > 240  # type: ignore[index]
    r, g, _ = out.getpixel((600, 600))  # type: ignore[misc]
    assert r > 150 and g < 60
    # top and bottom rows keep product (not cropped): full height used
    assert out.getpixel((600, 3))[1] < 60  # type: ignore[index]


def test_exif_is_stripped() -> None:
    out = Image.open(io.BytesIO(postprocess(_img(1000, 1000, fmt="JPEG", exif=True), 1200)))
    assert not out.getexif()


def test_transparent_background_becomes_white() -> None:
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = Image.open(io.BytesIO(postprocess(buf.getvalue(), 200)))
    assert out.getpixel((50, 50))[0] > 240  # type: ignore[index]


def test_already_right_size_is_kept() -> None:
    out = Image.open(io.BytesIO(postprocess(_img(1200, 1200), 1200)))
    assert out.size == (1200, 1200)
