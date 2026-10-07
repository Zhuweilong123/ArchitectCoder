"""Durable, bounded scheduling of read-only graph exploration work."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.run_state import RunConflict, RunStatus, get_run_store
from app.agent_base.host_api.errors import AgentInterrupted

from .impact import ImpactSlice
from .partition import ExplorationPackage, PartitionDecision, _unit_key
from .store import ScheduleConflict, SchedulePlan, ScheduleStore, WorkAssignment

logger = logging.getLogger(__name__)
PER_ITEM_TOKEN_LIMIT = 131072


def work_item_limit(total_tokens: int, partitions: int) -> int:
    return max(partitions, min(4, total_tokens // PER_ITEM_TOKEN_LIMIT))


def make_work_items(
    impact: ImpactSlice, decision: PartitionDecision, *, max_items: int | None = None,
) -> tuple[WorkAssignment, ...]:
    """Split partitions only when the shared budget can fund useful later waves."""
    items: list[WorkAssignment] = []
    split = max_items is None or max_items >= 2 * len(decision.packages)
    for slot, package in enumerate(decision.packages):
        units: dict[str, list[str]] = {}
        for node_id in package.node_ids:
            units.setdefault(_unit_key(node_id, impact.nodes[node_id]), []).append(node_id)
        groups: list[list[str]] = [[] for _ in range(min(2 if split else 1, len(units)))]
        loads = [0.0 for _ in groups]
        ordered = sorted(units.values(), key=lambda ids: -sum(decision.node_costs[i].score for i in ids))
        for ids in ordered:
            position = min(range(len(groups)), key=lambda i: loads[i])
            groups[position].extend(ids)
            loads[position] += sum(decision.node_costs[i].score for i in ids)
        total = sum(loads) or 1.0
        for index, ids in enumerate(groups, 1):
            if ids:
                items.append(WorkAssignment(
                    id=f"{package.id}_{index}", node_ids=tuple(sorted(ids)),
                    cost=max(0.1, package.cost * loads[index - 1] / total),
                    slot=slot, revision=1,
                ))
    return tuple(items)


def graph_fingerprint(
    impact: ImpactSlice, request_text: str, project_file: str,
    source_dir: str = "", test_dir: str = "",
) -> str:
    """Bind reusable evidence to the request, graph slice, and project file."""
    project = Path(project_file)
    try:
        stat = project.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        stamp = None
    file_stamps = []
    for node in impact.nodes.values():
        location = str(node.get("file") or node.get("path") or "").strip()
        if not location:
            continue
        path = Path(location)
        choices = (path,) if path.is_absolute() else (
            Path(source_dir) / path, Path(test_dir) / path, project.parent / path,
        )
        for choice in choices:
            try:
                info = choice.stat()
                file_stamps.append((str(choice.resolve()), info.st_size, info.st_mtime_ns))
                break
            except OSError:
                continue
    # Evidence reports now use a structured, read-range-backed contract.
    # Do not resume old free-form worker results under the new contract.
    payload = ("architecture-schedule-v5", request_text,
               str(project.resolve()), stamp, impact.project_id,
               impact.nodes, impact.edges, sorted(set(file_stamps)))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class ScheduleOutcome:
    results: tuple[dict[str, Any], ...]
    worker_tokens: int
    plan: SchedulePlan


def item_token_budget(item: WorkAssignment, total_tokens: int,
                      items: tuple[WorkAssignment, ...]) -> int:
    """Give each explorer the same ceiling within the shared reservation."""
    return min(PER_ITEM_TOKEN_LIMIT, max(0, total_tokens // max(1, len(items))))


class DynamicExplorationScheduler:
    """Use RunStore leases for execution and CAS plan revisions for ownership."""

    def __init__(self, *, max_workers: int = 2, worker_seconds: float = 90.0):
        self.runs = get_run_store()
        self.store = ScheduleStore(self.runs.db_path)
        self.max_workers = max(1, min(2, max_workers))
        self.worker_seconds = min(180.0, max(1.0, worker_seconds))

    async def _heartbeat(self, run_id: str, owner: str) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                self.runs.heartbeat(run_id, owner, lease_seconds=self.worker_seconds + 30)
            except RunConflict:
                return

    async def _execute(
        self, schedule_id: str, item: WorkAssignment, limit: int,
        worker: Callable[[ExplorationPackage, int], Awaitable[dict[str, Any]]],
        root_run_id: str,
    ) -> dict[str, Any]:
        owner = f"architecture:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        run = self.runs.get(item.child_run_id)
        if run is None:
            run = self.runs.create(
                kind="architecture_exploration", run_id=item.child_run_id,
                metadata={"parent_run_id": root_run_id, "schedule_id": schedule_id,
                          "item_id": item.id, "assignment_revision": item.revision},
            )
        self.runs.claim(run.run_id, owner, lease_seconds=self.worker_seconds + 30)
        heartbeat = asyncio.create_task(self._heartbeat(run.run_id, owner))
        started = time.monotonic()
        try:
            package = ExplorationPackage(item.id, item.node_ids, item.cost)
            result = await worker(package, limit)
            result["seconds"] = round(time.monotonic() - started, 3)
            result["slot"] = item.slot
            status = {
                "completed": RunStatus.SUCCEEDED,
                "partial": RunStatus.PARTIAL,
            }.get(result.get("status"), RunStatus.FAILED)
            self.runs.transition(run.run_id, status, expected={RunStatus.RUNNING},
                                 owner_id=owner, metadata_patch={"result": result})
            self.store.finish(schedule_id, item.id, run.run_id, item.lease_token,
                              item.revision, result)
            return result
        except (asyncio.CancelledError, AgentInterrupted):
            try:
                self.runs.transition(run.run_id, RunStatus.CANCELED,
                                     expected={RunStatus.RUNNING}, owner_id=owner)
            except RunConflict:
                pass
            raise
        except Exception as exc:
            logger.warning("[ArchitectureScheduling] work %s failed: %s", item.id, exc)
            result = {"package": item.id, "status": "failed", "summary": "",
                      "tokens": 0, "node_count": len(item.node_ids),
                      "estimated_cost": item.cost,
                      "seconds": round(time.monotonic() - started, 3), "slot": item.slot}
            try:
                self.runs.transition(run.run_id, RunStatus.FAILED,
                                     expected={RunStatus.RUNNING}, owner_id=owner,
                                     error=type(exc).__name__, metadata_patch={"result": result})
                self.store.finish(schedule_id, item.id, run.run_id, item.lease_token,
                                  item.revision, result)
            except (RunConflict, ScheduleConflict):
                pass
            return result
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    def _recover(self, plan: SchedulePlan) -> SchedulePlan:
        now = time.time()
        for item in plan.items:
            if item.result is not None or not item.child_run_id:
                continue
            run = self.runs.get(item.child_run_id)
            if run is None:
                if now - (item.claimed_at or now) < 5:
                    continue
            elif run.status == RunStatus.RUNNING.value:
                if run.lease_expires_at is None or run.lease_expires_at > now:
                    continue
                try:
                    self.runs.transition(run.run_id, RunStatus.ORPHANED,
                                         expected={RunStatus.RUNNING},
                                         error="exploration worker lease expired")
                except RunConflict:
                    continue
            elif run.status == RunStatus.QUEUED.value:
                continue
            if run is not None and isinstance(run.metadata.get("result"), dict):
                try:
                    self.store.finish(plan.id, item.id, item.child_run_id,
                                      item.lease_token, item.revision,
                                      run.metadata["result"])
                except ScheduleConflict:
                    pass
                continue
            if item.attempt >= 2:
                exhausted = {"package": item.id, "status": "failed",
                             "summary": "Exploration recovery attempts exhausted",
                             "tokens": 0, "node_count": len(item.node_ids),
                             "estimated_cost": item.cost, "seconds": 0.0,
                             "slot": item.slot}
                try:
                    self.store.finish(plan.id, item.id, item.child_run_id,
                                      item.lease_token, item.revision, exhausted)
                except ScheduleConflict:
                    pass
                continue
            try:
                self.store.requeue(plan.id, item.id, item.child_run_id,
                                   item.lease_token, item.revision)
            except ScheduleConflict:
                pass
        return self.store.get(plan.id) or plan

    def _rebalance(self, plan: SchedulePlan, active_items: tuple[WorkAssignment, ...]) -> SchedulePlan:
        pending = [item for item in plan.items if not item.child_run_id and item.result is None]
        if not pending or self.max_workers < 2:
            return plan
        speeds = [1.0, 1.0]
        for slot in range(self.max_workers):
            observed = [item.result for item in plan.items if item.slot == slot
                        and item.result and item.result.get("seconds", 0) > 0]
            if observed:
                speeds[slot] = sum(float(item["estimated_cost"]) for item in observed) / sum(
                    float(item["seconds"]) for item in observed)
        active_slots = {item.slot for item in active_items}
        loads = [sum(item.cost for item in (*pending, *active_items)
                     if item.slot == slot) / speeds[slot]
                 for slot in range(self.max_workers)]
        current = max(loads)
        best: tuple[float, WorkAssignment, int] | None = None
        for item in pending:
            target = 1 - item.slot
            trial = loads.copy()
            trial[item.slot] -= item.cost / speeds[item.slot]
            trial[target] += item.cost / speeds[target]
            worst = max(trial)
            # An idle slot can accept pending work promptly; a running slot
            # may not, so only move toward an idle slot.
            if target not in active_slots and worst < current * 0.9:
                if best is None or worst < best[0]:
                    best = (worst, item, target)
        if best is None:
            return plan
        try:
            return self.store.repartition(plan.id, plan.revision,
                                          {best[1].id: best[2]},
                                          "observed exploration cost and idle capacity",
                                          metrics={"migrated_cost": best[1].cost,
                                                   "predicted_seconds_saved": current - best[0]})
        except ScheduleConflict:
            return self.store.get(plan.id) or plan

    async def run(
        self, *, root_run_id: str, fingerprint: str,
        items: tuple[WorkAssignment, ...], total_tokens: int,
        worker: Callable[[ExplorationPackage, int], Awaitable[dict[str, Any]]],
    ) -> ScheduleOutcome:
        schedule_id = "architecture_" + hashlib.sha256(
            f"{root_run_id}:{fingerprint}".encode()).hexdigest()[:24]
        plan = self.store.open(schedule_id, root_run_id, fingerprint, items)
        active: dict[asyncio.Task[dict[str, Any]], WorkAssignment] = {}
        new_worker_tokens = 0
        try:
            while True:
                plan = self._recover(self.store.get(schedule_id) or plan)
                completed = [item for item in plan.items if item.result is not None]
                pending = [item for item in plan.items if item.result is None]
                if not pending:
                    break
                spent = sum(int(item.result.get("tokens") or 0) for item in completed)
                reserved = sum(item_token_budget(item, total_tokens, plan.items)
                               for item in active.values())
                external = []
                own_runs = {item.child_run_id for item in active.values()}
                for item in plan.items:
                    if not item.child_run_id or item.result is not None or item.child_run_id in own_runs:
                        continue
                    run = self.runs.get(item.child_run_id)
                    if run and run.status == RunStatus.RUNNING.value and (
                        run.lease_expires_at or 0) > time.time():
                        external.append(item)
                active_slots = {item.slot for item in (*active.values(), *external)}
                reserved += sum(item_token_budget(item, total_tokens, plan.items)
                                for item in external)
                if active:
                    plan = self._rebalance(plan, tuple(active.values()) + tuple(external))
                for slot in range(self.max_workers):
                    if slot in active_slots:
                        continue
                    candidates = []
                    for item in plan.items:
                        if item.slot != slot or item.result is not None:
                            continue
                        run = self.runs.get(item.child_run_id) if item.child_run_id else None
                        if not item.child_run_id or (run and run.status == RunStatus.QUEUED.value):
                            candidates.append(item)
                    if not candidates:
                        continue
                    item = candidates[0]
                    limit = item_token_budget(item, total_tokens, plan.items)
                    if spent + reserved + limit > total_tokens:
                        continue
                    try:
                        claimed = item if item.child_run_id else self.store.claim(schedule_id, item.id, slot)
                    except ScheduleConflict:
                        continue
                    task = asyncio.create_task(self._execute(schedule_id, claimed, limit,
                                                             worker, root_run_id))
                    active[task] = claimed
                    active_slots.add(slot)
                    reserved += limit
                if not active:
                    # An earlier invocation may still own a live child lease.
                    # Do not duplicate its work or wait indefinitely.
                    await asyncio.sleep(1)
                    plan = self.store.get(schedule_id) or plan
                    live = [item for item in plan.items if item.child_run_id and item.result is None
                            and (run := self.runs.get(item.child_run_id)) is not None
                            and run.status == RunStatus.RUNNING.value
                            and (run.lease_expires_at or 0) > time.time()]
                    live.extend(item for item in plan.items if item.child_run_id
                                and item.result is None and self.runs.get(item.child_run_id) is None
                                and time.time() - (item.claimed_at or 0) < 5)
                    if not live:
                        break
                    continue
                finished, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    active.pop(task)
                    try:
                        result = await task
                        new_worker_tokens += max(0, int(result.get("tokens") or 0))
                    except (asyncio.CancelledError, AgentInterrupted):
                        raise
                    except RunConflict:
                        # A resumed invocation may win the same queued child Run.
                        pass
        finally:
            if active:
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
        plan = self.store.get(schedule_id) or plan
        results = tuple(item.result for item in plan.items if item.result is not None)
        return ScheduleOutcome(results, new_worker_tokens, plan)
