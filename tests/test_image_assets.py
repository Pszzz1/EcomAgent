from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from backend.app.infrastructure.persistence import ImageAssetStore


def _image_bytes(image_format: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), "white").save(buffer, format=image_format)
    return buffer.getvalue()


def test_image_assets_store_metadata_and_delete_task_files(tmp_path: Path) -> None:
    store = ImageAssetStore(tmp_path / "runs.sqlite3", tmp_path / "assets")

    asset = store.save_source(
        "task-1",
        filename="商品图.png",
        mime_type="image/png",
        content=_image_bytes(),
    )

    assert store.get(asset.asset_id) == asset
    assert store.path_for(asset.asset_id).read_bytes() == _image_bytes()

    store.delete_task("task-1")

    assert store.get(asset.asset_id) is None
    assert store.path_for(asset.asset_id) is None
    assert not (tmp_path / "assets" / "task-1").exists()


def test_image_assets_reject_mismatched_content_type(tmp_path: Path) -> None:
    store = ImageAssetStore(tmp_path / "runs.sqlite3", tmp_path / "assets")

    with pytest.raises(ValueError, match="does not match"):
        store.save_source(
            "task-1",
            filename="product.jpg",
            mime_type="image/jpeg",
            content=_image_bytes("PNG"),
        )


def test_image_assets_reject_a_task_path_that_resolves_to_the_asset_root(tmp_path: Path) -> None:
    store = ImageAssetStore(tmp_path / "runs.sqlite3", tmp_path / "assets")

    with pytest.raises(ValueError, match="Invalid task image path"):
        store.save_source(
            ".",
            filename="product.png",
            mime_type="image/png",
            content=_image_bytes(),
        )
