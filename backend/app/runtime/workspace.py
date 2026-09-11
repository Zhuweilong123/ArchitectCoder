"""Canonical workspace description shared by Agent transports and tools."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


LayoutMode = Literal["project", "explicit"]


@dataclass(frozen=True)
class WorkspaceManifest:
    """Normalized design/source/test roots for one Agent session.

    Callers may provide a complete project root or individual paths.  The
    resolver keeps the legacy arguments compatible while giving downstream
    tools one canonical set of roots and one path-resolution policy.
    """

    workspace_root: str
    design_root: str
    source_root: str
    test_root: str
    project_file: str
    layout_mode: LayoutMode
    consistency_policy: Literal["strict"] = "strict"
    project_files: tuple[str, ...] = ()

    @property
    def workspace_roots(self) -> tuple[str, ...]:
        """Return roots that tools may access, without duplicate ancestors."""
        values = [self.workspace_root, self.design_root, self.source_root, self.test_root]
        result: list[str] = []
        for value in values:
            if not value:
                continue
            candidate = Path(value).resolve()
            if any(candidate == Path(existing) or candidate.is_relative_to(Path(existing)) for existing in result):
                continue
            result = [
                existing for existing in result
                if not Path(existing).is_relative_to(candidate)
            ]
            result.append(str(candidate))
        return tuple(result)

    @classmethod
    def from_paths(
        cls,
        *,
        project_file: str = "",
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        workspace_root: str = "",
    ) -> "WorkspaceManifest":
        supplied = any((project_file, source_dir, test_dir, design_dir, workspace_root))
        root_hint = _resolve(workspace_root)
        project = _resolve(project_file)

        # Accept a project directory at the same boundary as a project file.
        # Discover all design files.  Select an active project only when the
        # directory is unambiguous; callers may still provide one explicitly.
        discovered: tuple[str, ...] = ()
        selected_project = project if project and Path(project).is_file() else ""
        if project and Path(project).is_dir():
            root_hint = root_hint or project
            discovered = _discover_projects(Path(project) / "design")
            project = ""

        design = _resolve(design_dir)
        if design and Path(design).is_file():
            design = str(Path(design).parent)
        if selected_project:
            project = selected_project
            design = str(Path(project).parent)

        source = _resolve(source_dir)
        test = _resolve(test_dir)
        if root_hint:
            design = design or _child_dir(root_hint, "design")
            source = source or _child_dir(root_hint, "src")
            test = test or _child_dir(root_hint, "test")

        roots = [value for value in (design, source, test) if value]
        if root_hint:
            outside = [value for value in roots if not _inside(value, root_hint)]
            if outside:
                # The UI may report the active design file's directory as the
                # workspace (``.../design``), while source/test are its
                # conventional siblings. Promote that hint to the project
                # root instead of rejecting an otherwise valid layout.
                parent = Path(root_hint).parent
                conventional_hint = Path(root_hint).name.lower() in {
                    "design", "src", "test", "tests"
                }
                if conventional_hint and all(_inside(value, str(parent)) for value in roots):
                    root_hint = str(parent.resolve())
                else:
                    raise ValueError(
                        "workspace paths are outside workspace_root: "
                        + ", ".join(outside)
                    )

        inferred_root = root_hint or _common_root(roots)

        if design:
            discovered = discovered or _discover_projects(Path(design))
            if project and project not in discovered:
                discovered = tuple(sorted((*discovered, project)))
            if not project and len(discovered) == 1:
                project = discovered[0]

        conventional = bool(inferred_root) and all(
            not value or _inside(value, inferred_root)
            for value in (design, source, test)
        )
        layout_mode: LayoutMode = "project" if root_hint or (conventional and supplied) else "explicit"
        return cls(
            workspace_root=inferred_root,
            design_root=design,
            source_root=source,
            test_root=test,
            project_file=project,
            layout_mode=layout_mode,
            project_files=discovered,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the canonical layout for traces and run metadata."""
        return asdict(self)


def _resolve(value: str) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def _child_dir(root: str, name: str) -> str:
    candidate = Path(root) / name
    # A project layout may be loaded before conventional child directories
    # exist (for example a new project with no tests yet).  Keep their
    # canonical locations so creation tools remain inside the same boundary.
    return str(candidate.resolve()) if Path(root).is_dir() else ""


def _inside(value: str, root: str) -> bool:
    try:
        return Path(value).resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False


def _common_root(values: list[str]) -> str:
    if not values:
        return ""
    try:
        return os.path.commonpath(values)
    except ValueError:
        # Explicitly separated roots remain valid; tools will receive each
        # root individually instead of an unsafe first-path fallback.
        return ""


def _discover_projects(design_root: Path) -> tuple[str, ...]:
    if not design_root.is_dir():
        return ()
    files = sorted(
        path.resolve()
        for path in design_root.iterdir()
        if path.is_file() and path.suffix.lower() in {".umlproj", ".uml"}
    )
    return tuple(str(path) for path in files)


__all__ = ["WorkspaceManifest"]
