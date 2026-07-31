from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from PIL import Image


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class ImageAsset:
    asset_id: str
    task_id: str
    kind: str
    relative_path: str
    mime_type: str
    width: int
    height: int
    metadata: dict[str, Any]


class ImageAssetStore:
    """Stores image bytes on disk and queryable metadata in SQLite."""

    def __init__(self, db_path: Path | str, root: Path | str) -> None:
        self.db_path = Path(db_path)
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save_source(
        self,
        task_id: str,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> ImageAsset:
        return self._save(
            task_id,
            kind="source",
            mime_type=mime_type,
            content=content,
            metadata={"original_filename": Path(filename).name},
        )

    def save_generated(
        self,
        task_id: str,
        *,
        content: bytes,
        mime_type: str,
        metadata: dict[str, Any],
    ) -> ImageAsset:
        return self._save(
            task_id,
            kind="promotion_image",
            mime_type=mime_type,
            content=content,
            metadata=metadata,
        )

    def get(self, asset_id: str) -> ImageAsset | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT asset_id, task_id, kind, relative_path, mime_type,
                       width, height, metadata
                FROM image_assets WHERE asset_id = ?
                """,
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        return ImageAsset(
            asset_id=str(row["asset_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            relative_path=str(row["relative_path"]),
            mime_type=str(row["mime_type"]),
            width=int(row["width"]),
            height=int(row["height"]),
            metadata=json.loads(str(row["metadata"])),
        )

    def path_for(self, asset_id: str) -> Path | None:
        asset = self.get(asset_id)
        if asset is None:
            return None
        path = (self.root / asset.relative_path).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            return None
        return path

    def delete_task(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM image_assets WHERE task_id = ?", (task_id,))
        task_directory = (self.root / task_id).resolve()
        if (
            task_directory != self.root
            and task_directory.is_relative_to(self.root)
            and task_directory.is_dir()
        ):
            shutil.rmtree(task_directory)

    def delete_assets(self, asset_ids: list[str]) -> None:
        normalized = list(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        if not normalized:
            return
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT asset_id, relative_path FROM image_assets WHERE asset_id IN ({placeholders})",
                normalized,
            ).fetchall()
            connection.execute(
                f"DELETE FROM image_assets WHERE asset_id IN ({placeholders})",
                normalized,
            )
        for row in rows:
            path = (self.root / str(row["relative_path"])).resolve()
            if path.is_relative_to(self.root) and path.is_file():
                path.unlink()

    def _save(
        self,
        task_id: str,
        *,
        kind: str,
        mime_type: str,
        content: bytes,
        metadata: dict[str, Any],
    ) -> ImageAsset:
        normalized_type = mime_type.lower().strip()
        if normalized_type not in ALLOWED_IMAGE_TYPES:
            raise ValueError("Only JPG, PNG, and WebP images are supported.")
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError("Image must be non-empty and no larger than 15 MB.")
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                actual_format = str(image.format or "").lower()
        except Exception as exc:
            raise ValueError("Uploaded file is not a valid image.") from exc
        expected_formats = {
            "image/jpeg": "jpeg",
            "image/png": "png",
            "image/webp": "webp",
        }
        if actual_format != expected_formats[normalized_type]:
            raise ValueError("Image content does not match its declared file type.")

        asset_id = f"img-{uuid4().hex}"
        relative_path = Path(task_id) / f"{asset_id}{ALLOWED_IMAGE_TYPES[normalized_type]}"
        absolute_path = (self.root / relative_path).resolve()
        if absolute_path.parent == self.root or not absolute_path.is_relative_to(self.root):
            raise ValueError("Invalid task image path.")
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(content)

        asset = ImageAsset(
            asset_id=asset_id,
            task_id=task_id,
            kind=kind,
            relative_path=relative_path.as_posix(),
            mime_type=normalized_type,
            width=width,
            height=height,
            metadata=dict(metadata),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO image_assets (
                    asset_id, task_id, kind, relative_path, mime_type,
                    width, height, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.asset_id,
                    asset.task_id,
                    asset.kind,
                    asset.relative_path,
                    asset.mime_type,
                    asset.width,
                    asset.height,
                    json.dumps(asset.metadata, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
        return asset

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS image_assets (
                    asset_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    mime_type TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_image_assets_task
                    ON image_assets(task_id, created_at);
                """
            )
