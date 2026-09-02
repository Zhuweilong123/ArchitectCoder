"""Small helpers for exercising asynchronous tools in tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def run_tool(tool: Any, params: dict) -> str:
    """Execute an async tool using the same event-loop shape as production."""

    return asyncio.run(tool._execute(params))


def run_json_tool(tool: Any, params: dict) -> dict:
    """Execute a tool whose result contract is a JSON object."""

    return json.loads(run_tool(tool, params))


def tool_by_name(tools: list[Any], name: str) -> Any:
    """Return a registered tool by its stable public name."""

    return next(tool for tool in tools if tool.name == name)
