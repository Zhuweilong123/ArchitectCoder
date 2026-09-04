"""File operations API – new, open, save, export, browse, review."""

import logging
import os
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import PlainTextResponse

from app.models.uml import UmlDiagram, Project, create_default_project, ExportRequest
from app.services.file_service import (
    save_diagram, load_diagram, list_diagrams, export_markdown,
    save_project_with_result, load_project, list_projects,
)
from app.services.project_repository import ProjectConflictError
from app.core.config import get_settings
from app.core.auth import require_auth
from app.core.security import safe_path, resolve_path, sanitize_path_segment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/list")
async def list_files():
    """List all saved UML diagram files."""
    return {"files": list_diagrams()}


@router.post("/save", dependencies=[Depends(require_auth)])
async def save_file(diagram: UmlDiagram, filename: str = ""):
    """Save a UML diagram to a .uml file. Optional custom filename."""
    filepath = None
    if filename:
        # Sanitize filename — strip path and dangerous chars
        safe_name = sanitize_path_segment(filename.replace(".uml", ""))
        if safe_name:
            safe_name += ".uml"
            filepath = os.path.join(get_settings().uml_dir, safe_name)
    filepath = save_diagram(diagram, filepath)
    return {"success": True, "filepath": filepath, "filename": os.path.basename(filepath)}


@router.get("/open")
async def open_file(filepath: str, safe: bool = True):
    """Open a saved UML diagram file. Pass safe=false to allow any path on disk."""
    path_resolver = safe_path if safe else resolve_path
    try:
        resolved = path_resolver(filepath)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
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


@router.post("/upload/excel", dependencies=[Depends(require_auth)])
async def upload_excel(file: UploadFile = File(...)):
    """Upload an Excel test case file — parsed in memory, temp file cleaned up."""
    import pandas as pd
    import tempfile
    content = await file.read()

    # Sanitize filename — strip path components to prevent traversal
    raw_name = file.filename or "testCase.xlsx"
    safe_name = os.path.basename(raw_name)
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Save to temp file, parse, then clean up
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(content)
        tmp.flush()
        xls = pd.ExcelFile(tmp.name)
        sheets = {}
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            sheets[sheet_name] = df.fillna("").to_dict(orient="records")

    return {"filename": file.filename, "sheets": sheets, "sheet_names": xls.sheet_names}


# ── Directory browsing ──────────────────────────────────

@router.get("/browse")
async def browse_directory(path: str = "", safe: bool = True):
    """Browse a directory. Returns subdirectories and .uml files.

    Set safe=false to allow browsing any path on disk (pipeline directory selection).
    """
    settings = get_settings()
    path_resolver = safe_path if safe else resolve_path

    if not path:
        base = os.path.abspath(settings.uml_dir)
    else:
        try:
            base = path_resolver(path)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(base) or not os.path.isdir(base):
        base = os.path.abspath(settings.uml_dir)

    try:
        items = os.listdir(base)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    dirs = []
    files = []
    for name in sorted(items):
        full = os.path.join(base, name)
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

    # Parent navigation: always allowed when unrestricted; checked when restricted
    parent = ""
    if safe:
        project_root = os.path.abspath(os.path.join(settings.uml_dir, "..", ".."))
        if base != project_root and base != os.path.abspath(settings.uml_dir):
            parent_dir = os.path.dirname(base)
            try:
                safe_path(parent_dir)
                parent = parent_dir.replace("\\", "/")
            except HTTPException:
                parent = ""
    else:
        parent_dir = os.path.dirname(base)
        if parent_dir != base:
            parent = parent_dir.replace("\\", "/")

    return {
        "current": base.replace("\\", "/"),
        "parent": parent,
        "dirs": dirs,
        "files": files,
    }


# ── Unified review log ──────────────────────────────────

class ReviewRequest(BaseModel):
    action: str  # "accept", "reject", "case_review", etc.
    comment: str = ""
    requirements: str = ""
    original_name: str = ""
    optimized_name: str = ""
    timestamp: str = ""
    # Case review fields (optional)
    filename: str = ""
    sheet: str = ""
    case_id: str = ""
    details: str = ""


@router.post("/save-review", dependencies=[Depends(require_auth)])
async def save_review(req: ReviewRequest):
    """Save review record to dev_review.txt (unified UML + case review log)."""
    settings = get_settings()
    review_file = os.path.join(settings.uml_dir, "..", "dev_review.txt")
    review_file = os.path.abspath(review_file)

    ts = req.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if req.action in ("accept", "reject"):
        action_label = "接受" if req.action == "accept" else "拒绝"
        entry = f"""============================================================
[{ts}] UML评审结果: {action_label}
优化需求: {req.requirements if req.requirements else '(无)'}
原始版本: {req.original_name}
优化版本: {req.optimized_name}
评审意见: {req.comment if req.comment else '(无)'}
============================================================
"""
    else:
        # Case review operation log
        entry = f"[{ts}] {req.action}"
        if req.filename:
            entry += f" | File: {req.filename}"
        if req.sheet:
            entry += f" | Sheet: {req.sheet}"
        if req.case_id:
            entry += f" | Case: {req.case_id}"
        if req.comment:
            entry += f"\n  Comment: {req.comment}"
        if req.details:
            entry += f"\n  Details: {req.details}"
        entry += "\n"

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
    path-safety validation.  Otherwise treat it as a short name in uml_dir.

    Pass ``safe=false`` to allow saving outside the project root
    (e.g. overwriting an external file that was opened with safe=false).
    """
    path_resolver = safe_path if safe else resolve_path
    filepath = None
    if filename:
        if ':' in filename or '/' in filename or '\\' in filename:
            # Full path — validate and use directly
            try:
                path_resolver(filename)
                filepath = filename
            except Exception:
                raise HTTPException(status_code=403, detail="Invalid file path")
        else:
            safe_name = sanitize_path_segment(filename.replace(".umlproj", ""))
            if safe_name:
                safe_name += ".umlproj"
                filepath = os.path.join(get_settings().uml_dir, safe_name)
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
    Pass safe=false to allow any path on disk."""
    path_resolver = safe_path if safe else resolve_path
    try:
        resolved = path_resolver(filepath)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        project = load_project(resolved)
        logger.info(f"[API] Project opened: {project.name} ({len(project.diagrams)} diagrams)")
        return {"project": project.model_dump()}
    except Exception as e:
        logger.error(f"[API] Failed to open project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list-projects")
async def list_projects_endpoint():
    """List all saved .umlproj project files."""
    return {"projects": list_projects()}
