import pytest

from autolist.core.text_status import ALLOW_DRAFTS_ENV, refusal


@pytest.mark.parametrize("status", ["approved", "approved_dev"])
def test_usable(status: str) -> None:
    assert refusal(status) is None


@pytest.mark.parametrize("status", ["draft", "not-written", None, "", "Approved"])
def test_refused(status: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_DRAFTS_ENV, raising=False)
    assert refusal(status) is not None


def test_eval_runs_may_read_a_draft_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_DRAFTS_ENV, "1")
    assert refusal("draft") is None
    assert refusal("not-written") is not None
    assert refusal(None) is not None
