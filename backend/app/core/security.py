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
    """Resolve a user-supplied path and ensure it stays within the project root.

    Raises HTTPException(403) on directory traversal attempt.
    """
    settings = get_settings()
    # Resolve the project root (parent of backend/)
    project_root = os.path.abspath(os.path.join(settings.uml_dir, "..", ".."))

    if not user_path:
        return os.path.abspath(settings.uml_dir)

    # If relative, anchor it to the project root
    if not os.path.isabs(user_path):
        candidate = os.path.abspath(os.path.join(project_root, user_path))
    else:
        candidate = os.path.abspath(user_path)

    # Resolve symlinks to defeat symlink-based escapes
    try:
        real_candidate = os.path.realpath(candidate)
        real_root = os.path.realpath(project_root)
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Must be within the project root
    if os.path.commonpath([real_candidate, real_root]) != real_root:
        raise HTTPException(status_code=403, detail="Access denied: path outside project")

    return real_candidate


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
        configured = [p.strip() for p in settings.workspace_roots.split(",") if p.strip()]
        # The application repository is always a trusted workspace baseline.
        # Configured roots extend that baseline for external projects; they must
        # not accidentally make in-repository artifacts such as project/ or
        # temp/ inaccessible.
        roots = [repo_root, Path(settings.uml_dir).resolve(),
                 Path(settings.uml_dir).resolve().parent]
        roots.extend(Path(p).resolve() for p in configured)

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
