"""评测项目清单与受控 fixture 路径解析。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvalCase, ProjectManifest
from .paths import fixtures_dir, projects_dir


def load_projects() -> dict[str, ProjectManifest]:
    result: dict[str, ProjectManifest] = {}
    root = projects_dir()
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            manifest = ProjectManifest.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
            result[manifest.id] = manifest
        except Exception:
            continue
    return result


def resolve_fixture(case: EvalCase) -> tuple[Path | None, ProjectManifest | None]:
    """Resolve a case fixture, restricting project-backed cases to fixtures/."""
    if case.project_id:
        manifest = load_projects().get(case.project_id)
        if manifest is None:
            raise ValueError(f"evaluation project not found: {case.project_id}")
        root = (fixtures_dir() / manifest.fixture).resolve()
        fixtures_root = fixtures_dir().resolve()
        if not root.is_relative_to(fixtures_root):
            raise ValueError(f"project fixture escapes fixtures directory: {manifest.fixture}")
        if not root.is_dir():
            raise ValueError(f"project fixture not found: {root}")
        return root, manifest

    if not case.fixture:
        return None, None
    return Path(case.fixture).resolve(), None
