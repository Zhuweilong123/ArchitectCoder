"""评测用例、检查结果和运行结果模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EVAL_CASE_SCHEMA_VERSION = "1.0"
EVAL_TOOL_PROTOCOL_VERSION = "foundation-tools-v1"
EVAL_CHECKER_PROTOCOL_VERSION = "deterministic-checkers-v1"

SUPPORTED_CHECKER_TYPES = frozenset({
    "answer_contains_all",
    "answer_ordered_contains",
    "file_absent",
    "file_contains",
    "file_exists",
    "file_not_contains",
    "hidden_pytest",
    "json_field",
    "paths_unchanged",
    "pytest",
    "trace_policy",
    "uml_absent",
    "uml_component_names",
    "uml_contains",
    "uml_method",
    "uml_method_signature",
    "uml_relation",
    "uml_sequence",
    "uml_sequence_exact",
    "uml_valid",
})

LEGACY_TOOL_NAMES = frozenset({"edit_file", "write_file"})
EVAL_TRACE_TOOL_NAMES = frozenset({
    "apply_changes",
    "list_files",
    "read_file",
    "run_program",
    "run_task",
    "search_text",
    "shell",
})

CHECKER_REQUIRED_FIELDS = {
    "answer_contains_all": ("texts",),
    "answer_ordered_contains": ("texts",),
    "file_absent": ("path",),
    "file_contains": ("path", "text"),
    "file_exists": ("path",),
    "file_not_contains": ("path", "text"),
    "json_field": ("path", "field"),
    "paths_unchanged": ("paths",),
    "uml_absent": ("path", "kind", "name"),
    "uml_component_names": ("path", "names"),
    "uml_contains": ("path", "kind", "name"),
    "uml_method": ("path", "class_name", "method"),
    "uml_method_signature": ("path", "class_name", "method", "params"),
    "uml_relation": ("path", "source", "target"),
    "uml_sequence": ("path", "labels"),
    "uml_sequence_exact": ("path", "labels"),
    "uml_valid": ("path",),
}


def _validate_checker_contract(configs: list[dict[str, Any]], scope: str) -> None:
    for index, config in enumerate(configs):
        kind = str(config.get("type") or "")
        if kind not in SUPPORTED_CHECKER_TYPES:
            raise ValueError(f"{scope}[{index}] has unsupported checker type: {kind!r}")
        missing_fields = [
            field for field in CHECKER_REQUIRED_FIELDS.get(kind, ())
            if field not in config
        ]
        if missing_fields:
            raise ValueError(
                f"{scope}[{index}] is missing required fields: {missing_fields}"
            )
        if kind != "trace_policy":
            continue
        required_tools = {
            str(tool) for tool in (config.get("required_tools") or [])
        }
        declared_tools = {
            str(tool)
            for key in ("required_tools", "forbidden_tools")
            for tool in (config.get(key) or [])
        }
        legacy_tools = sorted(declared_tools & LEGACY_TOOL_NAMES)
        if legacy_tools:
            raise ValueError(
                f"{scope}[{index}] uses legacy tool names {legacy_tools}; "
                "use apply_changes under foundation-tools-v1"
            )
        unsupported_tools = sorted(required_tools - EVAL_TRACE_TOOL_NAMES)
        if unsupported_tools:
            raise ValueError(
                f"{scope}[{index}] requires tools outside the fixed "
                f"foundation-tools-v1 protocol: {unsupported_tools}"
            )


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = EVAL_CASE_SCHEMA_VERSION
    tool_protocol_version: str = EVAL_TOOL_PROTOCOL_VERSION
    id: str = Field(min_length=1, max_length=100)
    name: str = ""
    prompt: str = ""
    # Optional multi-turn script.  When present, turns share one Agent,
    # workspace, history and checkpoint; ``prompt`` remains the legacy
    # single-turn entry point for existing cases.
    turns: list["EvalTurn"] = Field(default_factory=list)
    project_id: str = ""
    fixture: str = ""
    checkers: list[dict[str, Any]] = Field(default_factory=list)
    hard_checkers: list[dict[str, Any]] = Field(default_factory=list)
    max_seconds: float = Field(default=600.0, gt=0, le=3600)
    max_tool_calls: int = Field(default=100, gt=0, le=1000)
    max_total_tokens: int = Field(default=200000, gt=0, le=1000000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "EvalCase":
        if self.schema_version != EVAL_CASE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported eval case schema_version: {self.schema_version!r}"
            )
        if self.tool_protocol_version != EVAL_TOOL_PROTOCOL_VERSION:
            raise ValueError(
                "eval case tool_protocol_version does not match the runtime "
                f"contract: {self.tool_protocol_version!r}"
            )
        if not self.prompt.strip() and not self.turns:
            raise ValueError("evaluation case requires prompt or turns")
        _validate_checker_contract(self.hard_checkers, "hard_checkers")
        _validate_checker_contract(self.checkers, "checkers")
        for turn_index, turn in enumerate(self.turns, 1):
            _validate_checker_contract(
                turn.hard_checkers, f"turns[{turn_index}].hard_checkers"
            )
            _validate_checker_contract(
                turn.checkers, f"turns[{turn_index}].checkers"
            )
        return self

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("eval case id must contain only letters, digits, '_' or '-'")
        return value

    def prompts(self) -> list[str]:
        """Return the canonical sequence while preserving legacy cases."""
        return [turn.prompt for turn in self.turns] if self.turns else [self.prompt]

    def turn_specs(self) -> list["EvalTurn"]:
        """Return explicit turn metadata, synthesizing the legacy prompt."""
        return list(self.turns) if self.turns else [EvalTurn(prompt=self.prompt)]


class EvalTurn(BaseModel):
    """One prompt in a shared-state evaluation conversation."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    checkers: list[dict[str, Any]] = Field(default_factory=list)
    hard_checkers: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class ProjectManifest(BaseModel):
    """可复现评测项目的固定边界。"""

    id: str = Field(min_length=1, max_length=100)
    version: str = "1.0.0"
    fixture: str = Field(min_length=1)
    # Optional complete fixture to materialize before applying this fixture's
    # sparse overlay. Empty means ``fixture`` is a standalone snapshot.
    base_fixture: str = ""
    entry_file: str = ""
    source_dir: str = "."
    test_dir: str = "test"
    protected_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=list)

    @field_validator("fixture", "base_fixture", "entry_file", "source_dir", "test_dir", "protected_paths", "allowed_write_paths")
    @classmethod
    def validate_relative_paths(cls, value):
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not item:
                continue
            path = Path(item)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("project manifest paths must be relative")
        return value


class CheckerResult(BaseModel):
    checker: str
    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


EvalStatus = Literal[
    "running",
    "passed",
    "failed",
    "timeout",
    "budget_exceeded",
    "budget_finalized",
    "error",
]

EvalFailureCategory = Literal[
    "none",
    "agent_failure",
    "tool_failure",
    "environment_failure",
    "checker_failure",
    "timeout",
    "budget_exceeded",
]


class EvalResult(BaseModel):
    run_id: str
    case_id: str
    agent: str = "devagent"
    status: EvalStatus
    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    started_at: str = ""
    duration_ms: float = 0.0
    workspace: str = ""
    trace_id: str = ""
    trace_path: str = ""
    model: str = ""
    tool_calls: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    prompt_cache_requests: int = 0
    prompt_prefix_chars: int = 0
    reused_prompt_prefix_chars: int = 0
    prompt_prefix_requests: int = 0
    checker_results: list[CheckerResult] = Field(default_factory=list)
    error: str = ""
    failure_category: EvalFailureCategory = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def started(cls, run_id: str, case_id: str) -> "EvalResult":
        return cls(
            run_id=run_id,
            case_id=case_id,
            status="running",
            passed=False,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
