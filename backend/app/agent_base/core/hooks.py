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

import logging
import copy
import time
import inspect
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .exceptions import AgentInterrupted
from .policy import ExecutionBudget
from ..convergence import ConvergenceController

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    RUN_START = "run_start"
    ROUND_BEFORE = "round_before"
    LLM_BEFORE = "llm_before"
    LLM_AFTER = "llm_after"
    TOOL_BATCH_BEFORE = "tool_batch_before"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"
    TOOL_BATCH_AFTER = "tool_batch_after"
    ROUND_AFTER = "round_after"
    RUN_FINALIZE = "run_finalize"
    RUN_END = "run_end"
    ERROR = "error"
    CANCEL = "cancel"


class HookAction(str, Enum):
    CONTINUE = "continue"
    REPLACE = "replace"
    VETO = "veto"
    RECOVER = "recover"
    FINALIZE = "finalize"
    STOP = "stop"


@dataclass(frozen=True)
class HookDecision:
    """Generic control result shared by policy and application hooks."""

    action: HookAction | str = HookAction.CONTINUE
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookContext:
    """Hook 触发时的上下文快照（纯数据，不携带 agent/registry 活对象）。"""

    event: HookEvent
    agent_name: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_status: Optional[str] = None       # TOOL_AFTER result status
    error_code: Optional[str] = None        # TOOL_AFTER normalized error code
    tool_output: Optional[str] = None      # 仅 TOOL_AFTER
    messages: Optional[list] = None        # 仅 LLM_BEFORE / LLM_AFTER
    llm_response: Optional[dict] = None    # 仅 LLM_AFTER
    run_id: str = ""
    phase: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    runtime: Optional["AgentRuntime"] = None


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
    run_id: str = ""
    execution_budget: Optional[ExecutionBudget] = None
    convergence_controller: Optional[ConvergenceController] = None
    control_decision: Optional[HookDecision] = None
    policy_metadata: dict[str, Any] = field(default_factory=dict)
    plugin_plan_id: str = ""
    lifecycle_round_open: bool = False
    lifecycle_step: int = 0
    lifecycle_status: str = "running"


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


HookResult = Optional[str | HookDecision]
Hook = Callable[[HookContext], HookResult]


