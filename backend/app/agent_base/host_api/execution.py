"""Requests shared by execution slots; no agent or transaction implementation."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypedDict
from .contexts import AnalysisRequest, ReviewPrompt


class ExecutionData(TypedDict, total=False):
    user_message: str
    project_file: str
    source_dir: str
    test_dir: str
    design_dir: str
    workspace_root: str
    workspace_manifest: dict[str, Any]
    changed_paths: tuple[dict[str, Any] | str, ...] | list[dict[str, Any]]
    previous_checkpoint: dict[str, Any]
    available_tools: tuple[str, ...]
    design_contract_enabled: bool | None
    record_event: Callable[..., None] | None
    rollback_completed: bool | None
    phase: str


class ExecutionCapabilities(TypedDict, total=False):
    emit: Callable[[dict], Awaitable[bool]]
    request_review: Callable[[ReviewPrompt], Awaitable[str]] | None
    analyze: Callable[[AnalysisRequest], Awaitable[str]]


@dataclass
class ExecutionRequest:
    interface_id: str
    run_id: str = ""
    data: ExecutionData = field(default_factory=dict)
    capabilities: ExecutionCapabilities = field(default_factory=dict)
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
