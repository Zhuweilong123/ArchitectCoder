"""Typed messages, budget telemetry and compaction results for model context."""
from __future__ import annotations

from typing import NamedTuple, NotRequired, Protocol, TypeAlias, TypedDict

from .data import ConversationRole, ModelMessage


class HistoryMessagePort(Protocol):
    @property
    def role(self) -> ConversationRole: ...
    @property
    def content(self) -> str: ...


HistoryMessage: TypeAlias = ModelMessage | HistoryMessagePort


class ContextSettingsPort(Protocol):
    @property
    def agent_context_hard_limit_tokens(self) -> int: ...
    @property
    def agent_context_max_history_turns(self) -> int: ...
    @property
    def agent_context_max_summary_tokens(self) -> int: ...
    @property
    def agent_context_soft_threshold_ratio(self) -> float: ...
    @property
    def agent_context_compaction_threshold_ratio(self) -> float: ...


class MessageFit(NamedTuple):
    messages: list[ModelMessage]
    current_user_index: int | None
    dropped_messages: int
    truncated_current_task: bool


class ToolHistoryCompaction(NamedTuple):
    messages: list[ModelMessage]
    current_user_index: int | None
    dropped_messages: int
    dropped_tokens: int


class ModelRequestContext(TypedDict, total=False):
    current_user_index: int | None
    scope: str
    estimated_context_tokens: int
    message_count: int
    tool_schema_tokens: int
    tool_schema_mode: str
    tool_schema_stable: bool
    request_budget_target_tokens: int
    request_budget_emergency_tokens: int | None
    context_soft_threshold_ratio: float
    context_compaction_threshold_ratio: float
    context_hard_limit_tokens: int
    task_total_tokens_observed: int
    cumulative_limit_tokens: int | None
    output_token_limit: int
    input_token_allowance: int


class ContextPolicy(TypedDict):
    max_context_tokens: int
    soft_threshold_ratio: float
    compaction_trigger_ratio: float
    compaction_target_tokens: int


class TokenBudgetPolicy(TypedDict):
    scope: str
    soft_target_tokens: int
    emergency_limit_tokens: int | None
    task_total_tokens_observed: int
    cumulative_limit_tokens: NotRequired[int | None]


class ConvergencePolicy(TypedDict):
    budget_threshold_tokens: int
    evidence_max_records: int
    open_ended_loop: bool
    max_stalled_rounds: int
    max_recovery_rounds: int
    repeat_action_threshold: int
    final_summary_max_tokens: int


class ConvergenceFinalization(TypedDict):
    reason: str
    stalled_rounds: int
    recovery_rounds: int


class ConvergenceEvent(ConvergenceFinalization):
    action: str
    tool_call_count: NotRequired[int]


class ContextCompactionTrigger(TypedDict):
    triggered_by: list[str]
    triggered_at_tool_calls: int
    context_tokens_before: int
    target_tokens: int


class ConvergenceTrigger(TypedDict):
    triggered_by: list[str]
    triggered_at_tool_calls: int
    token_budget_used: int


class ContextBuildMetadata(TypedDict, total=False):
    max_context_tokens: int


class ContextReport(TypedDict, total=False):
    estimated_tokens: int
    history_tokens: int
    tool_tokens: int
    summary_tokens: int
    dropped_messages: int
    truncated_current_task: bool
    max_context_tokens: int
    compacted_messages: int
    compacted_tokens: int
    convergence_policy: ConvergencePolicy
    context_policy: ContextPolicy
    token_budget_policy: TokenBudgetPolicy
    plugin_plan_id: str
    token_budget_used: int
    token_budget_remaining: int
    token_budget_finalization_mode: bool
    token_budget_stop_reason: str
    token_usage_total_observed: int
    token_budget_emergency_remaining: int
    token_budget_target: int
    token_budget_emergency_limit: int | None
    react_compacted_steps: int
    react_compacted_tokens: int
    context_budget_compaction: ContextCompactionTrigger
    convergence_directive_added: ConvergenceTrigger
    loop_dropped_messages: int
    last_request_context: ModelRequestContext
    context_hard_limit_tokens: int
    convergence_events: list[ConvergenceEvent]
    convergence_finalization: ConvergenceFinalization
    finalization_textual_tool_markup_blocked: bool
    subagent_stop_reason: str
    cumulative_budget_overrun_tokens: int
