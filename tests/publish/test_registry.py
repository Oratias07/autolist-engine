from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from autolist.publish.registry import CreatedProduct, JsonFileRegistry


def test_json_registry_persists_and_marks_deleted(tmp_path: Path) -> None:
    path = tmp_path / "local" / "created_products.json"
    reg = JsonFileRegistry(path)
    reg.add(CreatedProduct(product_id=970, created_at=datetime.now(UTC), job_id="P0-06"))
    assert JsonFileRegistry(path).is_active(970)
    reg.mark_deleted(970, datetime.now(UTC))
    reopened = JsonFileRegistry(path)
    assert not reopened.is_active(970)
    item = reopened.get(970)
    assert item is not None and item.deleted_at is not None
    assert not reopened.is_active(1)
