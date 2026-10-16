"""Adapt text-only plugin registries to the native tool execution contract."""
from ..host_api.agents import ToolRegistryPort
from ..host_api.data import JsonObject, ToolSchema
from ..tools.result import ToolResult
from app.runtime.tool_outputs import ToolOutputStore


class TextToolRegistryAdapter:
    def __init__(self, registry: ToolRegistryPort) -> None:
        self._registry = registry
        self._allowed_tools: set[str] | None = None
        self.output_store = ToolOutputStore()

    def set_allowed_tools(self, names: list[str] | None) -> None:
        self._allowed_tools = set(names) if names is not None else None
        self._registry.set_allowed_tools(names)

    def get_openai_specs(self) -> list[ToolSchema]:
        return self._registry.get_openai_specs()

    def get_openai_specs_for(self, names: list[str]) -> list[ToolSchema]:
        specs = {spec["function"]["name"]: spec for spec in self.get_openai_specs()}
        return [specs[name] for name in names if name in specs]

    def get_tools_description(self) -> str:
        return self._registry.get_tools_description()

    def list_tools(self) -> list[str]:
        return self._registry.list_tools()

    def get_tool(self, name: str) -> None:
        return None

    def can_parallel(self, name: str) -> bool:
        # Recorded tool observations must retain their consumption order.
        return False

    async def aexecute_tool_result_with_params(self, name: str, parameters: JsonObject) -> ToolResult:
        if self._allowed_tools is not None and name not in self._allowed_tools:
            return ToolResult(status="blocked", data=f"Tool '{name}' is not enabled", error_code="POLICY_BLOCKED")
        return ToolResult.from_value(await self._registry.aexecute_tool_with_params(name, parameters))

    async def aexecute_tool_with_params(self, name: str, parameters: JsonObject) -> str:
        return (await self.aexecute_tool_result_with_params(name, parameters)).text

    def __len__(self) -> int:
        return len(self._registry)
