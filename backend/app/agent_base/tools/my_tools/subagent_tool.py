"""SpawnSubagentTool — 通用子代理工具。"""

from __future__ import annotations

import logging
import asyncio

from app.agent_base.convergence import ConvergenceController
from app.agent_base.core.hooks import (
    AgentRuntime, HookAction, HookContext, HookDecision, HookEvent,
    get_hooks, get_runtime, reset_runtime, set_runtime,
)
from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.core.policy import ExecutionBudget
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
from app.agent_base.evidence import EvidenceLedger
from app.services.context_manager import ContextBudgetManager
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.async_tool import AsyncTool
from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
from app.agent_base.tools.my_tools.skill_loader import SkillTool, build_skills_section
from app.runtime import build_command_executor, workspace_root_for

logger = logging.getLogger(__name__)

SUBAGENT_SYSTEM = (
    "You are a coding subagent. Complete the given task, then return a concise "
    "final summary of what you did and found. Do not spawn more agents."
)

STRATEGY_SUBAGENT_SYSTEM = (
    "You are a read-only strategy advisor for a development task. Do not modify "
    "files and do not propose unverified implementation details. Inspect only the "
    "minimum evidence needed, then return a concise plan with: canonical source, "
    "target scope, ordered steps, acceptance criteria, and risks. Do not spawn more agents."
)

# ── 子代理工具包（toolkit）──────────────────────────────────────
# 主 agent 按任务类型选工具包，框架展开成受限工具集。安全不变量由本表强制，
# 不依赖主 agent 自觉：
#   * 任何工具包都不含 spawn_subagent / submit_uml_review（防递归 / 防审核绕过）
#   * 子代理工具集是主 agent 允许集的子集（无提权）
TOOLKIT_NAMES = ("standard", "read_only", "kg_analysis", "strategy")


def _build_toolkit_tools(
    kind: str,
    source_dir: str, test_dir: str, design_dir: str,
    db_path: str, project_file: str,
    review_manager, progress, command_executor, workspace_root,
) -> list:
    """按工具包名构建工具列表（不含 spawn_subagent / submit_uml_review）。"""
    foundation = create_foundation_tools(
        source_dir, test_dir, design_dir,
        review_manager=review_manager, progress=progress,
        command_executor=command_executor,
        workspace_root=workspace_root,
    )
    by_name = {tool.name: tool for tool in foundation}
    if kind == "standard":
        return [*foundation, SkillTool()]

    # Read-only toolkits intentionally expose only the foundation read contract.
    # Read-only toolkits intentionally use only file inspection and skills.
    # KG toolkit names remain for compatibility, but graph tools are disabled
    # for DevAgent to avoid broad exploration and repeated reads.
    read_tool = by_name["read_file"]
    if kind == "read_only":
        return [read_tool]
    if kind in {"kg_analysis", "strategy"}:
        return [read_tool, SkillTool()]
    raise ValueError(f"unknown toolkit: {kind}")


