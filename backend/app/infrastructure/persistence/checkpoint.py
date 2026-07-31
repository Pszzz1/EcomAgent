from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from types import TracebackType
from typing import Any, Iterator
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver


@dataclass(frozen=True)
class ThreadStateSnapshot:
    thread_id: str
    values: dict[str, Any]
    updated_at: str


class TaskLeaseUnavailable(RuntimeError):
    """Another process is already executing a turn for the same task."""


class SQLiteCheckpointSaver(SqliteSaver):
    """Canonical task state, task discovery, and per-task execution leases."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(sqlite3.connect(str(self.path), check_same_thread=False))
        self.setup()
        self._ensure_lease_schema()

    def latest_thread_states(self) -> list[ThreadStateSnapshot]:
        query = """
            SELECT checkpoint.thread_id, checkpoint.type, checkpoint.checkpoint
            FROM checkpoints AS checkpoint
            JOIN (
                SELECT thread_id, checkpoint_ns, MAX(checkpoint_id) AS checkpoint_id
                FROM checkpoints
                WHERE checkpoint_ns = ''
                GROUP BY thread_id, checkpoint_ns
            ) AS latest
              ON latest.thread_id = checkpoint.thread_id
             AND latest.checkpoint_ns = checkpoint.checkpoint_ns
             AND latest.checkpoint_id = checkpoint.checkpoint_id
            ORDER BY checkpoint.checkpoint_id DESC
        """
        snapshots: list[ThreadStateSnapshot] = []
        with self.cursor(transaction=False) as cursor:
            for thread_id, value_type, value in cursor.execute(query):
                checkpoint = self.serde.loads_typed((value_type, value))
                snapshots.append(
                    ThreadStateSnapshot(
                        thread_id=str(thread_id),
                        values=dict(checkpoint.get("channel_values", {})),
                        updated_at=str(checkpoint.get("ts", "")),
                    )
                )
        return snapshots

    @contextmanager
    def task_lease(self, task_id: str, *, ttl_seconds: int = 900) -> Iterator[str]:
        owner = uuid4().hex
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM task_leases WHERE expires_at <= ?", (now,))
            try:
                connection.execute(
                    "INSERT INTO task_leases (task_id, owner, expires_at) VALUES (?, ?, ?)",
                    (task_id, owner, now + ttl_seconds),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskLeaseUnavailable(
                    f"Release task is already processing another turn: {task_id}"
                ) from exc
        try:
            yield owner
        finally:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM task_leases WHERE task_id = ? AND owner = ?",
                    (task_id, owner),
                )

    def close(self) -> None:
        self.conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _ensure_lease_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_leases (
                    task_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_leases_expiry
                    ON task_leases(expires_at);
                """
            )

    def __enter__(self) -> "SQLiteCheckpointSaver":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