class HookRegistry:
    """事件 → 有序 hook 列表的全局单例注册表。"""

    def __init__(self):
        self._hooks: dict[HookEvent, list] = {e: [] for e in HookEvent}
        self._metadata: dict[tuple[HookEvent, int], dict] = {}
        self.plan_id = ""

    def register(
        self,
        event: HookEvent,
        hook: Hook,
        *,
        priority: int = 0,
        fail_closed: bool = False,
        contribution_id: str = "",
        plugin: str = "",
        mode: str = "legacy",
    ) -> None:
        """注册 hook。priority 越高越先触发；fail_closed 的 hook 抛异常视为 veto。"""
        self._hooks[event].append((priority, fail_closed, hook))
        self._metadata[event, id(hook)] = {
            "id": contribution_id, "plugin": plugin, "mode": mode,
        }
        self._hooks[event].sort(key=lambda item: item[0], reverse=True)

    def unregister(self, event: HookEvent, hook: Hook) -> None:
        """按引用移除 hook（幂等）。"""
        self._hooks[event] = [
            item for item in self._hooks[event] if item[2] is not hook
        ]
        self._metadata.pop((event, id(hook)), None)

    def clear(self, event: Optional[HookEvent] = None) -> None:
        """清空 hook（不传 event 则清空所有事件），主要用于测试隔离。"""
        if event is None:
            for e in self._hooks:
                self._hooks[e].clear()
            self._metadata.clear()
        else:
            self._hooks[event].clear()
            self._metadata = {key: value for key, value in self._metadata.items() if key[0] != event}

    def clear_managed(self):
        for stage, hooks in tuple(self._hooks.items()):
            for _, _, handler in tuple(hooks):
                if self._metadata.get((stage, id(handler)), {}).get("id"):
                    self.unregister(stage, handler)

    def has_contribution(self, identifier):
        return any(meta.get("id") == identifier for meta in self._metadata.values())

    def trigger(self, event: HookEvent, ctx: HookContext) -> HookResult:
        """首个有效结果短路后续处理器，观察接口仍收到独立快照。"""
        decision = None
        decision_id = ""
        for _, fail_closed, hook in tuple(self._hooks[event]):
            mode = self._metadata.get((event, id(hook)), {}).get("mode", "legacy")
            if decision is not None and mode != "observer":
                self._record(event, hook, ctx, "skipped", 0, result=decision, blocked_by=decision_id)
                continue
            started = time.monotonic()
            try:
                result = hook(self._observer_context(ctx) if mode == "observer" else ctx)
                self._validate_result(event, mode, result)
            except AgentInterrupted as exc:
                self._record(event, hook, ctx, "interrupted", time.monotonic() - started,
                             error_type=type(exc).__name__, error_message=str(exc),
                             failure_effect="continue" if mode == "observer" else "interrupt")
                if mode == "observer":
                    logger.warning("[Hooks] observer cannot interrupt execution")
                    continue
                raise
            except Exception as exc:
                if fail_closed:
                    logger.exception(
                        "[Hooks] fail-closed hook %r for %s", hook, event.value
                    )
                    message = f"Hook error (fail-closed): {type(exc).__name__}: {exc}"
                    decision = message if mode == "legacy" else HookDecision(
                        HookAction.VETO if event == HookEvent.TOOL_BEFORE else HookAction.STOP,
                        reason="hook_error", message=message,
                    )
                    decision_id = self._metadata.get((event, id(hook)), {}).get("id", "legacy")
                    self._record(event, hook, ctx, "error", time.monotonic() - started, result=decision,
                                 error_type=type(exc).__name__, error_message=str(exc), failure_effect="block")
                    continue
                self._record(event, hook, ctx, "error", time.monotonic() - started,
                             error_type=type(exc).__name__, error_message=str(exc), failure_effect="continue")
                logger.warning(
                    "[Hooks] hook %r for %s failed (non-fatal)",
                    hook, event.value, exc_info=True,
                )
                continue
            self._record(event, hook, ctx, "executed", time.monotonic() - started, result=result)
            if mode == "observer":
                continue
            if isinstance(result, HookDecision) and result.action in {HookAction.CONTINUE, "continue"}:
                continue
            if result is not None:
                decision = result
                decision_id = self._metadata.get((event, id(hook)), {}).get("id", "legacy")
        return decision

    def emit(self, event: HookEvent, ctx: HookContext) -> list[HookResult]:
        """Run every hook for broadcast-style lifecycle events.

        ``trigger`` remains the short-circuit API for veto/replace decisions;
        ``emit`` is used when all observers must see the event, such as the
        end of a tool batch.
        """
        results: list[HookResult] = []
        for _, fail_closed, hook in tuple(self._hooks[event]):
            mode = self._metadata.get((event, id(hook)), {}).get("mode", "legacy")
            started = time.monotonic()
            try:
                result = hook(self._observer_context(ctx) if mode == "observer" else ctx)
                self._validate_result(event, mode, result)
            except AgentInterrupted as exc:
                self._record(event, hook, ctx, "interrupted", time.monotonic() - started,
                             error_type=type(exc).__name__, error_message=str(exc),
                             failure_effect="continue" if mode == "observer" else "interrupt")
                if mode == "observer":
                    logger.warning("[Hooks] observer cannot interrupt execution")
                    continue
                raise
            except Exception as exc:
                if fail_closed:
                    logger.exception(
                        "[Hooks] fail-closed hook %r for %s", hook, event.value
                    )
                    results.append(
                        HookDecision(
                            action=HookAction.FINALIZE if event == HookEvent.TOOL_BATCH_AFTER else HookAction.VETO,
                            reason="hook_error",
                            message=f"Hook error (fail-closed): {type(exc).__name__}: {exc}",
                        )
                    )
                    self._record(event, hook, ctx, "error", time.monotonic() - started, result=results[-1],
                                 error_type=type(exc).__name__, error_message=str(exc),
                                 failure_effect="finalize" if event == HookEvent.TOOL_BATCH_AFTER else "block")
                else:
                    self._record(event, hook, ctx, "error", time.monotonic() - started,
                                 error_type=type(exc).__name__, error_message=str(exc), failure_effect="continue")
                    logger.warning(
                        "[Hooks] hook %r for %s failed (non-fatal)",
                        hook, event.value, exc_info=True,
                    )
                continue
            self._record(event, hook, ctx, "executed", time.monotonic() - started, result=result)
            mode = self._metadata.get((event, id(hook)), {}).get("mode", "legacy")
            if result is not None and mode != "observer":
                results.append(result)
        if event == HookEvent.TOOL_BATCH_AFTER and ctx.runtime is not None:
            decisions = [item for item in results if isinstance(item, HookDecision)
                         and item.action in {HookAction.RECOVER, HookAction.FINALIZE, "recover", "finalize"}]
            if decisions:
                ctx.runtime.control_decision = max(decisions, key=lambda item:
                    int(item.action in {HookAction.FINALIZE, "finalize"}))
        return results

    @staticmethod
    def _validate_result(event, mode, result):
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise ValueError("lifecycle handlers must not return awaitables")
        if mode in {"observer", "legacy"} or result is None:
            return
        if mode == "transform":
            if event == HookEvent.TOOL_AFTER and (isinstance(result, str) or
                isinstance(result, HookDecision) and result.action in {HookAction.REPLACE, "replace"}):
                return
            raise ValueError("transform must mutate stage data and return None, or replace TOOL_AFTER output")
        allowed = {
            HookEvent.LLM_BEFORE: {HookAction.CONTINUE, HookAction.STOP},
            HookEvent.TOOL_BEFORE: {HookAction.CONTINUE, HookAction.STOP, HookAction.VETO},
            HookEvent.TOOL_BATCH_AFTER: {HookAction.CONTINUE, HookAction.RECOVER, HookAction.FINALIZE},
        }
        if not isinstance(result, HookDecision) or result.action not in allowed.get(event, set()):
            raise ValueError("unsupported control decision for this stage")

    @staticmethod
    def _observer_context(ctx):
        from dataclasses import replace
        payload = copy.deepcopy(ctx.payload)
        payload["plugin_plan_id"] = ctx.runtime.plugin_plan_id if ctx.runtime else ""
        return replace(ctx, runtime=None, payload=payload,
                       messages=copy.deepcopy(ctx.messages), llm_response=copy.deepcopy(ctx.llm_response),
                       tool_input=copy.deepcopy(ctx.tool_input))

    def _record(self, event, hook, ctx, status, duration, result=None, **details):
        meta = self._metadata.get((event, id(hook)), {})
        if not meta.get("id"):
            return
        try:
            from app.trace.tracing import current_trace_sink
            sink = current_trace_sink()
            if sink is not None:
                if isinstance(result, HookDecision) and meta.get("mode") != "observer":
                    details.update(decision_action=result.action, decision_reason=result.reason,
                                   decision_message=result.message)
                sink.event("plugin_contribution", contribution_id=meta["id"], plugin=meta["plugin"],
                           stage=event.value, mode=meta["mode"], status=status,
                           duration_ms=round(duration * 1000, 3), run_id=ctx.run_id,
                           plan_id=ctx.runtime.plugin_plan_id if ctx.runtime else self.plan_id, **details)
        except Exception:
            logger.debug("[Hooks] contribution tracing failed", exc_info=True)


