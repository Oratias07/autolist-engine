"""Registry of created products in Firestore: one document per product id."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from autolist.publish.registry import CreatedProduct


class FirestoreRegistry:
    def __init__(self, client: Any, collection: str = "created_products") -> None:
        self._col = client.collection(collection)

    def add(self, product: CreatedProduct) -> None:
        self._col.document(str(product.product_id)).set(product.model_dump(mode="json"))

    def get(self, product_id: int) -> CreatedProduct | None:
        snap = self._col.document(str(int(product_id))).get()
        if not snap.exists:
            return None
        return CreatedProduct.model_validate(snap.to_dict())

    def is_active(self, product_id: int) -> bool:
        item = self.get(product_id)
        return item is not None and item.deleted_at is None

    def mark_deleted(self, product_id: int, at: datetime) -> None:
        item = self.get(product_id)
        if item is None:
            raise KeyError(product_id)
        self.add(item.model_copy(update={"deleted_at": at}))
