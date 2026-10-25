"""Shared security utilities — path safety, input sanitization."""

import os
import re
from pathlib import Path
from fastapi import HTTPException

from backend.config import Settings, get_settings


def sanitize_path_segment(segment: str) -> str:
    """Remove dangerous characters from a single path component.

    Returns an empty string if the segment is unsafe.
    """
    if not segment:
        return ""
    # Strip directory traversal sequences
    cleaned = segment.replace("\\", "/").replace("..", "").lstrip("/")
    # Keep only alphanumeric, dash, underscore, dot
    cleaned = re.sub(r"[^\w\-.]", "_", cleaned)
    return cleaned.strip("_") or ""


def resolve_path(user_path: str) -> str:
    """Normalise an absolute or relative path WITHOUT restricting it to the
    project root.  Returns the real absolute path with symlinks resolved.

    Raises HTTPException(400) on malformed paths.
    """
    if not user_path:
        raise HTTPException(status_code=400, detail="Empty path")

    try:
        candidate = os.path.abspath(user_path)
        return os.path.realpath(candidate)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def safe_path(user_path: str) -> str:
    """Resolve local paths; enforce deployment roots in strict production mode."""
    settings = get_settings()
    try:
        candidate = Path(user_path) if user_path else Path(settings.project_dir)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parents[3] / candidate
        candidate = candidate.resolve()
        if getattr(settings, "strict_production", False):
            roots = workspace_roots(settings)
            if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
                raise HTTPException(status_code=403, detail="Access denied: path outside configured workspace roots")
        return str(candidate)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def workspace_roots(settings: Settings) -> tuple[Path, ...]:
    """Configured roots; these restrict project selection only in strict mode."""
    repo_root = Path(__file__).resolve().parents[3]
    roots = [Path(settings.project_dir).resolve(), Path(settings.runtime_dir).resolve()]
    if not getattr(settings, "strict_production", False):
        roots.append(repo_root)
    for value in settings.workspace_roots.split(","):
        if value.strip():
            path = Path(value.strip())
            roots.append((path if path.is_absolute() else repo_root / path).resolve())
    return tuple(dict.fromkeys(roots))


def validate_agent_workspace_path(user_path: str, *, kind: str) -> tuple[str, str | None]:
    """Validate selected paths using the same local/deployment policy as file APIs.

    Agent tools remain scoped to the selected workspace by CapabilityPolicy.
    """
    if not user_path:
        return "", None
    try:
        candidate = Path(safe_path(user_path))

        if kind == "directory" and not candidate.is_dir():
            return str(candidate), "workspace path must be an existing directory"
        if kind == "file" and not candidate.is_file():
            return str(candidate), "project_file must be an existing file"
        return str(candidate), None
    except HTTPException as exc:
        # Do not resolve again: malformed paths must remain validation errors.
        return user_path, str(exc.detail)
    except (OSError, ValueError, TypeError) as exc:
        return str(user_path), f"invalid workspace path: {exc}"
