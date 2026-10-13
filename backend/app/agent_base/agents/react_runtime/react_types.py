"""Shared data types for the ReAct runtime components."""

from __future__ import annotations

from typing import Optional, TypedDict
from ...host_api.execution_types import StreamStep, ToolCallDetail

from ...outcome import RunOutcome


class NativeRunOptions(TypedDict, total=False):
    allowed_tools: list[str] | None
    initial_token_usage: int
    temperature: float
    llm_timeout_seconds: float


class ReActProgress:
    """A single streamed ReAct progress snapshot."""

    __slots__ = (
        "step", "actions", "tool_calls_detail", "thought",
        "is_final", "final_answer", "outcome",
    )

    def __init__(
        self,
        step: int,
        actions: Optional[list[str]] = None,
        tool_calls_detail: Optional[list[ToolCallDetail]] = None,
        thought: str = "",
        is_final: bool = False,
        final_answer: str = "",
        outcome: Optional[RunOutcome] = None,
    ) -> None:
        self.step = step
        self.actions = actions or []
        self.tool_calls_detail = tool_calls_detail or []
        self.thought = thought
        self.is_final = is_final
        self.final_answer = final_answer
        self.outcome = outcome

    def to_dict(self) -> StreamStep:
        return {
            "step": self.step,
            "actions": self.actions,
            "tool_calls_detail": self.tool_calls_detail,
            "thought": self.thought[:500],
            "is_final": self.is_final,
            "final_answer": self.final_answer,
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }


__all__ = ["ReActProgress"]
