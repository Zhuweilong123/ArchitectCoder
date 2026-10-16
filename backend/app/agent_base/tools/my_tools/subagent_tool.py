"""SpawnSubagentTool — 通用子代理工具。"""

from __future__ import annotations

from ...host_api.context import ContextReport, ModelRequestContext

import logging
import asyncio
import json

from app.agent_base.convergence import ConvergenceController
from app.agent_base.host_api.lifecycle import HookAction, HookContext, HookDecision, HookEvent
from app.agent_base.core.hooks import AgentRuntime, get_hooks, get_runtime, reset_runtime, set_runtime
from app.agent_base.host_api.errors import AgentInterrupted
from app.agent_base.host_api.agents import ModelPort, ReviewManagerPort, ProgressPort
from app.agent_base.core.policy import ExecutionBudget
from app.agent_base.core.subagent_budget import CumulativeTokenBudget
from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor, ToolObservation
from app.agent_base.agents.react_runtime.fc_model import ModelCaller, _invoke_fc_model
from app.agent_base.host_api.data import JsonObject, JsonValue, ModelMessage, ModelToolCall, ToolSchema
from app.agent_base.host_api.execution_types import EvidenceSnapshot
from app.agent_base.host_api.payloads import json_value
from app.agent_base.evidence import EvidenceLedger
from app.services.context_manager import ContextBudgetManager
from app.agent_base.tools.async_tool import AsyncTool
from app.agent_base.adapters.skills import SkillCatalog
from .subagent_toolkits import TOOLKIT_NAMES, build_subagent_toolkits
from app.runtime.command import CommandExecutor
from app.runtime import build_command_executor, workspace_root_for

logger = logging.getLogger(__name__)

SUBAGENT_TRACE_SPAN = "child_agent"


