"""Small durable state store for agent and evaluation runs.

The harness needs a durable record of what is running independently from the
transport that streams progress.  This module intentionally keeps the scope
small: SQLite persistence, an explicit state machine, and a worker lease.  It
does not execute jobs or contain application-specific orchestration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    ORPHANED = "orphaned"


TERMINAL_STATUSES = frozenset({
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.TIMED_OUT.value,
    RunStatus.CANCELED.value,
})

_TRANSITIONS: dict[str, frozenset[str]] = {
    RunStatus.QUEUED.value: frozenset({RunStatus.RUNNING.value, RunStatus.CANCELED.value}),
    RunStatus.RUNNING.value: frozenset({
        RunStatus.WAITING_APPROVAL.value,
        RunStatus.PAUSED.value,
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.TIMED_OUT.value,
        RunStatus.CANCELED.value,
        RunStatus.ORPHANED.value,
    }),
    RunStatus.WAITING_APPROVAL.value: frozenset({
        RunStatus.RUNNING.value,
        RunStatus.PAUSED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELED.value,
        RunStatus.ORPHANED.value,
    }),
    RunStatus.PAUSED.value: frozenset({RunStatus.RUNNING.value, RunStatus.CANCELED.value}),
    RunStatus.ORPHANED.value: frozenset({RunStatus.QUEUED.value, RunStatus.CANCELED.value}),
}


class RunStateError(RuntimeError):
    """Base error for invalid run state operations."""


class RunNotFound(RunStateError):
    pass


class RunConflict(RunStateError):
    pass


class InvalidRunTransition(RunStateError):
    pass


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    kind: str
    status: str
    session_id: str
    idempotency_key: str
    owner_id: str
    attempt: int
    version: int
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str
    heartbeat_at: float | None
    lease_expires_at: float | None
    error: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_run_state_path() -> Path:
    """Return the application data path without creating directories."""
    from app.core.config import get_settings

    return Path(get_settings().uml_dir).resolve().parent / "data" / "runs.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """SQLite-backed run state with atomic transitions and worker leases."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_run_state_path()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.db_path.parent:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        session_id TEXT NOT NULL DEFAULT '',
                        idempotency_key TEXT UNIQUE,
                        owner_id TEXT NOT NULL DEFAULT '',
                        attempt INTEGER NOT NULL DEFAULT 0,
                        version INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT NOT NULL DEFAULT '',
                        finished_at TEXT NOT NULL DEFAULT '',
                        heartbeat_at REAL,
                        lease_expires_at REAL,
                        error TEXT NOT NULL DEFAULT '',
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_updated_at ON runs(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_runs_session_id ON runs(session_id, updated_at DESC);
                    """
                )
            self._initialized = True

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> RunRecord | None:
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return RunRecord(
            run_id=row["run_id"], kind=row["kind"], status=row["status"],
            session_id=row["session_id"], idempotency_key=row["idempotency_key"] or "",
            owner_id=row["owner_id"], attempt=int(row["attempt"]), version=int(row["version"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            heartbeat_at=row["heartbeat_at"], lease_expires_at=row["lease_expires_at"],
            error=row["error"], metadata=metadata if isinstance(metadata, dict) else {},
        )

    def create(
        self,
        *,
        kind: str,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        idempotency_key: str = "",
    ) -> RunRecord:
        if not kind.strip():
            raise ValueError("run kind is required")
        now = _now()
        run_id = run_id or f"run_{uuid.uuid4().hex[:20]}"
        payload = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return self._decode(existing)  # type: ignore[return-value]
            try:
                connection.execute(
                    """INSERT INTO runs (
                        run_id, kind, status, session_id, idempotency_key,
                        created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id, kind, RunStatus.QUEUED.value, session_id,
                        idempotency_key or None, now, now, payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RunConflict(f"run id already exists: {run_id}") from exc
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            connection.commit()
        return self._decode(row)  # type: ignore[return-value]

    def get(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._decode(row)

    def list(self, *, limit: int = 50, session_id: str = "") -> list[RunRecord]:
        limit = max(1, min(limit, 500))
        query = "SELECT * FROM runs"
        params: tuple[Any, ...] = ()
        if session_id:
            query += " WHERE session_id = ?"
            params = (session_id,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params += (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [record for row in rows if (record := self._decode(row)) is not None]

    def transition(
        self,
        run_id: str,
        status: str | RunStatus,
        *,
        expected: Iterable[str | RunStatus] | None = None,
        owner_id: str = "",
        error: str = "",
        metadata_patch: dict[str, Any] | None = None,
    ) -> RunRecord:
        target = status.value if isinstance(status, RunStatus) else status
        expected_values = {
            item.value if isinstance(item, RunStatus) else item for item in expected or ()
        }
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            current = self._decode(row)
            if current is None:
                connection.rollback()
                raise RunNotFound(run_id)
            if expected_values and current.status not in expected_values:
                connection.rollback()
                raise RunConflict(
                    f"run {run_id} is {current.status}, expected one of {sorted(expected_values)}"
                )
            if owner_id and current.owner_id != owner_id:
                connection.rollback()
                raise RunConflict(f"run {run_id} is owned by another worker")
            if target != current.status and target not in _TRANSITIONS.get(current.status, frozenset()):
                connection.rollback()
                raise InvalidRunTransition(f"cannot transition {current.status} -> {target}")
            metadata = dict(current.metadata)
            if metadata_patch:
                metadata.update(metadata_patch)
            started_at = current.started_at or (now if target == RunStatus.RUNNING.value else "")
            finished_at = current.finished_at
            if target in TERMINAL_STATUSES:
                finished_at = now
            next_owner = current.owner_id if target in {
                RunStatus.RUNNING.value,
                RunStatus.WAITING_APPROVAL.value,
            } else ""
            heartbeat_at = current.heartbeat_at if target == RunStatus.RUNNING.value else None
            lease_expires_at = current.lease_expires_at if target == RunStatus.RUNNING.value else None
            connection.execute(
                """UPDATE runs SET status = ?, owner_id = ?, version = ?, updated_at = ?,
                    started_at = ?, finished_at = ?, heartbeat_at = ?, lease_expires_at = ?,
                    error = ?, metadata_json = ?
                    WHERE run_id = ? AND version = ?""",
                (
                    target, next_owner, current.version + 1, now, started_at, finished_at,
                    heartbeat_at, lease_expires_at, error or current.error,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    run_id, current.version,
                ),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            connection.commit()
        return self._decode(updated)  # type: ignore[return-value]

    def claim(self, run_id: str, owner_id: str, *, lease_seconds: float = 60.0) -> RunRecord:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        current = self.get(run_id)
        if current is None:
            raise RunNotFound(run_id)
        if current.status != RunStatus.QUEUED.value:
            raise RunConflict(f"run {run_id} is not queued")
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            record = self._decode(row)
            if record is None:
                connection.rollback()
                raise RunNotFound(run_id)
            if record.status != RunStatus.QUEUED.value:
                connection.rollback()
                raise RunConflict(f"run {run_id} is not queued")
            stamp = _now()
            connection.execute(
                """UPDATE runs SET status = ?, owner_id = ?, attempt = ?, version = ?,
                    updated_at = ?, started_at = ?, heartbeat_at = ?, lease_expires_at = ?
                    WHERE run_id = ? AND version = ?""",
                (
                    RunStatus.RUNNING.value, owner_id, record.attempt + 1, record.version + 1,
                    stamp, record.started_at or stamp, now, now + max(1.0, lease_seconds),
                    run_id, record.version,
                ),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            connection.commit()
        return self._decode(updated)  # type: ignore[return-value]

    def heartbeat(self, run_id: str, owner_id: str, *, lease_seconds: float = 60.0) -> RunRecord:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            record = self._decode(row)
            if record is None:
                connection.rollback()
                raise RunNotFound(run_id)
            if record.status != RunStatus.RUNNING.value or record.owner_id != owner_id:
                connection.rollback()
                raise RunConflict(f"run {run_id} is not owned by {owner_id}")
            connection.execute(
                "UPDATE runs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?, version = ? WHERE run_id = ? AND version = ?",
                (now, now + max(1.0, lease_seconds), _now(), record.version + 1, run_id, record.version),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            connection.commit()
        return self._decode(updated)  # type: ignore[return-value]

    def recover_expired(self, *, now: float | None = None) -> list[RunRecord]:
        cutoff = time.time() if now is None else now
        recovered: list[RunRecord] = []
        for record in self.list(limit=500):
            if (
                record.status == RunStatus.RUNNING.value
                and record.lease_expires_at is not None
                and record.lease_expires_at <= cutoff
            ):
                try:
                    recovered.append(self.transition(
                        record.run_id,
                        RunStatus.ORPHANED,
                        expected={RunStatus.RUNNING},
                        error="worker lease expired; explicit resume is required",
                    ))
                except RunStateError:
                    continue
        return recovered

    def resume(self, run_id: str) -> RunRecord:
        return self.transition(run_id, RunStatus.QUEUED, expected={RunStatus.ORPHANED})


_default_store: RunStore | None = None
_default_store_lock = threading.Lock()


def get_run_store() -> RunStore:
    """Return the process-wide store facade; SQLite remains the source of truth."""
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = RunStore()
    return _default_store
