"""Language and diagram independent validation ports. No implementation imports."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Literal, NotRequired, Protocol, TypedDict
from .data import JsonObject


@dataclass(frozen=True)
class ValidationDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class SourceOperation:
    kind: str
    line: int
    name: str = ""
    branches: tuple[tuple[int, bool], ...] = ()
    rank: int = 0
    order_known: bool = True


@dataclass(frozen=True)
class SourceFunction:
    symbol: str
    operations: tuple[SourceOperation, ...] = ()


@dataclass(frozen=True)
class SourceArtifact:
    status: str
    language: str = ""
    functions: tuple[SourceFunction, ...] = ()
    reason: str = ""

    @classmethod
    def from_dict(cls, value: SourceArtifactPayload) -> SourceArtifact:
        functions = tuple(SourceFunction(item["symbol"], tuple(SourceOperation(
            kind=op["kind"], line=op["line"], name=op.get("name", ""),
            branches=tuple((branch[0], branch[1]) for branch in op.get("branches", [])),
            rank=op.get("rank", 0), order_known=op.get("order_known", False),
        ) for op in item.get("operations", []))) for item in value.get("functions", []))
        return cls(value["status"], value.get("language", ""), functions, value.get("reason", ""))


class SourceOperationPayload(TypedDict):
    kind: str
    line: int
    name: NotRequired[str]
    branches: NotRequired[list[tuple[int, bool]]]
    rank: NotRequired[int]
    order_known: NotRequired[bool]

class SourceFunctionPayload(TypedDict):
    symbol: str
    operations: NotRequired[list[SourceOperationPayload]]

class SourceArtifactPayload(TypedDict):
    status: str
    language: NotRequired[str]
    functions: NotRequired[list[SourceFunctionPayload]]
    reason: NotRequired[str]

class SourceFactsProvider(Protocol):
    def source_facts(self, path: str, *, workspace_root: str) -> SourceArtifact: ...


@dataclass
class ValidationContext:
    workspace_root: str = ""
    diagrams: tuple[JsonObject, ...] = ()
    source_provider: SourceFactsProvider | None = None
    source_cache: dict[str, SourceArtifact] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    rule_id: str
    path: str
    status: str  # checked | partial | not_applicable | unavailable | unsupported
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    reason: str = ""
    coverage_met: bool = False  # bounded partial checks may explicitly satisfy their declared scope

    @property
    def satisfies_requirement(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics) and (
            self.status == "checked" or (self.status == "partial" and self.coverage_met))


@dataclass(frozen=True)
class ValidationRequirement:
    rule_id: str
    diagram_name: str

    @classmethod
    def from_dict(cls, value: object) -> ValidationRequirement:
        if not isinstance(value, dict) or set(value) != {"rule_id", "diagram_name"}:
            raise ValueError("Each validation requirement must contain rule_id and diagram_name only.")
        rule_id, diagram_name = value["rule_id"], value["diagram_name"]
        if not isinstance(rule_id, str) or not isinstance(diagram_name, str) or not rule_id.strip() or not diagram_name.strip():
            raise ValueError("Validation rule_id and diagram_name must be non-empty strings.")
        return cls(rule_id.strip(), diagram_name.strip())


def normalize_requirements(values: object) -> tuple[ValidationRequirement, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("validation_requirements must be a list")
    return tuple(dict.fromkeys(value if isinstance(value, ValidationRequirement)
                              else ValidationRequirement.from_dict(value) for value in values))


class ValidationRule(Protocol):
    rule_id: str
    diagram_types: tuple[str, ...]
    def check(self, diagram: JsonObject, path: str, context: ValidationContext) -> CheckResult: ...


@dataclass(frozen=True)
class ValidationReport:
    checks: tuple[CheckResult, ...]

    @property
    def diagnostics(self) -> tuple[ValidationDiagnostic, ...]:
        return tuple(d for check in self.checks for d in check.diagnostics)

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    @property
    def status(self) -> Literal["pass", "partial", "fail"]:
        if self.has_errors:
            return "fail"
        if any(c.status in {"partial", "unavailable", "unsupported"} for c in self.checks):
            return "partial"
        return "pass"

    def to_dict(self) -> JsonObject:
        return {"status": self.status, "checks": [asdict(c) for c in self.checks],
                "diagnostics": [d.to_dict() for d in self.diagnostics]}
