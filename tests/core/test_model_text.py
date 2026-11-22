"""Model-written and emailed text on its way to the agent's emails and the store (L6, L7)."""

from __future__ import annotations

import pytest

from autolist.core.model_text import clean_question, clean_title
from autolist.core.models import CategoryChoice


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("מה המחיר? פרטים ב-https://evil.example.com/x", "מה המחיר? פרטים ב-"),
        ("שלח את הסיסמה ל-a.b@example.com עכשיו", "שלח את הסיסמה ל- עכשיו"),
        ("ראה www.example.com", "ראה"),
        ("היכנס ל evil.co.il/login מיד", "היכנס ל מיד"),
        ("מה הצבע?\u202e<b>", "מה הצבע?b"),
        ("מה   המידה\x00?", "מה המידה?"),
    ],
)
def test_question_loses_links_addresses_markup_and_controls(raw: str, clean: str) -> None:
    assert clean_question(raw) == clean


@pytest.mark.parametrize(
    "kept",
    ["מה הרוחב, 3.5 או 4.5?", "האם הדגם Q9001-B?", "משקל 1.2kg?", "מארז 2 יחידות?"],
)
def test_ordinary_questions_are_unchanged(kept: str) -> None:
    assert clean_question(kept) == kept


def test_title_keeps_its_text_and_loses_markup_and_controls() -> None:
    assert clean_title("כיסא (שחור), דגם Q9001") == "כיסא (שחור), דגם Q9001"
    assert clean_title("כיסא <script>x</script>⁦ Q9001\n") == "כיסא scriptx/script Q9001"


def test_models_clean_on_the_way_in() -> None:
    c = CategoryChoice(category_ids=[], names=[], question="ראה https://evil.example.com")
    assert c.question == "ראה"
