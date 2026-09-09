"""HTTP adapter for the optional Trace-to-evaluation-case capability.

The route contract is owned by the Evals extension.  The host application only
mounts this router when the Evals plugin exposes its router entry point.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent_base.core.evals import load_evals

from .trace_cases import (
    TraceCaseCaptureRequest,
    TraceCaseDraftRequest,
    TraceCasePublishRequest,
    TraceCaseReviewRequest,
)

router = APIRouter(prefix="/api/evals", tags=["evals.trace_cases"])


@router.get("/trace-cases/projects")
async def list_trace_case_projects():
    try:
        return {"projects": load_evals().list_trace_case_projects()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/trace-cases/drafts")
async def list_trace_case_drafts():
    try:
        return {"drafts": load_evals().list_trace_case_drafts()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/trace-cases/drafts")
async def create_trace_case_draft(request: TraceCaseDraftRequest):
    try:
        return await load_evals().create_trace_case_draft(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/trace-cases/drafts/{draft_id}")
async def delete_trace_case_draft(draft_id: str):
    try:
        return load_evals().delete_trace_case_draft(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/trace-cases/drafts/{draft_id}")
async def get_trace_case_draft(draft_id: str):
    try:
        draft = load_evals().get_trace_case_draft(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Trace case draft not found")
    return draft


@router.post("/trace-cases/drafts/{draft_id}/capture-fixture")
async def capture_trace_case_fixture(draft_id: str, request: TraceCaseCaptureRequest):
    try:
        return load_evals().capture_trace_case_fixture(draft_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/trace-cases/drafts/{draft_id}/fixture-preview")
async def preview_trace_case_fixture(draft_id: str, request: TraceCaseCaptureRequest):
    try:
        return load_evals().preview_trace_case_fixture(draft_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/trace-cases/drafts/{draft_id}")
async def review_trace_case_draft(draft_id: str, request: TraceCaseReviewRequest):
    try:
        return load_evals().review_trace_case_draft(draft_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/trace-cases/drafts/{draft_id}/validate")
async def validate_trace_case_draft(draft_id: str):
    try:
        return await load_evals().validate_trace_case_draft(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/trace-cases/drafts/{draft_id}/publish")
async def publish_trace_case_draft(draft_id: str, request: TraceCasePublishRequest):
    try:
        return load_evals().publish_trace_case_draft(draft_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Trace case draft not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]