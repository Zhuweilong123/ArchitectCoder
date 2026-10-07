"""Requests shared by execution slots; no agent or transaction implementation."""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExecutionRequest:
    interface_id: str
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Callable] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    allowed_tools: list[str] | None = None
    allowed: bool = True
    message: str = ""
    stop_reason: str = "execution_check_failed"
    recovery_event: dict[str, Any] = field(default_factory=lambda: {
        "event": "candidate_recovery_available", "action": "修复检查问题并继续"})


class ExecutionSlots:
    PREPARE = "execution.prepare"
    CHECK = "execution.check"
    REJECTED = "execution.rejected"
    COMMITTED = "execution.committed"
