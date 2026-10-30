"""Registry of products this system created. PUT/DELETE are allowed only for these ids."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class CreatedProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    created_at: datetime
    job_id: str | None
    deleted_at: datetime | None = None


class CreatedProductsRegistry(Protocol):
    def add(self, product: CreatedProduct) -> None: ...
    def is_active(self, product_id: int) -> bool: ...
    def mark_deleted(self, product_id: int, at: datetime) -> None: ...
    def get(self, product_id: int) -> CreatedProduct | None: ...


class InMemoryRegistry:
    def __init__(self) -> None:
        self._items: dict[int, CreatedProduct] = {}
        self._lock = threading.Lock()

    def add(self, product: CreatedProduct) -> None:
        with self._lock:
            self._items[product.product_id] = product

    def is_active(self, product_id: int) -> bool:
        item = self._items.get(product_id)
        return item is not None and item.deleted_at is None

    def mark_deleted(self, product_id: int, at: datetime) -> None:
        with self._lock:
            item = self._items[product_id]
            self._items[product_id] = item.model_copy(update={"deleted_at": at})

    def get(self, product_id: int) -> CreatedProduct | None:
        return self._items.get(product_id)


class JsonFileRegistry(InMemoryRegistry):
    """Phase-0 only: persisted to a local JSON file after every change."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        if path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")):
                item = CreatedProduct.model_validate(raw)
                self._items[item.product_id] = item

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [i.model_dump(mode="json") for i in self._items.values()]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, product: CreatedProduct) -> None:
        super().add(product)
        self._flush()

    def mark_deleted(self, product_id: int, at: datetime) -> None:
        super().mark_deleted(product_id, at)
        self._flush()
