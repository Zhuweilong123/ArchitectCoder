"""Tool capabilities required by the native execution loop."""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from ..host_api.agents import ToolRegistryPort
from ..host_api.data import JsonObject, JsonValue, ToolSchema
from ..host_api.recovery import RecoveryToolLookup
from .result import ToolResult


ToolValue: TypeAlias = JsonValue | ToolResult
ToolExecution: TypeAlias = ToolValue | Awaitable[ToolValue]
ToolFunction: TypeAlias = Callable[[str], ToolExecution]


@dataclass(frozen=True)
class RegisteredFunction:
    description: str
    func: ToolFunction


class ToolOutputWriter(Protocol):
    def put(self, tool_name: str, text: str) -> str: ...


class ToolOutputStorePort(ToolOutputWriter, Protocol):
    def read(self, output_id: str, *, excluded_tool_names: frozenset[str] = frozenset()) -> str | None: ...


@runtime_checkable
class ToolOutputBindable(Protocol):
    def bind_output_store(self, store: ToolOutputStorePort) -> None: ...


@runtime_checkable
class NativeToolRegistryPort(ToolRegistryPort, RecoveryToolLookup, Protocol):
    @property
    def output_store(self) -> ToolOutputWriter: ...

    def get_openai_specs_for(self, names: list[str]) -> list[ToolSchema]: ...
    def can_parallel(self, name: str) -> bool: ...
    async def aexecute_tool_result_with_params(self, name: str, parameters: JsonObject) -> ToolResult: ...
