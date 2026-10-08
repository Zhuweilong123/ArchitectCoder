"""Bounded, path-checked file excerpts for graph-assigned read-only workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .impact import ImpactSlice
from .graph_files import resolve_node_file
from .partition import ExplorationPackage


@dataclass(frozen=True)
class FileExcerpt:
    """Verified executable coordinates kept separately from rendered evidence."""

    path: str
    start_line: int
    end_line: int
    text: str


def _excerpt(
    path: Path, terms: list[str], *, max_scan: int = 20000,
) -> FileExcerpt | None:
    lines: list[str] = []
    try:
        if path.stat().st_size > 2_000_000:
            return None
        with path.open("rb") as probe:
            if b"\0" in probe.read(4096):
                return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for number, raw in enumerate(handle, 1):
                if number > max_scan:
                    break
                lines.append(raw.rstrip("\r\n")[:180])
    except OSError:
        return None
    if not lines:
        return None
    matches: list[int] = []
    for term in dict.fromkeys(term for term in terms if term):
        # Prefer declarations over imports, comments, and call sites.  The
        # latter gave workers misleading, nearly empty evidence in the trade
        # evaluation despite the target method being present in the file.
        name = re.escape(term)
        declaration = re.compile(
            rf"^\s*(?:(?:export|public|private|protected|static|async|default)\s+)*"
            rf"(?:def|class|function|fn|func|interface|trait|struct|type)\s+{name}\b"
        )
        match = next((i for i, line in enumerate(lines) if declaration.search(line)), None)
        if match is None:
            match = next((i for i, line in enumerate(lines) if term in line), None)
        if match is not None and all(abs(match - previous) > 15 for previous in matches):
            matches.append(match)
        if matches:
            break
    if not matches:
        return None
    canonical = path.resolve().as_posix()
    excerpts = []
    ranges = []
    for match in matches:
        start = max(0, match - 2)
        stop = min(len(lines), match + 12)
        ranges.append((start + 1, stop))
        excerpts.append("\n".join(
            f"{canonical}:{index + 1}: {lines[index]}"
            for index in range(start, stop)))
    return FileExcerpt(canonical, ranges[0][0], ranges[-1][1], "\n...\n".join(excerpts))


def collect_file_evidence(
    request: Any, impact: ImpactSlice, package: ExplorationPackage,
) -> tuple[FileExcerpt, ...]:
    """Read at most two text source files and one UML excerpt for this package."""
    project = Path(request.project_file).resolve()
    names_by_path: dict[Path, list[tuple[str, str]]] = {}
    design_names: list[str] = []
    requested_methods = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
        str(request.user_message)[:2000],
    )[:8]
    for node_id in package.node_ids:
        node = impact.nodes[node_id]
        name = str(node.get("name") or "").strip()[:100]
        path = resolve_node_file(
            node, project_file=request.project_file,
            source_dir=request.source_dir, test_dir=request.test_dir,
        )
        if path is None:
            if name:
                design_names.append(name)
            continue
        if path.is_file():
            names_by_path.setdefault(path, []).append(
                (name, str(node.get("node_type") or node.get("type") or "")))

    evidence: list[FileExcerpt] = []
    for path, names in list(names_by_path.items())[:2]:
        assigned = {name for name, _ in names}
        preferred = [method for owner, method in requested_methods
                     if owner in assigned or method in assigned]
        methods = [name for name, kind in names if kind == "method" and len(name) >= 3]
        others = [name for name, kind in names if kind != "method" and len(name) >= 3]
        mentioned = [name for name in methods if re.search(
            rf"\b{re.escape(name)}\b", str(request.user_message)[:2000])]
        terms = (preferred + mentioned + methods + others)[:10]
        if terms and (excerpt := _excerpt(path, terms)):
            evidence.append(excerpt)
    if design_names and project.suffix == ".umlproj" and project.is_file():
        # Exact quoted JSON names reduce collisions with prose and IDs.
        assigned_names = {str(impact.nodes[node_id].get("name") or "")
                          for node_id in package.node_ids}
        terms = [f'"name": "{method}"' for owner, method in requested_methods
                 if owner in assigned_names or method in assigned_names]
        terms.extend(f'"name": "{name}"' for name in design_names[:8] if len(name) >= 3)
        if terms and (excerpt := _excerpt(
            project, terms,
        )):
            evidence.append(excerpt)
    return tuple(evidence)
