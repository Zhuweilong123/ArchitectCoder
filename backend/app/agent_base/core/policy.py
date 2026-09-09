"""Per-run execution policy primitives.

The policy objects are deliberately independent from the Agent loop.  Hooks
feed them observations and the loop only consumes the resulting control
decision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionBudget:
    """Per-run hard resource budget created from application Settings."""

    max_tool_calls: int
    max_run_seconds: float
    max_total_tokens: int
    token_finalization_reserve_tokens: int = 12000
    started_at: float = field(default_factory=time.monotonic, init=False)
    tool_call_count: int = field(default=0, init=False)
    total_tokens: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.max_tool_calls = max(1, int(self.max_tool_calls))
        self.max_run_seconds = max(1.0, float(self.max_run_seconds))
        self.max_total_tokens = max(1, int(self.max_total_tokens))
        self.token_finalization_reserve_tokens = min(
            max(1, int(self.token_finalization_reserve_tokens)),
            max(1, self.max_total_tokens - 1),
        )

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> "ExecutionBudget":
        """Build a budget from Settings, with optional bounded per-run overrides."""
        values = {
            "max_tool_calls": settings.agent_max_tool_calls,
            "max_run_seconds": settings.agent_max_run_seconds,
            "max_total_tokens": settings.agent_per_run_execution_budget_tokens,
            "token_finalization_reserve_tokens": (
                settings.agent_token_finalization_reserve_tokens
            ),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    def start(self, initial_token_usage: int = 0) -> None:
        """Reset counters when a budget is attached to a new run."""
        self.started_at = time.monotonic()
        self.tool_call_count = 0
        self.total_tokens = max(0, int(initial_token_usage or 0))

    def record_tokens(self, count: int) -> None:
        self.total_tokens += max(0, int(count or 0))

    def before_llm(self) -> str | None:
        if self.total_tokens >= self.max_total_tokens:
            return "hard_limit_before_next_llm"
        if time.monotonic() - self.started_at >= self.max_run_seconds:
            return "time_limit"
        return None

    def before_tool(self) -> str | None:
        if self.tool_call_count >= self.max_tool_calls:
            return "tool_call_limit"
        self.tool_call_count += 1
        return None

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_total_tokens - self.total_tokens)

    @property
    def finalization_required(self) -> bool:
        return self.remaining_tokens <= self.token_finalization_reserve_tokens


__all__ = ["ExecutionBudget"]
