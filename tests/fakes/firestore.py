"""A tiny in-memory stand-in for the parts of the Firestore client the engine uses."""

from __future__ import annotations

import copy
from typing import Any


class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class FakeDocument:
    def __init__(self, store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._store, self._id = store, doc_id

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._id] = copy.deepcopy(data)

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._id, self._store.get(self._id))

    def delete(self) -> None:
        self._store.pop(self._id, None)


class FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(self._store, doc_id)

    def stream(self) -> list[FakeSnapshot]:
        return [FakeSnapshot(k, v) for k, v in sorted(self._store.items())]


class FakeFirestore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.data.setdefault(name, {}))
