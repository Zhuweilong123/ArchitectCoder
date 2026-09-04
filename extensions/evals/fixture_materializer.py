"""Materialize sparse evaluation fixtures into isolated workspaces."""

from __future__ import annotations

import shutil
from pathlib import Path

from .models import ProjectManifest
from .paths import fixtures_dir

_IGNORED_NAMES = {".pytest_cache", "__pycache__", "temp_pytest.txt"}


def _copy_tree(source: Path, target: Path) -> None:
    """Copy one fixture layer while omitting generated test artifacts."""

    for child in source.iterdir():
        if child.name in _IGNORED_NAMES:
            continue
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(
                child,
                destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*_IGNORED_NAMES),
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def materialize_fixture(
    fixture: Path,
    workspace: Path,
    manifest: ProjectManifest | None = None,
) -> None:
    """Copy a standalone fixture or base fixture plus sparse overlay.

    The target is always a normal writable copy.  No links are used, keeping
    behavior consistent across native Linux, Windows, and WSL hosts.
    """

    if manifest and manifest.base_fixture:
        root = fixtures_dir().resolve()
        base = (root / manifest.base_fixture).resolve()
        if not base.is_relative_to(root):
            raise ValueError(
                f"base fixture escapes fixtures directory: {manifest.base_fixture}"
            )
        if not base.is_dir():
            raise ValueError(f"base fixture not found: {base}")
        if base == fixture.resolve():
            raise ValueError("fixture cannot inherit from itself")
        _copy_tree(base, workspace)

    _copy_tree(fixture, workspace)
