"""Errors raised by the store client. All inherit from ``AutolistError``."""

from __future__ import annotations

from autolist.core.errors import AutolistError


class EndpointNotAllowed(AutolistError):
    code = "endpoint_not_allowed"

    def __init__(self, method: str, path: str) -> None:
        super().__init__(f"{method} {path} is not on the store allowlist")


class ForeignProductError(AutolistError):
    """PUT/DELETE on a product this system did not create."""

    code = "foreign_product"

    def __init__(self, product_id: int) -> None:
        self.product_id = product_id
        super().__init__(f"product {product_id} was not created by this system")


class UnsafeCreate(AutolistError):
    code = "unsafe_create"


class UnsafeUnhide(AutolistError):
    code = "unsafe_unhide"


class UnhideDisabled(AutolistError):
    code = "unhide_disabled"


class CreateResultUnknown(AutolistError):
    """POST returned, but no product id could be parsed. Search by SKU before anything else."""

    code = "create_result_unknown"


class StoreRateLimited(AutolistError):
    code = "store_rate_limited"


class StoreAPIError(AutolistError):
    code = "store_api_error"

    def __init__(self, status: int | None, body_excerpt: str) -> None:
        self.status = status
        self.body_excerpt = body_excerpt
        super().__init__(f"store API error {status}: {body_excerpt}")


class DuplicateProduct(AutolistError):
    """A product with the same SKU or model already exists in the store."""

    code = "duplicate_product"

    def __init__(self, field: str, value: str, product_id: object) -> None:
        self.field, self.value, self.product_id = field, value, product_id
        super().__init__(f"store already has a product with {field}={value!r} (id {product_id})")


class UnobservedField(AutolistError):
    """A store field whose write shape was never observed (CLAUDE.md rule 6)."""

    code = "unobserved_field"


class VerifyMismatch(AutolistError):
    """The created product does not match the draft. It stays hidden and disabled."""

    code = "verify_mismatch"

    def __init__(self, product_id: int, problems: list[str]) -> None:
        self.product_id, self.problems = product_id, problems
        super().__init__(f"product {product_id} differs from the draft: {'; '.join(problems)}")
