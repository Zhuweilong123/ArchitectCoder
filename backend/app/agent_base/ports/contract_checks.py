"""Structured results shared by contract policies and the host lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


CheckStatus = Literal["pass", "warn", "block", "inconclusive", "not_applicable"]


@dataclass(frozen=True)
class ContractViolation:
    code: str
    severity: Literal["warning", "error"]
    message: str
    path: str = ""
    design_entity_id: str = ""
    source_entity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContractCheckResult:
    check_id: str
    status: CheckStatus
    project_id: str
    changed_paths: tuple[str, ...] = ()
    violations: tuple[ContractViolation, ...] = ()
    snapshot_status: str = ""
    graph_status: str = "not_requested"
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def requires_confirmation(self) -> bool:
        return self.status == "warn"

    @property
    def can_commit(self) -> bool:
        return self.status in {"pass", "warn"}

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["violations"] = [item.to_dict() for item in self.violations]
        value["requires_confirmation"] = self.requires_confirmation
        value["can_commit"] = self.can_commit
        return value
