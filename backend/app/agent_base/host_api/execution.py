"""Requests shared by execution slots; no agent or transaction implementation."""
from dataclasses import dataclass, field
from typing import Any, Callable
from contextlib import contextmanager
from copy import deepcopy


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
    rejections: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def contribution_scope(self, identifier: str):
        """Checks can add rejections, but cannot remove an earlier veto."""
        if self.interface_id != ExecutionSlots.CHECK:
            yield
            return
        prior = None if self.allowed else (self.message, self.stop_reason, deepcopy(self.recovery_event))
        try:
            yield
        finally:
            if not self.allowed and (prior is None or
                    (self.message, self.stop_reason, self.recovery_event) != prior):
                self.rejections.append({"contribution_id": identifier, "message": self.message,
                                        "stop_reason": self.stop_reason})
            if prior is not None:
                self.allowed = False
                self.message, self.stop_reason, self.recovery_event = prior


class ExecutionSlots:
    PREPARE = "execution.prepare"
    CHECK = "execution.check"
    REJECTED = "execution.rejected"
    COMMITTED = "execution.committed"
