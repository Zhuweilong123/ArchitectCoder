"""评测用例、检查结果和运行结果模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvalCase(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = ""
    prompt: str = Field(min_length=1)
    project_id: str = ""
    fixture: str = ""
    checkers: list[dict[str, Any]] = Field(default_factory=list)
    hard_checkers: list[dict[str, Any]] = Field(default_factory=list)
    max_seconds: float = Field(default=600.0, gt=0, le=3600)
    max_tool_calls: int = Field(default=100, gt=0, le=1000)
    max_total_tokens: int = Field(default=100000, gt=0, le=1000000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("eval case id must contain only letters, digits, '_' or '-'")
        return value


class ProjectManifest(BaseModel):
    """可复现评测项目的固定边界。"""

    id: str = Field(min_length=1, max_length=100)
    version: str = "1.0.0"
    fixture: str = Field(min_length=1)
    entry_file: str = ""
    source_dir: str = "."
    test_dir: str = "test"
    protected_paths: list[str] = Field(default_factory=list)
    allowed_write_paths: list[str] = Field(default_factory=list)

    @field_validator("fixture", "entry_file", "source_dir", "test_dir", "protected_paths", "allowed_write_paths")
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


class EvalResult(BaseModel):
    run_id: str
    case_id: str
    status: str
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
    checker_results: list[CheckerResult] = Field(default_factory=list)
    error: str = ""
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
