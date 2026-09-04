"""Project persistence boundary.

The repository owns project serialization, atomic writes and optimistic
revision checks.  Higher-level services may keep the historical
``save_project``/``load_project`` facade, but all project persistence goes
through this module.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import get_settings
from app.models.uml import Project, UmlDiagram


class ProjectConflictError(RuntimeError):
    """Raised when a caller saves against a stale project revision."""

    def __init__(self, filepath: str, expected: int, actual: int):
        self.filepath = filepath
        self.expected_revision = expected
        self.actual_revision = actual
        super().__init__(
            f"project revision conflict for {filepath}: "
            f"expected {expected}, actual {actual}"
        )


@dataclass(frozen=True)
class ProjectSaveResult:
    filepath: str
    project: Project

    @property
    def revision(self) -> int:
        return self.project.revision


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).lower()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class ProjectRepository:
    """Repository contract for file-backed UML projects."""

    def load(self, filepath: str | Path) -> Project:
        path = Path(filepath).resolve()
        data = self._read_json(path)
        if isinstance(data, dict) and "diagrams" in data:
            project = Project.model_validate(data)
            return project

        # Legacy .uml files are represented as a one-diagram project.
        diagram = UmlDiagram.model_validate(data)
        return Project(
            name=diagram.name,
            diagrams=[diagram],
            active_diagram_index=0,
            revision=0,
        )

    def revision(self, filepath: str | Path) -> int:
        path = Path(filepath).resolve()
        if not path.is_file():
            return 0
        data = self._read_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"project document must be a JSON object: {path}")
        try:
            return max(0, int(data.get("revision", 0) or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid project revision in {path}") from exc

    def save(
        self,
        project: Project,
        filepath: str | Path | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ProjectSaveResult:
        path = self._target_path(project, filepath)
        lock = _path_lock(path)
        with lock:
            current_revision = self.revision(path) if path.exists() else 0
            # A new Save As target has no prior revision and is always valid.
            if path.exists() and expected_revision is not None:
                if int(expected_revision) != current_revision:
                    raise ProjectConflictError(str(path), int(expected_revision), current_revision)
            return self._save_locked(project, path, current_revision + 1)

    def commit_external_change(
        self,
        filepath: str | Path,
        *,
        expected_revision: int,
    ) -> ProjectSaveResult:
        """Validate and version a project changed by an external file tool."""
        path = Path(filepath).resolve()
        lock = _path_lock(path)
        with lock:
            current_revision = self.revision(path)
            if current_revision != int(expected_revision):
                raise ProjectConflictError(str(path), int(expected_revision), current_revision)
            project = self.load(path)
            return self._save_locked(project, path, current_revision + 1)

    def list_projects(self, root: str | Path | None = None) -> list[dict[str, Any]]:
        base = Path(root or self._settings().uml_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        files = []
        for path in base.glob("*.umlproj"):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append({
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        return sorted(files, key=lambda item: item["modified"], reverse=True)

    def _save_locked(self, project: Project, path: Path, revision: int) -> ProjectSaveResult:
        persisted = project.model_copy(update={"revision": int(revision)})
        content = json.dumps(
            persisted.model_dump(), indent=2, ensure_ascii=False,
        )
        _atomic_write(path, content)
        return ProjectSaveResult(filepath=str(path), project=persisted)

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _target_path(self, project: Project, filepath: str | Path | None) -> Path:
        if filepath:
            return Path(filepath).resolve()
        settings = self._settings()
        root = Path(settings.uml_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        filename = f"{project.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.umlproj"
        return root / filename

    @staticmethod
    def _settings():
        return get_settings()


__all__ = ["ProjectConflictError", "ProjectRepository", "ProjectSaveResult"]
