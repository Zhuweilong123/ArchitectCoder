"""Agent 循环 Hook 机制 — 全局单例注册表 + contextvar 运行时上下文

把横切关注点（中断、截断、权限、日志等）从框架循环中解耦，通过注册 hook
注入，框架层不再 import 应用层。

Hook 语义（``trigger`` 按 priority 降序短路）:
- 返回 ``None``           → 放行，继续下一个
- ``TOOL_BEFORE`` 返回 ``str`` → veto，跳过工具，该 str 直接作为 tool_result 喂回模型
- ``TOOL_AFTER`` 返回 ``str``  → replace，覆盖喂给模型的口径
- 抛 ``AgentInterrupted``      → stop，异常传播到编排层
- 抛其他异常                   → ``fail_closed=True`` 视为 veto；否则吞掉 log 继续

Usage::

    from app.agent_base.core.hooks import get_hooks, HookEvent, HookContext

    def deny_rm(ctx: HookContext) -> str | None:
        if "rm -rf" in str(ctx.tool_input):
            return "Permission denied"
        return None

    get_hooks().register(HookEvent.TOOL_BEFORE, deny_rm)
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .exceptions import AgentInterrupted

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    LLM_BEFORE = "llm_before"
    LLM_AFTER = "llm_after"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"


@dataclass
class HookContext:
    """Hook 触发时的上下文快照（纯数据，不携带 agent/registry 活对象）。"""

    event: HookEvent
    agent_name: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_status: Optional[str] = None       # TOOL_AFTER result status
    error_code: Optional[str] = None        # TOOL_AFTER normalized error code
    max_repeated_tool_calls: int = 3        # TOOL_BEFORE policy supplied by agent
    tool_output: Optional[str] = None      # 仅 TOOL_AFTER
    messages: Optional[list] = None        # 仅 LLM_BEFORE / LLM_AFTER
    llm_response: Optional[dict] = None    # 仅 LLM_AFTER


@dataclass
class AgentRuntime:
    """每次 run 的运行时上下文（per-request 状态走 contextvar）。

    中断的 stop 标志等 per-连接状态通过 ``set_runtime`` 注入，
    hook 触发时经 ``get_runtime`` 读取，避免污染全局单例注册表。
    """

    stop_check: Callable[[], bool] = field(default_factory=lambda: (lambda: False))
    todos: list = field(default_factory=list)
    rounds_since_todo: int = 0
    # Complex cross-artifact tasks opt into an explicit acceptance contract.
    # Keep this per-run: ordinary repairs should retain the lightweight path.
    requires_todo_plan: bool = False
    requires_acceptance_todos: bool = False
    strategy_subagent_used: bool = False
    # Per-run convergence state.  Hooks own these counters so the ReAct loop
    # does not need to encode policy for repeated calls or repeated failures.
    repeated_tool_calls: dict[str, int] = field(default_factory=dict)
    repeated_tool_failures: dict[str, int] = field(default_factory=dict)
    repeated_tool_failure_codes: dict[str, str] = field(default_factory=dict)


def todo_plan_complete(runtime: AgentRuntime | None = None) -> bool:
    """Return whether the current task's required TODO plan is complete."""
    runtime = runtime or get_runtime()
    requires_plan = runtime.requires_todo_plan or runtime.requires_acceptance_todos
    if not requires_plan:
        return True
    if not runtime.todos:
        return False
    if not all(todo.get("status") == "completed" for todo in runtime.todos):
        return False
    return (
        not runtime.requires_acceptance_todos
        or any(todo.get("kind") == "verification" for todo in runtime.todos)
    )


def acceptance_todo_contract_complete(runtime: AgentRuntime | None = None) -> bool:
    """Backward-compatible alias for the general TODO completion check."""
    return todo_plan_complete(runtime)


_runtime_var: ContextVar[AgentRuntime] = ContextVar(
    "agent_runtime", default=AgentRuntime()
)


def get_runtime() -> AgentRuntime:
    """返回当前 run 的运行时上下文（无则返回默认空上下文）。"""
    return _runtime_var.get()


def set_runtime(runtime: AgentRuntime):
    """注入当前 run 的运行时上下文，返回 reset token。"""
    return _runtime_var.set(runtime)


def reset_runtime(token) -> None:
    """恢复 set_runtime 之前的运行时上下文。"""
    _runtime_var.reset(token)


Hook = Callable[[HookContext], Optional[str]]


