"""Data contracts shared by the task orchestrator and its workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskPhase(str, Enum):
    INIT = "init"
    PLAN = "plan"
    EXPLORE = "explore"
    DECIDE = "decide"
    MODIFY = "modify"
    VERIFY = "verify"
    FINALIZE = "finalize"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class ArtifactScope:
    name: str
    root: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class TaskContract:
    user_message: str
    project_file: str = ""
    artifact_scopes: tuple[ArtifactScope, ...] = ()
    permissions: frozenset[str] = frozenset()
    verification_available: bool = False


@dataclass
class TaskPlanStep:
    id: str
    content: str
    phase: TaskPhase
    status: str = "pending"
    acceptance: str = ""


@dataclass
class TaskPlan:
    goal: str
    steps: list[TaskPlanStep] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    needs_execution: bool = True
    needs_exploration: bool = False
    source: str = "deterministic"

    def as_context(self) -> str:
        lines = ["## Orchestrated task plan", f"Goal: {self.goal}"]
        if self.steps:
            lines.append("Steps:")
            for step in self.steps:
                lines.append(
                    f"- [{step.status}] {step.id}: {step.content}"
                    + (f" (acceptance: {step.acceptance})" if step.acceptance else "")
                )
        if self.target_files:
            lines.append("Target files: " + ", ".join(self.target_files))
        if self.acceptance_criteria:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {item}" for item in self.acceptance_criteria)
        if self.risks:
            lines.append("Risks:")
            lines.extend(f"- {item}" for item in self.risks)
        return "\n".join(lines)


@dataclass
class OrchestrationResult:
    contract: TaskContract
    plan: TaskPlan
    exploration_summary: str = ""
    planner_tokens: int = 0
    worker_tokens: int = 0
    runtime_directives: dict = field(default_factory=dict)
    phase: TaskPhase = TaskPhase.PLAN

    @property
    def should_continue_with_main_agent(self) -> bool:
        return self.plan.needs_execution

    @property
    def total_tokens(self) -> int:
        """Token cost incurred before the main ReAct loop starts."""
        return max(0, int(self.planner_tokens) + int(self.worker_tokens))

    def as_context(self) -> str:
        blocks = [self.plan.as_context()]
        if self.exploration_summary:
            blocks.extend([
                "## Read-only exploration report",
                self.exploration_summary,
                "Use this report as evidence. Do not repeat broad discovery; inspect only target files, then modify and verify.",
            ])
        return "\n\n".join(blocks)
