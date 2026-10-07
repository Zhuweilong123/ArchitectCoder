"""One path contract for workspace file scopes and execution directories."""

from __future__ import annotations

from pathlib import Path


class WorkspacePathError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class WorkspacePathResolver:
    """Resolve real project paths before compatibility aliases.

    ``workspace/`` explicitly selects the workspace root. Other aliases are
    fallbacks only when that name is not a real workspace entry. Returned
    inventory paths are absolute so they also work as literal program argv.
    """

    def __init__(self, workspace_root: str = "", source_dir: str = "",
                 test_dir: str = "", design_dir: str = ""):
        self.aliases = {
            "workspace": Path(workspace_root).resolve() if workspace_root else None,
            "source": Path(source_dir).resolve() if source_dir else None,
            "src": Path(source_dir).resolve() if source_dir else None,
            "test": Path(test_dir).resolve() if test_dir else None,
            "tests": Path(test_dir).resolve() if test_dir else None,
            "design": Path(design_dir).resolve() if design_dir else None,
        }
        self.workspace = self.aliases["workspace"]
        self.roots = list(dict.fromkeys(root for root in self.aliases.values() if root))

    def _checked(self, candidate: Path, base: Path | None = None) -> Path:
        path = candidate.resolve()
        if any(part.casefold() == ".architectcoder" for part in candidate.parts + path.parts):
            raise WorkspacePathError("Project state is managed by ArchitectCoder", "PROJECT_STATE_PROTECTED")
        if (base is not None and not path.is_relative_to(base)) or not any(
            path.is_relative_to(root) for root in self.roots
        ):
            raise WorkspacePathError(f"path escapes workspace: {candidate}", "PATH_OUTSIDE_WORKSPACE")
        return path

    def resolve(self, value: str, *, require_exist: bool = False,
                default_root: str = "", relative_to: str = "") -> Path:
        if not self.roots:
            raise WorkspacePathError("No workspace root configured", "WORKSPACE_NOT_CONFIGURED")
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise WorkspacePathError("path must be a non-empty string", "INVALID_ARGUMENT")
        normalized = value.strip().replace("\\", "/")
        requested = Path(normalized).expanduser()
        if requested.is_absolute():
            path = self._checked(requested)
        elif normalized == "." and default_root:
            path = self._checked(Path(default_root))
        else:
            head, _, tail = normalized.partition("/")
            alias = head.casefold()
            # Reserve workspace/ for an explicit root; preserve real src,
            # tests, design, etc. even when a differently named scope is loaded.
            actual_head = self.workspace / head if self.workspace else None
            if relative_to and alias not in self.aliases:
                base = self._checked(Path(relative_to))
                path = self._checked(base / requested, base)
            elif alias != "workspace" and actual_head is not None and actual_head.exists():
                path = self._checked(self.workspace / requested, self.workspace)
            elif alias in self.aliases:
                base = self.aliases[alias]
                if base is None:
                    raise WorkspacePathError(
                        f"workspace alias not configured: {head}", "WORKSPACE_ALIAS_NOT_CONFIGURED",
                    )
                path = self._checked(base / tail, base)
            else:
                candidates = [self._checked(root / requested, root) for root in self.roots]
                existing = list(dict.fromkeys(path for path in candidates if path.exists()))
                if len(existing) > 1:
                    raise WorkspacePathError(
                        f"ambiguous path: {value}; use an absolute path", "PATH_AMBIGUOUS",
                    )
                base = Path(default_root).resolve() if default_root else self.roots[0]
                path = existing[0] if existing else self._checked(base / requested, base)
        if require_exist and not path.exists():
            raise WorkspacePathError(f"path not found: {value}", "PATH_NOT_FOUND")
        return path

    def directory(self, value: str, *, default_root: str = "") -> Path:
        path = self.resolve(value, require_exist=True, default_root=default_root)
        if not path.is_dir():
            raise WorkspacePathError(f"not a directory: {value}", "NOT_A_DIRECTORY")
        return path

    def display(self, path: Path) -> str:
        return str(self._checked(path))
