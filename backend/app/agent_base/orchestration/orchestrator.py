"""Unified, capability-driven task orchestration.

The orchestrator keeps one main ReActAgent while making planning and read-only
exploration explicit.  It deliberately does not classify models or parse user
intent with regular expressions: the planner returns a small, validated task
contract and the orchestrator enforces the resulting phase boundary.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool

from .contracts import (
    ArtifactScope,
    OrchestrationResult,
    TaskContract,
    TaskPhase,
    TaskPlan,
    TaskPlanStep,
)

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = """You are the planning gate for a coding task.
Return JSON only with these keys:
{
  "needs_execution": boolean,
  "needs_exploration": boolean,
  "goal": string,
  "steps": [{"id": string, "content": string, "phase": "explore|modify|verify", "acceptance": string}],
  "target_files": [string],
  "acceptance_criteria": [string],
  "risks": [string]
}

For greetings or questions that need no workspace action, set needs_execution=false,
use an empty steps list, and do not invent tool work. For a task spanning design,
source, and tests, set needs_exploration=true. Keep the plan short (3-5 steps).
Do not call tools and do not include markdown outside the JSON object."""


class TaskOrchestrator:
    """Prepare a bounded plan and, when needed, run one read-only explorer."""

    _EXPLORATION_ONLY = {
        "get_project_map", "find_nodes", "expand_neighbors", "analyze_impact",
        "glob", "spawn_subagent",
    }

    def __init__(
        self,
        llm: BaseAgentsLLM,
        *,
        project_file: str = "",
        source_dir: str = "",
        test_dir: str = "",
        planner_max_tokens: int = 1200,
        planner_timeout_seconds: float = 30.0,
        worker_max_steps: int = 6,
    ):
        self.llm = llm
        self.project_file = project_file
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.planner_max_tokens = max(256, int(planner_max_tokens))
        self.planner_timeout_seconds = max(1.0, float(planner_timeout_seconds))
        self.worker_max_steps = max(1, int(worker_max_steps))

    def build_contract(self, user_message: str) -> TaskContract:
        scopes: list[ArtifactScope] = []
        if self.project_file:
            scopes.append(ArtifactScope("design", self.project_file, frozenset({"read", "write", "validate"})))
        if self.source_dir:
            scopes.append(ArtifactScope("source", self.source_dir, frozenset({"read", "write", "execute"})))
        if self.test_dir:
            scopes.append(ArtifactScope("tests", self.test_dir, frozenset({"read", "execute"})))
        permissions = {"read"}
        if any("write" in scope.capabilities for scope in scopes):
            permissions.add("write")
        if any("execute" in scope.capabilities for scope in scopes):
            permissions.add("execute")
        return TaskContract(
            user_message=user_message,
            project_file=self.project_file,
            artifact_scopes=tuple(scopes),
            permissions=frozenset(permissions),
            verification_available="execute" in permissions,
        )

    async def prepare(self, user_message: str, *, previous_checkpoint: dict | None = None) -> OrchestrationResult:
        contract = self.build_contract(user_message)
        plan, planner_tokens = await self._plan(contract, previous_checkpoint or {})
        if not plan.needs_execution or not plan.needs_exploration:
            directives = self._build_runtime_directives(
                plan,
                explored=False,
                acceptance_required=len(contract.artifact_scopes) >= 2,
            ) if plan.needs_execution else {}
            return OrchestrationResult(
                contract=contract, plan=plan, planner_tokens=planner_tokens,
                runtime_directives=directives,
                phase=TaskPhase.PLAN,
            )

        explorer = SpawnSubagentTool(
            llm=self.llm,
            source_dir=self.source_dir,
            test_dir=self.test_dir,
            design_dir=os.path.dirname(self.project_file) if self.project_file else "",
            project_file=self.project_file,
            toolkits=("strategy",),
            max_steps=self.worker_max_steps,
            single_use=True,
        )
        description = self._exploration_request(contract, plan)
        try:
            summary = await explorer._execute({
                "description": description,
                "toolkit": "strategy",
            })
        except Exception as exc:
            logger.warning("[Orchestrator] read-only exploration failed", exc_info=True)
            summary = f"Explorer failed: {type(exc).__name__}: {exc}"

        worker_tokens = int(getattr(explorer, "last_token_usage", 0) or 0)
        directives = self._build_runtime_directives(
            plan,
            explored=True,
            acceptance_required=True,
        )
        return OrchestrationResult(
            contract=contract,
            plan=plan,
            exploration_summary=str(summary or "(explorer returned no summary)"),
            planner_tokens=planner_tokens,
            worker_tokens=worker_tokens,
            runtime_directives=directives,
            phase=TaskPhase.EXPLORE,
        )

    async def _plan(self, contract: TaskContract, checkpoint: dict) -> tuple[TaskPlan, int]:
        scope_text = ", ".join(scope.name for scope in contract.artifact_scopes) or "none"
        prompt = (
            f"User request:\n{contract.user_message}\n\n"
            f"Available artifact scopes: {scope_text}\n"
            f"Verification available: {contract.verification_available}\n"
            f"Previous checkpoint: {json.dumps(checkpoint, ensure_ascii=False)[:1200]}"
        )
        try:
            response = await self.llm.ainvoke_with_metadata(
                [{"role": "system", "content": PLANNER_SYSTEM},
                 {"role": "user", "content": prompt}],
                max_tokens=self.planner_max_tokens,
                json_mode=True,
                timeout=self.planner_timeout_seconds,
                temperature=0.0,
            )
            content = str(response.get("content") or "")
            usage = response.get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0) if isinstance(usage, dict) else 0
            return self._parse_plan(content, contract), tokens
        except Exception as exc:
            logger.warning("[Orchestrator] planner failed; using deterministic fallback", exc_info=True)
            return self._fallback_plan(contract), 0

    def _parse_plan(self, content: str, contract: TaskContract) -> TaskPlan:
        raw = content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:].lstrip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("planner response must be an object")
        needs_execution = bool(data.get("needs_execution", True))
        if not needs_execution:
            return TaskPlan(goal=str(data.get("goal") or contract.user_message), needs_execution=False)
        raw_steps = data.get("steps") or []
        steps: list[TaskPlanStep] = []
        for index, item in enumerate(raw_steps[:5], 1):
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                continue
            phase_name = str(item.get("phase") or "explore").lower()
            phase = TaskPhase(phase_name) if phase_name in {"explore", "modify", "verify"} else TaskPhase.EXPLORE
            steps.append(TaskPlanStep(
                id=str(item.get("id") or f"step_{index}"),
                content=str(item["content"]).strip(),
                phase=phase,
                acceptance=str(item.get("acceptance") or "").strip(),
            ))
        if not steps:
            return self._fallback_plan(contract)
        needs_exploration = bool(data.get("needs_exploration", len(contract.artifact_scopes) >= 2))
        plan = TaskPlan(
            goal=str(data.get("goal") or contract.user_message).strip(),
            steps=steps,
            target_files=[str(item) for item in (data.get("target_files") or [])[:20]],
            acceptance_criteria=[str(item) for item in (data.get("acceptance_criteria") or [])[:10]],
            risks=[str(item) for item in (data.get("risks") or [])[:10]],
            needs_execution=True,
            needs_exploration=needs_exploration,
            source="model",
        )
        self._normalize_steps(plan, contract)
        return plan

    def _fallback_plan(self, contract: TaskContract) -> TaskPlan:
        multi_artifact = len(contract.artifact_scopes) >= 2
        steps = [TaskPlanStep("inspect", "Inspect the relevant artifacts and identify the smallest correct change.", TaskPhase.EXPLORE, acceptance="Relevant evidence is recorded.")]
        if "write" in contract.permissions:
            steps.append(TaskPlanStep("modify", "Apply the scoped change and preserve unrelated user work.", TaskPhase.MODIFY, acceptance="Only intended files are changed."))
        if contract.verification_available:
            steps.append(TaskPlanStep("verify", "Run focused verification and report any remaining failure.", TaskPhase.VERIFY, acceptance="Verification result is recorded."))
        plan = TaskPlan(
            goal=contract.user_message,
            steps=steps,
            acceptance_criteria=[step.acceptance for step in steps if step.acceptance],
            needs_execution=True,
            needs_exploration=multi_artifact,
        )
        self._normalize_steps(plan, contract)
        return plan

    @staticmethod
    def _normalize_steps(plan: TaskPlan, contract: TaskContract) -> None:
        if not plan.steps:
            return
        # Cross-artifact work uses the acceptance contract. Ensure malformed or
        # truncated planner output still has an inspect/modify/verify shape so
        # a later todo_write call cannot be rejected for having too few items.
        if len(contract.artifact_scopes) >= 2 and len(plan.steps) < 3:
            if not any(step.phase == TaskPhase.EXPLORE for step in plan.steps):
                plan.steps.insert(0, TaskPlanStep(
                    "inspect",
                    "Inspect the affected artifacts and record the evidence.",
                    TaskPhase.EXPLORE,
                    acceptance="Relevant evidence is recorded.",
                ))
            if len(plan.steps) < 3 and not any(step.phase == TaskPhase.MODIFY for step in plan.steps):
                insertion_index = next(
                    (index for index, step in enumerate(plan.steps) if step.phase == TaskPhase.VERIFY),
                    len(plan.steps),
                )
                plan.steps.insert(insertion_index, TaskPlanStep(
                    "modify",
                    "Apply the smallest scoped change.",
                    TaskPhase.MODIFY,
                    acceptance="Only intended files are changed.",
                ))
        if not plan.acceptance_criteria:
            plan.acceptance_criteria = [step.acceptance for step in plan.steps if step.acceptance]
        if contract.verification_available and not any(step.phase == TaskPhase.VERIFY for step in plan.steps):
            plan.steps.append(TaskPlanStep("verify", "Run focused verification.", TaskPhase.VERIFY, acceptance="Verification result is recorded."))

    def _build_runtime_directives(
        self,
        plan: TaskPlan,
        *,
        explored: bool,
        acceptance_required: bool,
    ) -> dict:
        todos = []
        for step in plan.steps[:5]:
            todos.append({
                "content": step.content,
                "status": "completed" if explored and step.phase == TaskPhase.EXPLORE else (
                    "in_progress" if not todos else "pending"
                ),
                "kind": "verification" if step.phase == TaskPhase.VERIFY else (
                    "execution" if step.phase == TaskPhase.MODIFY else "analysis"
                ),
                "acceptance": step.acceptance or "The step is completed and evidenced.",
            })
        return {
            "requires_todo_plan": True,
            "requires_acceptance_todos": acceptance_required,
            "todos": todos,
            "strategy_subagent_used": explored,
        }

    def _exploration_request(self, contract: TaskContract, plan: TaskPlan) -> str:
        scopes = ", ".join(scope.name for scope in contract.artifact_scopes)
        return (
            "Perform a read-only exploration for the following task. Do not modify files, "
            "run destructive commands, or spawn another agent. Inspect only the supplied "
            f"artifact scopes ({scopes}) and return a concise structured report with exact "
            "files/lines, design-vs-code findings, relevant tests, recommended changes, "
            "and unverified items. Do not repeat broad discovery.\n\n"
            + plan.as_context()
            + f"\n\nOriginal request:\n{contract.user_message}"
        )

    def allowed_main_tools(self, tool_names: list[str]) -> list[str]:
        """Keep broad graph discovery in the worker after exploration."""
        return [name for name in tool_names if name not in self._EXPLORATION_ONLY]