def _tool_call_key(tool_name: Optional[str], tool_input: Optional[dict]) -> str:
    """Build a stable, bounded-enough fingerprint for one tool invocation."""
    try:
        encoded = json.dumps(tool_input or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(tool_input)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"{tool_name or ''}:{digest}"


class RepeatedToolCallHook:
    """Stop identical calls and repeated identical failures at the hook boundary.

    The first guard preserves the agent's configurable identical-call limit.
    The second guard is stricter for a failing call: after two failures with
    the same normalized input, the next attempt receives a capability fact
    instead of executing the same failing operation again.  A changed input
    has a different fingerprint and remains eligible for a corrective retry.
    """

    def __call__(self, ctx: HookContext) -> Optional[str]:
        runtime = get_runtime()
        if ctx.event == HookEvent.RUN_START:
            runtime.repeated_tool_calls.clear()
            runtime.repeated_tool_failures.clear()
            runtime.repeated_tool_failure_codes.clear()
            return None

        if not ctx.tool_name or ctx.event not in (HookEvent.TOOL_BEFORE, HookEvent.TOOL_AFTER):
            return None

        key = _tool_call_key(ctx.tool_name, ctx.tool_input)
        if ctx.event == HookEvent.TOOL_BEFORE:
            failure_code = runtime.repeated_tool_failure_codes.get(key)
            failure_key = f"{key}:{failure_code or 'TOOL_ERROR'}"
            failure_count = runtime.repeated_tool_failures.get(failure_key, 0)
            if failure_count >= 2:
                failure_code = runtime.repeated_tool_failure_codes.get(key) or "TOOL_ERROR"
                return (
                    "Repeated tool failure blocked after 2 identical failures. "
                    f"Tool '{ctx.tool_name}' returned the same failure for the same input; "
                    f"error_code={failure_code}. Change the input or choose a different "
                    "capability instead of retrying it."
                )

            call_count = runtime.repeated_tool_calls.get(key, 0)
            max_calls = max(1, int(getattr(ctx, "max_repeated_tool_calls", 3) or 3))
            if call_count >= max_calls:
                return (
                    "Repeated identical tool call blocked by circuit breaker. "
                    "Use a different input or provide the current result."
                )
            runtime.repeated_tool_calls[key] = call_count + 1
            return None

        if ctx.tool_status == "error":
            failure_code = ctx.error_code or "TOOL_ERROR"
            failure_key = f"{key}:{failure_code}"
            runtime.repeated_tool_failures[failure_key] = (
                runtime.repeated_tool_failures.get(failure_key, 0) + 1
            )
            runtime.repeated_tool_failure_codes[key] = failure_code
        else:
            # A successful retry proves that the previous failure was transient
            # and must not poison later work with the same input.
            failure_code = runtime.repeated_tool_failure_codes.pop(key, None)
            if failure_code:
                runtime.repeated_tool_failures.pop(f"{key}:{failure_code}", None)
        return None


class HookRegistry:
    """事件 → 有序 hook 列表的全局单例注册表。"""

    def __init__(self):
        self._hooks: dict[HookEvent, list] = {e: [] for e in HookEvent}

    def register(
        self,
        event: HookEvent,
        hook: Hook,
        *,
        priority: int = 0,
        fail_closed: bool = False,
    ) -> None:
        """注册 hook。priority 越高越先触发；fail_closed 的 hook 抛异常视为 veto。"""
        self._hooks[event].append((priority, fail_closed, hook))
        self._hooks[event].sort(key=lambda item: item[0], reverse=True)

    def unregister(self, event: HookEvent, hook: Hook) -> None:
        """按引用移除 hook（幂等）。"""
        self._hooks[event] = [
            item for item in self._hooks[event] if item[2] is not hook
        ]

    def clear(self, event: Optional[HookEvent] = None) -> None:
        """清空 hook（不传 event 则清空所有事件），主要用于测试隔离。"""
        if event is None:
            for e in self._hooks:
                self._hooks[e].clear()
        else:
            self._hooks[event].clear()

    def trigger(self, event: HookEvent, ctx: HookContext) -> Optional[str]:
        """按 priority 降序触发 hook，首个非 None 返回值短路。"""
        for _, fail_closed, hook in self._hooks[event]:
            try:
                result = hook(ctx)
            except AgentInterrupted:
                raise
            except Exception as exc:
                if fail_closed:
                    logger.exception(
                        "[Hooks] fail-closed hook %r for %s", hook, event.value
                    )
                    return f"Hook error (fail-closed): {type(exc).__name__}: {exc}"
                logger.warning(
                    "[Hooks] hook %r for %s failed (non-fatal)",
                    hook, event.value, exc_info=True,
                )
                continue
            if result is not None:
                return result
        return None


_registry = HookRegistry()


def get_hooks() -> HookRegistry:
    """返回全局单例 hook 注册表。"""
    return _registry


# ── 内置默认 hook ─────────────────────────────────────────
# 中断与截断是所有 agent 的通用默认行为，模块加载时注册一次；
# 可被更高 priority 的自定义 hook 短路覆盖。

def _interrupt_hook(ctx: HookContext) -> Optional[str]:
    if get_runtime().stop_check():
        raise AgentInterrupted("User requested stop")
    return None


class TruncateHook:
    """把 tool 输出截断到指定长度（replace 语义，只作用于喂给模型的口径）。

    完整 observation 由框架统一记录，本 hook 只决定喂给模型的内容，
    因此截断策略可插拔而不影响 trace / 前端展示的完整口径。

    截断后**必须**追加显式标记：否则模型会把腰斩的内容当成完整内容，
    进而基于不存在的文本构造 ``edit_file`` 的 ``old_string``，匹配必然失败
    且无法归因。标记会超出 ``max_chars`` 若干字符，这是有意的——
    宁可多几十字符，也不能让模型对"内容被删过"这件事无感。

    ``per_tool`` 按工具名覆盖上限。默认 2000 是针对 ``read_file`` 这类读**任意
    用户文件**的工具定的；而 ``skill`` 读的是仓库内受控、体量有界的知识包，
    被腰斩等于让模型照着半份规范执行，故单独放宽。
    """

    def __init__(self, max_chars: int = 2000, per_tool: Optional[dict] = None):
        self.max_chars = max_chars
        self.per_tool = per_tool or {}

    def __call__(self, ctx: HookContext) -> Optional[str]:
        output = ctx.tool_output
        limit = self.per_tool.get(ctx.tool_name, self.max_chars)
        if output is not None and len(output) > limit:
            return (
                output[:limit]
                + f"\n\n... [truncated: showing first {limit} "
                  f"of {len(output)} chars — request a narrower range to see more]"
            )
        return None


_TODO_REMINDER_INTERVAL = 3  # 连续 N 轮无 todo 更新且存在未完成项时提醒


def _todo_reminder_hook(ctx: HookContext) -> Optional[str]:
    """LLM_BEFORE 触发：todo 有未完成项且连续 N 轮未更新时注入提醒。

    通过副作用往 ctx.messages append 提醒消息（LLM_BEFORE 的 messages 是
    react_agent 循环里 messages 的引用），返回 None 表示不 veto。
    """
    runtime = get_runtime()
    runtime.rounds_since_todo += 1
    has_open = any(
        t.get("status") in ("pending", "in_progress") for t in runtime.todos
    )
    if runtime.rounds_since_todo >= _TODO_REMINDER_INTERVAL and has_open:
        if ctx.messages is not None:
            ctx.messages.append({
                "role": "user",
                "content": "<reminder>Update your todos.</reminder>",
            })
        runtime.rounds_since_todo = 0
    return None


def _register_default_hooks() -> None:
    get_hooks().register(HookEvent.LLM_BEFORE, _interrupt_hook, priority=100)
    get_hooks().register(HookEvent.LLM_BEFORE, _todo_reminder_hook, priority=50)
    get_hooks().register(HookEvent.TOOL_BEFORE, _interrupt_hook, priority=100)
    repeated_call_hook = RepeatedToolCallHook()
    get_hooks().register(HookEvent.RUN_START, repeated_call_hook, priority=90)
    get_hooks().register(HookEvent.TOOL_BEFORE, repeated_call_hook, priority=90)
    get_hooks().register(HookEvent.TOOL_AFTER, repeated_call_hook, priority=90)
    get_hooks().register(
        HookEvent.TOOL_AFTER,
        # 20000 覆盖当前最大的 skill 引用文件（约 11KB），仍留兜底不会无限膨胀
        # Keep iterative tool history compact without forcing the model to
        # reopen ordinary source files just because a useful read window was
        # clipped. Recent evaluation traces showed 20-48 repeated ``read_file``
        # calls in a single task: the old 1200-character cap commonly cut a
        # 30-line method in half. Source reads are still bounded, while
        # focused searches and task output retain a moderate cap. The full
        # observation remains in ChatTrace for audit.
        TruncateHook(
            max_chars=1200,
            per_tool={
                "read_file": 6000,
                "search_text": 4000,
                "run_task": 6000,
                "skill": 20000,
            },
        ),
        priority=0,
    )


_register_default_hooks()
