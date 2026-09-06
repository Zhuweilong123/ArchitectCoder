"""Browse and archive persisted performance-evaluation JSONL files."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import EvalResult


_VERSION_RE = re.compile(r"-v(?P<version>[0-9][A-Za-z0-9_.-]*)\.jsonl$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _result_roots(eval_root: Path) -> list[Path]:
    """Return canonical and CLI output locations, without duplicate roots."""

    roots = [
        (eval_root / "results").resolve(),
        eval_root.resolve(),
        (_repo_root() / "backend" / "temp" / "evals").resolve(),
    ]
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _result_id(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root()).as_posix()
    except ValueError:
        return path.name


def _load_rows(path: Path) -> list[EvalResult]:
    rows: list[EvalResult] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(EvalResult.model_validate(value))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return rows


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _execution_window(rows: list[EvalResult]) -> tuple[str, str]:
    starts = [parsed for row in rows if (parsed := _parse_time(row.started_at))]
    ends = [
        parsed + timedelta(milliseconds=row.duration_ms)
        for row in rows
        if (parsed := _parse_time(row.started_at))
    ]
    if not starts:
        return "", ""
    return min(starts).isoformat(), (max(ends) if ends else max(starts)).isoformat()


def _version_for(path: Path, rows: list[EvalResult]) -> str:
    versions = {
        str(row.metadata.get("version"))
        for row in rows
        if isinstance(row.metadata, dict) and row.metadata.get("version")
    }
    if len(versions) == 1:
        return versions.pop()
    match = _VERSION_RE.search(path.name)
    if match:
        return match.group("version")
    return ""


def _suite_for(path: Path) -> str:
    stem = path.stem
    if stem.startswith("performance-"):
        return stem.split("-202", 1)[0]
    return "performance"


def _summary(rows: list[EvalResult]) -> dict[str, Any]:
    from .batches import summarize

    return summarize(rows).model_dump(mode="json")


def _archive_sources(eval_root: Path) -> set[str]:
    sources: set[str] = set()
    archive_root = eval_root / "archives"
    if not archive_root.is_dir():
        return sources
    for path in archive_root.glob("archive_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            batch = data.get("batch") or {}
            source = batch.get("source_result_id")
            if source:
                sources.add(str(source))
            note = str(data.get("note") or "")
            sources.update(
                match.group(0).replace("\\", "/")
                for match in re.finditer(r"(?:backend/)?temp/evals/(?:results/)?performance-[A-Za-z0-9_.-]+\.jsonl", note)
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            continue
    return sources


def list_performance_results(eval_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    files: dict[str, Path] = {}
    for root in _result_roots(eval_root):
        if not root.is_dir():
            continue
        for path in root.glob("performance-*.jsonl"):
            files.setdefault(_result_id(path), path)

    archived = _archive_sources(eval_root)
    rows: list[dict[str, Any]] = []
    for result_id, path in files.items():
        results = _load_rows(path)
        started_at, finished_at = _execution_window(results)
        rows.append({
            "result_id": result_id,
            "file_name": path.name,
            "source_path": result_id,
            "version": _version_for(path, results),
            "suite": _suite_for(path),
            "started_at": started_at,
            "finished_at": finished_at,
            "result_count": len(results),
            "summary": _summary(results),
            "archived": result_id in archived or path.name in archived,
        })
    rows.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return rows[: max(1, min(limit, 100))]


def _resolve_result(eval_root: Path, result_id: str) -> tuple[str, Path] | None:
    if not result_id or Path(result_id).is_absolute():
        return None
    candidate = (_repo_root() / Path(result_id)).resolve()
    for root in _result_roots(eval_root):
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and candidate.name.startswith("performance-") and candidate.suffix == ".jsonl":
            return _result_id(candidate), candidate
    return None


def get_performance_result(eval_root: Path, result_id: str) -> dict[str, Any] | None:
    resolved = _resolve_result(eval_root, result_id)
    if resolved is None:
        return None
    canonical_id, path = resolved
    results = _load_rows(path)
    started_at, finished_at = _execution_window(results)
    archived = canonical_id in _archive_sources(eval_root) or path.name in _archive_sources(eval_root)
    return {
        "result_id": canonical_id,
        "file_name": path.name,
        "source_path": canonical_id,
        "version": _version_for(path, results),
        "suite": _suite_for(path),
        "started_at": started_at,
        "finished_at": finished_at,
        "result_count": len(results),
        "summary": _summary(results),
        "archived": archived,
        "results": [row.model_dump(mode="json") for row in results],
    }


def archive_performance_result(
    eval_root: Path,
    result_id: str,
    version: str,
    note: str,
    archive_writer,
) -> dict[str, Any]:
    snapshot = get_performance_result(eval_root, result_id)
    if snapshot is None:
        raise KeyError(result_id)
    if not snapshot["results"]:
        raise ValueError("performance result file contains no valid evaluation results")
    batch = {
        "batch_id": f"import_{Path(result_id).stem}",
        "agent": "devagent",
        "suite": snapshot["suite"],
        "version": version.strip() or snapshot["version"] or "unlabeled",
        "label": f"{version.strip() or snapshot['version'] or 'unlabeled'} {snapshot['suite']} performance",
        "case_ids": [row["case_id"] for row in snapshot["results"]],
        "status": "completed",
        "started_at": snapshot["started_at"],
        "finished_at": snapshot["finished_at"],
        "current_case_id": "",
        "results": snapshot["results"],
        "summary": snapshot["summary"],
        "error": "",
        "source_result_id": snapshot["result_id"],
    }
    return archive_writer(batch, note or f"{batch['version']} {batch['suite']} performance archive")
