"""Read-only API for durable harness run state."""

from fastapi import APIRouter, HTTPException

from app.services.run_state import get_run_store

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
async def list_runs(limit: int = 50, session_id: str = ""):
    return {
        "runs": [
            item.to_dict()
            for item in get_run_store().list(limit=limit, session_id=session_id)
        ]
    }


@router.get("/{run_id}")
async def get_run(run_id: str):
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.to_dict()
