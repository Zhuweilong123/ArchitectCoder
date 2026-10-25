"""File operations API – new, open, save, export, browse, review."""

import logging
import os
import string
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import PlainTextResponse

from app.models.uml import UmlDiagram, Project, ExportRequest
from app.services.file_service import (
    save_diagram, load_diagram, list_diagrams, export_markdown,
    save_project_with_result, load_project, list_projects,
    ensure_project_graph,
)
from app.services.project_repository import ProjectConflictError, ProjectRepository
from backend.config import get_settings
from backend.config.paths import runtime_root
from app.core.auth import require_auth
from app.core.security import (
    safe_path,
    sanitize_path_segment,
    validate_agent_workspace_path,
    workspace_roots,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(require_auth)])


def _resolve_open_path(filepath: str, *, safe: bool, mask_access_error: bool = False) -> str:
    """Resolve an open request while preserving each endpoint's error response."""
    try:
        # ``safe`` is retained for old clients, but never widens server policy.
        return safe_path(filepath)
    except HTTPException as exc:
        if mask_access_error:
            raise HTTPException(status_code=403, detail="Access denied") from exc
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc


@router.get("/list")
async def list_files():
    """List all saved UML diagram files."""
    return {"files": list_diagrams()}


@router.post("/save", dependencies=[Depends(require_auth)])
async def save_file(diagram: UmlDiagram, filename: str = ""):
    """Save a UML diagram to a .uml file. Optional custom filename."""
    # Display names must not become absolute or traversal paths.
    safe_name = sanitize_path_segment((filename or diagram.name).replace(".uml", "")) or "Untitled"
    if not filename:
        safe_name += "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = safe_path(os.path.join(get_settings().project_dir, safe_name + ".uml"))
    if not filepath.lower().endswith(".uml"):
        raise HTTPException(status_code=403, detail="Invalid diagram target")
    filepath = save_diagram(diagram, filepath)
    return {"success": True, "filepath": filepath, "filename": os.path.basename(filepath)}


@router.get("/open")
async def open_file(filepath: str, safe: bool = True):
    """Open a saved UML diagram within the configured workspace roots."""
    resolved = _resolve_open_path(filepath, safe=safe)
    if not resolved.endswith(".uml"):
        raise HTTPException(status_code=400, detail="Only .uml files can be opened")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    diagram = load_diagram(resolved)
    return {"diagram": diagram.model_dump()}


@router.post("/new")
async def new_diagram(name: str = "Untitled"):
    """Create a new empty UML diagram."""
    diagram = UmlDiagram(name=name)
    return {"diagram": diagram.model_dump()}


@router.post("/export/markdown", response_class=PlainTextResponse)
async def export_to_markdown(req: ExportRequest):
    """Export UML diagram to Markdown design document."""
    md = export_markdown(req.diagram)
    return md


# ── Directory browsing ──────────────────────────────────

class BrowseRoot(BaseModel):
    name: str
    path: str


class BrowseRootsResponse(BaseModel):
    roots: list[BrowseRoot]


@router.get("/browse-roots", response_model=BrowseRootsResponse)
async def list_browse_roots() -> BrowseRootsResponse:
    """Local disk/home entry points, or configured roots for strict deployments."""
    settings = get_settings()
    if settings.strict_production:
        candidates = workspace_roots(settings)
    else:
        disks = (tuple(Path(f"{letter}:/") for letter in string.ascii_uppercase)
                 if os.name == "nt" else (Path("/"),))
        candidates = (Path.home(), *disks)
    roots: list[BrowseRoot] = []
    for root in dict.fromkeys(candidates):
        try:
            if root.is_dir():
                roots.append(BrowseRoot(name=root.name or root.as_posix(), path=root.as_posix()))
        except OSError:
            continue  # Disconnected or inaccessible drives are not entry points.
    return BrowseRootsResponse(roots=roots)


@router.get("/browse")
async def browse_directory(path: str = "", safe: bool = True):
    """Browse a directory. Returns subdirectories and .uml files.

    Local mode accepts any OS-accessible directory. In strict mode, the legacy
    safe flag cannot expand the server-configured workspace roots.
    """
    path_resolver = safe_path

    if not path:
        base = safe_path("")
    else:
        try:
            base = path_resolver(path)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(base) or not os.path.isdir(base):
        base = safe_path("")

    try:
        items = os.listdir(base)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    dirs = []
    files = []
    for name in sorted(items):
        full = os.path.join(base, name)
        try:
            safe_path(full)
        except HTTPException:
            # Do not advertise links that escape the allowed roots.
            continue
        if os.path.isdir(full) and not name.startswith("."):
            dirs.append({"name": name, "path": full.replace("\\", "/")})
        elif name.endswith(".uml") or name.endswith(".umlproj"):
            stat = os.stat(full)
            files.append({
                "name": name,
                "path": full.replace("\\", "/"),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "type": "project" if name.endswith(".umlproj") else "diagram",
            })

    # Parent navigation is subject to the same policy, including safe=false.
    parent = ""
    parent_dir = os.path.dirname(base)
    if parent_dir != base:
        try:
            safe_path(parent_dir)
            parent = parent_dir.replace("\\", "/")
        except HTTPException:
            pass

    return {
        "current": base.replace("\\", "/"),
        "parent": parent,
        "dirs": dirs,
        "files": files,
    }


