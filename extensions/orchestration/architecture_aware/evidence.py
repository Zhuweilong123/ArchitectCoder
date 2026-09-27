"""Bounded, path-checked file excerpts for graph-assigned read-only workers."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .impact import ImpactSlice
from .partition import ExplorationPackage


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _excerpt(path: Path, terms: list[str], *, max_scan: int = 20000) -> str:
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for number, raw in enumerate(handle, 1):
                if number > max_scan:
                    break
                lines.append(raw.rstrip("\r\n")[:300])
    except OSError:
        return ""
    match = next((index for term in terms for index, line in enumerate(lines)
                  if term in line), None)
    if match is None:
        return ""
    start = max(0, match - 2)
    stop = min(len(lines), match + 13)
    return "\n".join(
        f"{path}:{index + 1}: {lines[index]}" for index in range(start, stop))


def collect_file_evidence(
    request: Any, impact: ImpactSlice, package: ExplorationPackage,
) -> tuple[str, ...]:
    """Read at most two source files and one UML excerpt for this package."""
    project = Path(request.project_file).resolve()
    roots = tuple(
        Path(value).resolve() for value in (request.source_dir, request.test_dir)
        if value
    )
    names_by_path: dict[Path, list[str]] = {}
    design_names: list[str] = []
    requested_methods = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
        str(request.user_message)[:2000],
    )[:8]
    for node_id in package.node_ids:
        node = impact.nodes[node_id]
        name = str(node.get("name") or "").strip()[:100]
        location = str(node.get("file") or node.get("path") or "").strip()
        if not location:
            if name:
                design_names.append(name)
            continue
        raw = Path(location)
        candidates = (raw,) if raw.is_absolute() else tuple(root / raw for root in roots)
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except (OSError, ValueError):
                continue
            if path.suffix == ".py" and _within(path, roots) and path.is_file():
                names_by_path.setdefault(path, []).append(name)
                break

    evidence: list[str] = []
    for path, names in list(names_by_path.items())[:2]:
        normalized_stem = path.stem.replace("_", "").casefold()
        preferred = [method for owner, method in requested_methods
                     if owner.replace("_", "").casefold() == normalized_stem]
        terms = preferred + [name for name in names if name and len(name) >= 3][:8]
        if terms and (excerpt := _excerpt(path, terms)):
            evidence.append(excerpt)
    if design_names and project.suffix == ".umlproj" and project.is_file():
        # Exact quoted JSON names reduce collisions with prose and IDs.
        assigned_names = {str(impact.nodes[node_id].get("name") or "")
                          for node_id in package.node_ids}
        terms = [f'"name": "{method}"' for owner, method in requested_methods
                 if owner in assigned_names or method in assigned_names]
        terms.extend(f'"name": "{name}"' for name in design_names[:8] if len(name) >= 3)
        if terms and (excerpt := _excerpt(project, terms)):
            evidence.append(excerpt)
    return tuple(evidence)
