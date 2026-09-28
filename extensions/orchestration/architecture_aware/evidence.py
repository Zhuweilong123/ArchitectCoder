"""Bounded, path-checked file excerpts for graph-assigned read-only workers."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .impact import ImpactSlice
from .partition import ExplorationPackage


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _excerpt(
    path: Path, terms: list[str], *, display_path: str = "", max_scan: int = 20000,
) -> str:
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for number, raw in enumerate(handle, 1):
                if number > max_scan:
                    break
                lines.append(raw.rstrip("\r\n")[:180])
    except OSError:
        return ""
    if not lines:
        return ""
    matches: list[int] = []
    for term in dict.fromkeys(term for term in terms if term):
        # Prefer declarations over imports, comments, and call sites.  The
        # latter gave workers misleading, nearly empty evidence in the trade
        # evaluation despite the target method being present in the file.
        name = re.escape(term)
        declaration = re.compile(rf"^\s*(?:async\s+)?(?:def|class)\s+{name}\b")
        match = next((i for i, line in enumerate(lines) if declaration.search(line)), None)
        if match is None:
            match = next((i for i, line in enumerate(lines) if term in line), None)
        if match is not None and all(abs(match - previous) > 15 for previous in matches):
            matches.append(match)
        if matches:
            break
    if not matches:
        return ""
    excerpts = []
    for match in matches:
        start = max(0, match - 2)
        stop = min(len(lines), match + 12)
        excerpts.append("\n".join(
            f"{display_path or path}:{index + 1}: {lines[index]}"
            for index in range(start, stop)))
    return "\n...\n".join(excerpts)


def collect_file_evidence(
    request: Any, impact: ImpactSlice, package: ExplorationPackage,
) -> tuple[str, ...]:
    """Read at most two source files and one UML excerpt for this package."""
    project = Path(request.project_file).resolve()
    named_roots = tuple(
        (alias, Path(value).resolve())
        for alias, value in (("source", request.source_dir), ("test", request.test_dir))
        if value
    )
    roots = tuple(root for _, root in named_roots)
    names_by_path: dict[Path, list[tuple[str, str]]] = {}
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
                names_by_path.setdefault(path, []).append(
                    (name, str(node.get("node_type") or node.get("type") or "")))
                break

    evidence: list[str] = []
    for path, names in list(names_by_path.items())[:2]:
        assigned = {name for name, _ in names}
        preferred = [method for owner, method in requested_methods
                     if owner in assigned or method in assigned]
        methods = [name for name, kind in names if kind == "method" and len(name) >= 3]
        others = [name for name, kind in names if kind != "method" and len(name) >= 3]
        mentioned = [name for name in methods if re.search(
            rf"\b{re.escape(name)}\b", str(request.user_message)[:2000])]
        terms = (preferred + mentioned + methods + others)[:10]
        display = next(
            f"{alias}/{path.relative_to(root).as_posix()}"
            for alias, root in named_roots if _within(path, (root,))
        )
        if terms and (excerpt := _excerpt(path, terms, display_path=display)):
            evidence.append(excerpt)
    if design_names and project.suffix == ".umlproj" and project.is_file():
        # Exact quoted JSON names reduce collisions with prose and IDs.
        assigned_names = {str(impact.nodes[node_id].get("name") or "")
                          for node_id in package.node_ids}
        terms = [f'"name": "{method}"' for owner, method in requested_methods
                 if owner in assigned_names or method in assigned_names]
        terms.extend(f'"name": "{name}"' for name in design_names[:8] if len(name) >= 3)
        if terms and (excerpt := _excerpt(
            project, terms, display_path=f"design/{project.name}",
        )):
            evidence.append(excerpt)
    return tuple(evidence)
