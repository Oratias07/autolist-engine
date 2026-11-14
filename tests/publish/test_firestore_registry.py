"""The Firestore registry behaves like the in-memory one (infra SPEC acceptance 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from autolist.publish.firestore_registry import FirestoreRegistry
from autolist.publish.registry import CreatedProduct, InMemoryRegistry
from tests.fakes.firestore import FakeFirestore

T0 = datetime(2026, 11, 14, 12, tzinfo=UTC)


@pytest.fixture(params=["memory", "firestore"])
def registry(request: pytest.FixtureRequest) -> Any:
    return InMemoryRegistry() if request.param == "memory" else FirestoreRegistry(FakeFirestore())


def test_add_get_active_delete(registry: Any) -> None:
    assert registry.get(7) is None
    assert not registry.is_active(7)
    registry.add(CreatedProduct(product_id=7, created_at=T0, job_id="job-1"))
    assert registry.is_active(7)
    assert registry.get(7).job_id == "job-1"
    registry.mark_deleted(7, T0)
    assert not registry.is_active(7)
    assert registry.get(7).deleted_at == T0


def test_mark_deleted_unknown_raises(registry: Any) -> None:
    with pytest.raises(KeyError):
        registry.mark_deleted(99, T0)


def test_survives_a_new_instance() -> None:
    fake = FakeFirestore()
    FirestoreRegistry(fake).add(CreatedProduct(product_id=5, created_at=T0, job_id=None))
    assert FirestoreRegistry(fake).is_active(5)
