"""Provider-neutral execution boundary for registered agent tools."""

from __future__ import annotations

import inspect
from collections.abc import MutableMapping
from typing import Any, Callable

from app.agent_base.tools.base import Tool
from app.agent_base.tools.result import ToolResult
from app.core.capabilities import CapabilityPolicy


class ToolExecutor:
    """Execute tools after applying the run capability policy.

    The executor deliberately receives tool mappings instead of a registry.
    This keeps lookup and registration separate, and makes the execution
    boundary replaceable without changing Agent code.
    """

    def __init__(
        self,
        tools: MutableMapping[str, Tool],
        functions: MutableMapping[str, dict[str, Any]],
        policy: CapabilityPolicy,
    ) -> None:
        self.tools = tools
        self.functions = functions
        self.policy = policy

    def _policy_error(
        self, name: str, parameters: dict[str, Any],
    ) -> ToolResult | None:
        message = self.policy.check(name, parameters)
        if message is None:
            return None
        return ToolResult(
            status="blocked",
            data=f"Error: {message}",
            error_code="POLICY_BLOCKED",
        )

    @staticmethod
    def _sync_async_error() -> str:
        return (
            "ERROR: async tool returned coroutine in sync context. "
            "Use aexecute_tool_with_params() instead."
        )

    def execute_tool(self, name: str, input_data: str) -> str:
        """Execute the legacy string-input API."""
        policy_input = {"command": input_data} if name == "bash" else {"input": input_data}
        policy_error = self._policy_error(name, policy_input)
        if policy_error is not None:
            return policy_error.text

        tool = self.tools.get(name)
        if tool is not None:
            try:
                result = tool.run({"input": input_data})
                if inspect.isawaitable(result):
                    return self._sync_async_error()
                return result.text if isinstance(result, ToolResult) else str(result)
            except Exception as exc:
                return f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}"

        function = self.functions.get(name)
        if function is not None:
            try:
                result = function["func"](input_data)
                if inspect.isawaitable(result):
                    return self._sync_async_error()
                return result.text if isinstance(result, ToolResult) else str(result)
            except Exception as exc:
                return f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}"

        return f"错误：未找到工具 '{name}'"

    def execute_tool_with_params(
        self, name: str, parameters: dict[str, Any],
    ) -> str:
        """Execute a tool synchronously with structured parameters."""
        policy_error = self._policy_error(name, parameters)
        if policy_error is not None:
            return policy_error.text

        tool = self.tools.get(name)
        if tool is not None:
            try:
                result = tool.run(parameters)
                if inspect.isawaitable(result):
                    return self._sync_async_error()
                return result.text if isinstance(result, ToolResult) else str(result)
            except Exception as exc:
                return f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}"

        function = self.functions.get(name)
        if function is not None:
            try:
                result = function["func"](parameters.get("input", ""))
                if inspect.isawaitable(result):
                    return self._sync_async_error()
                return result.text if isinstance(result, ToolResult) else str(result)
            except Exception as exc:
                return f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}"

        return f"错误：未找到工具 '{name}'"

    async def aexecute_tool_with_params(
        self, name: str, parameters: dict[str, Any],
    ) -> str:
        return (await self.aexecute_tool_result_with_params(name, parameters)).text

    async def aexecute_tool_result_with_params(
        self, name: str, parameters: dict[str, Any],
    ) -> ToolResult:
        """Execute asynchronously and always return a structured result."""
        policy_error = self._policy_error(name, parameters)
        if policy_error is not None:
            return policy_error

        tool = self.tools.get(name)
        if tool is not None:
            try:
                result = tool.run(parameters)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, ToolResult):
                    return result
                text = str(result)
                if text.lstrip().lower().startswith(("error:", "鉂?")):
                    return ToolResult.error(text, "TOOL_REPORTED_ERROR")
                return ToolResult.success(result)
            except Exception as exc:
                return ToolResult.error(
                    f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}",
                    "TOOL_EXECUTION_ERROR",
                )

        function = self.functions.get(name)
        if function is not None:
            try:
                result = function["func"](parameters.get("input", ""))
                if inspect.isawaitable(result):
                    result = await result
                return result if isinstance(result, ToolResult) else ToolResult.success(result)
            except Exception as exc:
                return ToolResult.error(
                    f"鉂?宸ュ叿 '{name}' 鎵ц澶辫触: {exc}",
                    "TOOL_EXECUTION_ERROR",
                )

        return ToolResult.error(
            f"错误：未找到工具 '{name}'",
            "TOOL_NOT_FOUND",
        )


__all__ = ["ToolExecutor"]
