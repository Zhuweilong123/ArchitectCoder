"""Per-turn evaluation state; checker providers receive serializable configuration."""
from typing import TypedDict
from app.agent_base.host_api.data import JsonObject
from app.agent_base.host_api.execution_types import RunCheckpoint, ToolCallDetail

class EvaluationActiveTurn(TypedDict):
    turn: int
    details: list[ToolCallDetail]
    checkpoint: RunCheckpoint
    summary_written: bool

class EvaluationTurnRecord(TypedDict, total=False):
    turn: int
    prompt: str
    model: str
    status: str
    tool_calls: int
    total_tokens: int
    answer: str
    checker_results: list[JsonObject]
    token_budget_stop_reason: str

class CheckerRuntime(TypedDict, total=False):
    total_tool_calls: int
    turn_tool_calls: int
    turn_tool_names: list[str]
