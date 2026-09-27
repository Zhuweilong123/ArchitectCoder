"""Focused checks for durable read-only exploration scheduling."""

from __future__ import annotations

import asyncio

import pytest

from app.services.run_state import RunStatus, RunStore
from extensions.orchestration.architecture_aware import scheduler as scheduling
from extensions.orchestration.architecture_aware.store import (
    ScheduleConflict,
    ScheduleStore,
    WorkAssignment,
)


def test_dynamic_scheduler_rebalances_and_reuses_completed_work(tmp_path, monkeypatch):
    runs = RunStore(tmp_path / "runs.db")
    monkeypatch.setattr(scheduling, "get_run_store", lambda: runs)
    scheduler = scheduling.DynamicExplorationScheduler(max_workers=2, worker_seconds=1)
    items = tuple(
        WorkAssignment(item_id, (item_id,), 1.0, slot, 1)
        for item_id, slot in (("a", 0), ("b", 1), ("c", 0), ("d", 1))
    )
    calls: list[str] = []

    async def explore(package, limit):
        calls.append(package.id)
        await asyncio.sleep(0.2 if package.id == "b" else 0.01)
        return {
            "package": package.id,
            "status": "completed",
            "summary": "evidence",
            "tokens": 100,
            "node_count": 1,
            "estimated_cost": package.cost,
        }

    async def run_once():
        return await scheduler.run(
            root_run_id="root", fingerprint="graph-v1", items=items,
            total_tokens=12000, worker=explore,
        )

    first = asyncio.run(run_once())
    resumed = asyncio.run(run_once())

    assert len(first.results) == len(resumed.results) == 4
    assert first.plan.revision >= 2
    assert first.worker_tokens == 400
    assert resumed.worker_tokens == 0
    assert len(calls) == 4
    assert all(
        runs.get(item.child_run_id).status == RunStatus.SUCCEEDED.value
        for item in first.plan.items
    )


def test_schedule_rejects_stale_assignment_result(tmp_path):
    store = ScheduleStore(tmp_path / "runs.db")
    item = WorkAssignment("a", ("node",), 1.0, 0, 1)
    store.open("schedule", "root", "graph-v1", (item,))
    revised = store.repartition("schedule", 1, {"a": 1}, "idle slot")
    claimed = store.claim("schedule", "a", 1)

    assert revised.revision == claimed.revision == 2
    with pytest.raises(ScheduleConflict):
        store.finish("schedule", "a", claimed.child_run_id,
                     claimed.lease_token, 1, {"status": "completed"})
    assert store.get("schedule").items[0].result is None
