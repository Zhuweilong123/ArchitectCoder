"""Bounded file coordinates and freshness checks for a selected graph slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .impact import ImpactSlice


def resolve_node_file(
    node: dict[str, Any], *, project_file: str = "",
    source_dir: str = "", test_dir: str = "",
) -> Path | None:
    """Resolve a graph coordinate only inside configured source/test roots."""
    location = str(node.get("file") or node.get("path") or "").strip()
    if not location:
        return None
    roots = tuple(
        Path(value).resolve() for value in (source_dir, test_dir) if value
    )
    if not roots:
        return None
    raw = Path(location)
    project = Path(project_file).resolve() if project_file else None
    candidates = (raw,) if raw.is_absolute() else (
        *(root / raw for root in roots),
        *((project.parent / raw, project.parent.parent / raw) if project else ()),
    )
    missing: Path | None = None
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except (OSError, ValueError):
            continue
        if any(path.is_relative_to(root) for root in roots):
            if path.is_file():
                return path
            missing = missing or path
    return missing


def _indexed_ns(value: Any) -> int | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return int(stamp.timestamp() * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class SliceReadiness:
    reason: str = ""
    checked_files: int = 0
    code_nodes: int = 0
    unmatched_files: int = 0


def check_slice_files(
    impact: ImpactSlice, *, project_file: str = "",
    source_dir: str = "", test_dir: str = "",
) -> SliceReadiness:
    """Reject stale or ungrounded code coordinates before worker assignment.

    This checks the selected slice, not every file in a large workspace. A
    newly added file absent from the graph remains a coverage limitation.
    """
    source_nodes = [node for node in impact.nodes.values()
                    if node.get("source") == "code"]
    code_nodes = source_nodes + [node for node in impact.nodes.values()
                                 if node.get("source") == "test"]
    if source_dir and not source_nodes:
        return SliceReadiness(reason="affected graph has no source-backed nodes")
    checked: set[Path] = set()
    missing: list[str] = []
    stale: list[str] = []
    for node in code_nodes:
        path = resolve_node_file(
            node, project_file=project_file,
            source_dir=source_dir, test_dir=test_dir,
        )
        if path is None:
            missing.append(str(node.get("name") or node.get("id") or "unknown"))
            continue
        indexed = _indexed_ns(node.get("indexed_at"))
        if indexed is None:
            missing.append(path.name + " (index time unknown)")
            continue
        try:
            stat = path.stat()
            if not path.is_file():
                raise OSError("not a file")
        except OSError:
            missing.append(path.name + " (file missing)")
            continue
        checked.add(path)
        if stat.st_mtime_ns > indexed + 1_000_000_000:
            stale.append(path.name)
    if stale:
        return SliceReadiness(
            reason="source graph is stale for: " + ", ".join(sorted(set(stale))[:3]),
            checked_files=len(checked), code_nodes=len(code_nodes),
            unmatched_files=len(missing),
        )
    if missing:
        return SliceReadiness(
            reason="source graph lacks verifiable file coordinates: "
                   + ", ".join(sorted(set(missing))[:3]),
            checked_files=len(checked), code_nodes=len(code_nodes),
            unmatched_files=len(missing),
        )
    return SliceReadiness(checked_files=len(checked), code_nodes=len(code_nodes))
