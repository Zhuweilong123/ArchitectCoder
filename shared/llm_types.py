"""Serializable model messages and schemas shared by gateways and Agent ports."""
from __future__ import annotations
from typing import Literal, NotRequired, TypeAlias, TypedDict, TYPE_CHECKING
from typing_extensions import TypeAliasType

if TYPE_CHECKING:
    JsonValue: TypeAlias = str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue']
else:
    # A named recursive alias lets runtime validators resolve the schema once.
    JsonValue = TypeAliasType('JsonValue', str | int | float | bool | None | list['JsonValue'] | dict[str, 'JsonValue'])
JsonObject: TypeAlias = dict[str, JsonValue]

class FunctionCall(TypedDict):
    name: str
    arguments: str

class ModelToolCall(TypedDict):
    id: str
    type: Literal['function']
    function: FunctionCall

class ModelMessage(TypedDict):
    role: Literal['system', 'user', 'assistant', 'tool', 'summary']
    content: str | None
    tool_calls: NotRequired[list[ModelToolCall]]
    tool_call_id: NotRequired[str]
    name: NotRequired[str]

class FunctionSchema(TypedDict):
    name: str
    description: NotRequired[str]
    parameters: JsonObject

class ToolSchema(TypedDict):
    type: Literal['function']
    function: FunctionSchema

class TokenUsage(TypedDict, total=False):
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int

class ModelResponse(TypedDict):
    content: str | None
    tool_calls: list[ModelToolCall] | None
    usage: TokenUsage | None

class ModelCallOptions(TypedDict, total=False):
    model: str
    temperature: float
    max_tokens: int | None
    timeout: float
    json_mode: bool
    trace_context: JsonObject