_registry = HookRegistry()


def get_hooks() -> HookRegistry:
    """返回全局单例 hook 注册表。"""
    return _registry


# ── 内置默认 hook ─────────────────────────────────────────
# 通用中断与运行策略在模块加载时注册；工具输出分页由执行器负责。
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
    进而基于不存在的文本构造 ``apply_changes`` 的修改内容，匹配必然失败
    且无法归因。标记会超出 ``max_chars`` 若干字符，这是有意的——
    宁可多几十字符，也不能让模型对"内容被删过"这件事无感。

    ``per_tool`` 按工具名覆盖上限。此类保留给显式注册的定制策略；
    默认运行时使用可续读的工具结果协议，``read_file`` 单独按行限量。
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


class RunPolicyHook:
    """Adapt execution policy components to the generic hook protocol.

    The agent loop only publishes lifecycle events. Resource accounting and
    convergence decisions live here and are exposed through the per-run
    ``AgentRuntime`` control signal.
    """

    def __call__(self, ctx: HookContext) -> HookResult:
        runtime = ctx.runtime or get_runtime()
        budget = runtime.execution_budget

        if ctx.event == HookEvent.RUN_START:
            runtime.control_decision = None
            if budget is not None:
                budget.start(int(ctx.payload.get("initial_token_usage", 0) or 0))
            return None

        if ctx.event == HookEvent.LLM_AFTER and budget is not None:
            usage = (ctx.llm_response or {}).get("usage") or {}
            total_tokens = usage.get("total_tokens", 0)
            budget.record_tokens(int(total_tokens or 0))
            return None

        if ctx.event == HookEvent.LLM_BEFORE and budget is not None:
            reason = budget.before_llm()
            if reason:
                message = (
                    "The run time limit was reached; finalize with the verified evidence already gathered."
                    if reason == "time_limit" else
                    "The per-request emergency token ceiling was reached; finalize with the verified evidence already gathered."
                )
                decision = HookDecision(
                    action=HookAction.STOP,
                    reason=reason,
                    message=message,
                )
                runtime.control_decision = decision
                return decision
            return None

        if ctx.event == HookEvent.TOOL_BEFORE and budget is not None:
            reason = budget.before_tool()
            if reason:
                decision = HookDecision(
                    action=HookAction.VETO,
                    reason=reason,
                    message="Tool execution budget exhausted.",
                )
                runtime.control_decision = decision
                return decision
            return None

        if ctx.event == HookEvent.TOOL_BATCH_AFTER:
            controller = runtime.convergence_controller
            if controller is None:
                return None
            runtime.control_decision = None
            convergence = controller.observe(ctx.payload.get("details", []))
            if convergence.action in {"recover", "finalize"}:
                decision = HookDecision(
                    action=convergence.action,
                    reason=convergence.reason,
                    message=convergence.message,
                )
                runtime.control_decision = decision
                return decision
            return None

        return None


_run_policy_hook = RunPolicyHook()


def default_hook_bindings():
    bindings = [
        (HookEvent.LLM_BEFORE, _interrupt_hook, 100, "control", "core.interrupt.llm_before"),
        (HookEvent.TOOL_BEFORE, _interrupt_hook, 100, "control", "core.interrupt.tool_before"),
        (HookEvent.LLM_BEFORE, _todo_reminder_hook, 50, "transform", "core.todo.reminder"),
    ]
    bindings.extend((stage, _run_policy_hook, 90, "control", f"core.policy.{stage.value}")
                    for stage in (HookEvent.RUN_START, HookEvent.LLM_BEFORE, HookEvent.LLM_AFTER,
                                  HookEvent.TOOL_BEFORE, HookEvent.TOOL_BATCH_AFTER))
    return tuple(bindings)


def _register_default_hooks() -> None:
    for stage, handler, priority, mode, identifier in default_hook_bindings():
        get_hooks().register(
            stage, handler, priority=priority,
            contribution_id=identifier, plugin="core", mode=mode,
        )
    # The ToolRoundExecutor pages every long tool result through its trace.


_register_default_hooks()