class SpawnSubagentTool(AsyncTool):
    """通用子代理工具 — 委托一个子任务，返回 summary。

    子代理复用主代理的已选模型，并以受限工具集（文件系统原语）独立跑简化 FC
    循环，避免主上下文膨胀。工具集不含 submit_uml_review / spawn_subagent，
    防止递归子代理和 UML 审核嵌套；bash 敏感命令仍走人工审核（与主代理
    共用同一审核通道），防止委托绕过。
    """

    def __init__(
        self,
        llm: ModelPort,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        project_file: str = "",
        max_total_tokens: int | None = None,
        emergency_max_total_tokens: int | None = None,
        context_budget: ContextBudgetManager | None = None,
        max_tool_calls: int | None = None,
        max_run_seconds: float | None = None,
        token_finalization_reserve_tokens: int | None = None,
        llm_timeout_seconds: float | None = None,
        review_manager: ReviewManagerPort | None = None,
        progress: ProgressPort | None = None,
        command_executor: CommandExecutor | None = None,
        workspace_root: str = "",
        toolkits: tuple[str, ...] = TOOLKIT_NAMES,
        single_use: bool = False,
        max_cumulative_tokens: int | None = 131072,
        child_run_name: str = "subagent",
        skill_catalog: SkillCatalog | None = None,
    ) -> None:
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
        from backend.config import get_settings
        settings = get_settings()
        self.max_total_tokens = max(1, int(
            max_total_tokens if max_total_tokens is not None
            else settings.agent_context_soft_limit_tokens
        ))
        self.emergency_max_total_tokens = (
            max(1, int(emergency_max_total_tokens))
            if emergency_max_total_tokens is not None else settings.agent_context_hard_limit_tokens
        )
        if command_executor is None:
            command_executor = build_command_executor(settings)
        if not workspace_root:
            workspace_root = workspace_root_for(source_dir, test_dir, design_dir)
        self.max_tool_calls = max(0, int(
            max_tool_calls if max_tool_calls is not None else settings.agent_max_tool_calls
        ))
        self.max_run_seconds = max(0.0, float(
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
        self.last_context_report: ContextReport = {}
        self.last_evidence_summary: list[EvidenceSnapshot] = []
        self.final_summary_max_tokens = settings.agent_final_summary_max_tokens
        self.toolkits = tuple(toolkits)
        self.single_use = single_use
        self._single_use_used = False
        # Separate cumulative ceiling from the shared request context policy.
        # Callers may explicitly pass None to disable this ceiling.
        self.max_cumulative_tokens = (
            max(1, int(max_cumulative_tokens))
            if max_cumulative_tokens is not None else None
        )
        self.child_run_name = child_run_name
        assembled = build_subagent_toolkits(
            self.toolkits, source_dir=source_dir, test_dir=test_dir, design_dir=design_dir,
            review_manager=review_manager, progress=progress, command_executor=command_executor,
            workspace_root=workspace_root, skill_catalog=skill_catalog,
        )
        self.sub_registries = assembled.registries
        self.system_prompts = assembled.prompts

    @staticmethod
    def _normalise_tool_calls(raw_tool_calls: object, step: int) -> tuple[list[ModelToolCall], bool]:
        """Normalize provider output before it is put back into the prompt.

        Providers and test doubles occasionally return a single call, non-string
        arguments, or a partially malformed call.  The parent ReAct loop should
        never receive a malformed assistant message from a child run, because a
        subsequent provider request would fail with an opaque protocol error.
        Valid calls are retained; malformed calls are discarded and reported to
        the caller so the child can terminate with a useful fallback summary.
        """
        if raw_tool_calls is None:
            return [], False
        candidates = raw_tool_calls if isinstance(raw_tool_calls, list) else [raw_tool_calls]
        normalized: list[ModelToolCall] = []
        malformed = False
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                malformed = True
                continue
            function = candidate.get("function")
            if not isinstance(function, dict) or not str(function.get("name") or "").strip():
                malformed = True
                continue
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(json_value(arguments), ensure_ascii=False)
                except (TypeError, ValueError):
                    malformed = True
                    continue
            call_id = str(candidate.get("id") or f"subagent-{step}-{index}").strip()
            if not call_id:
                malformed = True
                continue
            normalized.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": str(function["name"]).strip(),
                    # Keep invalid JSON as a string so the executor can return a
                    # recoverable INVALID_ARGUMENTS observation to the model.
                    "arguments": arguments,
                },
            })
        return normalized, malformed and not normalized

    @staticmethod
    def _tool_messages(tool_results: list[ToolObservation]) -> list[ModelMessage]:
        """Convert executor results to provider-valid ``role=tool`` messages."""
        messages: list[ModelMessage] = []
        for result in tool_results:
            if not isinstance(result, dict):
                raise ValueError("subagent tool result must be an object")
            call_id = str(result.get("tool_call_id") or "").strip()
            if not call_id:
                raise ValueError("subagent tool result is missing tool_call_id")
            content = result.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, default=str)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": content,
            })
        return messages

    def _safe_failure_summary(
        self,
        reason: str,
        evidence_summary: list[EvidenceSnapshot],
        budget: ExecutionBudget | None = None,
        error: Exception | None = None,
    ) -> str:
        """Return a bounded, evidence-preserving result instead of raising.

        A subagent is an optional delegation.  Its transport/protocol failure
        must be visible to the parent model but must not abort the parent task.
        Only compact evidence facts are returned; raw tool output remains in the
        trace and is not copied into the parent context.
        """
        error_text = ""
        if error is not None:
            error_text = f" ({type(error).__name__}: {str(error)[:240]})"
        if budget is not None:
            self.last_token_usage = budget.total_tokens
            self.last_context_report.update({
                "token_budget_used": budget.request_tokens,
                "token_usage_total_observed": budget.total_tokens,
                "token_budget_stop_reason": reason,
            })
        self.last_context_report["subagent_stop_reason"] = reason
        compact_evidence = []
        for item in evidence_summary[-8:]:
            if not isinstance(item, dict):
                continue
            facts = "; ".join(str(value) for value in item.get("facts") or [])
            detail = str(item.get("detail") or "")
            line = " | ".join(
                part for part in (
                    str(item.get("tool_name") or "tool"),
                    str(item.get("status") or "unknown"),
                    facts,
                    detail,
                ) if part
            )
            if line:
                compact_evidence.append(line[:360])
        evidence_text = "\n".join(f"- {line}" for line in compact_evidence)
        return (
            f"Subagent stopped safely: {reason}{error_text}. "
            "The parent task may continue without subagent findings."
            + (f"\nVerified evidence gathered before stopping:\n{evidence_text}" if evidence_text else "")
        )

    async def _execute(self, params: JsonObject) -> str:
        from ...core.operations import operation_scope
        parent_run_id = get_runtime().run_id
        child_run_id = f"{parent_run_id}/{self.child_run_name}" if parent_run_id else self.child_run_name
        with operation_scope("subagent", run_id=child_run_id, stage=HookEvent.RUN_START.value):
            return await self._execute_impl(params)

    async def _execute_impl(self, params: JsonObject) -> str:
        description = params.get("description", "")
        if not isinstance(description, str) or not description.strip():
            return "Error: description is required"

        if self.single_use and self._single_use_used:
            return "Error: the subagent may be used only once per task"

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
            run_id=(
                f"{parent_runtime.run_id}/{self.child_run_name}"
                if parent_runtime.run_id else self.child_run_name
            ),
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
        evidence_ledger = EvidenceLedger(max_records=max(self.max_tool_calls, 128))
        evidence_summary: list[EvidenceSnapshot] = []
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
        current_user_index: int | None = built.current_user_index
        self.last_token_usage = 0
        self.last_evidence_summary = evidence_summary
        self.last_context_report = built.to_dict()
        self.last_context_report["context_policy"] = {
            "max_context_tokens": self.context_budget.budget.max_context_tokens,
            "soft_threshold_ratio": self.context_budget.budget.soft_threshold_ratio,
            "compaction_trigger_ratio": self.context_budget.budget.compaction_trigger_ratio,
            "compaction_target_tokens": self.context_budget.budget.max_history_tokens,
        }
        self.last_context_report["token_budget_policy"] = {
            "scope": "single_llm_request",
            "soft_target_tokens": budget.max_total_tokens,
            "emergency_limit_tokens": budget.emergency_max_total_tokens,
            "task_total_tokens_observed": budget.total_tokens,
            "cumulative_limit_tokens": self.max_cumulative_tokens,
        }
        forced_finalization_reason = ""
        finalization_added = False
        context_soft_notified = False
        step = 0
        round_open = False
        terminal_status = "completed"
        cumulative_budget = (
            CumulativeTokenBudget(
                self.max_cumulative_tokens, self.final_summary_max_tokens,
                self.token_finalization_reserve_tokens,
            )
            if self.max_cumulative_tokens is not None else None
        )

        async def publish(stage: HookEvent, **data: JsonValue) -> None:
            await get_hooks().aemit(stage, HookContext(stage, "spawn_subagent",
                run_id=child_runtime.run_id, runtime=child_runtime,
                payload={"step": step, **data}))

        def budget_message(reason: str) -> str:
            return (
                "Subagent execution budget exceeded: "
                f"{reason}; latest request used {budget.request_tokens} of "
                f"{budget.max_total_tokens} target tokens "
                f"({budget.total_tokens} observed across requests). "
                "Return the verified evidence gathered so far."
            )

        def stopped_message(reason: str) -> str:
            return (
                "Subagent stopped: "
                f"{reason}; latest request used {budget.request_tokens} of "
                f"{budget.max_total_tokens} target tokens "
                f"({budget.total_tokens} observed across requests). "
                "Return the verified evidence gathered so far."
            )

        def safe_failure(reason: str, error: Exception | None = None) -> str:
            nonlocal terminal_status
            terminal_status = "failed"
            return self._safe_failure_summary(reason, evidence_summary, budget, error)

        runtime_token = set_runtime(child_runtime)
        try:
            await get_hooks().atrigger(
                HookEvent.RUN_START,
                HookContext(
                    event=HookEvent.RUN_START,
                    agent_name="spawn_subagent",
                    run_id=child_runtime.run_id,
                    runtime=child_runtime,
                ),
            )
            while True:
                if round_open:
                    round_open = False
                    await publish(HookEvent.ROUND_AFTER)
                step += 1
                round_open = True
                await publish(HookEvent.ROUND_BEFORE)
                # Token usage is observed per request. Convergence, time, and
                # tool-call policies decide whether the subagent continues.
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

                if (
                    not context_soft_notified
                    and self.context_budget.soft_threshold_reached(messages, tools=active_tools)
                ):
                    context_soft_notified = True
                    messages.append({
                        "role": "system",
                        "content": (
                            "## Context convergence checkpoint\n"
                            "The request context is nearing its hard limit. Stop broad exploration, "
                            "use only the minimum evidence-gathering actions, and prepare a concise summary."
                        ),
                    })

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

                fitted = self.context_budget.fit_messages(
                    messages,
                    tools=active_tools,
                    current_user_index=current_user_index,
                )
                messages = fitted.messages
                current_user_index = fitted.current_user_index
                if finalization_mode and self.max_cumulative_tokens is not None:
                    remaining = max(0, self.max_cumulative_tokens - budget.total_tokens)
                    call_ids = [str(m.get("tool_call_id") or "") for m in messages
                                if m.get("role") == "tool"]
                    messages, current_user_index, dropped, dropped_tokens = (
                        self.context_budget.compact_tool_history(
                            messages, current_user_index=current_user_index,
                            target_tokens=max(1, min(6000, remaining // 3)),
                            evidence_by_call=evidence_ledger.summary_for(call_ids),
                        )
                    )
                    self.last_context_report["compacted_messages"] = (
                        self.last_context_report.get("compacted_messages", 0) + dropped
                    )
                    self.last_context_report["compacted_tokens"] = (
                        self.last_context_report.get("compacted_tokens", 0) + dropped_tokens
                    )
                request_context: ModelRequestContext = {
                    "scope": "single_llm_request",
                    "estimated_context_tokens": self.context_budget.estimate_request_tokens(
                        messages, tools=active_tools,
                    ),
                    "message_count": len(messages),
                    "tool_schema_tokens": self.context_budget.tool_schema_tokens(active_tools),
                    "tool_schema_mode": "full" if active_tools else "omitted_for_finalization",
                    "request_budget_target_tokens": budget.max_total_tokens,
                    "request_budget_emergency_tokens": budget.emergency_max_total_tokens,
                    "context_soft_threshold_ratio": self.context_budget.budget.soft_threshold_ratio,
                    "context_compaction_threshold_ratio": self.context_budget.budget.compaction_trigger_ratio,
                    "context_hard_limit_tokens": self.context_budget.budget.max_context_tokens,
                    "task_total_tokens_observed": budget.total_tokens,
                    "cumulative_limit_tokens": self.max_cumulative_tokens,
                }
                self.last_context_report["last_request_context"] = request_context
                if cumulative_budget is not None:
                    allowance = cumulative_budget.allowance(
                        budget.total_tokens, request_context["estimated_context_tokens"],
                        finalizing=finalization_mode,
                    )
                    if allowance.finalize:
                        forced_finalization_reason = "task token budget approaching"
                        continue
                    if allowance.output_tokens < 128:
                        terminal_status = "partial"
                        self.last_token_usage = budget.total_tokens
                        self.last_context_report["token_budget_stop_reason"] = "task_token_limit"
                        return stopped_message("task token limit") + "\nEvidence snapshot:\n" + json.dumps(
                            {"status": "partial", "stop_reason": "task_token_limit",
                             "evidence": [{
                                 "tool_name": str(item.get("tool_name", ""))[:80],
                                 "status": item.get("status"),
                                 "facts": [str(fact)[:240] for fact in (item.get("facts") or [])[:6]],
                             } for item in evidence_summary[-8:]]}, ensure_ascii=False, default=str,
                        )
                    request_context["output_token_limit"] = allowance.output_tokens
                    request_context["input_token_allowance"] = allowance.input_tokens
                if request_context["estimated_context_tokens"] >= self.context_budget.budget.max_context_tokens:
                    self.last_token_usage = budget.total_tokens
                    self.last_context_report.update({
                        "token_budget_used": budget.request_tokens,
                        "token_usage_total_observed": budget.total_tokens,
                        "token_budget_stop_reason": "context_hard_limit",
                    })
                    return stopped_message("context hard limit")
                invocation = await _invoke_fc_model(
                    ModelCaller(self.name, self.llm), messages=messages, tool_specs=active_tools,
                    finalization_mode=finalization_mode, request_context=request_context,
                    runtime=child_runtime, temperature=0.3, timeout_seconds=self.llm_timeout_seconds,
                    trace_span_name=SUBAGENT_TRACE_SPAN,
                )
                if invocation.failure_kind == "timeout":
                    return safe_failure("LLM call timed out")
                response = invocation.response
                if response is None:
                    self.last_token_usage = budget.total_tokens
                    return budget_message("before the next model call")
                self.last_token_usage = budget.total_tokens
                usage = (response.get("usage") or {}) if isinstance(response, dict) else {}
                actual_input = int(usage.get("prompt_tokens") or 0)
                if cumulative_budget is not None:
                    cumulative_budget.observe_input(actual_input, request_context["estimated_context_tokens"])
                self.last_context_report["cumulative_budget_overrun_tokens"] = max(
                    0, budget.total_tokens - self.max_cumulative_tokens,
                ) if self.max_cumulative_tokens is not None else 0
                self.last_context_report.update({
                    "token_budget_used": budget.request_tokens,
                    "token_budget_remaining": budget.remaining_tokens,
                    "token_usage_total_observed": budget.total_tokens,
                })
                if not isinstance(response, dict):
                    return safe_failure("provider returned a non-object response")
                content = str(response.get("content") or "")
                tool_calls, malformed_tool_calls = self._normalise_tool_calls(
                    response.get("tool_calls"), step,
                )
                if malformed_tool_calls:
                    return safe_failure("provider returned malformed tool calls")
                if finalization_mode:
                    tool_calls = []

                if not tool_calls:
                    if content.strip():
                        return content.strip()
                    child_runtime.control_decision = None
                    await get_hooks().aemit(
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
                # ToolRoundExecutor records the live call/result span. Keep
                # this batch under the child span so blocking reviews and
                # nested tools retain their real order without duplicates.
                from app.agent_base.core.observability import trace_span
                await publish(HookEvent.TOOL_BATCH_BEFORE, tool_count=len(tool_calls))
                with trace_span(SUBAGENT_TRACE_SPAN):
                    round_result = await executor.execute(tool_calls, step=step)
                # ToolRoundExecutor returns an internal compact result shape.
                # The child loop must add the provider-facing role explicitly;
                # the parent FC loop does the same in fc_loop.py.
                messages.extend(self._tool_messages(round_result.tool_results))
                child_runtime.control_decision = None
                await get_hooks().aemit(
                    HookEvent.TOOL_BATCH_AFTER,
                    HookContext(
                        event=HookEvent.TOOL_BATCH_AFTER,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                        phase="tool_batch_complete",
                        payload={
                            "details": json_value(round_result.details),
                            "actions": json_value(round_result.actions),
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

        except AgentInterrupted:
            terminal_status = "cancelled"
            await publish(HookEvent.CANCEL)
            raise
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            await publish(HookEvent.CANCEL)
            raise
        except Exception as exc:
            await publish(HookEvent.ERROR, error_type=type(exc).__name__, source="subagent")
            logger.exception("[spawn_subagent] child run failed; degrading safely")
            return safe_failure("internal child-run error", exc)
        finally:
            try:
                from ...core.operations import current_operation
                operation = current_operation()
                if operation is not None:
                    operation.status = terminal_status
                if round_open:
                    await publish(HookEvent.ROUND_AFTER, status=terminal_status)
                await publish(HookEvent.FINALIZE, status=terminal_status)
                await get_hooks().atrigger(
                    HookEvent.RUN_END,
                    HookContext(
                        event=HookEvent.RUN_END,
                        agent_name="spawn_subagent",
                        run_id=child_runtime.run_id,
                        runtime=child_runtime,
                        payload={"status": terminal_status, "execution_only": True},
                    ),
                )
            finally:
                reset_runtime(runtime_token)

    def to_openai_schema(self) -> ToolSchema:
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
