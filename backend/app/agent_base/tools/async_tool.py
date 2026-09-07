"""Shared asynchronous Tool base class.

This is framework infrastructure and must not live in a conversation-specific
tool factory. Extensions can depend on this module without depending on the
application's tool assembly layer.
"""

from __future__ import annotations

from .base import Tool
from .result import ToolResult


class AsyncTool(Tool):
    """Tool adapter whose ``run`` method returns an awaitable execution."""

    def get_parameters(self) -> list:
        return []

    def run(self, parameters: dict) -> str:
        return self._execute(parameters)  # type: ignore[return-value]

    async def _execute(self, parameters: dict) -> str:
        raise NotImplementedError

    async def run_result(self, parameters: dict) -> ToolResult:
        return ToolResult.from_value(await self._execute_result(parameters))

    async def _execute_result(self, parameters: dict):
        return await self._execute(parameters)

    def to_openai_schema(self) -> dict:
        raise NotImplementedError


__all__ = ["AsyncTool"]