@router.get("/validate-workspace")
async def validate_workspace_path(path: str, kind: str = "directory"):
    """Validate a persisted Agent workspace path without changing filesystem state."""

    if kind not in {"directory", "file"}:
        raise HTTPException(status_code=400, detail="Invalid workspace path kind")

    normalized, error = validate_agent_workspace_path(path, kind=kind)
    return {
        "valid": error is None,
        "path": normalized,
        "error": error,
    }


# ── UML review log ──────────────────────────────────

class ReviewRequest(BaseModel):
    action: Literal["accept", "reject"]
    comment: str = ""
    requirements: str = ""
    original_name: str = ""
    optimized_name: str = ""
    timestamp: str = ""


@router.post("/save-review", dependencies=[Depends(require_auth)])
async def save_review(req: ReviewRequest):
    """Save a UML review record to dev_review.txt."""
    settings = get_settings()
    review_file = str(runtime_root(settings) / "dev_review.txt")
    review_file = os.path.abspath(review_file)

    ts = req.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    action_label = "接受" if req.action == "accept" else "拒绝"
    entry = f"""============================================================
[{ts}] UML评审结果: {action_label}
优化需求: {req.requirements if req.requirements else '(无)'}
原始版本: {req.original_name}
优化版本: {req.optimized_name}
评审意见: {req.comment if req.comment else '(无)'}
============================================================
"""
    with open(review_file, "a", encoding="utf-8") as f:
        f.write(entry)

    return {"success": True, "file": review_file.replace("\\", "/")}


# ── Project operations (.umlproj) ────────────────────────


@router.post("/save-project", dependencies=[Depends(require_auth)])
async def save_project_endpoint(
    project: Project,
    filename: str = "",
    safe: bool = True,
    expected_revision: int | None = None,
):
    """Save a Project to a .umlproj file.

    If filename looks like a full path (contains : or /), use it directly after
    path-safety validation. Otherwise create a new project directory.

    Strict deployments require WORKSPACE_ROOTS; safe=false cannot bypass policy.
    """
    path_resolver = safe_path
    filepath = None
    if filename:
        if ':' in filename or '/' in filename or '\\' in filename:
            # Full path — validate and use directly
            try:
                filepath = path_resolver(filename)
                if not filepath.lower().endswith(".umlproj"):
                    raise HTTPException(status_code=400, detail="Only .umlproj files can be saved")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=403, detail="Invalid file path")
        else:
            safe_name = sanitize_path_segment(filename.replace(".umlproj", ""))
            if safe_name:
                filepath = safe_path(str(ProjectRepository().new_project_path(safe_name)))
    try:
        result = save_project_with_result(
            project,
            filepath,
            expected_revision=expected_revision,
        )
        logger.info(f"[API] Project saved: {result.filepath}")
        return {
            "success": True,
            "filepath": result.filepath,
            "filename": os.path.basename(result.filepath),
            "revision": result.revision,
        }
    except ProjectConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Project has changed since it was loaded",
                "expected_revision": exc.expected_revision,
                "actual_revision": exc.actual_revision,
            },
        ) from exc
    except Exception as e:
        logger.error(f"[API] Failed to save project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/open-project")
async def open_project(filepath: str, safe: bool = True):
    """Open a .umlproj (or legacy .uml) file as a Project.
    Strict deployments restrict projects to configured workspace roots."""
    resolved = _resolve_open_path(filepath, safe=safe, mask_access_error=True)
    if not resolved.lower().endswith((".umlproj", ".uml")):
        raise HTTPException(status_code=400, detail="Only .umlproj or .uml files can be opened")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        project = load_project(resolved)
        ensure_project_graph(project, resolved)
        logger.info(f"[API] Project opened: {project.name} ({len(project.diagrams)} diagrams)")
        return {"project": project.model_dump()}
    except Exception as e:
        logger.error(f"[API] Failed to open project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list-projects")
async def list_projects_endpoint():
    """List all saved .umlproj project files."""
    return {"projects": list_projects()}
