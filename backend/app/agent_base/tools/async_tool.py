"""Shared asynchronous Tool base class.

This is framework infrastructure and must not live in a conversation-specific
tool factory. Extensions can depend on this module without depending on the
application's tool assembly layer.
"""

from __future__ import annotations

from app.agent_base.host_api.data import JsonObject, ToolSchema

from .base import Tool, ToolParameter
from .contracts import ToolValue
from .result import ToolResult


class AsyncTool(Tool):
    """Tool adapter whose ``run`` method returns an awaitable execution."""

    def get_parameters(self) -> list[ToolParameter]:
        return []

    async def run(self, parameters: JsonObject) -> str:
        return await self._execute(parameters)

    async def _execute(self, parameters: JsonObject) -> str:
        raise NotImplementedError

    async def run_result(self, parameters: JsonObject) -> ToolResult:
        return ToolResult.from_value(await self._execute_result(parameters))

    async def _execute_result(self, parameters: JsonObject) -> ToolValue:
        return await self._execute(parameters)

    def to_openai_schema(self) -> ToolSchema:
        raise NotImplementedError


__all__ = ["AsyncTool"]
