"""Private object storage for images, behind one interface.

The store platform fetches images by URL, so the publisher hands it a short-lived signed URL
to a private bucket. Nothing in the bucket is public.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal, Protocol

from autolist.core.models import ImageRef, StoreConfig

MAX_TTL_S = 3600
ContentType = Literal["image/jpeg", "image/png", "image/webp"]


class BlobStore(Protocol):
    def put(self, name: str, data: bytes, content_type: ContentType) -> ImageRef: ...

    def signed_url(self, ref: ImageRef, ttl_s: int) -> str: ...

    def delete(self, ref: ImageRef) -> None: ...


def _check_ttl(ttl_s: int) -> None:
    if not 0 < ttl_s <= MAX_TTL_S:
        raise ValueError(f"signed URL ttl must be 1..{MAX_TTL_S} seconds, got {ttl_s}")


class InMemoryBlobStore:
    """For tests. URLs use the reserved example.com domain."""

    def __init__(self, bucket: str = "test-bucket") -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}

    def put(self, name: str, data: bytes, content_type: ContentType) -> ImageRef:
        self.objects[name] = data
        return ImageRef(bucket=self.bucket, name=name, content_type=content_type)

    def signed_url(self, ref: ImageRef, ttl_s: int) -> str:
        _check_ttl(ttl_s)
        if ref.name not in self.objects:
            raise KeyError(ref.name)
        return f"https://storage.example.com/{ref.bucket}/{ref.name}?sig=test&ttl={ttl_s}"

    def delete(self, ref: ImageRef) -> None:
        self.objects.pop(ref.name, None)


class GcsBlobStore:
    """Google Cloud Storage. V4 signed URLs are signed through IAM, so no key file exists."""

    def __init__(self, config: StoreConfig, client: Any = None, credentials: Any = None) -> None:
        self._bucket_name: str = config.require("gcp.reference_bucket")
        if client is None:  # pragma: no cover - needs real credentials
            import google.auth
            from google.cloud import storage  # type: ignore[attr-defined]

            # explicit scope: refreshing impersonated (WIF) credentials without one is refused
            # with INVALID_ARGUMENT (observed, live run 43610951196)
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            client = storage.Client(project=config.gcp.project_id, credentials=credentials)
        self._client = client
        self._credentials = credentials

    def put(self, name: str, data: bytes, content_type: ContentType) -> ImageRef:
        blob = self._client.bucket(self._bucket_name).blob(name)
        blob.upload_from_string(data, content_type=content_type)
        return ImageRef(bucket=self._bucket_name, name=name, content_type=content_type)

    def signed_url(self, ref: ImageRef, ttl_s: int) -> str:
        _check_ttl(ttl_s)
        if ref.bucket != self._bucket_name:
            raise ValueError(f"image is in bucket {ref.bucket!r}, not the configured bucket")
        kwargs: dict[str, Any] = {}
        creds = self._credentials
        if creds is not None and not hasattr(creds, "sign_bytes"):
            # token-only credentials (Cloud Run, WIF): sign through the IAM signBlob API
            if not getattr(creds, "token", None):  # pragma: no cover - needs real credentials
                from google.auth.transport.requests import Request

                creds.refresh(Request())
            kwargs = {
                "service_account_email": creds.service_account_email,
                "access_token": creds.token,
            }
        blob = self._client.bucket(self._bucket_name).blob(ref.name)
        url: str = blob.generate_signed_url(
            version="v4", expiration=timedelta(seconds=ttl_s), method="GET", **kwargs
        )
        return url

    def delete(self, ref: ImageRef) -> None:
        self._client.bucket(self._bucket_name).blob(ref.name).delete()
