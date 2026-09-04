"""评测 MVP API：用例目录受控，运行结果与 Trace 可追溯。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent_base.core.evals import load_evals
from extensions.evals.models import EvalResult
from extensions.evals.batches import (
    EvalArchiveRequest,
    EvalBatchRequest,
)
from extensions.evals.paths import baseline_path

router = APIRouter(prefix="/api/evals", tags=["evals"])
BASELINE_PATH = baseline_path()
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class EvalRunRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)


class EvalBaselineArchiveRequest(BaseModel):
    note: str = Field(default="", max_length=500)


def _read_baseline() -> dict:
    if not BASELINE_PATH.is_file():
        raise HTTPException(status_code=404, detail="evaluation baseline not found")
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="evaluation baseline is invalid") from exc


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _repository_info() -> dict[str, str | bool]:
    commit = _git_output("rev-parse", "HEAD")
    branch = _git_output("branch", "--show-current") or "detached"
    dirty = bool(_git_output("status", "--porcelain"))
    version = f"{branch}@{commit}" if commit else "unknown"
    return {
        "branch": branch,
        "commit": commit,
        "version": version,
        "dirty": dirty,
    }


@router.get("/cases")
async def list_cases():
    cases = load_evals().list_cases()
    return {
        "cases": [
            {
                "id": case.id,
                "agent": "devagent",
                "name": case.name,
                "prompt": case.prompt,
                "project_id": case.project_id,
                "checkers": case.checkers,
                "hard_checkers": case.hard_checkers,
                "metadata": case.metadata,
            }
            for case in cases
        ]
    }


@router.get("/baseline")
async def get_baseline():
    return _read_baseline()


@router.get("/repository")
async def get_repository():
    return _repository_info()


@router.post("/baseline/archive")
async def archive_baseline(request: EvalBaselineArchiveRequest):
    try:
        return load_evals().archive_baseline(_read_baseline(), request.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/run", response_model=EvalResult)
async def run_eval(request: EvalRunRequest):
    provider = load_evals()
    case = provider.get_case(request.case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Evaluation case not found: {request.case_id}")
    return await provider.run_case(case)


@router.get("/results")
async def list_results(limit: int = 100):
    return {"results": load_evals().list_results(limit)}


@router.post("/runs")
async def start_eval_batch(request: EvalBatchRequest):
    try:
        batch = await load_evals().start_batch(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return batch


@router.get("/runs")
async def list_eval_batches(limit: int = 20):
    return {"runs": load_evals().list_batches(limit)}


@router.get("/runs/{batch_id}")
async def get_eval_batch(batch_id: str):
    batch = load_evals().get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="evaluation batch not found")
    return batch


@router.get("/trends")
async def eval_trends(limit: int = 20):
    return {"trends": load_evals().trends(limit)}


@router.post("/archives")
async def archive_eval(request: EvalArchiveRequest):
    try:
        return load_evals().archive(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evaluation batch not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/archives")
async def list_eval_archives(limit: int = 20):
    return {"archives": load_evals().list_archives(limit)}
