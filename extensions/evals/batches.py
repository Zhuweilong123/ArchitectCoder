"""Asynchronous evaluation batches, summaries, and immutable snapshots."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.config import evaluation_root
from app.agent_base.core.evals import EvalArchiveRequest, EvalBatchRequest

from .models import EvalResult
from .registry import load_cases
from .runner import EvalRunner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eval_root() -> Path:
    return evaluation_root()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


class EvalSummary(BaseModel):
    total: int = 0
    completed: int = 0
    passed: int = 0
    failed: int = 0
    timeout: int = 0
    budget_exceeded: int = 0
    budget_finalized: int = 0
    errors: int = 0
    pass_rate: float = 0.0
    average_score: float = 0.0
    average_duration_ms: float = 0.0
    total_tokens: int = 0
    total_tool_calls: int = 0


class EvalBatch(BaseModel):
    batch_id: str
    agent: str = "devagent"
    suite: str = ""
    version: str
    label: str = ""
    case_ids: list[str]
    status: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    current_case_id: str = ""
    results: list[EvalResult] = Field(default_factory=list)
    summary: EvalSummary = Field(default_factory=EvalSummary)
    error: str = ""


def summarize(results: list[EvalResult], total: int | None = None) -> EvalSummary:
    completed = len(results)
    passed = sum(
        bool(getattr(item, "passed", item.status == "passed"))
        for item in results
    )
    failed = sum(
        item.status == "failed"
        or (item.status == "budget_finalized" and not bool(getattr(item, "passed", False)))
        for item in results
    )
    timeout = sum(item.status == "timeout" for item in results)
    budget_exceeded = sum(item.status == "budget_exceeded" for item in results)
    budget_finalized = sum(item.status == "budget_finalized" for item in results)
    errors = sum(item.status == "error" for item in results)
    return EvalSummary(
        total=total if total is not None else completed,
        completed=completed,
        passed=passed,
        failed=failed,
        timeout=timeout,
        budget_exceeded=budget_exceeded,
        budget_finalized=budget_finalized,
        errors=errors,
        pass_rate=round(passed / completed, 4) if completed else 0.0,
        average_score=round(sum(item.score for item in results) / completed, 4) if completed else 0.0,
        average_duration_ms=round(sum(item.duration_ms for item in results) / completed, 1) if completed else 0.0,
        total_tokens=sum(item.total_tokens for item in results),
        total_tool_calls=sum(item.tool_calls for item in results),
    )


class EvalBatchManager:
    """Process-local job manager; completed summaries are persisted for trends."""

    def __init__(self) -> None:
        self._batches: dict[str, EvalBatch] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def _active(self) -> bool:
        return any(batch.status in {"queued", "running"} for batch in self._batches.values())

    def get(self, batch_id: str) -> EvalBatch | None:
        batch = self._batches.get(batch_id)
        if batch is not None:
            return batch
        for row in _read_jsonl(_eval_root() / "batches.jsonl"):
            if row.get("batch_id") == batch_id:
                try:
                    return EvalBatch.model_validate(row)
                except Exception:
                    return None
        return None

    def list_batches(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        current = [batch.model_dump(mode="json") for batch in self._batches.values()]
        persisted = _read_jsonl(_eval_root() / "batches.jsonl")
        seen = {item.get("batch_id") for item in current}
        rows = current + [item for item in persisted if item.get("batch_id") not in seen]
        rows.sort(key=lambda item: item.get("started_at") or item.get("created_at") or "", reverse=True)
        return rows[:limit]

    async def start(self, request: EvalBatchRequest) -> EvalBatch:
        if self._active():
            raise RuntimeError("another evaluation batch is already running")
        cases = load_cases()
        if request.case_ids:
            missing = sorted(set(request.case_ids) - set(cases))
            if missing:
                raise ValueError(f"evaluation cases not found: {', '.join(missing)}")
            selected = [cases[case_id] for case_id in request.case_ids]
        elif request.suite:
            selected = [case for case in cases.values() if case.metadata.get("suite") == request.suite]
        else:
            selected = list(cases.values())
        selected.sort(key=lambda case: case.id)
        if not selected:
            raise ValueError("no evaluation cases selected")

        batch = EvalBatch(
            batch_id=f"batch_{uuid.uuid4().hex[:16]}",
            suite=request.suite,
            version=request.version,
            label=request.label,
            case_ids=[case.id for case in selected],
            summary=EvalSummary(total=len(selected)),
        )
        self._batches[batch.batch_id] = batch
        self._tasks[batch.batch_id] = asyncio.create_task(self._run(batch, selected))
        return batch

    async def _run(self, batch: EvalBatch, cases: list[Any]) -> None:
        batch.status = "running"
        batch.started_at = _now()
        try:
            runner = EvalRunner()
            for case in cases:
                batch.current_case_id = case.id
                result = await runner.run_case(case)
                result.metadata.setdefault("batch_id", batch.batch_id)
                result.metadata.setdefault("version", batch.version)
                batch.results.append(result)
                batch.summary = summarize(batch.results, len(cases))
            batch.status = "completed"
        except asyncio.CancelledError:
            batch.status = "cancelled"
            raise
        except Exception as exc:
            batch.status = "failed"
            batch.error = f"{type(exc).__name__}: {exc}"
        finally:
            batch.current_case_id = ""
            batch.finished_at = _now()
            batch.summary = summarize(batch.results, len(cases))
            self._persist_batch(batch)
            self._tasks.pop(batch.batch_id, None)

    @staticmethod
    def _persist_batch(batch: EvalBatch) -> None:
        root = _eval_root()
        root.mkdir(parents=True, exist_ok=True)
        with (root / "batches.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(batch.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def trends(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.list_batches(limit=1000)
        trends = []
        for row in rows:
            summary = row.get("summary") or {}
            trends.append({
                "batch_id": row.get("batch_id", ""),
                "agent": row.get("agent", "devagent"),
                "version": row.get("version", ""),
                "label": row.get("label", ""),
                "suite": row.get("suite", ""),
                "status": row.get("status", ""),
                "started_at": row.get("started_at", ""),
                "finished_at": row.get("finished_at", ""),
                "summary": summary,
            })
        return trends[:max(1, min(limit, 100))]

    def archive(self, request: EvalArchiveRequest) -> dict[str, Any]:
        batch = self.get(request.batch_id)
        if batch is None:
            batch = self.get(request.batch_id)
        if batch is None:
            raise KeyError(request.batch_id)
        if batch.status in {"queued", "running"}:
            raise ValueError("evaluation batch is still running")

        return self._write_archive(batch.model_dump(mode="json"), request.note)

    def archive_baseline(self, snapshot: dict[str, Any], note: str = "") -> dict[str, Any]:
        """Archive the tracked DevAgent baseline as a completed snapshot."""
        case_count = int(snapshot.get("case_count") or 0)
        if case_count <= 0:
            raise ValueError("evaluation baseline has no cases")
        batch = {
            "batch_id": f"baseline_{uuid.uuid4().hex[:16]}",
            "agent": snapshot.get("agent", "devagent"),
            "suite": "baseline",
            "version": snapshot.get("version", "baseline"),
            "label": snapshot.get("label", "DevAgent baseline"),
            "case_ids": [],
            "status": "completed",
            "started_at": snapshot.get("captured_at", ""),
            "finished_at": snapshot.get("captured_at", ""),
            "results": [],
            "summary": {
                "total": case_count,
                "completed": case_count,
                "passed": int(snapshot.get("passed") or 0),
                "failed": int(snapshot.get("failed") or 0),
                "timeout": int(snapshot.get("timeout") or 0),
                "errors": 0,
                "pass_rate": float(snapshot.get("pass_rate") or 0),
                "average_score": float(snapshot.get("average_score") or 0),
                "average_duration_ms": round(float(snapshot.get("total_duration_ms") or 0) / case_count, 1),
                "total_tokens": int(snapshot.get("total_tokens") or 0),
                "total_tool_calls": int(snapshot.get("total_tool_calls") or 0),
            },
        }
        return self._write_archive(batch, note)

    @staticmethod
    def _write_archive(batch: dict[str, Any], note: str) -> dict[str, Any]:
        archive_id = f"archive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        archive = {
            "archive_id": archive_id,
            "created_at": _now(),
            "note": note,
            "batch": batch,
        }
        root = _eval_root() / "archives"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{archive_id}.json"
        path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"archive_id": archive_id, "created_at": archive["created_at"], "path": str(path), "batch_id": batch.get("batch_id", "")}

    def list_archives(self, limit: int = 20) -> list[dict[str, Any]]:
        root = _eval_root() / "archives"
        if not root.is_dir():
            return []
        rows = []
        for path in sorted(root.glob("archive_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                batch = data.get("batch") or {}
                rows.append({
                    "archive_id": data.get("archive_id", path.stem),
                    "created_at": data.get("created_at", ""),
                    "note": data.get("note", ""),
                    "batch_id": batch.get("batch_id", ""),
                    "agent": batch.get("agent", "devagent"),
                    "version": batch.get("version", ""),
                    "suite": batch.get("suite", ""),
                    "started_at": batch.get("started_at", ""),
                    "finished_at": batch.get("finished_at", ""),
                    "summary": batch.get("summary", {}),
                })
            except (OSError, json.JSONDecodeError):
                continue
        return rows[:max(1, min(limit, 100))]


_manager: EvalBatchManager | None = None


def get_batch_manager() -> EvalBatchManager:
    global _manager
    if _manager is None:
        _manager = EvalBatchManager()
    return _manager
