"""Shared data types for the ReAct runtime components."""

from __future__ import annotations

from typing import Optional

from ...outcome import RunOutcome


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
        tool_calls_detail: Optional[list[dict]] = None,
        thought: str = "",
        is_final: bool = False,
        final_answer: str = "",
        outcome: Optional[RunOutcome] = None,
    ):
        self.step = step
        self.actions = actions or []
        self.tool_calls_detail = tool_calls_detail or []
        self.thought = thought
        self.is_final = is_final
        self.final_answer = final_answer
        self.outcome = outcome

    def to_dict(self) -> dict:
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
