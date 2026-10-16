"""统一工具执行结果协议。"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from copy import deepcopy
from ..host_api.data import JsonValue
from ..host_api.payloads import json_value, parse_tool_execution_evidence
from ..host_api.execution_types import (FileChangeEvidence, CommandEvidencePayload, ToolEffects,
    ToolExecutionEvidence, ToolResultPayload, VerificationEvidence as VerificationPayload)


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str
    before_hash: str = ""
    after_hash: str = ""

    def to_dict(self) -> FileChangeEvidence:
        return {"path": self.path, "operation": self.operation,
                "before_hash": self.before_hash, "after_hash": self.after_hash}


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    cwd: str
    exit_code: int

    def to_dict(self) -> CommandEvidencePayload:
        return {"command": self.command, "cwd": self.cwd, "exit_code": self.exit_code}


@dataclass(frozen=True)
class VerificationEvidence:
    kind: str
    scope: str
    passed: bool
    exit_code: int | None = None

    def to_dict(self) -> VerificationPayload:
        return {"kind": self.kind, "scope": self.scope, "passed": self.passed, "exit_code": self.exit_code}


def command_result(command: str, cwd: str | None, exit_code: int, output: str,
                   argv: list[str] | None = None) -> "ToolResult":
    """Capture process facts; only recognized verification commands get a verdict."""
    try:
        parts = argv if argv is not None else shlex.split(command)
    except ValueError:
        parts = []
    parts = [part.lower() for part in parts]
    program = parts[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe") if parts else ""
    args = parts[1:]
    if program in {"python", "python3"} and args[:1] == ["-m"] and len(args) > 1:
        program, args = args[1], args[2:]
    kind = {"pytest": "test", "mypy": "typecheck", "tsc": "typecheck"}.get(program)
    if program == "ruff" and args[:1] == ["check"]:
        kind = "lint"
    if program == "npm" and args[:1] == ["test"]:
        kind = "test"
    if program == "npm" and args[:1] == ["run"] and len(args) > 1:
        kind = {"test": "test", "build": "build", "lint": "lint", "typecheck": "typecheck"}.get(args[1])
    result = ToolResult(
        status="success" if exit_code == 0 else "error", data=output,
        error_code="" if exit_code == 0 else "PROCESS_EXIT_ERROR",
        execution=CommandEvidence(command, cwd or "", exit_code),
    )
    if kind:
        result.verification = VerificationEvidence(kind, command, exit_code == 0, exit_code)
    return result


@dataclass
class ToolResult:
    status: str = "success"  # success | error | blocked
    data: JsonValue = ""
    error_code: str = ""
    retryable: bool = False
    changes: list[FileChange] = field(default_factory=list)
    execution: CommandEvidence | None = None
    verification: VerificationEvidence | None = None
    execution_evidence: ToolExecutionEvidence | None = None

    def __post_init__(self) -> None:
        self.data = json_value(self.data)
        if self.execution_evidence is not None:
            self.execution_evidence = parse_tool_execution_evidence(self.execution_evidence)

    def effects(self) -> ToolEffects:
        return {
            "changes": [change.to_dict() for change in self.changes],
            "execution": self.execution.to_dict() if self.execution else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "execution_evidence": deepcopy(self.execution_evidence),
        }

    @classmethod
    def from_value(cls, value: object) -> "ToolResult":
        """Normalize legacy text tools at the execution boundary only."""
        if isinstance(value, cls):
            return value
        if str(value).lstrip().lower().startswith(("error:", "conflict:", "鉂?")):
            return cls.error(json_value(value), "TOOL_REPORTED_ERROR")
        return cls.success(json_value(value))

    @property
    def text(self) -> str:
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, ensure_ascii=False)

    def to_dict(self) -> ToolResultPayload:
        return {
            "status": self.status,
            "data": self.data,
            "error_code": self.error_code,
            "retryable": self.retryable,
            **self.effects(),
        }

    @classmethod
    def success(cls, data: JsonValue = "") -> "ToolResult":
        return cls(status="success", data=data)

    @classmethod
    def error(cls, message: JsonValue, code: str = "TOOL_ERROR", retryable: bool = False) -> "ToolResult":
        return cls(status="error", data=message, error_code=code, retryable=retryable)
