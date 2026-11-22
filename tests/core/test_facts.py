def test_renamed_title_reads_the_owner_text() -> None:
    from autolist.core.facts import renamed_title
    from autolist.core.models import JobFact

    def name(value: str, quote: str) -> JobFact:
        return JobFact(key="name", value=value, quote=quote, source="reply", gmail_id="g")

    assert (
        renamed_title(name("x", "כיסא, שחור"), 'השם ל"[בדיקה] כיסא, שחור"') == "[בדיקה] כיסא, שחור"
    )
    assert renamed_title(name("x", "כיסא"), "השם ל״כיסא שחור״ בבקשה") == "כיסא שחור"
    assert renamed_title(name("x", "כיסא"), "השם ל“כיסא שחור”") == "כיסא שחור"
    assert renamed_title(name("x", "כיסא"), "שם: כיסא שחור.\nתודה") == "כיסא שחור"  # noqa: RUF001
