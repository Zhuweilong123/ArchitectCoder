"""Project-owned state paths for memory and knowledge graph providers."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


_MANIFEST_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProjectStorage:
    root: Path
    state_dir: Path
    project_id: str

    @property
    def memory_db(self) -> Path:
        return self.state_dir / "memories.db"

    @property
    def graph_db(self) -> Path:
        return self.state_dir / "knowledge_graph.db"


def project_storage(
    project_file: str = "", *, workspace_root: str = "", create: bool = True,
) -> ProjectStorage | None:
    """Resolve state from an actual project path, never from a display name.

    Conventional ``design/`` layouts store state at the project root. A
    standalone file in the shared project directory gets a distinct sidecar
    directory, so unrelated files there cannot share a database.
    """
    project = Path(project_file).expanduser().resolve() if project_file else None
    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    if project and root and not project.is_relative_to(root):
        raise ValueError("project file is outside workspace root")
    if project is not None:
        root = project.parent.parent if project.parent.name.lower() == "design" else project.parent
    if root is None:
        return None

    from .settings import get_settings

    shared_project_dir = Path(get_settings().project_dir).expanduser().resolve()
    if project and project.parent == shared_project_dir:
        state_dir = root / ".architectcoder" / project.stem
    else:
        state_dir = root / ".architectcoder"
    manifest = state_dir / "project.json"
    if create:
        with _MANIFEST_LOCK:
            state_dir.mkdir(parents=True, exist_ok=True)
            try:
                with manifest.open("x", encoding="utf-8") as handle:
                    json.dump({"schema_version": 1, "project_id": uuid.uuid4().hex}, handle)
                    handle.flush()
            except FileExistsError:
                pass
    if not manifest.is_file():
        return None
    for attempt in range(10):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            break
        except json.JSONDecodeError:
            if attempt == 9:
                raise
            time.sleep(0.01)
    project_id = str(payload.get("project_id") or "")
    if payload.get("schema_version") != 1 or not project_id:
        raise ValueError(f"invalid project storage manifest: {manifest}")
    return ProjectStorage(root=root, state_dir=state_dir, project_id=project_id)


def project_id_for(project_file: str = "", *, workspace_root: str = "") -> str:
    storage = project_storage(project_file, workspace_root=workspace_root)
    return storage.project_id if storage else ""


__all__ = ["ProjectStorage", "project_storage", "project_id_for"]
