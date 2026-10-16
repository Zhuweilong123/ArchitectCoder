"""Shared security utilities — path safety, input sanitization."""

import os
import re
from pathlib import Path
from fastapi import HTTPException

from backend.config import get_settings


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
    """Resolve a path inside server-configured workspace roots, including links."""
    settings = get_settings()
    try:
        candidate = Path(user_path) if user_path else Path(settings.project_dir)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parents[3] / candidate
        candidate = candidate.resolve()
        roots = workspace_roots(settings)
        if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
            raise HTTPException(status_code=403, detail="Access denied: path outside configured workspace roots")
        return str(candidate)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def workspace_roots(settings) -> tuple[Path, ...]:
    """Shared server-side boundary for file APIs and Agent workspace selection."""
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
    """校验 Agent/WS 提供的文件或目录，返回规范路径与错误信息。

    Agent 的底层 safe_path 会把传入目录当作根，因此这里必须在更外层
    先限制这些根本身，避免客户端通过 ``source_dir`` 扩大访问范围。
    """
    if not user_path:
        return "", None
    try:
        settings = get_settings()
        # security.py lives at backend/app/core/.  Agent workspaces are scoped
        # to the repository root (one level above backend), not backend/ alone.
        repo_root = Path(__file__).resolve().parents[3]
        # Local development includes the application repository; strict
        # production only trusts deployment roots and its storage directories.
        roots = workspace_roots(settings)

        candidate = Path(user_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        candidate = candidate.resolve()
        if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
            return str(candidate), "path is outside configured workspace roots"

        if kind == "directory" and not candidate.is_dir():
            return str(candidate), "workspace path must be an existing directory"
        if kind == "file" and not candidate.is_file():
            return str(candidate), "project_file must be an existing file"
        return str(candidate), None
    except (OSError, ValueError, TypeError) as exc:
        return str(user_path), f"invalid workspace path: {exc}"
