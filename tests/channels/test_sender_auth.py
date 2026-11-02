from __future__ import annotations

from autolist.channels.email.sender_auth import verify_sender
from autolist.core.models import EmailChannelConfig

OWNER = "owner@example.com"
GOOD_AR = (
    "mx.google.com; dkim=pass header.i=@example.com header.s=s1 header.b=abc; "
    "spf=pass (google.com: domain of owner@example.com designates 1.2.3.4) "
    "smtp.mailfrom=owner@example.com; dmarc=pass (p=NONE sp=QUARANTINE dis=NONE) "
    "header.from=example.com"
)


def _h(from_: str = OWNER, ar: str | None = GOOD_AR, extra: list[tuple[str, str]] | None = None):
    headers: list[tuple[str, str]] = []
    if ar is not None:
        headers.append(("Authentication-Results", ar))
    headers += extra or []
    headers.append(("From", f"Owner <{from_}>"))
    return headers


def test_allowlisted_sender_with_dkim_and_dmarc_pass_is_accepted(
    email_cfg: EmailChannelConfig,
) -> None:
    v = verify_sender(_h(), email_cfg)
    assert v.accepted and v.sender == OWNER


def test_same_sender_with_dkim_fail_is_rejected(email_cfg: EmailChannelConfig) -> None:
    v = verify_sender(_h(ar=GOOD_AR.replace("dkim=pass", "dkim=fail")), email_cfg)
    assert not v.accepted and "dkim" in v.reason


def test_dmarc_fail_is_rejected(email_cfg: EmailChannelConfig) -> None:
    v = verify_sender(_h(ar=GOOD_AR.replace("dmarc=pass", "dmarc=fail")), email_cfg)
    assert not v.accepted and "dmarc" in v.reason


def test_other_address_with_pass_is_rejected(email_cfg: EmailChannelConfig) -> None:
    v = verify_sender(_h(from_="someone@example.com"), email_cfg)
    assert not v.accepted and "allowlist" in v.reason


def test_spoofed_from_with_pass_for_another_domain_is_rejected(
    email_cfg: EmailChannelConfig,
) -> None:
    other = GOOD_AR.replace("@example.com", "@evil.test").replace(
        "header.from=example.com", "header.from=evil.test"
    )
    v = verify_sender(_h(ar=other), email_cfg)
    assert not v.accepted


def test_injected_lower_header_is_not_trusted(email_cfg: EmailChannelConfig) -> None:
    real = GOOD_AR.replace("dkim=pass", "dkim=fail").replace("dmarc=pass", "dmarc=fail")
    v = verify_sender(_h(ar=real, extra=[("Authentication-Results", GOOD_AR)]), email_cfg)
    assert not v.accepted


def test_missing_or_foreign_authserv_and_multiple_from(email_cfg: EmailChannelConfig) -> None:
    assert not verify_sender(_h(ar=None), email_cfg).accepted
    foreign = GOOD_AR.replace("mx.google.com", "mx.evil.test")
    assert not verify_sender(_h(ar=foreign), email_cfg).accepted
    two_from = [*_h(), ("From", "other@example.com")]
    assert not verify_sender(two_from, email_cfg).accepted
    assert not verify_sender([("From", "Owner <a@example.com>, b@example.com")], email_cfg).accepted


def test_dkim_by_header_d_and_case_insensitive_sender(email_cfg: EmailChannelConfig) -> None:
    ar = GOOD_AR.replace("header.i=@example.com", "header.d=EXAMPLE.com")
    assert verify_sender(_h(from_="Owner@Example.com", ar=ar), email_cfg).accepted
