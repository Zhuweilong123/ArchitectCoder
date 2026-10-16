"""Provider-neutral execution boundary for registered agent tools."""

from __future__ import annotations

import inspect
from collections.abc import MutableMapping

from app.agent_base.tools.base import Tool
from app.agent_base.tools.contracts import RegisteredFunction, ToolExecution
from app.agent_base.tools.result import ToolResult
from app.agent_base.host_api.data import JsonObject
from app.agent_base.host_api.errors import AgentInterrupted
from app.core.capabilities import CapabilityPolicy


class ToolExecutor:
    """Execute typed tools/functions after applying the run capability policy."""

    def __init__(
        self, tools: MutableMapping[str, Tool],
        functions: MutableMapping[str, RegisteredFunction], policy: CapabilityPolicy,
    ) -> None:
        self.tools = tools
        self.functions = functions
        self.policy = policy

    def _policy_error(self, name: str, parameters: JsonObject) -> ToolResult | None:
        message = self.policy.check(name, parameters)
        if message is None:
            return None
        return ToolResult(status="blocked", data=f"Error: {message}", error_code="POLICY_BLOCKED")

    @staticmethod
    def _sync_async_error() -> str:
        return "ERROR: async tool cannot run in sync context. Use aexecute_tool_with_params() instead."

    @staticmethod
    def _function_input(parameters: JsonObject) -> str | ToolResult:
        value = parameters.get("input", "")
        if not isinstance(value, str):
            return ToolResult.error("Error: function input must be a string", "INVALID_ARGUMENT", True)
        return value

    @staticmethod
    def _execution_error(name: str, error: Exception) -> ToolResult:
        return ToolResult.error(f"Error: tool '{name}' failed: {error}", "TOOL_EXECUTION_ERROR")

    def execute_tool(self, name: str, input_data: str) -> str:
        """Retain legacy string input and its shell-specific policy check."""
        policy_input: JsonObject = {"command": input_data} if name == "shell" else {"input": input_data}
        policy_error = self._policy_error(name, policy_input)
        if policy_error is not None:
            return policy_error.text
        return self._execute_sync(name, {"input": input_data})

    def execute_tool_with_params(self, name: str, parameters: JsonObject) -> str:
        policy_error = self._policy_error(name, parameters)
        if policy_error is not None:
            return policy_error.text
        return self._execute_sync(name, parameters)

    def _execute_sync(self, name: str, parameters: JsonObject) -> str:
        try:
            tool = self.tools.get(name)
            result: ToolExecution
            if tool is not None:
                # Reject declared async entry points before creating a coroutine.
                if inspect.iscoroutinefunction(tool.run):
                    return self._sync_async_error()
                result = tool.run(parameters)
            else:
                function = self.functions.get(name)
                if function is None:
                    return f"错误：未找到工具 '{name}'"
                value = self._function_input(parameters)
                if isinstance(value, ToolResult):
                    return value.text
                if inspect.iscoroutinefunction(function.func):
                    return self._sync_async_error()
                result = function.func(value)
            if inspect.isawaitable(result):
                # A synchronous wrapper can still return a coroutine. Close
                # that unstarted coroutine so rejection does not leak it.
                if inspect.iscoroutine(result):
                    result.close()
                return self._sync_async_error()
            return result.text if isinstance(result, ToolResult) else str(result)
        except AgentInterrupted:
            raise
        except Exception as exc:
            return self._execution_error(name, exc).text

    async def aexecute_tool_with_params(self, name: str, parameters: JsonObject) -> str:
        return (await self.aexecute_tool_result_with_params(name, parameters)).text

    async def aexecute_tool_result_with_params(self, name: str, parameters: JsonObject) -> ToolResult:
        policy_error = self._policy_error(name, parameters)
        if policy_error is not None:
            return policy_error
        try:
            tool = self.tools.get(name)
            result: ToolExecution
            if tool is not None:
                result = tool.run_result(parameters)
            else:
                function = self.functions.get(name)
                if function is None:
                    return ToolResult.error(f"错误：未找到工具 '{name}'", "TOOL_NOT_FOUND")
                value = self._function_input(parameters)
                if isinstance(value, ToolResult):
                    return value
                result = function.func(value)
            if inspect.isawaitable(result):
                result = await result
            return ToolResult.from_value(result)
        except AgentInterrupted:
            raise
        except Exception as exc:
            return self._execution_error(name, exc)


__all__ = ["ToolExecutor"]
