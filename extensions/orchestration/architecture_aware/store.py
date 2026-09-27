"""Durable plan revisions and atomic ownership for graph exploration."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ScheduleConflict(RuntimeError):
    """An assignment changed while a worker was preparing its result."""


@dataclass(frozen=True)
class WorkAssignment:
    id: str
    node_ids: tuple[str, ...]
    cost: float
    slot: int
    revision: int
    child_run_id: str = ""
    lease_token: str = ""
    attempt: int = 0
    claimed_at: float | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class SchedulePlan:
    id: str
    root_run_id: str
    fingerprint: str
    revision: int
    items: tuple[WorkAssignment, ...]


class ScheduleStore:
    """One SQLite authority for plan revisions and unstarted assignments.

    RunStore remains authoritative for child execution status and lease expiry.
    The schedule records accepted evidence and the current child Run reference.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS architecture_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    root_run_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS architecture_work_items (
                    schedule_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    node_ids_json TEXT NOT NULL,
                    cost REAL NOT NULL,
                    slot INTEGER NOT NULL,
                    assignment_revision INTEGER NOT NULL,
                    child_run_id TEXT NOT NULL DEFAULT '',
                    lease_token TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    claimed_at REAL,
                    result_json TEXT,
                    PRIMARY KEY (schedule_id, item_id)
                );
                CREATE INDEX IF NOT EXISTS idx_architecture_items_plan
                    ON architecture_work_items(schedule_id, slot);
                CREATE TABLE IF NOT EXISTS architecture_schedule_events (
                    schedule_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    @staticmethod
    def _read(db: sqlite3.Connection, schedule_id: str) -> SchedulePlan | None:
        header = db.execute(
            "SELECT * FROM architecture_schedules WHERE schedule_id = ?",
            (schedule_id,),
        ).fetchone()
        if header is None:
            return None
        rows = db.execute(
            "SELECT * FROM architecture_work_items WHERE schedule_id = ? ORDER BY item_id",
            (schedule_id,),
        ).fetchall()
        return SchedulePlan(
            id=schedule_id,
            root_run_id=header["root_run_id"],
            fingerprint=header["fingerprint"],
            revision=int(header["revision"]),
            items=tuple(WorkAssignment(
                id=row["item_id"],
                node_ids=tuple(json.loads(row["node_ids_json"])),
                cost=float(row["cost"]),
                slot=int(row["slot"]),
                revision=int(row["assignment_revision"]),
                child_run_id=row["child_run_id"],
                lease_token=row["lease_token"],
                attempt=int(row["attempt"]),
                claimed_at=row["claimed_at"],
                result=json.loads(row["result_json"]) if row["result_json"] else None,
            ) for row in rows),
        )

    def get(self, schedule_id: str) -> SchedulePlan | None:
        with self._connect() as db:
            return self._read(db, schedule_id)

    def open(
        self,
        schedule_id: str,
        root_run_id: str,
        fingerprint: str,
        items: tuple[WorkAssignment, ...],
    ) -> SchedulePlan:
        """Create a plan or reuse a matching snapshot on resumed execution."""

        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db, schedule_id)
            if current is not None:
                if current.fingerprint != fingerprint or current.root_run_id != root_run_id:
                    db.rollback()
                    raise ScheduleConflict("project snapshot changed; a new plan is required")
                db.commit()
                return current
            db.execute(
                "INSERT INTO architecture_schedules VALUES (?, ?, ?, ?, ?, ?)",
                (schedule_id, root_run_id, fingerprint, 1, now, now),
            )
            for item in items:
                db.execute(
                    """INSERT INTO architecture_work_items (
                        schedule_id, item_id, node_ids_json, cost, slot, assignment_revision
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        schedule_id, item.id, json.dumps(item.node_ids),
                        item.cost, item.slot, 1,
                    ),
                )
            db.execute(
                "INSERT INTO architecture_schedule_events VALUES (?, ?, ?, ?, ?)",
                (schedule_id, 1, "plan_created", "{}", now),
            )
            plan = self._read(db, schedule_id)
            db.commit()
        assert plan is not None
        return plan

    def claim(self, schedule_id: str, item_id: str, slot: int) -> WorkAssignment:
        """Reserve one pending item with a unique attempt and lease token."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM architecture_work_items WHERE schedule_id = ? AND item_id = ?",
                (schedule_id, item_id),
            ).fetchone()
            if (row is None or row["child_run_id"] or row["result_json"]
                    or int(row["slot"]) != slot):
                db.rollback()
                raise ScheduleConflict(f"work item {item_id} is already claimed or complete")
            run_id = f"run_{uuid.uuid4().hex[:20]}"
            token = uuid.uuid4().hex
            db.execute(
                """UPDATE architecture_work_items
                   SET child_run_id = ?, lease_token = ?, attempt = attempt + 1,
                       claimed_at = ? WHERE schedule_id = ? AND item_id = ?
                       AND child_run_id = '' AND result_json IS NULL""",
                (run_id, token, time.time(), schedule_id, item_id),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.rollback()
                raise ScheduleConflict(f"work item {item_id} lost its claim race")
            db.execute(
                "INSERT INTO architecture_schedule_events VALUES (?, ?, ?, ?, ?)",
                (schedule_id, row["assignment_revision"], "item_claimed",
                 json.dumps({"item_id": item_id, "slot": slot, "run_id": run_id}),
                 time.time()),
            )
            plan = self._read(db, schedule_id)
            db.commit()
        assert plan is not None
        return next(item for item in plan.items if item.id == item_id)

    def finish(
        self,
        schedule_id: str,
        item_id: str,
        run_id: str,
        lease_token: str,
        assignment_revision: int,
        result: dict[str, Any],
    ) -> None:
        """Accept a result only from the current child and assignment."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE architecture_work_items SET result_json = ?
                   WHERE schedule_id = ? AND item_id = ? AND child_run_id = ?
                     AND lease_token = ? AND assignment_revision = ?
                     AND result_json IS NULL""",
                (json.dumps(result, ensure_ascii=False), schedule_id, item_id,
                 run_id, lease_token, assignment_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.rollback()
                raise ScheduleConflict(f"stale result for work item {item_id}")
            db.execute(
                "INSERT INTO architecture_schedule_events VALUES (?, ?, ?, ?, ?)",
                (schedule_id, assignment_revision, "item_finished",
                 json.dumps({"item_id": item_id, "run_id": run_id,
                             "status": result.get("status")}), time.time()),
            )
            db.commit()

    def requeue(
        self, schedule_id: str, item_id: str, run_id: str,
        lease_token: str, assignment_revision: int,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE architecture_work_items
                   SET child_run_id = '', lease_token = '', claimed_at = NULL
                   WHERE schedule_id = ? AND item_id = ? AND child_run_id = ?
                     AND lease_token = ? AND assignment_revision = ?
                     AND result_json IS NULL""",
                (schedule_id, item_id, run_id, lease_token, assignment_revision),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                db.rollback()
                raise ScheduleConflict(f"cannot requeue stale work item {item_id}")
            db.commit()

    def repartition(
        self, schedule_id: str, expected_revision: int,
        new_slots: dict[str, int], reason: str,
        metrics: dict[str, float] | None = None,
    ) -> SchedulePlan:
        """Atomically change ownership of only pending assignments."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = self._read(db, schedule_id)
            if plan is None or plan.revision != expected_revision:
                db.rollback()
                raise ScheduleConflict("plan revision changed")
            pending = {
                item.id: item for item in plan.items
                if not item.child_run_id and item.result is None
            }
            changed = {
                item_id: slot for item_id, slot in new_slots.items()
                if item_id in pending and slot != pending[item_id].slot
            }
            if set(new_slots) - set(pending):
                db.rollback()
                raise ScheduleConflict("cannot migrate a running or completed item")
            if changed:
                next_revision = plan.revision + 1
                for item_id, slot in changed.items():
                    db.execute(
                        """UPDATE architecture_work_items
                           SET slot = ?, assignment_revision = ?
                           WHERE schedule_id = ? AND item_id = ? AND child_run_id = ''
                             AND result_json IS NULL""",
                        (slot, next_revision, schedule_id, item_id),
                    )
                db.execute(
                    "UPDATE architecture_schedules SET revision = ?, updated_at = ? WHERE schedule_id = ?",
                    (next_revision, time.time(), schedule_id),
                )
                db.execute(
                    "INSERT INTO architecture_schedule_events VALUES (?, ?, ?, ?, ?)",
                    (schedule_id, next_revision, "repartition",
                     json.dumps({"reason": reason, "new_slots": changed,
                                 "metrics": metrics or {}}), time.time()),
                )
            latest = self._read(db, schedule_id)
            db.commit()
        assert latest is not None
        return latest
