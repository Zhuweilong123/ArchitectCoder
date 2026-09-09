"""ReActAgent — Reasoning + Acting 范式

支持两种运行模式:

1. **原生 Function Calling** (推荐, 默认) — ``await agent.arun(input)``
   利用 LLM 内置的工具调用能力，结构化 JSON 参数，支持多工具并行。

Usage::

    agent = ReActAgent(name="推理助手", llm=llm, tool_registry=registry)

    # 一次性获取结果
    result = await agent.arun("最近有什么关于AI的热点新闻？")

    # 流式获取每轮进度（用于前端实时展示）
    async for progress in agent.arun_stream("帮我优化这段代码"):
        print(f"Round {progress['step']}: {progress['actions']}")

"""

import logging
from typing import Optional, List, AsyncIterator

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from backend.config import AgentConfig
from ..core.hooks import get_runtime, todo_plan_complete
from ..tools.registry import ToolRegistry
from ..outcome import RunOutcome
from ..core.policy import ExecutionBudget
from app.services.context_manager import ContextBudgetManager
from .react_runtime.react_types import ReActProgress
from .react_runtime.fc_loop import run_fc_loop

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

# ── Function Calling system prompt ───────────────────────
class ReActAgent(Agent):
    """ReAct (Reasoning + Acting) Agent

    核心循环:
    1. 构建 messages → 2. LLM Function Calling 推理 →
    3. 解析 tool_calls → 4. 执行工具 →
    5. 观察结果 → 回到 1 或返回最终答案

    Attributes:
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        tool_registry: ToolRegistry,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        max_tool_calls: int = 100,
        max_run_seconds: float = 600.0,
        max_total_tokens: int = 200000,
        token_finalization_reserve_tokens: int = 12000,
        convergence_budget_ratio: float = 0.8,
        convergence_max_stalled_rounds: int = 3,
        convergence_max_recovery_rounds: int = 2,
        convergence_repeat_action_threshold: int = 3,
        evidence_max_records: int = 128,
        final_summary_max_tokens: int = 3000,
        llm_timeout_seconds: float = 120.0,
        context_budget: ContextBudgetManager | None = None,
        execution_budget: ExecutionBudget | None = None,
    ):
        super().__init__(name, llm, system_prompt, config)
        self.tool_registry = tool_registry
        self.current_history: List[str] = []
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
        self.convergence_budget_ratio = min(1.0, max(0.0, float(convergence_budget_ratio)))
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
            name, "启用",
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

        使用原生 Function Calling 执行开放式 ReAct 循环。

        ``context`` 为可选易变上下文尾块，追加在最后一条 user 消息末尾
        （history 之后），不影响 system+history 前缀的 KV 缓存命中。
        """
        return await self._arun_with_fc(input_text, context=context, **kwargs)

    async def arun_stream(self, input_text: str, context: str = "", **kwargs) -> AsyncIterator[ReActProgress]:
        """流式运行 ReAct 循环 — 每轮 yield :class:`ReActProgress`。

        用于前端实时展示、编排层监控等需要逐轮获取进度的场景。

        """
        async for progress in self._arun_with_fc_stream(input_text, context=context, **kwargs):
            yield progress

    def run(self, input_text: str, **kwargs) -> str:
        """同步入口不支持；请使用异步 Function Calling 入口。"""
        raise RuntimeError("ReActAgent requires async arun() or arun_stream(); textual ReAct is no longer supported")
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
        async for progress in run_fc_loop(self, input_text, context=context, **kwargs):
            yield progress
