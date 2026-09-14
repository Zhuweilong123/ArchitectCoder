"""统一工具执行结果协议。"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FileChange:
    path: str
    operation: str
    before_hash: str = ""
    after_hash: str = ""


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    cwd: str
    exit_code: int


@dataclass(frozen=True)
class VerificationEvidence:
    kind: str
    scope: str
    passed: bool
    exit_code: int | None = None


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
    data: Any = ""
    error_code: str = ""
    retryable: bool = False
    changes: list[FileChange] = field(default_factory=list)
    execution: CommandEvidence | None = None
    verification: VerificationEvidence | None = None
    execution_evidence: dict[str, Any] | None = None

    def effects(self) -> dict:
        return {
            "changes": [asdict(change) for change in self.changes],
            "execution": asdict(self.execution) if self.execution else None,
            "verification": asdict(self.verification) if self.verification else None,
            "execution_evidence": self.execution_evidence,
        }

    @classmethod
    def from_value(cls, value: Any) -> "ToolResult":
        """Normalize legacy text tools at the execution boundary only."""
        if isinstance(value, cls):
            return value
        if str(value).lstrip().lower().startswith(("error:", "conflict:", "鉂?")):
            return cls.error(value, "TOOL_REPORTED_ERROR")
        return cls.success(value)

    @property
    def text(self) -> str:
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, ensure_ascii=False, default=str)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "data": self.data,
            "error_code": self.error_code,
            "retryable": self.retryable,
            **self.effects(),
        }

    @classmethod
    def success(cls, data: Any = "") -> "ToolResult":
        return cls(status="success", data=data)

    @classmethod
    def error(cls, message: Any, code: str = "TOOL_ERROR", retryable: bool = False) -> "ToolResult":
        return cls(status="error", data=message, error_code=code, retryable=retryable)
