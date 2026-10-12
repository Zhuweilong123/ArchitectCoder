"""Structured state shared by the loop, execution coordinator and evaluations."""
from typing import Literal, NotRequired, TypedDict
from .data import JsonValue, JsonObject, TodoItem, WorkspaceSnapshot

class OutcomePayload(TypedDict):
    status: str
    stop_reason: str
    final_answer: str
    total_tokens: int

class VerificationEvidence(TypedDict):
    kind: str
    scope: str
    passed: bool
    command: NotRequired[str]
    exit_code: NotRequired[int | None]

class FileChangeEvidence(TypedDict):
    path: str
    operation: str
    before_hash: NotRequired[str | None]
    after_hash: NotRequired[str | None]

class ToolCallDetail(TypedDict, total=False):
    name: str
    arguments: JsonObject
    observation: str
    status: str
    error_code: str
    fed_truncated: bool
    fed_length: int
    retryable: bool
    total_tokens: int
    duration_ms: float
    changes: list[FileChangeEvidence]
    verification: VerificationEvidence | None
    execution: JsonObject | None
    evidence: JsonObject
    call_id: str
    execution_evidence: JsonObject

class StreamStep(TypedDict):
    step: int
    actions: list[str]
    tool_calls_detail: list[ToolCallDetail]
    thought: str
    is_final: bool
    final_answer: str
    outcome: OutcomePayload | None

class TodoProgressState(TypedDict):
    todos: list[TodoItem]
    planning_mode: bool
    strategy_advised: bool

class CompactionReport(TypedDict, total=False):
    summary: str
    dropped_messages: int
    dropped_tokens: int
    reason: str
    triggered_by: list[str]
    tool_call_count: int
    token_budget_used: int
    context_target_tokens: int

class RunCheckpoint(TypedDict, total=False):
    run_id: str
    task_id: str
    plugin_plan_id: str
    status: str
    request_summary: str
    completed_items: list[str]
    pending_items: list[str]
    changed_files: list[str]
    verification: list[str]
    verification_results: list[VerificationEvidence]
    mutation_evidence: dict[str, FileChangeEvidence]
    last_error: str | None
    stop_reason: str | None
    resume_available: bool
    resume_of: str
    candidate_artifact: JsonObject | None
    candidate_artifact_error: str
    candidate_recovery: bool
    candidate_restore: JsonObject
    candidate_recovery_phase: str
    project_file: str
    source_dir: str
    test_dir: str
    design_dir: str
    workspace_root: str
    last_step: int
    tool_calls: list[ToolCallDetail]
    outcome: OutcomePayload
    pre_gate_outcome: OutcomePayload
    review_status: str
    post_review_status: str
    review_baseline: list[JsonObject] | None
    pending_review: JsonObject
    review_candidate: JsonObject | None
    task_summary: str
    todos: list[TodoItem]
    validation_requirements: list[dict[str, str]]
    task_validation: JsonObject
    contract_check: JsonObject
    workspace_manifest: WorkspaceSnapshot

class TaskResultPayload(TypedDict, total=False):
    project_id: str
    status: str
    user_message: str
    final_answer: str
    tool_steps: list[ToolCallDetail]
    checkpoint: RunCheckpoint
    conversation_history: tuple[dict[str, str], ...]
    trace_id: str
