"""Language-neutral contracts for project task execution.

This module deliberately contains no process-launching code.  Task resolvers,
toolchain adapters, and execution brokers can depend on these immutable
contracts without depending on a particular host shell or language runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class TaskKind(str, Enum):
    """Semantic operation requested from a project toolchain."""

    CONFIGURE = "configure"
    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    FORMAT = "format"
    TYPECHECK = "typecheck"
    RUN = "run"
    CUSTOM = "custom"


class NetworkPolicy(str, Enum):
    """Network access requested by a task."""

    DENY = "deny"
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"


class ApprovalClass(str, Enum):
    """Approval level required before a task may run."""

    SANDBOX_AUTO = "sandbox_auto"
    USER_APPROVAL = "user_approval"
    DENY = "deny"


_CONTROL_CHARS = frozenset("\r\n;|><`$")


def _clean_text(value: str, field_name: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _literal_argv(values: tuple[str, ...], field_name: str = "argv") -> tuple[str, ...]:
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} values must be non-empty strings")
        if any(char in value for char in _CONTROL_CHARS):
            raise ValueError(
                f"{field_name} values must be literal argv without shell control characters"
            )
        cleaned.append(value)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Hard limits applied by an execution broker."""

    timeout_seconds: float = 600.0
    cpu_seconds: float | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None
    max_processes: int | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        for name in ("cpu_seconds", "memory_mb", "disk_mb", "max_processes"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A resolved, language-neutral project task.

    ``argv`` is produced by a trusted resolver/adapter or an explicitly
    approved custom task.  Models never need to provide a shell command string.
    """

    task_id: str
    kind: TaskKind
    argv: tuple[str, ...]
    cwd: str = "workspace"
    toolchain_id: str = "host"
    toolchain_version: str = "unknown"
    network: NetworkPolicy = NetworkPolicy.DENY
    approval: ApprovalClass = ApprovalClass.SANDBOX_AUTO
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    expected_outputs: tuple[str, ...] = ()
    source: str = "resolver"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _clean_text(self.task_id, "task_id"))
        object.__setattr__(self, "cwd", _clean_text(self.cwd, "cwd"))
        object.__setattr__(self, "toolchain_id", _clean_text(self.toolchain_id, "toolchain_id"))
        object.__setattr__(self, "toolchain_version", _clean_text(self.toolchain_version, "toolchain_version"))
        object.__setattr__(self, "source", _clean_text(self.source, "source"))
        object.__setattr__(self, "argv", _literal_argv(tuple(self.argv)))
        object.__setattr__(
            self,
            "expected_outputs",
            tuple(_clean_text(path, "expected_outputs") for path in self.expected_outputs),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["network"] = self.network.value
        value["approval"] = self.approval.value
        value["argv"] = list(self.argv)
        value["expected_outputs"] = list(self.expected_outputs)
        return value


@dataclass(frozen=True, slots=True)
class TaskPlan:
    """A deterministic plan for one semantic task.

    ``steps`` are ordered and literal; the final step is the requested task
    and preceding steps are runtime-managed prerequisites.  Keeping the plan
    in the language-neutral contract lets the model preview and reason about
    execution without receiving shell-specific instructions in its prompt.
    """

    requested_task: str
    steps: tuple[TaskSpec, ...]
    profile: str = ""
    rationale: str = ""
    available_profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested_task", _clean_text(self.requested_task, "requested_task"))
        if not self.steps:
            raise ValueError("steps must not be empty")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "profile", str(self.profile or "").strip())
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "available_profiles", tuple(
            _clean_text(profile, "available_profiles") for profile in self.available_profiles
        ))

    @property
    def final_task(self) -> TaskSpec:
        return self.steps[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_task": self.requested_task,
            "profile": self.profile,
            "available_profiles": list(self.available_profiles),
            "rationale": self.rationale,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class ToolchainProfile:
    """A managed language/build environment selected by a resolver."""

    toolchain_id: str
    family: str
    version: str = "unknown"
    executor: str = "local-restricted"
    capabilities: tuple[str, ...] = ()
    image: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "toolchain_id", _clean_text(self.toolchain_id, "toolchain_id"))
        object.__setattr__(self, "family", _clean_text(self.family, "family"))
        object.__setattr__(self, "version", _clean_text(self.version, "version"))
        object.__setattr__(self, "executor", _clean_text(self.executor, "executor"))
        object.__setattr__(
            self,
            "capabilities",
            tuple(_clean_text(capability, "capabilities") for capability in self.capabilities),
        )
        if self.image is not None:
            object.__setattr__(self, "image", _clean_text(self.image, "image"))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        return value


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Effective sandbox and approval boundary for one task."""

    sandbox: str = "workspace"
    network: NetworkPolicy = NetworkPolicy.DENY
    writable_roots: tuple[str, ...] = ()
    approval: ApprovalClass = ApprovalClass.SANDBOX_AUTO
    environment: str = "auto"

    def __post_init__(self) -> None:
        object.__setattr__(self, "sandbox", _clean_text(self.sandbox, "sandbox"))
        object.__setattr__(self, "environment", _clean_text(self.environment, "environment"))
        object.__setattr__(
            self,
            "writable_roots",
            tuple(_clean_text(path, "writable_roots") for path in self.writable_roots),
        )


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """Auditable result returned by an execution broker."""

    task_id: str
    status: str
    toolchain_id: str
    command: tuple[str, ...]
    cwd: str
    toolchain_version: str = "unknown"
    toolchain_actual_version: str = ""
    toolchain_version_match: bool | None = None
    toolchain_probe: Mapping[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    duration_ms: float | None = None
    timeout_reason: str | None = None
    sandbox: str = "workspace"
    network: NetworkPolicy = NetworkPolicy.DENY
    output: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _clean_text(self.task_id, "task_id"))
        object.__setattr__(self, "status", _clean_text(self.status, "status"))
        object.__setattr__(self, "toolchain_id", _clean_text(self.toolchain_id, "toolchain_id"))
        object.__setattr__(self, "toolchain_version", _clean_text(self.toolchain_version, "toolchain_version"))
        object.__setattr__(self, "toolchain_actual_version", str(self.toolchain_actual_version or "").strip())
        object.__setattr__(self, "cwd", _clean_text(self.cwd, "cwd"))
        object.__setattr__(self, "command", _literal_argv(tuple(self.command), "command"))
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must not be negative")
        if self.status == "timeout" and not self.timeout_reason:
            raise ValueError("timeout_reason is required for timeout evidence")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["command"] = list(self.command)
        value["network"] = self.network.value
        value["diagnostics"] = dict(self.diagnostics)
        return value


__all__ = [
    "ApprovalClass",
    "ExecutionEvidence",
    "ExecutionPolicy",
    "NetworkPolicy",
    "ResourceLimits",
    "TaskKind",
    "TaskPlan",
    "TaskSpec",
    "ToolchainProfile",
]