class SpawnSubagentTool(AsyncTool):
    """通用子代理工具 — 委托一个子任务，返回 summary。

    子代理复用主代理的已选模型，并以受限工具集（文件系统原语）独立跑简化 FC
    循环，避免主上下文膨胀。工具集不含 submit_uml_review / spawn_subagent，
    防止递归子代理和 UML 审核嵌套；bash 敏感命令仍走人工审核（与主代理
    共用同一审核通道），防止委托绕过。
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        project_file: str = "",
        max_total_tokens: int = 500000,
        emergency_max_total_tokens: int | None = None,
        context_budget: ContextBudgetManager | None = None,
        max_tool_calls: int | None = None,
        max_run_seconds: float | None = None,
        token_finalization_reserve_tokens: int | None = None,
        llm_timeout_seconds: float | None = None,
        review_manager=None,
        progress=None,
        command_executor=None,
        workspace_root: str = "",
        toolkits: tuple[str, ...] = TOOLKIT_NAMES,
        single_use: bool = False,
    ):
        super().__init__(
            name="spawn_subagent",
            description=(
                "Delegate one bounded, self-contained exploration to a subagent and "
                "receive only a concise summary. The selected toolkit uses the same "
                "capability contracts as DevAgent. Use for cross-file or "
                "UML/source impact analysis when many small reads would clutter the "
                "main context; do not use for greetings, simple single-file tasks, "
                "editing, review, or final verification. Strategy/read_only toolkits "
                "are read-only; subagents cannot spawn agents or submit UML review."
            ),
        )
        self.llm = llm
        self.max_total_tokens = max(1, int(max_total_tokens))
        self.emergency_max_total_tokens = (
            max(1, int(emergency_max_total_tokens))
            if emergency_max_total_tokens is not None else None
        )
        from backend.config import get_settings
        settings = get_settings()
        if command_executor is None:
            command_executor = build_command_executor(settings)
        if not workspace_root:
            workspace_root = workspace_root_for(source_dir, test_dir, design_dir)
        self.max_tool_calls = max(1, int(
            max_tool_calls if max_tool_calls is not None else settings.agent_max_tool_calls
        ))
        self.max_run_seconds = max(1.0, float(
            max_run_seconds if max_run_seconds is not None else settings.agent_max_run_seconds
        ))
        self.token_finalization_reserve_tokens = (
            token_finalization_reserve_tokens
            if token_finalization_reserve_tokens is not None
            else settings.agent_token_finalization_reserve_tokens
        )
        self.llm_timeout_seconds = max(1.0, float(
            llm_timeout_seconds if llm_timeout_seconds is not None
            else settings.agent_llm_timeout_seconds
        ))
        self.context_budget = context_budget or ContextBudgetManager.from_settings(settings)
        self.last_token_usage = 0
        self.last_context_report: dict = {}
        self.last_evidence_summary: list[dict] = []
        self.toolkits = tuple(toolkits)
        self.single_use = single_use
        self._single_use_used = False
        unknown_toolkits = set(self.toolkits) - set(TOOLKIT_NAMES)
        if not self.toolkits or unknown_toolkits:
            raise ValueError(f"unknown or empty subagent toolkits: {sorted(unknown_toolkits)}")

        # Each toolkit has its own restricted registry.  Review and execution
        # policy are inherited through the shared foundation tool constructors.
        # db_path/project_file are retained in _build_toolkit_tools' signature
        # for compatibility with callers that construct custom toolkits.
        db_path = ""
        skills = build_skills_section()
        self.sub_registries: dict[str, ToolRegistry] = {}
        self.system_prompts: dict[str, str] = {}
        for kind in self.toolkits:
            registry = ToolRegistry()
            for t in _build_toolkit_tools(
                kind, source_dir, test_dir, design_dir,
                db_path, project_file, review_manager, progress,
                command_executor, workspace_root,
            ):
                registry.register_tool(t)
            self.sub_registries[kind] = registry
            prompt = STRATEGY_SUBAGENT_SYSTEM if kind == "strategy" else SUBAGENT_SYSTEM
            if kind == "standard" and skills:
                prompt = f"{SUBAGENT_SYSTEM}\n\n{skills}"
            self.system_prompts[kind] = prompt

    async def _execute(self, params: dict) -> str:
        description = params.get("description", "")
        if not isinstance(description, str) or not description.strip():
            return "Error: description is required"

        if self.single_use and self._single_use_used:
            return "Error: the strategy subagent may be used only once per task"

        toolkit = str(params.get("toolkit") or self.toolkits[0]).strip().lower()
        if toolkit not in self.sub_registries:
            toolkit = self.toolkits[0]
        if self.single_use:
            self._single_use_used = True
        registry = self.sub_registries[toolkit]
        sub_tools = registry.get_openai_specs()
        parent_runtime = get_runtime()
        child_runtime = AgentRuntime(
            stop_check=parent_runtime.stop_check,
            run_id=f"{parent_runtime.run_id}/subagent" if parent_runtime.run_id else "subagent",
        )
        budget = ExecutionBudget(
            max_tool_calls=self.max_tool_calls,
            max_run_seconds=self.max_run_seconds,
            max_total_tokens=self.max_total_tokens,
            emergency_max_total_tokens=self.emergency_max_total_tokens,
            token_finalization_reserve_tokens=self.token_finalization_reserve_tokens,
        )
        child_runtime.execution_budget = budget
        child_runtime.convergence_controller = ConvergenceController()
        runtime_token = set_runtime(child_runtime)
        evidence_ledger = EvidenceLedger(max_records=max(self.max_tool_calls, 128))
        evidence_summary: list[dict] = []
        current_history: list[str] = []
        executor = ToolRoundExecutor(
            registry,
            agent_name="spawn_subagent",
            allowed_tools=set(registry.list_tools()),
            evidence_ledger=evidence_ledger,
            evidence_summary=evidence_summary,
            current_history=current_history,
        )
        built = self.context_budget.build_messages(
            self.system_prompts[toolkit], [], description, tools=sub_tools,
        )
        messages = built.messages
        current_user_index = built.current_user_index
        self.last_token_usage = 0
        self.last_evidence_summary = evidence_summary
        self.last_context_report = built.to_dict()
        self.last_context_report["context_policy"] = {
            "max_context_tokens": self.context_budget.budget.max_context_tokens,
            "output_reserve_tokens": self.context_budget.budget.output_reserve_tokens,
            "compaction_trigger_ratio": self.context_budget.budget.compaction_trigger_ratio,
            "compaction_target_tokens": self.context_budget.budget.max_history_tokens,
        }
        self.last_context_report["token_budget_policy"] = {
            "soft_target_tokens": budget.max_total_tokens,
            "emergency_limit_tokens": budget.emergency_max_total_tokens,
            "convergence_threshold_tokens": budget.max_total_tokens,
        }
        forced_finalization_reason = ""
        finalization_added = False
        soft_budget_notified = False
        step = 0

        def budget_message(reason: str) -> str:
            return (
                "Subagent execution budget exceeded: "
                f"{reason}; used {budget.total_tokens} of {budget.max_total_tokens} tokens. "
                "Return the verified evidence gathered so far."
            )

        def stopped_message(reason: str) -> str:
            return (
                "Subagent stopped: "
                f"{reason}; used {budget.total_tokens} of {budget.max_total_tokens} tokens. "
                "Return the verified evidence gathered so far."
            )

        try:
            get_hooks().trigger(
                HookEvent.RUN_START,
                HookContext(
                    event=HookEvent.RUN_START,
                    agent_name="spawn_subagent",
                    run_id=child_runtime.run_id,
                    runtime=child_runtime,
                ),
            )
            while True:
                step += 1
                if (
                    not soft_budget_notified
                    and budget.soft_limit_reached
                ):
                    soft_budget_notified = True
                    self.last_context_report["soft_budget_reached_tokens"] = budget.total_tokens
                    messages.append({
                        "role": "system",
                        "content": (
                            "## Subagent convergence checkpoint\n"
                            "The soft token target has been reached. Stop broad discovery, "
                            "avoid repeating reads, and take only the smallest action needed "
                            "to verify or summarize the requested finding."
                        ),
                    })
                # Soft token pressure narrows the next action; it does not
                # disable tools. Only convergence or the emergency ceiling
                # may finalize this subagent.
                finalization_mode = bool(forced_finalization_reason)
                active_tools = [] if finalization_mode else sub_tools
                if finalization_mode and not finalization_added:
                    messages.append({
                        "role": "system",
                        "content": (
                            "## Subagent finalization checkpoint\n"
                            "Do not call tools. Return a concise summary of verified findings, "
                            "including uncertainty and any missing evidence."
                            + (
                                f" Convergence stopped further exploration because: {forced_finalization_reason}."
                                if forced_finalization_reason else ""
                            )
                        ),
                    })
                    finalization_added = True

                if self.context_budget.should_compact(messages, tools=active_tools):
                    prior_call_ids = [
                        str(message.get("tool_call_id") or "")
                        for message in messages
                        if message.get("role") == "tool" and message.get("tool_call_id")
                    ]
                    messages, current_user_index, dropped, dropped_tokens = (
                        self.context_budget.compact_tool_history(
                            messages,
                            current_user_index=current_user_index,
                            target_tokens=self.context_budget.budget.max_history_tokens,
                            evidence_by_call=evidence_ledger.summary_for(prior_call_ids),
                        )
                    )
                    if dropped:
                        self.last_context_report["compacted_messages"] = (
                            self.last_context_report.get("compacted_messages", 0) + dropped
                        )
                        self.last_context_report["compacted_tokens"] = (
                            self.last_context_report.get("compacted_tokens", 0) + dropped_tokens
                        )

                messages, _ = self.context_budget.fit_messages(
                    messages,
                    tools=active_tools,
                    current_user_index=current_user_index,
                )
                before = get_hooks().trigger(
                    HookEvent.LLM_BEFORE,
                    HookContext(
                        event=HookEvent.LLM_BEFORE,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                        messages=messages,
                    ),
                )
                if isinstance(before, HookDecision) and before.action in {HookAction.STOP, "stop"}:
                    self.last_token_usage = budget.total_tokens
                    return budget_message("before the next model call")

                from app.trace.tracing import trace_span
                try:
                    with trace_span("spawn_subagent"):
                        response = await asyncio.wait_for(
                            self.llm.ainvoke_with_tools(
                                messages=messages,
                                tools=active_tools,
                                tool_choice="none" if finalization_mode else "auto",
                                temperature=0.3,
                            ),
                            timeout=self.llm_timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    self.last_token_usage = budget.total_tokens
                    return "Subagent LLM call timed out; return the verified evidence gathered so far."

                get_hooks().trigger(
                    HookEvent.LLM_AFTER,
                    HookContext(
                        event=HookEvent.LLM_AFTER,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                        messages=messages,
                        llm_response=response,
                    ),
                )
                self.last_token_usage = budget.total_tokens
                self.last_context_report.update({
                    "token_budget_used": budget.total_tokens,
                    "token_budget_remaining": budget.remaining_tokens,
                })
                content = str(response.get("content") or "")
                tool_calls = response.get("tool_calls")
                if finalization_mode:
                    tool_calls = None

                if not tool_calls:
                    if content.strip():
                        return content.strip()
                    child_runtime.control_decision = None
                    get_hooks().emit(
                        HookEvent.TOOL_BATCH_AFTER,
                        HookContext(
                            event=HookEvent.TOOL_BATCH_AFTER,
                            agent_name="spawn_subagent",
                            run_id=child_runtime.run_id,
                            runtime=child_runtime,
                            phase="empty_progress",
                            payload={"details": [], "actions": [], "step": step},
                        ),
                    )
                    decision = child_runtime.control_decision
                    if isinstance(decision, HookDecision):
                        if decision.action in {HookAction.FINALIZE, "finalize"}:
                            return stopped_message("convergence stalled")
                        if decision.action in {HookAction.RECOVER, "recover"}:
                            messages.append({
                                "role": "system",
                                "content": f"## Subagent convergence controller\n{decision.message}",
                            })
                            continue
                    messages.append({
                        "role": "system",
                        "content": "Return a concise evidence-based summary or call the minimum required tool.",
                    })
                    continue

                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })
                round_result = await executor.execute(tool_calls, step=step)
                messages.extend(round_result.tool_results)
                child_runtime.control_decision = None
                get_hooks().emit(
                    HookEvent.TOOL_BATCH_AFTER,
                    HookContext(
                        event=HookEvent.TOOL_BATCH_AFTER,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                        phase="tool_batch_complete",
                        payload={
                            "details": round_result.details,
                            "actions": round_result.actions,
                            "step": step,
                        },
                    ),
                )
                decision = child_runtime.control_decision
                if isinstance(decision, HookDecision) and decision.action in {
                    HookAction.RECOVER, HookAction.FINALIZE, "recover", "finalize",
                }:
                    messages.append({
                        "role": "system",
                        "content": f"## Subagent convergence controller\n{decision.message}",
                    })
                    if decision.action in {HookAction.FINALIZE, "finalize"}:
                        forced_finalization_reason = decision.reason or "convergence_stalled"

                if budget.total_tokens >= budget.emergency_max_total_tokens:
                    self.last_token_usage = budget.total_tokens
                    return stopped_message("emergency token safety limit after the current tool round")
        finally:
            try:
                get_hooks().trigger(
                    HookEvent.RUN_END,
                    HookContext(
                        event=HookEvent.RUN_END,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                    ),
                )
            finally:
                reset_runtime(runtime_token)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "The self-contained sub-task to delegate.",
                        },
                        "toolkit": {
                            "type": "string",
                            "enum": list(self.toolkits),
                            "description": (
                                "Subagent tool scope. Available values: "
                                + ", ".join(self.toolkits) + "."
                            ),
                        },
                    },
                    "required": ["description"],
                },
            },
        }
