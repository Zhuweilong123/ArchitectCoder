"""Workspace path matching shared by file and execution tools."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RecoveryPathResolver(Protocol):
    def resolve(self, value: str, *, relative_to: str = "") -> Path: ...


class WorkspaceRecoveryMixin:
    _paths: RecoveryPathResolver

    @property
    def recovery_working_directory(self) -> str:
        return ""

    def resolve_recovery_path(self, value: str, *, relative_to: str = "") -> str:
        return str(self._paths.resolve(value, relative_to=relative_to))
