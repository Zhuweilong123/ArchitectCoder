"""Public phase, contribution and hook data contracts; no loading or execution."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from .services import RuntimePort

class HookEvent(str, Enum):
    INITIALIZE = "initialize"
    PREPARE = "prepare"
    RUN_START = "run_start"
    ROUND_BEFORE = "round_before"
    MODEL_BEFORE = "model_before"
    MODEL_AFTER = "model_after"
    TOOL_BATCH_BEFORE = "tool_batch_before"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"
    TOOL_BATCH_AFTER = "tool_batch_after"
    ROUND_AFTER = "round_after"
    FINALIZE = "finalize"
    RUN_END = "run_end"
    # Notifications are deliberately outside PUBLIC_STAGES.
    ERROR = "error"
    CANCEL = "cancel"
    REVIEW_AFTER = "review_after"
    BACKGROUND_BEFORE = "background_before"
    BACKGROUND_AFTER = "background_after"
    TASK_AFTER = "task_after"


PUBLIC_STAGES = tuple(HookEvent[name] for name in (
    "INITIALIZE", "PREPARE", "RUN_START", "ROUND_BEFORE", "MODEL_BEFORE", "MODEL_AFTER",
    "TOOL_BATCH_BEFORE", "TOOL_BEFORE", "TOOL_AFTER", "TOOL_BATCH_AFTER", "ROUND_AFTER", "FINALIZE", "RUN_END",
))
NOTIFICATIONS = tuple(stage for stage in HookEvent if stage not in PUBLIC_STAGES)


class HookAction(str, Enum):
    CONTINUE = "continue"
    REPLACE = "replace"
    VETO = "veto"
    RECOVER = "recover"
    FINALIZE = "finalize"
    STOP = "stop"


@dataclass(frozen=True)
class HookDecision:
    """Generic control result shared by policy and application hooks."""

    action: HookAction | str = HookAction.CONTINUE
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookContext:
    """Hook 触发时的上下文快照（纯数据，不携带 agent/registry 活对象）。"""

    event: HookEvent
    agent_name: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_status: Optional[str] = None       # TOOL_AFTER result status
    error_code: Optional[str] = None        # TOOL_AFTER normalized error code
    tool_output: Optional[str] = None      # 仅 TOOL_AFTER
    messages: Optional[list] = None        # 仅 MODEL_BEFORE / MODEL_AFTER
    llm_response: Optional[dict] = None    # 仅 MODEL_AFTER
    run_id: str = ""
    phase: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    runtime: Optional[RuntimePort] = None
    invocation: Any = None  # Private per-call service state; hidden from observers.


@dataclass(frozen=True)
class Contribution:
    id: str
    stage: HookEvent
    handler: str
    mode: str = "observer"
    priority: int = 0
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    scope: str = "run"
    fail_closed: bool = False
    interface_id: str = ""
