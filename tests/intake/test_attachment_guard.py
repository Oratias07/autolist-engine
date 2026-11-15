"""intake.attachment_guard: untrusted attachments."""

from __future__ import annotations

import copy
import io

from PIL import Image

from autolist.core.config import parse_store_config
from autolist.core.models import Attachment, IntakeConfig
from autolist.intake.attachment_guard import guard, safe_name
from tests.core.conftest import VALID_STORE


def _cfg(**over: object) -> IntakeConfig:
    data = copy.deepcopy(VALID_STORE)
    data["intake"].update(over)
    return parse_store_config(data).intake


def _img(w: int, h: int, fmt: str = "PNG", exif: bool = False) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (w, h), (9, 9, 9))
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "Maker"
        im.save(buf, format=fmt, exif=ex)
    else:
        im.save(buf, format=fmt)
    return buf.getvalue()


def _att(name: str, data: bytes, mime: str = "image/png") -> Attachment:
    return Attachment(filename=name, mime_type=mime, size_bytes=len(data), data=data)


def test_accepts_images_reencodes_and_downscales() -> None:
    res = guard([_att("a.jpg", _img(3000, 1000, "JPEG", exif=True), "image/jpeg")], _cfg())
    assert res.rejected == []
    out = Image.open(io.BytesIO(res.accepted[0].data))
    assert out.format == "JPEG"
    assert max(out.size) == 1568
    assert not out.getexif()


def test_declared_type_is_not_trusted() -> None:
    res = guard([_att("x.png", b"%PDF-1.7 not an image", "image/png")], _cfg())
    assert res.accepted == []
    assert res.rejected == ["x.png: not a readable image"]


def test_unlisted_type_is_refused() -> None:
    res = guard([_att("a.png", _img(10, 10))], _cfg(image_types=["image/jpeg"]))
    assert res.rejected == ["a.png: not a supported image type"]


def test_pixel_limit_checked_before_decode() -> None:
    res = guard([_att("big.png", _img(400, 400))], _cfg(max_image_pixels=100_000))
    assert res.rejected == ["big.png: image is too large"]


def test_size_and_count_limits() -> None:
    small = _img(10, 10)
    cfg = _cfg(max_attachment_bytes=len(small), max_attachments=2)
    res = guard(
        [
            _att("1.png", small),
            _att("2.png", small + b"x"),
            _att("3.png", small),
            _att("4.png", small),
        ],
        cfg,
    )
    assert [i.name for i in res.accepted] == ["1.png", "3.png"]
    assert res.rejected == ["2.png: file is too large", "4.png: too many attachments"]


def test_missing_data_is_refused() -> None:
    att = Attachment(filename="a.png", mime_type="image/png", size_bytes=10, data=None)
    assert guard([att], _cfg()).rejected == ["a.png: file is too large"]


def test_safe_name_strips_paths_and_markup() -> None:
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\x\\<script>.png") == "_script_.png"
    assert safe_name("תמונה 1.jpg") == "תמונה 1.jpg"
    assert len(safe_name("a" * 300)) == 80
    assert safe_name("") == "attachment"


def test_heic_is_accepted_and_converted_to_jpeg() -> None:
    import pillow_heif

    heif = pillow_heif.from_pillow(Image.new("RGB", (2000, 1500), (200, 10, 10)))
    buf = io.BytesIO()
    heif.save(buf, quality=80)
    data = buf.getvalue()
    types = ["image/jpeg", "image/heic"]
    res = guard([_att("IMG_0001.HEIC", data, "image/heic")], _cfg(image_types=types))
    assert res.rejected == []
    out = Image.open(io.BytesIO(res.accepted[0].data))
    assert out.format == "JPEG"
    assert max(out.size) == 1568


def test_heic_refused_when_not_listed() -> None:
    import pillow_heif

    heif = pillow_heif.from_pillow(Image.new("RGB", (100, 100)))
    buf = io.BytesIO()
    heif.save(buf)
    res = guard([_att("a.heic", buf.getvalue(), "image/heic")], _cfg())
    assert res.rejected == ["a.heic: not a supported image type"]
