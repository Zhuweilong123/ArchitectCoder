"""Tool catalog and OpenAI schema registry.

Execution is delegated to :class:`app.agent_base.execution.ToolExecutor` so
the registry remains focused on discovery and compatibility APIs.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, Optional

from app.agent_base.execution.tool_executor import ToolExecutor
from app.core.capabilities import CapabilityPolicy

from .base import Tool
from .result import ToolResult


class ToolRegistry:
    """Register tools/functions and expose their model schemas."""

    def __init__(self, policy: CapabilityPolicy | None = None):
        self._tools: dict[str, Tool] = {}
        self._functions: dict[str, dict[str, Any]] = {}
        self.executor = ToolExecutor(
            self._tools,
            self._functions,
            policy or CapabilityPolicy(),
        )

    @property
    def policy(self) -> CapabilityPolicy:
        return self.executor.policy

    def set_allowed_tools(self, names: list[str] | None) -> None:
        """Set the current run's tool capability allowlist."""
        self.policy.set_allowed_tools(names)

    def register_tool(self, tool: Tool) -> None:
        if tool.name in self._tools:
            print(f"Warning: tool '{tool.name}' already exists and will be replaced")
        self._tools[tool.name] = tool

    def register_function(
        self,
        name: str,
        description: str,
        func: Callable[[str], Any],
    ) -> None:
        if name in self._functions:
            print(f"Warning: function tool '{name}' already exists and will be replaced")
        self._functions[name] = {
            "description": description,
            "func": func,
        }

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        if name in self._functions:
            del self._functions[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def get_tools_description(self) -> str:
        descriptions = [
            f"- {tool.name}: {tool.description}"
            for tool in self._tools.values()
        ]
        descriptions.extend(
            f"- {name}: {info['description']}"
            for name, info in self._functions.items()
        )
        return "\n".join(descriptions) if descriptions else "暂无可用工具"

    @staticmethod
    def _compact_openai_spec(schema: dict) -> dict:
        """Reduce repeated schema prose while preserving call shape."""
        compact = copy.deepcopy(schema)
        function = compact.get("function", {})
        description = str(function.get("description") or "")
        if len(description) > 240:
            function["description"] = (
                description[:160].rstrip() + " ... " + description[-70:].lstrip()
            )
        parameters = function.get("parameters")

        def strip_parameter_descriptions(value: Any) -> None:
            if isinstance(value, dict):
                value.pop("description", None)
                for child in value.values():
                    strip_parameter_descriptions(child)
            elif isinstance(value, list):
                for child in value:
                    strip_parameter_descriptions(child)

        strip_parameter_descriptions(parameters)
        return compact

    def get_openai_specs(self, compact: bool = False) -> list[dict]:
        specs = []
        for tool in self._tools.values():
            schema = tool.to_openai_schema()
            params = schema.get("function", {}).get("parameters", {})
            if params.get("type") == "object":
                params.setdefault("additionalProperties", False)
            specs.append(self._compact_openai_spec(schema) if compact else schema)

        for name, info in self._functions.items():
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": info["description"],
                            },
                        },
                        "required": ["input"],
                        "additionalProperties": False,
                    },
                },
            }
            specs.append(self._compact_openai_spec(schema) if compact else schema)
        return specs

    def get_openai_specs_for(self, names: list[str], compact: bool = False) -> list[dict]:
        all_specs = {
            spec["function"]["name"]: spec
            for spec in self.get_openai_specs(compact=compact)
        }
        return [all_specs[name] for name in names if name in all_specs]

    def can_parallel(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.read_only and tool.can_parallel)

    def list_tools(self) -> list[str]:
        return list(self._tools) + list(self._functions)

    # Compatibility execution facade. New adapters can inject/replace the
    # executor without changing registration or Agent call sites.
    def execute_tool(self, name: str, input_data: str) -> str:
        return self.executor.execute_tool(name, input_data)

    def execute_tool_with_params(self, name: str, parameters: dict[str, Any]) -> str:
        return self.executor.execute_tool_with_params(name, parameters)

    async def aexecute_tool_with_params(
        self, name: str, parameters: dict[str, Any],
    ) -> str:
        return await self.executor.aexecute_tool_with_params(name, parameters)

    async def aexecute_tool_result_with_params(
        self, name: str, parameters: dict[str, Any],
    ) -> ToolResult:
        return await self.executor.aexecute_tool_result_with_params(name, parameters)

    def __len__(self) -> int:
        return len(self._tools) + len(self._functions)

    def __contains__(self, name: str) -> bool:
        return name in self._tools or name in self._functions

    def __bool__(self) -> bool:
        return bool(len(self))


__all__ = ["ToolRegistry"]
