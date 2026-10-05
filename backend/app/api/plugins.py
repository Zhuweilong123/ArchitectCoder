"""Read-only plugin organization and execution-plan inspection."""
from typing import Literal

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


@router.get("/graph", response_class=PlainTextResponse)
async def plugin_graph(request: Request, view: Literal["schedule", "organization"] = "schedule"):
    return _plan(request).mermaid(view)
