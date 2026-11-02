"""Errors raised by the images module."""

from __future__ import annotations


class ImageError(Exception):
    code = "image_error"


class BillingRequired(ImageError):
    """HTTP 402: the provider's prepaid balance is empty. The owner must add credit."""

    code = "billing_required"


class ImageUnavailable(ImageError):
    """Rate-limited or server errors after all retries, or a network failure."""

    code = "image_unavailable"


class ImageRequestRejected(ImageError):
    """A non-retryable 4xx from the provider."""

    code = "image_request_rejected"


class ImageNotReturned(ImageError):
    """A 200 response without an image part (for example blocked by safety)."""

    code = "image_not_returned"

    def __init__(self, message: str, *, finish_reason: str | None, text: str, shape: str) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.text = text
        self.shape = shape  # response keys only, never image data
