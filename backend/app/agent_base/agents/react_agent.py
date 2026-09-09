"""ReActAgent — Reasoning + Acting 范式

支持两种运行模式:

1. **原生 Function Calling** (推荐, 默认) — ``await agent.arun(input)``
   利用 LLM 内置的工具调用能力，结构化 JSON 参数，支持多工具并行。

2. **文本解析降级** (兼容) — ``agent.run(input)``
   正则匹配 ``Thought:/Action:`` 文本格式，兼容不支持 FC 的模型。

Usage::

    # 推荐：异步 FC 模式
    agent = ReActAgent(name="推理助手", llm=llm, tool_registry=registry, max_steps=5)

    # 一次性获取结果
    result = await agent.arun("最近有什么关于AI的热点新闻？")

    # 流式获取每轮进度（用于前端实时展示）
    async for progress in agent.arun_stream("帮我优化这段代码"):
        print(f"Round {progress['step']}: {progress['actions']}")

    # 降级：同步文本解析模式
    result = agent.run("你好！")
"""

import asyncio
import json
import re
import logging
import time
from typing import Optional, List, AsyncIterator

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from backend.config import AgentConfig
from ..core.hooks import (
    get_hooks, HookEvent, HookContext, get_runtime,
    HookAction, HookDecision, todo_plan_complete,
)
from ..core.exceptions import AgentInterrupted
from ..tools.registry import ToolRegistry
from ..tools.result import ToolResult
from ..evidence import EvidenceLedger
from ..outcome import RunOutcome
from ..convergence import ConvergenceController
from ..core.policy import ExecutionBudget
from app.services.context_manager import ContextBudgetManager

logger = logging.getLogger(__name__)

# ── Function Calling 模式 system prompt ─────────────────
FC_SYSTEM_PROMPT = """You are an AI assistant with reasoning and action capabilities.
You can call tools to fetch information, perform operations, analyze problems step by step, and finally give an accurate answer.

When using tools:
- You may call multiple independent tools at once (they run in parallel)
- Observe the results, then decide whether to continue calling tools
- When you have enough information, give the final answer directly (no tool calls)

Never fabricate answers. If the tool results are insufficient, continue using other tools or retry with adjusted parameters.
"""

# ── ReAct 文本模式提示词模板 (降级兼容) ──────────────────
REACT_PROMPT = """You are an AI assistant with reasoning and action capabilities. Analyze the problem by thinking, call the appropriate tools to gather information, and finally produce an accurate answer.

## Available tools
{tools}

## Workflow
Respond strictly in the following format, one step at a time:

Thought: Analyze the current problem; think about what information or action is needed.
Action: Choose one action in one of these formats:
- `{{tool_name}}[{{tool_input}}]` - call the specified tool
- `Finish[final answer]` - when you have enough information to answer

## Important notes
1. Every response must include both Thought and Action.
2. Tool call format must strictly follow: tool_name[parameters]
3. Only use Finish when you are confident you have enough information.
4. If tool results are insufficient, continue with other tools or different parameters of the same tool.

## Current task
**Question:** {question}

## Execution history
{history}

Now begin your reasoning and actions:
"""


def _remove_textual_tool_markup(content: str) -> tuple[str, bool]:
    """Remove provider-specific pseudo tool calls from a tool-free response."""
    text = str(content or "")
    patterns = (
        r"<[^>]*tool_calls[^>]*>.*?</[^>]*tool_calls[^>]*>",
        r"<[^>]*invoke\b[^>]*>.*?</[^>]*invoke[^>]*>",
    )
    removed = False
    for pattern in patterns:
        text, count = re.subn(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
        removed = removed or bool(count)
    return text.strip(), removed

# ── 流式 progress 中的 step 数据类 ──────────────────────

class ReActProgress:
    """单轮 ReAct 进度快照，通过 ``arun_stream()`` yield 给上层。

    Attributes:
        step: 当前轮次（1-based）
        actions: 本轮调用的工具名列表
        tool_calls_detail: ``[{name, arguments, observation}]`` 详情
        thought: LLM 文本内容（工具调用以外的思考部分）
        is_final: 是否为本轮后终止
        final_answer: 若 is_final 为 True，则为最终答案
    """

    __slots__ = (
        "step", "actions", "tool_calls_detail", "thought",
        "is_final", "final_answer",
        "outcome",
    )

    def __init__(
        self,
        step: int,
        actions: list[str] | None = None,
        tool_calls_detail: list[dict] | None = None,
        thought: str = "",
        is_final: bool = False,
        final_answer: str = "",
        outcome: RunOutcome | None = None,
    ):
        self.step = step
        self.actions = actions or []
        self.tool_calls_detail = tool_calls_detail or []
        self.thought = thought
        self.is_final = is_final
        self.final_answer = final_answer
        self.outcome = outcome

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "actions": self.actions,
            "tool_calls_detail": self.tool_calls_detail,
            "thought": self.thought[:500],
            "is_final": self.is_final,
            "final_answer": self.final_answer,
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }


