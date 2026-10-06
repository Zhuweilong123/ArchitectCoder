"""Read-only plugin organization and execution-plan inspection."""
from typing import Literal
import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _plan(request):
    plan = getattr(request.app.state, "plugin_plan", None)
    if plan is None:
        raise HTTPException(503, "Plugin plan has not been initialized")
    return plan


@router.get("/plan")
async def plugin_plan(request: Request):
    return _plan(request).as_dict()


@router.get("/diagnostics")
async def plugin_diagnostics(request: Request):
    plan = _plan(request).as_dict()
    manager = getattr(request.app.state, "plugin_manager", None)
    return {"plan_id": plan["plan_id"], "plugins": manager.diagnostics() if manager else []}


@router.get("/plans/{plan_id}")
async def archived_plugin_plan(request: Request, plan_id: str):
    current = _plan(request).as_dict()
    if not re.fullmatch(r"[0-9a-f]{64}", plan_id):
        raise HTTPException(422, "Invalid plugin plan ID")
    if plan_id == current["plan_id"]:
        return current
    directory = getattr(request.app.state, "plugin_plan_dir", None)
    if directory is None:
        raise HTTPException(404, "Archived plugin plan not found")
    try:
        data = json.loads((Path(directory) / "history" / f"{plan_id}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, "Archived plugin plan not found")
    except (ValueError, OSError):
        raise HTTPException(503, "Archived plugin plan cannot be read")
    if not isinstance(data, dict):
        raise HTTPException(503, "Archived plugin plan is invalid")
    payload = {key: value for key, value in data.items() if key != "plan_id"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if digest != plan_id or data.get("plan_id") != plan_id:
        raise HTTPException(503, "Archived plugin plan integrity check failed")
    return data


@router.get("/graph", response_class=PlainTextResponse)
async def plugin_graph(request: Request, view: Literal["schedule", "organization"] = "schedule"):
    return _plan(request).mermaid(view)
