"""Minimal capability policy enforced at the tool execution boundary."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterable


_PATH_KEYS = frozenset({"path", "project_file"})
_PROTECTED_PATTERNS = (".git", ".git/*", ".env", ".env.*", ".ssh", ".ssh/*")
_ABSOLUTE_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\s'\"]+|/[A-Za-z0-9_.-][^\s'\"]*)")


class CapabilityPolicy:
    """Allowlist tools and keep direct tool calls inside the workspace.

    Existing tools still own their domain-specific validation.  This policy is
    the common last boundary so callers cannot bypass it by invoking the
    registry directly instead of going through the Agent loop.
    """

    def __init__(
        self,
        *,
        workspace_roots: Iterable[str] = (),
        allowed_tools: Iterable[str] | None = None,
        protected_paths: Iterable[str] = _PROTECTED_PATTERNS,
    ) -> None:
        self._roots = tuple(Path(root).resolve() for root in workspace_roots if root)
        self._allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self._protected_paths = tuple(protected_paths)

    def set_allowed_tools(self, names: Iterable[str] | None) -> None:
        self._allowed_tools = set(names) if names is not None else None

    def check(self, name: str, parameters: dict[str, Any]) -> str | None:
        if self._allowed_tools is not None and name not in self._allowed_tools:
            return f"Tool '{name}' is not enabled for this run"

        if name in {"bash", "shell"}:
            command = parameters.get("command", "")
            if not isinstance(command, str) or not command.strip():
                return "command must be a non-empty string"
            return self._check_command(command)

        if name == "run_program":
            program = parameters.get("program", "")
            args = parameters.get("args", [])
            if not isinstance(program, str) or not program.strip():
                return "program must be a non-empty string"
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                return "args must be a list of strings"
            return self._check_command(" ".join([program, *args]))

        if name == "run_task":
            target = parameters.get("target")
            if target is not None:
                if not isinstance(target, str) or not target.strip():
                    return "target must be a non-empty string"
                return self._check_path(target)
            return None

        if name in {"apply_changes", "apply_patch"}:
            changes = parameters.get("changes")
            if changes is None:
                changes = parameters.get("patches", [])
            if not isinstance(changes, list) or not changes:
                return "changes must be a non-empty list"
            for change in changes:
                if not isinstance(change, dict):
                    return "each change must be an object"
                for key in ("path", "from", "to"):
                    value = change.get(key)
                    if value is None:
                        continue
                    if not isinstance(value, str) or not value.strip():
                        return f"each change {key} must be a non-empty string"
                    error = self._check_path(value)
                    if error:
                        return error
            return None

        if name in {"read_file", "write_file", "edit_file"}:
            path = parameters.get("path", "")
            if not isinstance(path, str) or not path.strip():
                return "path must be a non-empty string"
            return self._check_path(path)
        return None

    def _check_path(self, value: str) -> str | None:
        normalized = value.replace("\\", "/").lstrip("/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if any(part == ".." for part in value.replace("\\", "/").split("/")):
            return "path traversal is not allowed"
        if self._is_protected(normalized):
            return f"access to protected path is denied: {value}"
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if not any(resolved == root or resolved.is_relative_to(root) for root in self._roots):
                return f"path is outside configured workspace roots: {value}"
        return None

    def _check_command(self, command: str) -> str | None:
        if any(part == ".." for part in re.split(r"[\\/\s]+", command)):
            return "path traversal is not allowed in shell commands"
        for raw_path in _ABSOLUTE_PATH.findall(command):
            path = Path(raw_path.rstrip(".,;"))
            try:
                resolved = path.resolve()
            except OSError:
                return f"invalid path in shell command: {raw_path}"
            if self._roots and not any(
                resolved == root or resolved.is_relative_to(root) for root in self._roots
            ):
                return f"shell path is outside configured workspace roots: {raw_path}"
        return None

    def _is_protected(self, value: str) -> bool:
        normalized = value.strip("/")
        return any(
            fnmatch.fnmatchcase(normalized, pattern)
            or normalized.startswith(pattern.rstrip("/*") + "/")
            for pattern in self._protected_paths
        )