class ReActAgent(Agent):
    """ReAct (Reasoning + Acting) Agent

    核心循环:
    1. 构建 messages → 2. LLM 推理 (FC 或文本) →
    3. 解析 tool_calls / Thought-Action → 4. 执行工具 →
    5. 观察结果 → 回到 1 或返回最终答案

    Attributes:
        max_steps: 兼容旧调用的参数；不再作为执行终止条件
        use_native_fc: 是否使用原生 Function Calling（默认 True）
        custom_prompt: 自定义提示词模板（仅文本模式使用）
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        tool_registry: ToolRegistry,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        max_steps: int = 5,
        use_native_fc: bool = True,
        custom_prompt: Optional[str] = None,
        max_tool_calls: int = 100,
        max_run_seconds: float = 600.0,
        max_total_tokens: int = 200000,
        token_finalization_reserve_tokens: int = 12000,
        convergence_tool_steps: int = 25,
        convergence_budget_ratio: float = 0.8,
        convergence_keep_recent_steps: int = 3,
        convergence_max_stalled_rounds: int = 3,
        convergence_max_recovery_rounds: int = 2,
        convergence_repeat_action_threshold: int = 3,
        evidence_max_records: int = 128,
        # Deprecated compatibility argument; open-ended loops do not use it.
        force_final_summary_on_step_limit: bool = True,
        final_summary_max_tokens: int = 3000,
        llm_timeout_seconds: float = 120.0,
        context_budget: ContextBudgetManager | None = None,
        execution_budget: ExecutionBudget | None = None,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.tool_registry = tool_registry
        # Kept as a backwards-compatible constructor argument only. Both
        # execution paths use an open-ended loop and rely on resource budgets
        # plus the convergence controller for termination.
        self.max_steps = max_steps
        self.use_native_fc = use_native_fc
        self.current_history: List[str] = []
        self.prompt_template = custom_prompt or REACT_PROMPT
        self.execution_budget = execution_budget
        self.max_tool_calls = max(1, max_tool_calls)
        self.max_run_seconds = max(1.0, max_run_seconds)
        self.max_total_tokens = max(1, max_total_tokens)
        self.token_finalization_reserve_tokens = min(
            max(1, token_finalization_reserve_tokens),
            max(1, self.max_total_tokens - 1),
        )
        if self.execution_budget is not None:
            self.max_tool_calls = self.execution_budget.max_tool_calls
            self.max_run_seconds = self.execution_budget.max_run_seconds
            self.max_total_tokens = self.execution_budget.max_total_tokens
            self.token_finalization_reserve_tokens = (
                self.execution_budget.token_finalization_reserve_tokens
            )
        self.convergence_tool_steps = max(1, convergence_tool_steps)
        self.convergence_budget_ratio = min(1.0, max(0.0, float(convergence_budget_ratio)))
        self.convergence_keep_recent_steps = max(1, convergence_keep_recent_steps)
        self.convergence_max_stalled_rounds = max(1, convergence_max_stalled_rounds)
        self.convergence_max_recovery_rounds = max(1, convergence_max_recovery_rounds)
        self.convergence_repeat_action_threshold = max(2, convergence_repeat_action_threshold)
        self.evidence_max_records = max(self.max_tool_calls, evidence_max_records)
        self.final_summary_max_tokens = max(1, final_summary_max_tokens)
        self.llm_timeout_seconds = max(1.0, llm_timeout_seconds)
        self.context_budget = context_budget or ContextBudgetManager()
        self._history_summary = ""
        self.last_context_report: dict = {}
        self.last_evidence_summary: list[dict] = []
        # Structured hand-off for a follow-up status query. The transport
        # updates this at run boundaries so status requests do not re-explore
        # the workspace.
        self.last_run_checkpoint: dict = {}
        self.on_context_compacted = None
        logger.info(
            "✅ %s 初始化完成，开放循环模式，FC模式: %s",
            name, "启用" if use_native_fc else "禁用（文本解析）",
        )

    def restore_history(self, messages: list[dict]) -> None:
        """从 [{role, content}] 恢复对话历史（结论级：仅 user/assistant）。

        用于「恢复历史会话」——agent 据此在继续对话时记住之前的结论。
        """
        self._history_summary = "\n\n".join(
            str(m.get("content") or "")
            for m in messages
            if m.get("role") == "summary"
            and (m.get("metadata") or {}).get("kind") != "task_execution"
        )
        self._history = [
            Message(m.get("content", ""), m.get("role", "user"))
            for m in messages
            if m.get("role") in ("user", "assistant")
            or (
                m.get("role") == "summary"
                and (m.get("metadata") or {}).get("kind") == "task_execution"
            )
        ]

    def append_task_summary(self, summary: str) -> None:
        """Keep a bounded internal checkpoint for the next turn.

        Tool calls/results are intentionally not added to chat history.  A
        compact task summary is kept beside the completed user/assistant pair
        and converted to a system message only when building model input.
        """
        value = str(summary or "").strip()
        if not value:
            return
        # Keep the checkpoint adjacent to the user/assistant pair that
        # produced it.  It is converted to a model-valid system message by
        # ContextBudgetManager and remains hidden from the user-facing chat.
        self._history.append(Message(value, "summary"))

    def _record_turn(self, input_text: str, answer: str) -> None:
        """Append the user request and final answer as one conversational turn."""
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(answer, "assistant"))

    # ═══════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════

    async def arun(self, input_text: str, context: str = "", **kwargs) -> str:
        """异步运行 ReAct 循环（推荐入口）。

        当 ``use_native_fc=True`` 且有工具注册时使用原生 Function Calling；
        否则降级到同步文本解析 ``run()``。

        ``context`` 为可选易变上下文尾块，追加在最后一条 user 消息末尾
        （history 之后），不影响 system+history 前缀的 KV 缓存命中。
        """
        if self.use_native_fc and self.tool_registry:
            return await self._arun_with_fc(input_text, context=context, **kwargs)
        logger.info("⚡ %s 降级到文本解析模式", self.name)
        return self.run(input_text, **kwargs)

    async def arun_stream(self, input_text: str, context: str = "", **kwargs) -> AsyncIterator[ReActProgress]:
        """流式运行 ReAct 循环 — 每轮 yield :class:`ReActProgress`。

        用于前端实时展示、编排层监控等需要逐轮获取进度的场景。

        当 ``use_native_fc=False`` 时，自动降级为仅 yield 最终结果。
        """
        if self.use_native_fc and self.tool_registry:
            async for progress in self._arun_with_fc_stream(input_text, context=context, **kwargs):
                yield progress
        else:
            # 降级：同步 run() 只产出一个最终结果
            result = self.run(input_text, **kwargs)
            yield ReActProgress(step=1, is_final=True, final_answer=result)

    def run(self, input_text: str, **kwargs) -> str:
        """同步运行 ReAct 循环（文本解析模式，向后兼容）。

        使用 Thought:/Action: 正则解析。保留给不支持 FC 的模型。
        """
        self.current_history = []
        current_step = 0
        tool_call_count = 0
        started_at = time.monotonic()
        convergence = ConvergenceController()

        logger.info("\n🤖 %s 开始处理问题: %s", self.name, input_text)

        while True:
            current_step += 1
            if time.monotonic() - started_at >= self.max_run_seconds:
                final_answer = "Execution time budget reached before the task converged."
                self._record_turn(input_text, final_answer)
                return final_answer
            logger.info("\n--- 第 %d 步 ---", current_step)

            # 1. 构建提示词
            tools_desc = self.tool_registry.get_tools_description()
            history_str = "\n".join(self.current_history)
            prompt = self.prompt_template.format(
                tools=tools_desc,
                question=input_text,
                history=history_str,
            )

            # 2. 调用 LLM
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm.invoke(messages, **kwargs)

            # 3. 解析输出
            thought, action = self._parse_output(response_text)
            logger.info("  Thought: %s", thought[:100] if thought else "无")
            if action:
                logger.info("  Action: %s", action)

            # 4. 检查是否完成
            if action and action.startswith("Finish"):
                final_answer = self._parse_action_input(action)
                self._record_turn(input_text, final_answer)
                logger.info("🏁 %s 完成", self.name)
                return final_answer

            # 5. 执行工具调用
            if action:
                tool_name, tool_input = self._parse_action(action)
                if tool_name:
                    tool_call_count += 1
                    if tool_call_count > self.max_tool_calls:
                        final_answer = "Tool-call budget reached before the task converged."
                        self._record_turn(input_text, final_answer)
                        return final_answer
                    observation = self.tool_registry.execute_tool(tool_name, tool_input)
                    self.current_history.append(f"Step {current_step}: Action: {action}")
                    self.current_history.append(f"Step {current_step}: Observation: {observation}")
                    logger.info("  Observation: %s", observation[:100])
                    decision = convergence.observe([{
                        "name": tool_name,
                        "arguments": tool_input,
                        "status": "success",
                        "observation": observation,
                    }])
                    if decision.action == "finalize":
                        final_answer = (
                            "The legacy text loop stopped after repeated non-progressing actions.\n\n"
                            f"Last observation: {observation}"
                        )
                        self._record_turn(input_text, final_answer)
                        return final_answer
                else:
                    self.current_history.append(f"Step {current_step}: 无效的Action格式")
            else:
                self.current_history.append(f"Step {current_step}: 未解析到Action")


    # ═══════════════════════════════════════════════════════
    # Function Calling 核心
    # ═══════════════════════════════════════════════════════

    def _build_fc_system_prompt(self) -> str:
        """构建 FC 模式的 system prompt。"""
        base = self.system_prompt or FC_SYSTEM_PROMPT
        return base

    async def _arun_with_fc(self, input_text: str, context: str = "", **kwargs) -> str:
        """一次性 FC 循环 — 收集流式输出，返回最终答案。"""
        final_answer = ""
        async for progress in self._arun_with_fc_stream(input_text, context=context, **kwargs):
            if progress.is_final:
                final_answer = progress.final_answer
        if not final_answer:
            final_answer = "抱歉，执行循环未产生最终答案。"
        return final_answer

    def _final_progress(self, *, total_tokens: int, **kwargs) -> ReActProgress:
        self.last_context_report["token_budget_used"] = total_tokens
        outcome = RunOutcome.from_stop(
            self.last_context_report.get("token_budget_stop_reason", "model_answer"),
            kwargs.get("final_answer", ""),
            total_tokens=total_tokens,
            plan_complete=todo_plan_complete(get_runtime()) and all(
                todo.get("status") == "completed" for todo in get_runtime().todos
            ),
            verification_failed=any(
                not passed for passed in getattr(self, "_run_verifications", {}).values()
            ),
        )
        return ReActProgress(**kwargs, outcome=outcome)

    async def _arun_with_fc_stream(
        self, input_text: str, context: str = "", **kwargs
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
            self.tool_registry.get_openai_specs_for(allowed_tools)
            if allowed_tools is not None else self.tool_registry.get_openai_specs()
        )
        compact_tool_specs = (
            self.tool_registry.get_openai_specs_for(allowed_tools, compact=True)
            if allowed_tools is not None else self.tool_registry.get_openai_specs(compact=True)
        )
        compacted = self.context_budget.prepare_history(
            self._history, self._history_summary,
        )
        self._history_summary = compacted.summary
        built = self.context_budget.build_messages(
            self._build_fc_system_prompt(),
            compacted.messages,
            input_text,
            context=context,
            history_summary=self._history_summary,
            tools=full_tool_specs,
        )
        messages = built.messages
        current_user_index = built.current_user_index
        self.last_context_report = built.to_dict()
        self.last_context_report["token_budget_stop_reason"] = "model_answer"
        self._run_verifications = {}
        self.last_context_report.update({
            "compacted_messages": compacted.dropped_messages,
            "compacted_tokens": compacted.dropped_tokens,
            "convergence_policy": {
                "tool_steps": self.convergence_tool_steps,
                "budget_ratio": self.convergence_budget_ratio,
                "keep_recent_steps": self.convergence_keep_recent_steps,
                "evidence_max_records": self.evidence_max_records,
                "open_ended_loop": True,
                "max_stalled_rounds": self.convergence_max_stalled_rounds,
                "max_recovery_rounds": self.convergence_max_recovery_rounds,
                "repeat_action_threshold": self.convergence_repeat_action_threshold,
                "legacy_step_limit_parameter_ignored": True,
                "final_summary_max_tokens": self.final_summary_max_tokens,
            },
        })
        if compacted.dropped_messages and self.on_context_compacted:
            self.on_context_compacted({
                "summary": compacted.summary,
                "dropped_messages": compacted.dropped_messages,
                "dropped_tokens": compacted.dropped_tokens,
            })

        self.current_history = []
        no_tool_call_streak = 0
        _turn_recorded = False
        # Planning and read-only exploration happen immediately before this
        # loop. Count their measured usage against the same task budget so a
        # worker cannot silently extend the run beyond max_total_tokens.
        budget = self.execution_budget or ExecutionBudget(
            max_tool_calls=self.max_tool_calls,
            max_run_seconds=self.max_run_seconds,
            max_total_tokens=self.max_total_tokens,
            token_finalization_reserve_tokens=self.token_finalization_reserve_tokens,
        )
        budget.start(initial_token_usage)
        tool_call_count = budget.tool_call_count
        total_tokens = budget.total_tokens
        soft_budget_notified = False
        convergence_compaction_active = False
        convergence_directive_added = False
        last_failure_directive_signature: tuple[tuple[str, str], ...] = ()
        evidence_ledger = EvidenceLedger(max_records=self.evidence_max_records)
        convergence = ConvergenceController(
            max_stalled_rounds=self.convergence_max_stalled_rounds,
            max_recovery_rounds=self.convergence_max_recovery_rounds,
            repeat_action_threshold=self.convergence_repeat_action_threshold,
        )
        forced_finalization_reason = ""
        self.last_evidence_summary = []

        runtime = get_runtime()
        runtime.execution_budget = budget
        runtime.convergence_controller = convergence
        runtime.control_decision = None
        get_hooks().trigger(
            HookEvent.RUN_START,
            HookContext(
                event=HookEvent.RUN_START,
                agent_name=self.name,
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
                finalization_mode = bool(forced_finalization_reason) or (
                    budget.finalization_required
                )
                active_tool_specs = [] if finalization_mode else (
                    compact_tool_specs if tool_call_count else full_tool_specs
                )
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
                    self.last_context_report.update({
                        "token_budget_used": total_tokens,
                        "token_budget_remaining": remaining_tokens,
                        "token_budget_finalization_mode": True,
                    })
                convergence_reasons = []
                if tool_call_count >= self.convergence_tool_steps:
                    convergence_reasons.append("tool_call_count")
                if total_tokens >= budget.max_total_tokens * self.convergence_budget_ratio:
                    convergence_reasons.append("token_budget_ratio")
                if convergence_reasons:
                    convergence_compaction_active = True
                retained_react_steps = (
                    self.convergence_keep_recent_steps
                    if convergence_compaction_active
                    else self.context_budget.budget.max_react_steps
                )
                prior_call_ids = [
                    str(message.get("tool_call_id") or "")
                    for message in messages
                    if message.get("role") == "tool" and message.get("tool_call_id")
                ]
                messages, current_user_index, compacted_steps, compacted_tokens = self.context_budget.compact_react_steps(
                    messages,
                    current_user_index=current_user_index,
                    max_steps=min(
                        retained_react_steps,
                        self.context_budget.budget.max_history_turns,
                    ),
                    evidence_by_call=evidence_ledger.summary_for(prior_call_ids),
                )
                if compacted_steps:
                    self.last_context_report["react_compacted_steps"] = (
                        self.last_context_report.get("react_compacted_steps", 0) + compacted_steps
                    )
                    self.last_context_report["react_compacted_tokens"] = (
                        self.last_context_report.get("react_compacted_tokens", 0) + compacted_tokens
                    )
                    if convergence_compaction_active:
                        self.last_context_report["convergence_evidence_compaction"] = {
                            "triggered_by": convergence_reasons,
                            "triggered_at_tool_calls": tool_call_count,
                            "token_budget_used": total_tokens,
                            "keep_recent_steps": retained_react_steps,
                        }
                    if self.on_context_compacted:
                        checkpoint = next(
                            (
                                str(message.get("content") or "")
                                for message in messages
                                if message.get("role") == "system"
                                and str(message.get("content") or "").startswith("## Tool execution checkpoint")
                            ),
                            "Tool execution steps compacted.",
                        )
                        self.on_context_compacted({
                            "summary": checkpoint,
                            "dropped_messages": compacted_steps,
                            "dropped_tokens": compacted_tokens,
                            "reason": (
                                "convergence" if convergence_compaction_active
                                else "history_limit"
                            ),
                            "triggered_by": convergence_reasons,
                            "tool_call_count": tool_call_count,
                            "token_budget_used": total_tokens,
                            "keep_recent_steps": retained_react_steps,
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
                    self.last_context_report["convergence_directive_added"] = {
                        "triggered_by": convergence_reasons,
                        "triggered_at_tool_calls": tool_call_count,
                        "token_budget_used": total_tokens,
                    }
                messages, dropped = self.context_budget.fit_messages(
                    messages,
                    tools=active_tool_specs,
                    current_user_index=current_user_index,
                )
                if dropped:
                    self.last_context_report["loop_dropped_messages"] = (
                        self.last_context_report.get("loop_dropped_messages", 0) + dropped
                    )
                logger.info("\n--- FC loop round %d ---", step)
                # 1. 调用 LLM（带工具 schemas）
                llm_before_decision = get_hooks().trigger(
                    HookEvent.LLM_BEFORE,
                    HookContext(
                        event=HookEvent.LLM_BEFORE,
                        agent_name=self.name,
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
                    self.last_context_report.update({
                        "token_budget_used": budget.total_tokens,
                        "token_budget_stop_reason": reason,
                    })
                    final_answer = (
                        "Execution budget reached before the next model call; "
                        "the task was finalized with the available evidence."
                    )
                    self._record_turn(input_text, final_answer)
                    yield self._final_progress(
                        total_tokens=budget.total_tokens,
                        step=step,
                        thought=final_answer,
                        is_final=True,
                        final_answer=final_answer,
                    )
                    return
                with trace_span(f"{self.name}"):
                    try:
                        response = await asyncio.wait_for(
                            self.llm.ainvoke_with_tools(
                                messages=messages,
                                tools=active_tool_specs,
                                tool_choice="none" if finalization_mode else "auto",
                                temperature=kwargs.get("temperature", 0.3),
                            ),
                            timeout=kwargs.get("llm_timeout_seconds", self.llm_timeout_seconds),
                        )
                    except asyncio.TimeoutError:
                        self.last_context_report.update({
                            "token_budget_used": total_tokens,
                            "token_budget_stop_reason": "llm_timeout",
                        })
                        final_answer = "LLM 调用超过时间预算，已停止本轮任务。"
                        self._record_turn(input_text, final_answer)
                        yield self._final_progress(total_tokens=total_tokens,step=step, thought=final_answer,
                                            is_final=True, final_answer=final_answer)
                        return
                get_hooks().trigger(
                    HookEvent.LLM_AFTER,
                    HookContext(
                        event=HookEvent.LLM_AFTER,
                        agent_name=self.name,
                        run_id=runtime.run_id,
                        runtime=runtime,
                        messages=messages,
                        llm_response=response,
                    ),
                )

                tool_calls = response.get("tool_calls")
                content = response.get("content") or ""
                usage = response.get("usage") or {}
                total_tokens = budget.total_tokens
                # A finalization request is deliberately tool-free.  Protect
                # against non-conforming test doubles/providers returning a
                # tool call despite tool_choice='none'.
                textual_tool_markup = False
                if finalization_mode:
                    tool_calls = None
                    content, textual_tool_markup = _remove_textual_tool_markup(content)
                if not soft_budget_notified and total_tokens >= budget.max_total_tokens * self.convergence_budget_ratio:
                    soft_budget_notified = True
                    self.last_context_report["soft_budget_reached_tokens"] = total_tokens
                    messages.append({
                        "role": "system",
                        "content": (
                            "## Token budget warning\n"
                            f"The run has used at least {self.convergence_budget_ratio:.0%} of its token budget. "
                            "Stop broad discovery, "
                            "do not repeat failed exploration, and use only the minimum remaining "
                            "actions needed to edit, verify, or accurately report partial completion."
                        ),
                    })

                # 2. 无 tool_calls → 纯文本回复
                if not tool_calls:
                    no_tool_call_streak += 1
                    self.current_history.append(
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
                                agent_name=self.name,
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
                            self.last_context_report.setdefault("convergence_events", []).append({
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
                                self.last_context_report["convergence_finalization"] = {
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
                            self.last_context_report.update({
                                "token_budget_used": total_tokens,
                                "token_budget_stop_reason": (
                                    forced_finalization_reason or "reserve_finalization"
                                ),
                                "finalization_textual_tool_markup_blocked": textual_tool_markup,
                            })
                            if not _turn_recorded:
                                self._record_turn(input_text, content)
                                _turn_recorded = True
                            yield self._final_progress(total_tokens=total_tokens,
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
                        logger.info("🏁 %s FC 完成（模型直接回复）", self.name)
                        # 必须在 yield 之前写入历史：流式消费方（_handle_dev）在收到
                        # is_final=True 后立即 return 并关闭生成器，yield 之后的代码
                        # 不会再执行，若把 add_message 放在生成器末尾会永远丢历史。
                        if not _turn_recorded:
                            self._record_turn(input_text, content)
                            _turn_recorded = True
                        yield self._final_progress(total_tokens=total_tokens,
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
                        self.last_context_report.update({
                            "token_budget_used": total_tokens,
                            "token_budget_stop_reason": (
                                forced_finalization_reason or "reserve_finalization_empty_response"
                            ),
                            "finalization_textual_tool_markup_blocked": textual_tool_markup,
                        })
                        self._record_turn(input_text, final_answer)
                        yield self._final_progress(total_tokens=total_tokens,
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
                tool_results: list[dict] = []
                actions: list[str] = []
                details: list[dict] = []

                parsed_calls: list[tuple[dict, str, dict, str | None]] = []
                for tc in tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        tool_args = json.loads(fn["arguments"])
                    except json.JSONDecodeError:
                        err_obs = (
                            f"Invalid JSON arguments for '{tool_name}'. "
                            f"Raw: {fn.get('arguments', '')[:200]}. Please re-send with valid JSON."
                        )
                        parsed_calls.append((tc, tool_name, fn.get("arguments", ""), err_obs))
                        continue

                    blocked: str | None = None
                    if allowed_set is not None and tool_name not in allowed_set:
                        blocked = (f"Tool '{tool_name}' is not enabled for this turn. "
                                   f"Use one of: {', '.join(sorted(allowed_set)) or '(none)'}")
                    elif budget.tool_call_count >= budget.max_tool_calls:
                        self.last_context_report["token_budget_stop_reason"] = "tool_call_limit"
                        blocked = (f"Tool-call budget exceeded ({budget.max_tool_calls}). "
                                   "Stop calling tools and summarize the result.")
                    parsed_calls.append((tc, tool_name, tool_args, blocked))

                async def _execute_one(tool_name: str, tool_args: dict, blocked: str | None):
                    if blocked is not None:
                        return blocked, blocked, ToolResult(status="blocked", data=blocked,
                                                            error_code="POLICY_BLOCKED"), 0.0
                    # Check the planning gate immediately before execution.
                    # A preceding todo_write in the same response can therefore
                    # establish the plan before business tools run.
                    if (get_runtime().requires_todo_plan
                            and not get_runtime().todos
                            and tool_name != "todo_write"):
                        blocked = ("Task planning is required before other tools. "
                                   "Call todo_write first with the task checklist.")
                        return blocked, blocked, ToolResult(
                            status="blocked", data=blocked, error_code="POLICY_BLOCKED",
                        ), 0.0
                    veto = get_hooks().trigger(
                        HookEvent.TOOL_BEFORE,
                        HookContext(
                            event=HookEvent.TOOL_BEFORE,
                            agent_name=self.name,
                            run_id=runtime.run_id,
                            runtime=runtime,
                            tool_name=tool_name,
                            tool_input=tool_args,
                        ),
                    )
                    if veto is not None:
                        if isinstance(veto, HookDecision):
                            if veto.action not in {HookAction.VETO, HookAction.STOP, "veto", "stop"}:
                                veto = None
                            else:
                                veto = veto.message or veto.reason
                        if veto is not None:
                            veto_message = str(veto)
                            return veto_message, veto_message, ToolResult(
                                status="blocked", data=veto_message,
                                error_code="HOOK_VETO",
                            ), 0.0
                    with trace_span(f"{self.name}/{tool_name}"):
                        started_tool = time.monotonic()
                        result = await self.tool_registry.aexecute_tool_result_with_params(
                            tool_name, tool_args,
                        )
                    duration_ms = (time.monotonic() - started_tool) * 1000
                    try:
                        from app.services.agent_metrics import get_agent_metrics
                        get_agent_metrics().record_tool(
                            tool_name, result.status,
                            duration_ms,
                        )
                    except Exception:
                        pass
                    observation_full = result.text
                    fed = get_hooks().trigger(
                        HookEvent.TOOL_AFTER,
                        HookContext(
                            event=HookEvent.TOOL_AFTER,
                            agent_name=self.name,
                            run_id=runtime.run_id,
                            runtime=runtime,
                            tool_name=tool_name,
                            tool_input=tool_args,
                            tool_output=observation_full,
                            tool_status=result.status,
                            error_code=result.error_code,
                        ),
                    )
                    if isinstance(fed, HookDecision):
                        fed = (
                            fed.message
                            if fed.action in {HookAction.REPLACE, "replace"}
                            else None
                        )
                    return (
                        observation_full,
                        fed if fed is not None else observation_full,
                        result,
                        duration_ms,
                    )

                executable = [item for item in parsed_calls if item[3] is None]
                parallel = len(executable) > 1 and all(
                    self.tool_registry.can_parallel(item[1]) for item in executable
                )
                if parallel:
                    executions = await asyncio.gather(*(
                        _execute_one(item[1], item[2], item[3]) for item in parsed_calls
                    ))
                else:
                    executions = []
                    for item in parsed_calls:
                        executions.append(await _execute_one(item[1], item[2], item[3]))

                for item, execution in zip(parsed_calls, executions):
                    tc, tool_name, tool_args, blocked = item
                    if blocked is not None and isinstance(tool_args, str):
                        observation_full = observation_fed = blocked
                        result = ToolResult(status="blocked", data=blocked,
                                             error_code="INVALID_ARGUMENTS")
                        duration_ms = 0.0
                    else:
                        observation_full, observation_fed, result, duration_ms = execution
                    if isinstance(tool_args, str):
                        observation_full = observation_fed = execution[0]
                    if result.verification is not None:
                        check = result.verification
                        self._run_verifications[(check.kind, check.scope)] = check.passed
                    evidence = evidence_ledger.record(
                        call_id=str(tc.get("id") or ""), tool_name=tool_name,
                        arguments=tool_args if isinstance(tool_args, dict) else {},
                        observation=observation_full, status=result.status,
                        error_code=result.error_code, effects=result.effects(),
                    )
                    self.last_evidence_summary.append(evidence.to_dict())
                    # Keep the public diagnostic surface bounded just like
                    # the in-run ledger. Full evidence remains in ChatTrace.
                    self.last_evidence_summary = self.last_evidence_summary[-32:]
                    self.current_history.append(
                        f"Step {step}: {tool_name}({json.dumps(tool_args, ensure_ascii=False)})"
                        f" → {observation_fed[:150]}"
                    )
                    tool_results.append({"tool_call_id": tc["id"], "content": observation_fed})
                    actions.append(tool_name)
                    details.append({
                        "name": tool_name,
                        "arguments": tool_args,
                        "observation": observation_full,
                        "fed_truncated": observation_full != observation_fed,
                        "fed_length": len(observation_fed),
                        "status": result.status,
                        "error_code": result.error_code,
                        "retryable": result.retryable,
                        "duration_ms": round(duration_ms, 1),
                        "evidence": evidence.to_dict(),
                        **result.effects(),
                    })
                    logger.info("  🔧 %s(%s) → %s", tool_name,
                                json.dumps(tool_args, ensure_ascii=False)[:80],
                                observation_fed[:80])

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
                        agent_name=self.name,
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
                    self.last_context_report.setdefault("convergence_events", []).append({
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
                        self.last_context_report["convergence_finalization"] = {
                            "reason": convergence_decision.reason,
                            "stalled_rounds": convergence.stalled_rounds,
                            "recovery_rounds": convergence.recovery_rounds,
                        }

                # This response was already paid for and its tools were
                # selected before the hard limit was observed.  Execute them
                # under their normal safety policies, then prevent any new
                # model call instead of silently discarding the evidence.
                if total_tokens >= budget.max_total_tokens:
                    final_answer = (
                        f"已达到 token 预算（{budget.max_total_tokens}）。"
                        "已执行本轮已返回的工具调用，但不会再发起新的模型调用。"
                    )
                    self.last_context_report.update({
                        "token_budget_used": total_tokens,
                        "token_budget_stop_reason": "hard_limit_after_current_tools",
                    })
                    self._record_turn(input_text, final_answer)
                    _turn_recorded = True
                    yield self._final_progress(total_tokens=total_tokens,
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
                    HookContext(event=HookEvent.RUN_END, agent_name=self.name),
                )
            except AgentInterrupted:
                pass  # 结束阶段不再响应中断
            except Exception:
                logger.warning("[Hooks] RUN_END trigger failed", exc_info=True)

        # 除了已 yield 的 progress 外，不再额外 yield
        # — 调用方已经拿到了最终答案

    # ═══════════════════════════════════════════════════════
    # 文本解析 (降级兼容)
    # ═══════════════════════════════════════════════════════

    def _parse_output(self, text: str) -> tuple:
        """解析 LLM 输出，提取 (Thought, Action)"""
        thought = None
        action = None

        thought_match = re.search(r"Thought:\s*(.+?)(?=\n\s*(?:Action:|$))", text, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()

        action_match = re.search(r"Action:\s*(.+)", text)
        if action_match:
            action = action_match.group(1).strip()

        return thought, action

    def _parse_action(self, action_text: str) -> tuple:
        """解析 Action 文本，提取 (tool_name, tool_input)"""
        # Format: tool_name[tool_input]  or  Finish[final_answer]
        match = re.match(r"(\w+)\[(.*)\]", action_text)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _parse_action_input(self, action_text: str) -> str:
        """解析 Finish[answer] 中的最终答案"""
        match = re.match(r"Finish\[(.*)\]", action_text, re.DOTALL)
        if match:
            return match.group(1)
        return action_text
