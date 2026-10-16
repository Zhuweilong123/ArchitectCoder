"""Tool capabilities required by the native execution loop."""
from typing import Protocol, runtime_checkable

from ..host_api.agents import ToolRegistryPort
from ..host_api.data import JsonObject, ToolSchema
from ..host_api.recovery import RecoveryToolLookup
from .result import ToolResult


class ToolOutputWriter(Protocol):
    def put(self, tool_name: str, text: str) -> str: ...


@runtime_checkable
class NativeToolRegistryPort(ToolRegistryPort, RecoveryToolLookup, Protocol):
    @property
    def output_store(self) -> ToolOutputWriter: ...

    def get_openai_specs_for(self, names: list[str]) -> list[ToolSchema]: ...
    def can_parallel(self, name: str) -> bool: ...
    async def aexecute_tool_result_with_params(self, name: str, parameters: JsonObject) -> ToolResult: ...
