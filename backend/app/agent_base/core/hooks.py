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
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .exceptions import AgentInterrupted
from .policy import ExecutionBudget
from ..convergence import ConvergenceController

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    INITIALIZE = "initialize"
    PREPARE = "prepare"
    RUN_START = "run_start"
    ROUND_BEFORE = "round_before"
    MODEL_BEFORE = "model_before"
    MODEL_AFTER = "model_after"
    TOOL_BATCH_BEFORE = "tool_batch_before"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"
    TOOL_BATCH_AFTER = "tool_batch_after"
    ROUND_AFTER = "round_after"
    FINALIZE = "finalize"
    RUN_END = "run_end"
    # Notifications are deliberately outside PUBLIC_STAGES.
    ERROR = "error"
    CANCEL = "cancel"
    REVIEW_AFTER = "review_after"
    BACKGROUND_BEFORE = "background_before"
    BACKGROUND_AFTER = "background_after"
    TASK_AFTER = "task_after"


PUBLIC_STAGES = tuple(HookEvent[name] for name in (
    "INITIALIZE", "PREPARE", "RUN_START", "ROUND_BEFORE", "MODEL_BEFORE", "MODEL_AFTER",
    "TOOL_BATCH_BEFORE", "TOOL_BEFORE", "TOOL_AFTER", "TOOL_BATCH_AFTER", "ROUND_AFTER", "FINALIZE", "RUN_END",
))
NOTIFICATIONS = tuple(stage for stage in HookEvent if stage not in PUBLIC_STAGES)


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
    messages: Optional[list] = None        # 仅 MODEL_BEFORE / MODEL_AFTER
    llm_response: Optional[dict] = None    # 仅 MODEL_AFTER
    run_id: str = ""
    phase: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    runtime: Optional["AgentRuntime"] = None
    invocation: Any = None  # Private per-call service state; hidden from observers.


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
    run_operation_id: str = ""
    lifecycle_round_open: bool = False
    lifecycle_step: int = 0
    lifecycle_status: str = "running"
    lifecycle_finalized: bool = False


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
        interface_id: str = "",
        plugin_version: str = "",
        plugin_revision: str = "",
    ) -> None:
        """注册 hook。priority 越高越先触发；fail_closed 的 hook 抛异常视为 veto。"""
        self._hooks[event].append((priority, fail_closed, hook))
        self._metadata[event, id(hook)] = {
            "id": contribution_id, "plugin": plugin, "mode": mode, "interface_id": interface_id,
            "plugin_version": plugin_version, "plugin_revision": plugin_revision,
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

    @staticmethod
    def _drive_sync(iterator):
        try:
            value = next(iterator)
            while True:
                if inspect.isawaitable(value):
                    close = getattr(value, "close", None)
                    if close is not None:
                        close()
                    value = iterator.throw(ValueError("async contribution requires asynchronous dispatch"))
                else:
                    value = iterator.send(value)
        except StopIteration as done:
            return done.value
        finally:
            iterator.close()

    @staticmethod
    async def _drive_async(iterator):
        try:
            value = next(iterator)
            while True:
                try:
                    result = await value if inspect.isawaitable(value) else value
                except BaseException as exc:
                    value = iterator.throw(exc)
                else:
                    value = iterator.send(result)
        except StopIteration as done:
            return done.value
        finally:
            iterator.close()

    def trigger(self, event: HookEvent, ctx: HookContext) -> HookResult:
        return self._drive_sync(self._trigger(event, ctx))

    async def atrigger(self, event: HookEvent, ctx: HookContext) -> HookResult:
        return await self._drive_async(self._trigger(event, ctx))

    def emit(self, event: HookEvent, ctx: HookContext) -> list[HookResult]:
        return self._drive_sync(self._emit(event, ctx))

    async def aemit(self, event: HookEvent, ctx: HookContext) -> list[HookResult]:
        return await self._drive_async(self._emit(event, ctx))

    def invoke(self, ctx: HookContext):
        return self._drive_sync(self._invoke(ctx))

    async def ainvoke(self, ctx: HookContext):
        return await self._drive_async(self._invoke(ctx))

    def _invoke(self, ctx):
        """Execute only the requested interface plus its stage observers."""
        if ctx.invocation is None:
            raise ValueError("service dispatch requires an invocation")
        for _, _, hook in tuple(self._hooks[ctx.event]):
            meta = self._metadata.get((ctx.event, id(hook)), {})
            mode = meta.get("mode")
            if not (mode == "service" and meta.get("interface_id") == ctx.invocation.interface_id):
                continue
            started = time.monotonic()
            try:
                result = yield hook(self._observer_context(ctx) if mode == "observer" else ctx)
                self._validate_result(ctx.event, mode, result)
            except BaseException as exc:
                self._record(ctx.event, hook, ctx, "interrupted" if isinstance(exc, (asyncio.CancelledError, AgentInterrupted)) else "error",
                             time.monotonic() - started, error_type=type(exc).__name__, error_message=str(exc),
                             failure_effect="propagate" if mode == "service" else "continue")
                if mode == "service" or not isinstance(exc, Exception):
                    raise
            else:
                self._record(ctx.event, hook, ctx, "executed", time.monotonic() - started)

    def _trigger(self, event: HookEvent, ctx: HookContext):
        """首个有效结果短路后续处理器，观察接口仍收到独立快照。"""
        decision = None
        decision_id = ""
        from .operations import current_operation
        operation = current_operation()
        if operation is not None and event in PUBLIC_STAGES:
            operation.stage = event.value
        for _, fail_closed, hook in tuple(self._hooks[event]):
            mode = self._metadata.get((event, id(hook)), {}).get("mode", "legacy")
            if mode == "service":
                continue  # Provider executors activate only through invoke/ainvoke.
            if ctx.payload.get("observers_only") and mode != "observer":
                continue
            if decision is not None and mode != "observer":
                self._record(event, hook, ctx, "skipped", 0, result=decision, blocked_by=decision_id)
                continue
            started = time.monotonic()
            try:
                result = yield hook(self._observer_context(ctx) if mode == "observer" else ctx)
                self._validate_result(event, mode, result)
            except asyncio.CancelledError as exc:
                self._record(event, hook, ctx, "interrupted", time.monotonic() - started,
                             error_type=type(exc).__name__, failure_effect="interrupt")
                raise
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

    def _emit(self, event: HookEvent, ctx: HookContext):
        """Run every hook for broadcast-style lifecycle events.

        ``trigger`` remains the short-circuit API for veto/replace decisions;
        ``emit`` is used when all observers must see the event, such as the
        end of a tool batch.
        """
        results: list[HookResult] = []
        from .operations import current_operation
        operation = current_operation()
        if operation is not None and event in PUBLIC_STAGES:
            operation.stage = event.value
        for _, fail_closed, hook in tuple(self._hooks[event]):
            mode = self._metadata.get((event, id(hook)), {}).get("mode", "legacy")
            if mode == "service":
                continue
            if ctx.payload.get("observers_only") and mode != "observer":
                continue
            started = time.monotonic()
            try:
                result = yield hook(self._observer_context(ctx) if mode == "observer" else ctx)
                self._validate_result(event, mode, result)
            except asyncio.CancelledError as exc:
                self._record(event, hook, ctx, "interrupted", time.monotonic() - started,
                             error_type=type(exc).__name__, failure_effect="interrupt")
                raise
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
            HookEvent.MODEL_BEFORE: {HookAction.CONTINUE, HookAction.STOP},
            HookEvent.TOOL_BEFORE: {HookAction.CONTINUE, HookAction.STOP, HookAction.VETO},
            HookEvent.TOOL_BATCH_AFTER: {HookAction.CONTINUE, HookAction.RECOVER, HookAction.FINALIZE},
        }
        if not isinstance(result, HookDecision) or result.action not in allowed.get(event, set()):
            raise ValueError("unsupported control decision for this stage")

    def _observer_context(self, ctx):
        from dataclasses import replace
        payload = copy.deepcopy(ctx.payload)
        payload["plugin_plan_id"] = (ctx.runtime.plugin_plan_id if ctx.runtime else "") or self.plan_id
        return replace(ctx, runtime=None, invocation=None, payload=payload,
                       messages=copy.deepcopy(ctx.messages), llm_response=copy.deepcopy(ctx.llm_response),
                       tool_input=copy.deepcopy(ctx.tool_input))

    def _record(self, event, hook, ctx, status, duration, result=None, **details):
        meta = self._metadata.get((event, id(hook)), {})
        if not meta.get("id"):
            return
        try:
            from app.trace.tracing import current_trace_sink
            from .operations import current_operation
            operation = current_operation()
            sink = current_trace_sink()
            if sink is not None:
                if operation is not None:
                    details.update(operation_id=operation.operation_id, parent_operation_id=operation.parent_operation_id,
                                   operation_kind=operation.operation_kind, scope=operation.scope,
                                   binding_stage=event.value)
                if isinstance(result, HookDecision) and meta.get("mode") != "observer":
                    details.update(decision_action=result.action, decision_reason=result.reason,
                                   decision_message=result.message)
                sink.event("plugin_contribution", contribution_id=meta["id"], plugin=meta["plugin"],
                           stage=operation.stage if operation else event.value, mode=meta["mode"], status=status,
                           duration_ms=round(duration * 1000, 3), run_id=ctx.run_id,
                           plan_id=(ctx.runtime.plugin_plan_id or self.plan_id) if ctx.runtime else self.plan_id,
                           interface_id=meta.get("interface_id", ""),
                           plugin_version=meta.get("plugin_version", ""),
                           plugin_revision=meta.get("plugin_revision", ""), **details)
        except Exception:
            logger.debug("[Hooks] contribution tracing failed", exc_info=True)


