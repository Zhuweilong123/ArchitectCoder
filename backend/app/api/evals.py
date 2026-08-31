"""评测 MVP API：用例目录受控，运行结果与 Trace 可追溯。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.evals.models import EvalResult
from app.evals.batches import (
    EvalArchiveRequest,
    EvalBatchRequest,
    get_batch_manager,
)
from app.evals.registry import load_cases
from app.evals.runner import EvalRunner

router = APIRouter(prefix="/api/evals", tags=["evals"])


class EvalRunRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)


@router.get("/cases")
async def list_cases():
    cases = load_cases()
    return {
        "cases": [
            {
                "id": case.id,
                "name": case.name,
                "prompt": case.prompt,
                "project_id": case.project_id,
                "checkers": case.checkers,
                "hard_checkers": case.hard_checkers,
                "metadata": case.metadata,
            }
            for case in cases.values()
        ]
    }


@router.post("/run", response_model=EvalResult)
async def run_eval(request: EvalRunRequest):
    case = load_cases().get(request.case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Evaluation case not found: {request.case_id}")
    return await EvalRunner().run_case(case)


@router.get("/results")
async def list_results(limit: int = 100):
    return {"results": EvalRunner().list_results(limit)}


@router.post("/runs")
async def start_eval_batch(request: EvalBatchRequest):
    try:
        batch = await get_batch_manager().start(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return batch


@router.get("/runs")
async def list_eval_batches(limit: int = 20):
    return {"runs": get_batch_manager().list_batches(limit)}


@router.get("/runs/{batch_id}")
async def get_eval_batch(batch_id: str):
    batch = get_batch_manager().get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="evaluation batch not found")
    return batch


@router.get("/trends")
async def eval_trends(limit: int = 20):
    return {"trends": get_batch_manager().trends(limit)}


@router.post("/archives")
async def archive_eval(request: EvalArchiveRequest):
    try:
        return get_batch_manager().archive(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evaluation batch not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/archives")
async def list_eval_archives(limit: int = 20):
    return {"archives": get_batch_manager().list_archives(limit)}
