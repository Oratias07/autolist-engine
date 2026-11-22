"""core.blob_store: in-memory round trip and the GCS adapter against a fake client."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from autolist.core.blob_store import GcsBlobStore, InMemoryBlobStore
from autolist.core.config import parse_store_config
from autolist.core.errors import ConfigMissing
from autolist.core.models import ImageRef
from tests.core.conftest import VALID_STORE


def test_in_memory_round_trip_and_delete() -> None:
    store = InMemoryBlobStore()
    ref = store.put("a/b.jpg", b"x", "image/jpeg")
    assert store.signed_url(ref, 60).startswith("https://storage.example.com/test-bucket/a/b.jpg")
    store.delete(ref)
    with pytest.raises(KeyError):
        store.signed_url(ref, 60)


@pytest.mark.parametrize("ttl", [0, -1, 3601])
def test_ttl_out_of_range_is_refused(ttl: int) -> None:
    store = InMemoryBlobStore()
    ref = store.put("a.jpg", b"x", "image/jpeg")
    with pytest.raises(ValueError, match="ttl"):
        store.signed_url(ref, ttl)


class FakeBlob:
    def __init__(self, log: list[Any], name: str) -> None:
        self.log, self.name = log, name

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.log.append(("upload", self.name, len(data), content_type))

    def generate_signed_url(self, **kw: Any) -> str:
        self.log.append(("sign", self.name, kw))
        return f"https://signed.example.com/{self.name}"

    def delete(self) -> None:
        self.log.append(("delete", self.name))


class FakeClient:
    def __init__(self) -> None:
        self.log: list[Any] = []

    def bucket(self, name: str) -> Any:
        client = self

        class B:
            def blob(self, blob_name: str) -> FakeBlob:
                client.log.append(("bucket", name))
                return FakeBlob(client.log, blob_name)

        return B()


class TokenCreds:
    service_account_email = "runner@example.iam.gserviceaccount.com"
    token = "tok"  # noqa: S105


def _cfg(bucket: str | None) -> Any:
    data = copy.deepcopy(VALID_STORE)
    data["gcp"]["reference_bucket"] = bucket
    return parse_store_config(data)


def test_gcs_missing_bucket_raises_config_missing() -> None:
    with pytest.raises(ConfigMissing):
        GcsBlobStore(_cfg(None), client=FakeClient())


def test_gcs_put_sign_delete_with_token_credentials() -> None:
    fake = FakeClient()
    store = GcsBlobStore(_cfg("refs"), client=fake, credentials=TokenCreds())
    ref = store.put("j/main.jpg", b"abc", "image/jpeg")
    assert ref == ImageRef(bucket="refs", name="j/main.jpg", content_type="image/jpeg")
    assert store.signed_url(ref, 900) == "https://signed.example.com/j/main.jpg"
    sign = next(e for e in fake.log if e[0] == "sign")[2]
    assert sign["version"] == "v4"
    assert sign["method"] == "GET"
    assert sign["expiration"].total_seconds() == 900
    assert sign["service_account_email"] == TokenCreds.service_account_email
    assert sign["access_token"] == TokenCreds.token
    store.delete(ref)
    assert fake.log[-1] == ("delete", "j/main.jpg")


def test_gcs_refuses_other_bucket_and_long_ttl() -> None:
    store = GcsBlobStore(_cfg("refs"), client=FakeClient(), credentials=TokenCreds())
    other = ImageRef(bucket="else", name="a.jpg", content_type="image/jpeg")
    with pytest.raises(ValueError, match="bucket"):
        store.signed_url(other, 60)
    mine = ImageRef(bucket="refs", name="a.jpg", content_type="image/jpeg")
    with pytest.raises(ValueError, match="ttl"):
        store.signed_url(mine, 7200)
