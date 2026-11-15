import pytest

from autolist.agent_runtime.commands import mentions, parse_command

WORDS = {
    "approve": "מאשר",
    "cancel": "בטל",
    "image_1": "תמונה 1",
    "image_2": "תמונה 2",
    "lifestyle_add": "תמונת אווירה",
    "lifestyle_remove": "בלי תמונת אווירה",
    "no_sale": "בלי מבצע",
}


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("מאשר", "approve"),
        ("  מאשר.\n", "approve"),
        ("‏מאשר", "approve"),
        ("בטל", "cancel"),
        ("תמונה  2", "image_2"),
        ("תמונה 1", "image_1"),
        ("לא מאשר", None),
        ("מאשר אבל המחיר 480", None),
        ("מאשר!", None),
        ("מאשר..", None),
        ("מאשר\n\nנשלח מה-iPhone שלי", None),
        ("", None),
        ("תמונה 3", None),
        ("תמונת אווירה", "lifestyle_add"),
        ("תמונת אווירה.", "lifestyle_add"),
        ("בלי תמונת אווירה", "lifestyle_remove"),
        ("בלי מבצע", "no_sale"),
        ("תעשה גם תמונת אווירה", None),  # free text: the classifier, never a command
        ("בלי מבצע ומאשר", None),
    ],
)
def test_parse_command(text: str, command: str | None) -> None:
    assert parse_command(text, WORDS) == command


def test_mentions() -> None:
    assert mentions("מאשר אבל המחיר 480", "מאשר")
    assert not mentions("המחיר 480", "מאשר")