_registry = HookRegistry()


def get_hooks() -> HookRegistry:
    """返回全局单例 hook 注册表。"""
    from .plugin_runtime import current_snapshot
    snapshot = current_snapshot()
    if snapshot is not None:
        return snapshot.registry
    from .extension_context import current_extension_context
    context = current_extension_context()
    if context is not None:
        return context.hooks_for(_registry)
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
    """MODEL_BEFORE 触发：todo 有未完成项且连续 N 轮未更新时注入提醒。

    通过副作用往 ctx.messages append 提醒消息（MODEL_BEFORE 的 messages 是
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

        if ctx.event == HookEvent.MODEL_AFTER and budget is not None:
            usage = (ctx.llm_response or {}).get("usage") or {}
            total_tokens = usage.get("total_tokens", 0)
            budget.record_tokens(int(total_tokens or 0))
            return None

        if ctx.event == HookEvent.MODEL_BEFORE and budget is not None:
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
        (HookEvent.MODEL_BEFORE, _interrupt_hook, 100, "control", "core.interrupt.model_before"),
        (HookEvent.TOOL_BEFORE, _interrupt_hook, 100, "control", "core.interrupt.tool_before"),
        (HookEvent.MODEL_BEFORE, _todo_reminder_hook, 50, "transform", "core.todo.reminder"),
    ]
    bindings.extend((stage, _run_policy_hook, 90, "control", f"core.policy.{stage.value}")
                    for stage in (HookEvent.RUN_START, HookEvent.MODEL_BEFORE, HookEvent.MODEL_AFTER,
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
