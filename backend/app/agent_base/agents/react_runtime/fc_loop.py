"""Native Function Calling loop for ReActAgent."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from ...convergence import ConvergenceController
from ...core.exceptions import AgentInterrupted
from ...core.hooks import (
    HookAction,
    HookContext,
    HookDecision,
    HookEvent,
    get_hooks,
    get_runtime,
    todo_plan_complete,
)
from ...core.policy import ExecutionBudget
from ...evidence import EvidenceLedger
from app.services.context_manager import HistoryCompaction
from .react_parser import remove_textual_tool_markup
from .react_types import ReActProgress
from .tool_round_executor import ToolRoundExecutor

logger = logging.getLogger(__name__)


async def run_fc_loop(
    agent,
    input_text: str,
    context: str = "",
    **kwargs,
) -> AsyncIterator[ReActProgress]:
    """原生 Function Calling 驱动的流式主循环。

    每轮 yield :class:`ReActProgress` — 包含步骤号、工具调用详情、
    思考内容、是否为最终轮。

    流程:
    1. 构建 messages（system + user）
    2. 调用 llm.ainvoke_with_tools(tool_specs)
    3. 遍历 tool_calls → 全部执行（支持多工具并行）
    4. yield ReActProgress → 追加 assistant + tool 消息
    5. 重复直到模型返回最终文本，或触发资源预算/收敛保护
    """
    from app.trace.tracing import trace_span

    allowed_tools = kwargs.pop("allowed_tools", None)
    initial_token_usage = max(0, int(kwargs.pop("initial_token_usage", 0) or 0))
    allowed_set = set(allowed_tools) if allowed_tools is not None else None
    # Preserve caller-provided tool order for stable schemas/cache keys;
    # use a set only for membership checks below. This is unrelated to
    # model selection, which is fixed for the session.
    full_tool_specs = (
        agent.tool_registry.get_openai_specs_for(allowed_tools)
        if allowed_tools is not None else agent.tool_registry.get_openai_specs()
    )
    # Session-level semantic compression runs between turns. Do not compact
    # user/assistant conversation inside an active task execution.
    compacted = HistoryCompaction(
        messages=list(agent._history),
        summary=agent._history_summary,
    )
    built = agent.context_budget.build_messages(
        agent._build_fc_system_prompt(),
        compacted.messages,
        input_text,
        context=context,
        history_summary=agent._history_summary,
        tools=full_tool_specs,
    )
    messages = built.messages
    current_user_index = built.current_user_index
    agent.last_context_report = built.to_dict()
    agent.last_context_report["token_budget_stop_reason"] = "model_answer"
    agent._run_verifications = {}
    agent.last_context_report.update({
        "compacted_messages": compacted.dropped_messages,
        "compacted_tokens": compacted.dropped_tokens,
        "convergence_policy": {
            "budget_threshold_tokens": agent.max_total_tokens,
            "evidence_max_records": agent.evidence_max_records,
            "open_ended_loop": True,
            "max_stalled_rounds": agent.convergence_max_stalled_rounds,
            "max_recovery_rounds": agent.convergence_max_recovery_rounds,
            "repeat_action_threshold": agent.convergence_repeat_action_threshold,
            "final_summary_max_tokens": agent.final_summary_max_tokens,
        },
        "context_policy": {
            "max_context_tokens": agent.context_budget.budget.max_context_tokens,
            "soft_threshold_ratio": agent.context_budget.budget.soft_threshold_ratio,
            "compaction_trigger_ratio": agent.context_budget.budget.compaction_trigger_ratio,
            "compaction_target_tokens": agent.context_budget.budget.max_history_tokens,
        },
    })
    if compacted.dropped_messages and agent.on_context_compacted:
        agent.on_context_compacted({
            "summary": compacted.summary,
            "dropped_messages": compacted.dropped_messages,
            "dropped_tokens": compacted.dropped_tokens,
        })

    agent.current_history = []
    no_tool_call_streak = 0
    _turn_recorded = False
    # Planning and read-only exploration happen immediately before this loop.
    # Token usage is observed for metrics, while the request ceiling is
    # enforced by ContextBudgetManager for each individual LLM request.
    budget = agent.execution_budget or ExecutionBudget(
        max_tool_calls=agent.max_tool_calls,
        max_run_seconds=agent.max_run_seconds,
        max_total_tokens=agent.max_total_tokens,
        emergency_max_total_tokens=agent.emergency_max_total_tokens,
        token_finalization_reserve_tokens=agent.token_finalization_reserve_tokens,
    )
    agent.execution_budget = budget
    budget.start(initial_token_usage)
    agent.last_context_report["token_budget_policy"] = {
        "scope": "single_llm_request",
        "soft_target_tokens": budget.max_total_tokens,
        "emergency_limit_tokens": budget.emergency_max_total_tokens,
        "task_total_tokens_observed": budget.total_tokens,
    }
    tool_call_count = budget.tool_call_count
    total_tokens = budget.total_tokens
    convergence_directive_added = False
    last_failure_directive_signature: tuple[tuple[str, str], ...] = ()
    evidence_ledger = EvidenceLedger(max_records=agent.evidence_max_records)
    convergence = ConvergenceController(
        max_stalled_rounds=agent.convergence_max_stalled_rounds,
        max_recovery_rounds=agent.convergence_max_recovery_rounds,
        repeat_action_threshold=agent.convergence_repeat_action_threshold,
    )
    forced_finalization_reason = ""
    agent.last_evidence_summary = []
    tool_round_executor = ToolRoundExecutor(
        agent.tool_registry,
        agent_name=agent.name,
        allowed_tools=allowed_set,
        evidence_ledger=evidence_ledger,
        evidence_summary=agent.last_evidence_summary,
        current_history=agent.current_history,
        verifications=agent._run_verifications,
    )

    runtime = get_runtime()
    runtime.execution_budget = budget
    runtime.convergence_controller = convergence
    runtime.control_decision = None
    get_hooks().trigger(
        HookEvent.RUN_START,
        HookContext(
            event=HookEvent.RUN_START,
            agent_name=agent.name,
            run_id=runtime.run_id,
            runtime=runtime,
            payload={"initial_token_usage": initial_token_usage},
        ),
    )
    try:
        step = 0
        while True:
            step += 1
            remaining_tokens = budget.remaining_tokens
            # Only the convergence controller is allowed to enter tool-free
            # finalization mode; token usage is request-scoped telemetry.
            finalization_mode = bool(forced_finalization_reason)
            # Keep one stable tool payload for every request in this run so
            # provider prompt-cache prefixes remain reusable.
            active_tool_specs = [] if finalization_mode else full_tool_specs
            if finalization_mode:
                messages.append({
                    "role": "system",
                    "content": (
                        "## Finalization checkpoint\n"
                        "Do not call tools. Provide the final user-facing answer now, using only "
                        "completed tool evidence. Clearly distinguish completed changes and verification "
                        "from anything still unverified or incomplete."
                        + (
                            f" The convergence controller stopped further exploration because: "
                            f"{forced_finalization_reason}."
                            if forced_finalization_reason else ""
                        )
                    ),
                })
                agent.last_context_report.update({
                    "token_budget_used": budget.request_tokens,
                    "token_budget_remaining": remaining_tokens,
                    "token_budget_finalization_mode": True,
                })
            context_tokens_before = agent.context_budget.estimate_request_tokens(
                messages, tools=active_tool_specs,
            )
            context_compaction_triggered = agent.context_budget.should_compact(
                messages, tools=active_tool_specs,
            )
            convergence_reasons = []
            if agent.context_budget.soft_threshold_reached(
                messages, tools=active_tool_specs,
            ):
                convergence_reasons.append("context_soft_threshold")
            prior_call_ids = [
                str(message.get("tool_call_id") or "")
                for message in messages
                if message.get("role") == "tool" and message.get("tool_call_id")
            ]
            compacted_steps = 0
            compacted_tokens = 0
            if context_compaction_triggered:
                messages, current_user_index, compacted_steps, compacted_tokens = (
                    agent.context_budget.compact_tool_history(
                        messages,
                        current_user_index=current_user_index,
                        target_tokens=agent.context_budget.budget.max_history_tokens,
                        evidence_by_call=evidence_ledger.summary_for(prior_call_ids),
                    )
                )
            if compacted_steps:
                agent.last_context_report["react_compacted_steps"] = (
                    agent.last_context_report.get("react_compacted_steps", 0) + compacted_steps
                )
                agent.last_context_report["react_compacted_tokens"] = (
                    agent.last_context_report.get("react_compacted_tokens", 0) + compacted_tokens
                )
                if context_compaction_triggered:
                    agent.last_context_report["context_budget_compaction"] = {
                        "triggered_by": ["context_usage_ratio"],
                        "triggered_at_tool_calls": tool_call_count,
                        "context_tokens_before": context_tokens_before,
                        "target_tokens": agent.context_budget.budget.max_history_tokens,
                    }
                if agent.on_context_compacted:
                    checkpoint = next(
                        (
                            str(message.get("content") or "")
                            for message in messages
                            if message.get("role") == "system"
                            and str(message.get("content") or "").startswith("## Tool execution checkpoint")
                        ),
                        "Tool execution steps compacted.",
                    )
                    agent.on_context_compacted({
                        "summary": checkpoint,
                        "dropped_messages": compacted_steps,
                        "dropped_tokens": compacted_tokens,
                        "reason": "context_budget" if context_compaction_triggered else "history_limit",
                        "triggered_by": ["context_usage_ratio"] if context_compaction_triggered else [],
                        "tool_call_count": tool_call_count,
                        "token_budget_used": budget.request_tokens,
                        "context_target_tokens": agent.context_budget.budget.max_history_tokens,
                    })
            if convergence_reasons and not convergence_directive_added:
                messages.append({
                    "role": "system",
                    "content": (
                        "## Convergence checkpoint\n"
                        "The productive exploration threshold has been reached. Stop broad discovery now. "
                        "If the requested change is not applied, make the smallest scoped edit next; "
                        "if it is applied, run the focused existing verification next; for analysis-only "
                        "requests, provide the evidence-based answer now. Do not reread files or repeat "
                        "searches already covered by the evidence unless a specific missing range is required. "
                        "After that minimum action, finish with a concise status and remaining uncertainty."
                    ),
                })
                convergence_directive_added = True
                agent.last_context_report["convergence_directive_added"] = {
                    "triggered_by": convergence_reasons,
                    "triggered_at_tool_calls": tool_call_count,
                    "token_budget_used": budget.request_tokens,
                }
            messages, dropped = agent.context_budget.fit_messages(
                messages,
                tools=active_tool_specs,
                current_user_index=current_user_index,
            )
            if dropped:
                agent.last_context_report["loop_dropped_messages"] = (
                    agent.last_context_report.get("loop_dropped_messages", 0) + dropped
                )
            request_context = {
                "scope": "single_llm_request",
                "estimated_context_tokens": agent.context_budget.estimate_request_tokens(
                    messages, tools=active_tool_specs,
                ),
                "message_count": len(messages),
                "tool_schema_tokens": agent.context_budget.tool_schema_tokens(active_tool_specs),
                "tool_schema_mode": "full" if active_tool_specs else "omitted_for_finalization",
                "tool_schema_stable": bool(active_tool_specs) and active_tool_specs is full_tool_specs,
                "request_budget_target_tokens": budget.max_total_tokens,
                "request_budget_emergency_tokens": budget.emergency_max_total_tokens,
                "context_soft_threshold_ratio": agent.context_budget.budget.soft_threshold_ratio,
                "context_compaction_threshold_ratio": agent.context_budget.budget.compaction_trigger_ratio,
                "context_hard_limit_tokens": agent.context_budget.budget.max_context_tokens,
                "task_total_tokens_observed": budget.total_tokens,
            }
            agent.last_context_report["last_request_context"] = request_context
            if request_context["estimated_context_tokens"] >= agent.context_budget.budget.max_context_tokens:
                final_answer = (
                    "请求上下文已达到硬上限，已停止继续扩展上下文；"
                    "请基于已保留的证据完成任务。"
                )
                agent.last_context_report.update({
                    "token_budget_used": budget.request_tokens,
                    "token_usage_total_observed": budget.total_tokens,
                    "token_budget_stop_reason": "context_hard_limit",
                    "context_hard_limit_tokens": agent.context_budget.budget.max_context_tokens,
                })
                agent._record_turn(input_text, final_answer)
                yield agent._final_progress(
                    total_tokens=budget.total_tokens,
                    step=step,
                    thought=final_answer,
                    is_final=True,
                    final_answer=final_answer,
                )
                return
            logger.info("\n--- FC loop round %d ---", step)
            # 1. 调用 LLM（带工具 schemas）
            llm_before_decision = get_hooks().trigger(
                HookEvent.LLM_BEFORE,
                HookContext(
                    event=HookEvent.LLM_BEFORE,
                    agent_name=agent.name,
                    run_id=runtime.run_id,
                    runtime=runtime,
                    messages=messages,
                ),
            )
            if (
                isinstance(llm_before_decision, HookDecision)
                and llm_before_decision.action == HookAction.STOP
            ):
                reason = llm_before_decision.reason
                agent.last_context_report.update({
                    "token_budget_used": budget.request_tokens,
                    "token_usage_total_observed": budget.total_tokens,
                    "token_budget_stop_reason": reason,
                })
                final_answer = (
                    "Execution budget reached before the next model call; "
                    "the task was finalized with the available evidence."
                )
                agent._record_turn(input_text, final_answer)
                yield agent._final_progress(
                    total_tokens=budget.total_tokens,
                    step=step,
                    thought=final_answer,
                    is_final=True,
                    final_answer=final_answer,
                )
                return
            with trace_span(f"{agent.name}"):
                try:
                    response = await asyncio.wait_for(
                        agent.llm.ainvoke_with_tools(
                            messages=messages,
                            tools=active_tool_specs,
                            tool_choice="none" if finalization_mode else "auto",
                            temperature=kwargs.get("temperature", 0.3),
                            trace_context=request_context,
                        ),
                        timeout=kwargs.get("llm_timeout_seconds", agent.llm_timeout_seconds),
                    )
                except asyncio.TimeoutError:
                    agent.last_context_report.update({
                        "token_budget_used": budget.request_tokens,
                        "token_budget_stop_reason": "llm_timeout",
                    })
                    final_answer = "LLM 调用超过时间预算，已停止本轮任务。"
                    agent._record_turn(input_text, final_answer)
                    yield agent._final_progress(total_tokens=total_tokens,step=step, thought=final_answer,
                                        is_final=True, final_answer=final_answer)
                    return
            get_hooks().trigger(
                HookEvent.LLM_AFTER,
                HookContext(
                    event=HookEvent.LLM_AFTER,
                    agent_name=agent.name,
                    run_id=runtime.run_id,
                    runtime=runtime,
                    messages=messages,
                    llm_response=response,
                ),
            )

            tool_calls = response.get("tool_calls")
            content = response.get("content") or ""
            total_tokens = budget.total_tokens
            # A finalization request is deliberately tool-free.  Protect
            # against non-conforming test doubles/providers returning a
            # tool call despite tool_choice='none'.
            textual_tool_markup = False
            if finalization_mode:
                tool_calls = None
                content, textual_tool_markup = remove_textual_tool_markup(content)
            # 2. 无 tool_calls → 纯文本回复
            if not tool_calls:
                no_tool_call_streak += 1
                agent.current_history.append(
                    f"Step {step}: 模型返回纯文本 ({len(content)} 字符)"
                )
                logger.info("  → 无工具调用，streak=%d，内容预览: %s",
                           no_tool_call_streak, content[:120])

                messages.append({"role": "assistant", "content": content})

                if not content.strip() or not todo_plan_complete(get_runtime()):
                    runtime.control_decision = None
                    get_hooks().emit(
                        HookEvent.TOOL_BATCH_AFTER,
                        HookContext(
                            event=HookEvent.TOOL_BATCH_AFTER,
                            agent_name=agent.name,
                            run_id=runtime.run_id,
                            runtime=runtime,
                            phase="empty_progress",
                            payload={"details": [], "actions": [], "step": step},
                        ),
                    )
                    empty_progress_decision = runtime.control_decision
                    if (
                        isinstance(empty_progress_decision, HookDecision)
                        and empty_progress_decision.action in {
                            HookAction.RECOVER, HookAction.FINALIZE, "recover", "finalize",
                        }
                    ):
                        agent.last_context_report.setdefault("convergence_events", []).append({
                            "action": empty_progress_decision.action,
                            "reason": empty_progress_decision.reason,
                            "stalled_rounds": convergence.stalled_rounds,
                            "recovery_rounds": convergence.recovery_rounds,
                        })
                        messages.append({
                            "role": "system",
                            "content": (
                                "## Convergence controller\n"
                                f"{empty_progress_decision.message}"
                            ),
                        })
                        if empty_progress_decision.action in {HookAction.FINALIZE, "finalize"}:
                            forced_finalization_reason = empty_progress_decision.reason
                            agent.last_context_report["convergence_finalization"] = {
                                "reason": empty_progress_decision.reason,
                                "stalled_rounds": convergence.stalled_rounds,
                                "recovery_rounds": convergence.recovery_rounds,
                            }

                # 模型本轮直接给出实质回复（未调用工具）→ 这就是最终答案。
                # 不要求 streak>=2 或 tool_executed，避免"你好"这类问候被循环
                # 逼着再走一步（继续问模型"下一步做什么"）而触发无意义的工具调用。
                if content.strip():
                    if finalization_mode:
                        if textual_tool_markup:
                            content = (
                                "预算收尾阶段已停止工具调用；模型尝试发起的文本工具调用未执行。"
                                f"当前进展：{content}"
                            )
                        if not todo_plan_complete(get_runtime()):
                            content = (
                                "Task is not complete: the required todo plan or acceptance "
                                "verification still has unfinished work.\n\n"
                                f"{content}"
                            )
                        agent.last_context_report.update({
                            "token_budget_used": budget.request_tokens,
                            "token_budget_stop_reason": (
                                forced_finalization_reason or "reserve_finalization"
                            ),
                            "finalization_textual_tool_markup_blocked": textual_tool_markup,
                        })
                        if not _turn_recorded:
                            agent._record_turn(input_text, content)
                            _turn_recorded = True
                        yield agent._final_progress(total_tokens=total_tokens,
                            step=step, thought=content,
                            is_final=True, final_answer=content,
                        )
                        return
                    # Planning mode is opt-in and only closes after its
                    # observable acceptance contract has been satisfied.
                    if not todo_plan_complete(get_runtime()):
                        gate_message = (
                            "<planning-gate>Do not finish yet. Create or update the task "
                            "todo list and complete every item. If this is an acceptance-driven "
                            "plan, complete its verification item only after checking its "
                            "criterion.</planning-gate>"
                        )
                        messages.append({"role": "user", "content": gate_message})
                        yield ReActProgress(
                            step=step, thought=content, actions=[], is_final=False,
                        )
                        continue
                    logger.info("🏁 %s FC 完成（模型直接回复）", agent.name)
                    # 必须在 yield 之前写入历史：流式消费方（_handle_dev）在收到
                    # is_final=True 后立即 return 并关闭生成器，yield 之后的代码
                    # 不会再执行，若把 add_message 放在生成器末尾会永远丢历史。
                    if not _turn_recorded:
                        agent._record_turn(input_text, content)
                        _turn_recorded = True
                    yield agent._final_progress(total_tokens=total_tokens,
                        step=step, thought=content,
                        is_final=True, final_answer=content,
                    )
                    return

                # 空内容：提示模型使用工具
                if finalization_mode:
                    final_answer = (
                        "Token 预算即将耗尽，且未能生成最终交付说明。"
                        "已停止发起新的工具调用。"
                    )
                    agent.last_context_report.update({
                        "token_budget_used": budget.request_tokens,
                        "token_budget_stop_reason": (
                            forced_finalization_reason or "reserve_finalization_empty_response"
                        ),
                        "finalization_textual_tool_markup_blocked": textual_tool_markup,
                    })
                    agent._record_turn(input_text, final_answer)
                    yield agent._final_progress(total_tokens=total_tokens,
                        step=step, thought=final_answer,
                        is_final=True, final_answer=final_answer,
                    )
                    return
                if step == 1:
                    messages.append({
                        "role": "user",
                        "content": "Please call appropriate tools to answer the question. "
                                   "If you need more information, you may call tools multiple times.",
                    })
                    yield ReActProgress(
                        step=step, thought="(empty)", actions=[],
                        is_final=False,
                    )
                else:
                    yield ReActProgress(
                        step=step, thought=content, actions=[],
                        is_final=False,
                    )
                continue

            # 3. 有 tool_calls → 全部执行
            no_tool_call_streak = 0
            round_result = await tool_round_executor.execute(tool_calls, step=step)
            tool_results = round_result.tool_results
            actions = round_result.actions
            details = round_result.details
            tool_call_count = budget.tool_call_count
            total_tokens = budget.total_tokens

            # 4. 追加 assistant + tool 消息到对话
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

            failed_tools = tuple(sorted(
                (
                    str(detail.get("name") or "tool"),
                    str(detail.get("error_code") or "TOOL_ERROR"),
                )
                for detail in details
                if detail.get("status") != "success"
            ))
            if failed_tools and failed_tools != last_failure_directive_signature:
                messages.append({
                    "role": "system",
                    "content": (
                        "## Recovery checkpoint\n"
                        "The previous tool round reported a failure "
                        f"({', '.join(name + ':' + code for name, code in failed_tools)}). "
                        "Treat the failure output as primary evidence. Do not repeat the same "
                        "call or resume broad discovery. For a repair task, inspect only the "
                        "reported target, apply the smallest corrective change, and run the "
                        "focused existing verification immediately. For an analysis-only task, "
                        "use a different read or report the limitation. After recovery, finish "
                        "with the verified result and remaining uncertainty."
                    ),
                })
                last_failure_directive_signature = failed_tools

            get_hooks().emit(
                HookEvent.TOOL_BATCH_AFTER,
                HookContext(
                    event=HookEvent.TOOL_BATCH_AFTER,
                    agent_name=agent.name,
                    run_id=runtime.run_id,
                    runtime=runtime,
                    phase="tool_batch_complete",
                    payload={
                        "details": details,
                        "actions": actions,
                        "step": step,
                    },
                ),
            )
            convergence_decision = runtime.control_decision
            if (
                isinstance(convergence_decision, HookDecision)
                and convergence_decision.action in {
                    HookAction.RECOVER, HookAction.FINALIZE, "recover", "finalize",
                }
            ):
                agent.last_context_report.setdefault("convergence_events", []).append({
                    "action": convergence_decision.action,
                    "reason": convergence_decision.reason,
                    "stalled_rounds": convergence.stalled_rounds,
                    "recovery_rounds": convergence.recovery_rounds,
                    "tool_call_count": tool_call_count,
                })
                messages.append({
                    "role": "system",
                    "content": (
                        "## Convergence controller\n"
                        f"{convergence_decision.message}"
                    ),
                })
                if convergence_decision.action in {HookAction.FINALIZE, "finalize"}:
                    forced_finalization_reason = convergence_decision.reason
                    agent.last_context_report["convergence_finalization"] = {
                        "reason": convergence_decision.reason,
                        "stalled_rounds": convergence.stalled_rounds,
                        "recovery_rounds": convergence.recovery_rounds,
                    }

            # This response was already paid for and its tools were
            # selected before the hard limit was observed.  Execute them
            # under their normal safety policies, then prevent any new
            # model call instead of silently discarding the evidence.
            # A single provider request above the emergency ceiling remains a
            # hard safety stop; usage from earlier requests is not involved.
            if budget.emergency_limit_reached:
                final_answer = (
                    f"已达到 token 预算（{budget.max_total_tokens}）。"
                    "已执行本轮已返回的工具调用，但不会再发起新的模型调用。"
                )
                agent.last_context_report.update({
                    "token_budget_used": budget.request_tokens,
                    "token_usage_total_observed": total_tokens,
                    "token_budget_emergency_remaining": budget.remaining_emergency_tokens,
                    "token_budget_stop_reason": "emergency_token_limit_after_current_tools",
                    "token_budget_target": budget.max_total_tokens,
                    "token_budget_emergency_limit": budget.emergency_max_total_tokens,
                })
                agent._record_turn(input_text, final_answer)
                _turn_recorded = True
                yield agent._final_progress(total_tokens=total_tokens,
                    step=step, actions=actions, tool_calls_detail=details,
                    thought=final_answer, is_final=True, final_answer=final_answer,
                )
                return

            yield ReActProgress(
                step=step,
                actions=actions,
                tool_calls_detail=details,
                thought=content,
                is_final=False,
            )

    finally:
        try:
            get_hooks().trigger(
                HookEvent.RUN_END,
                HookContext(event=HookEvent.RUN_END, agent_name=agent.name),
            )
        except AgentInterrupted:
            pass  # 结束阶段不再响应中断
        except Exception:
            logger.warning("[Hooks] RUN_END trigger failed", exc_info=True)
