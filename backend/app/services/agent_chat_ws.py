"""
Agent 对话 WebSocket 端点 — 前端对话框驱动开发的后端服务

架构：
    用户消息 → 单 ReActAgent（依据 system prompt 自行决定聊天回复或调用工具）

WebSocket 协议:
    客户端 → 服务端: JSON
        {"type": "chat", "message": "创建一个计算器系统"}
        {"type": "task_status"}                  # 读取当前会话断点
        {"type": "stop"}                          # 中断当前 Agent
        {"type": "review_response", "review_id": 0, "response": "批准"}  # 人工审核回复
        {"type": "ping"}                          # 心跳，服务端回 {"event": "pong"}

    服务端 → 客户端: JSON (stream)
        {"event": "progress", "step": 1, "actions": [...], "tool_calls_detail": [...]}
        {"event": "request_review", "review_id": 0, "review_type": "bash_command", "title": "...", "question": "..."}
        {"event": "done", "result": "..."}
        {"event": "stopped", "reason": "..."}
        {"event": "error", "message": "..."}
        {"event": "pong"}                         # 心跳响应
"""

import asyncio
import contextvars
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Callable, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.auth import require_ws_auth
from app.core.security import validate_agent_workspace_path
from app.core.config import get_settings

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.hooks import AgentRuntime, get_runtime, set_runtime, reset_runtime
from app.agent_base.core.exceptions import AgentInterrupted
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools, ProgressRelay,
)
from app.agent_base.core.orchestration import (
    OrchestrationRequest,
    apply_runtime_directives,
    exclude_tools,
    load_orchestrator,
)
from app.agent_base.core.memory import (
    MemoryArchiveRequest,
    MemoryPort,
    MemoryRecallRequest,
    NoOpMemory,
    load_memory,
)
from app.agent_base.execution import build_linux_command_executor
from app.services.chat_trace import ChatTraceLogger, push_trace_hook, pop_trace_hook
from app.services.trace_reader import reconstruct_history
from app.services.agent_session import get_or_create
from app.services.change_set import ChangeSet
from app.services.run_state import RunStateError, RunStatus, get_run_store
from app.core.capabilities import CapabilityPolicy
from app.services.audit_log import get_audit_logger
from app.services.context_manager import ContextBudget, ContextBudgetManager, estimate_tokens

logger = logging.getLogger(__name__)

def _record_audit(event_type: str, *, run_id: str, session_id: str, **payload) -> None:
    """Keep audit failures observable without breaking the Agent response."""
    try:
        get_audit_logger().record(
            event_type, run_id=run_id, session_id=session_id, **payload,
        )
    except Exception:
        logger.exception("[Audit] Could not persist %s for run %s", event_type, run_id)

router = APIRouter(prefix="/agent", tags=["agent-chat"])


def _trace_hook_bridge(kind: str, *args, **kwargs):
    """全局 LLM trace hook 处理器 — 转发到当前会话的 ChatTraceLogger。

    由 llm.py 的 _trace_hook() 调用，签名: (kind, **kwargs)。
    kind: 'llm_request' | 'llm_response'
    """
    from app.services.chat_trace import current_trace_spans

    tracer = _TRACE_BRIDGE.get()
    if tracer is None:
        return None
    spans = current_trace_spans()
    span_path = "/".join(spans) if spans else ""
    try:
        if kind == "llm_request":
            return tracer.llm_request(
                provider=kwargs.get("provider", "unknown"),
                model=kwargs.get("model", ""),
                messages=kwargs.get("messages", []),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                tools=kwargs.get("tools"),
                tool_choice=kwargs.get("tool_choice"),
                response_format=kwargs.get("response_format"),
                timeout=kwargs.get("timeout"),
                span_path=span_path,
            )
        elif kind == "llm_response":
            tracer.llm_response(
                span_id=kwargs.get("span_id", ""),
                content=kwargs.get("content", ""),
                tool_calls=kwargs.get("tool_calls"),
                usage=kwargs.get("usage"),
                error=kwargs.get("error", ""),
                duration_ms=kwargs.get("duration_ms", 0.0),
                span_path=span_path,
            )
            return None
    except Exception:
        logger.exception("[Trace] Bridge failed for kind=%s", kind)
    return None


def _enabled_tools_context() -> str:
    """Describe the stable core tool surface without interpreting user intent."""
    return "## Tool policy\nUse only the supplied tool schemas; do not invent tools."


def _history_structure(agent: ReActAgent | None) -> dict:
    """Return history shape metadata without duplicating message content."""
    history = list(getattr(agent, "_history", []) or []) if agent is not None else []
    roles = []
    contents = []
    for message in history:
        if isinstance(message, dict):
            roles.append(str(message.get("role") or "unknown"))
            contents.append(str(message.get("content") or ""))
        else:
            roles.append(str(getattr(message, "role", "unknown") or "unknown"))
            contents.append(str(getattr(message, "content", "") or ""))
    return {
        "history_message_count": len(history),
        "history_role_sequence": roles,
        "task_execution_summary_count": sum(
            role == "summary" or content.startswith("## Task execution checkpoint")
            for role, content in zip(roles, contents)
        ),
        "legacy_history_summary_present": bool(
            getattr(agent, "_history_summary", "") if agent is not None else ""
        ),
    }


_TRACE_BRIDGE: contextvars.ContextVar[ChatTraceLogger | None] = contextvars.ContextVar(
    "agent_chat_trace_bridge", default=None,
)


def _set_trace_bridge(tracer: ChatTraceLogger | None):
    _TRACE_BRIDGE.set(tracer)


# ── 对话 Agent — ReActAgent + 工具 ──────────────────────

class DevPromptBuilder:
    """组装 dev_agent 的 prompt，最大化 KV 缓存命中。

    拆分原则（前缀缓存是「字节前缀一致才命中」）：
    - 静态核心（身份 / 行为准则 / 工具策略）在创建时生成一次，session 内字节
      恒定，作为 system prompt —— 永远占据前缀最前段。
    - 易变上下文（workspace 目录 / 项目文件 / 记忆 / 日期）作为「尾块」，每轮
      追加到最后一条 user 消息末尾（history 之后）。尾块变化不影响 system +
      tools + history 的稳定前缀，仍然命中缓存。
    - 尾块按 (project_file, source_dir, test_dir, design_dir, 日期, user_message)
      memo 化：项目/目录/日期不变时结构稳定；user_message 入键是为了让记忆
      recall 跟随当前查询，避免同项目内沿用首轮的过期记忆块。
    """

    def __init__(
        self,
        memory: MemoryPort | None = None,
        *,
        memory_recall_top_k: int = 3,
        memory_recall_max_tokens: int = 500,
    ):
        # One production prompt keeps the system prefix byte-stable across
        # sessions. Prompt experiments belong to isolated eval runs, not an
        # environment flag that can split cache cohorts in production.
        self.prompt_version = "3.1-r3"
        self.system_prompt = self._build_static_prompt()
        self.memory = memory if memory is not None else NoOpMemory()
        self.memory_recall_top_k = max(1, int(memory_recall_top_k))
        self.memory_recall_max_tokens = max(1, int(memory_recall_max_tokens))
        self.static_prompt_report = {
            "chars": len(self.system_prompt),
            "estimated_tokens": estimate_tokens(self.system_prompt),
        }
        self._ctx_key: tuple | None = None
        self._ctx_value: str = ""
        self.last_context_report: dict = {
            "sections": {},
            "total_chars": 0,
            "estimated_tokens": 0,
        }

    @staticmethod
    def _build_static_prompt() -> str:
        """The single production system prompt for DevAgent 3.1-r3."""
        return "\n".join([
            "You are DevAgent, a coding and UML engineering agent operating only inside the configured workspace.",
            "Complete the user's request end to end: inspect relevant state, evolve existing artifacts instead of redesigning them unless requested, make scoped changes, verify results, and report what was done and what remains.",
            "",
            "## Execution rules",
            "- Do only what was asked. For a greeting or pure chat, reply briefly without tools.",
            "- Read the smallest useful context before editing. Preserve unrelated user changes; do not add comments or emojis to code unless requested; do not invent files, tool results, tests, or completion.",
            "- Make the minimal correct edit. For a repair, run the focused existing test early, fix its exact failure before broadening scope, then rerun it. Do not claim success until required verification passes.",
            "- For a concrete multi-step request, keep one short execution thread: inspect only the named artifact, make the requested edit with the narrowest matching tool, verify it immediately, then move to the next user-requested step.",
            "- When a command or tool fails, treat its error as evidence and change approach. Do not spend the remaining budget probing interpreters, package managers, shells, or unrelated directories; use the supplied file/domain tools and report any unverified step.",
            "- In a continuing conversation, preserve the latest accepted state and never reopen a completed step unless a later request explicitly changes it. A status question should summarize the checkpoint, not start a new investigation.",
            "- Do not duplicate discovery, inspect a directory merely to find the supplied workspace, or create a helper script solely to inspect or summarize an existing file.",
            "- Treat the supplied Source directory as the working root for relative paths and shell commands.",
            "- Use only tools exposed for the current task and their supplied schemas. Prefer direct domain tools over shell workarounds; treat tool errors as capability facts and change approach instead of repeating them.",
            "",
            "## UML and verification",
            "- Do not modify UML unless requested or required by an externally visible code-contract change. For UML work, prefer graph and targeted file tools; load a skill only when its guidance is necessary.",
            "- For a full migration from a known canonical .umlproj to a stale peer, reuse the canonical file instead of reconstructing diagrams. Use one workspace-local copy operation, then validate the target.",
            "- JSON parsing alone does not verify a UML repair: verify the project loads with a non-empty diagram set, using a valid project as structural reference rather than creating an empty placeholder.",
            "- Follow the current task policy for planning and verification; do not create plans or worktrees when they are not required. Report modified, verified, partial, blocked, and failed states distinctly.",
            "",
            "If a safety rule, missing authority, or hard budget prevents completion, stop safely and report completed work, remaining work, and the exact reason.",
        ])

    async def build_context(
        self, project_file: str, source_dir: str, test_dir: str, user_message: str
    ) -> str:
        """构建易变上下文尾块（memo 化），返回空串表示无易变内容。"""
        from app.core.config import get_settings

        design_dir = (os.path.dirname(os.path.abspath(project_file))
                      if project_file else os.path.abspath(get_settings().uml_dir))
        today = datetime.now().strftime("%Y-%m-%d")
        # user_message 必须入 key：记忆 recall 按查询相关性排序，
        # 否则同项目同日内后续轮次会一直沿用首轮的记忆块。
        key = (project_file, source_dir, test_dir, design_dir, today, user_message)
        if key == self._ctx_key:
            return self._ctx_value

        sections: list[tuple[str, str]] = []

        def add_section(name: str, value: str) -> None:
            if value:
                sections.append((name, value))

        # ── workspace 目录（每轮固定注入）──
        # Workspace and open-project facts are session invariants.  They are
        # never conditional on a natural-language task classifier: a wording
        # miss must not make the agent rediscover its own project boundary.
        workspace_entries = [
            ("Source directory", source_dir),
            ("Test directory", test_dir),
            ("Design directory", design_dir),
        ]
        provided = [(label, d) for label, d in workspace_entries if d]
        if provided:
            lines = ["## Workspace (host paths; bash executes in WSL Linux)"]
            for label, d in provided:
                lines.append(f"- {label}: {d}")
            lines.append(
                "- Use the cwd aliases supplied by the bash schema. Do not convert these "
                "host paths or invoke WSL yourself."
            )
            add_section("workspace", "\n".join(lines))

        # ── 项目上下文 ──
        if project_file:
            add_section("project_context",
                "## Project Context\n"
                f"- Current project file: {project_file}\n"
                "  (use this exact path as project_file parameter; "
                "do NOT guess or shorten the filename)"
            )

        # ── 记忆 recall（按项目，只在 key 变化时重算）──
        project_id = (os.path.splitext(os.path.basename(project_file))[0]
                      if project_file else "")
        memory_block = await self._recall_memory_block(project_id, user_message)
        if memory_block:
            add_section("memory", memory_block)

        add_section("date", f"Current date: {today}")

        self._ctx_key = key
        self._ctx_value = "\n\n".join(value for _, value in sections)
        self.last_context_report = {
            "sections": {
                name: {"chars": len(value), "estimated_tokens": estimate_tokens(value)}
                for name, value in sections
            },
            "total_chars": len(self._ctx_value),
            "estimated_tokens": estimate_tokens(self._ctx_value),
        }
        return self._ctx_value

    async def _recall_memory_block(self, project_id: str, user_message: str) -> str:
        """recall 项目相关记忆，返回注入用的 section 文本；失败返回空串。"""
        if not project_id:
            return ""
        try:
            result = await self.memory.recall(MemoryRecallRequest(
                project_id=project_id,
                query=user_message,
                top_k=self.memory_recall_top_k,
                max_tokens=self.memory_recall_max_tokens,
            ))
            if result.context_block and result.memory_ids:
                await self.memory.reinforce(result.memory_ids, project_id=project_id)
            return result.context_block
        except Exception:
            logger.warning("[Memory] Recall failed (non-fatal)", exc_info=True)
            return ""


async def _create_dev_agent(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    user_message: str = "",
    progress: ProgressRelay | None = None,
    restore_history: list[dict] | None = None,
    task_scope: str = "",
    auto_approve_reviews: bool = False,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    max_run_seconds: float | None = None,
    max_total_tokens: int | None = None,
    convergence_tool_steps: int | None = None,
):
    """创建对话 Agent 实例，注册全部工具，并返回 prompt 组装器。

    静态 system prompt 由 DevPromptBuilder 一次生成；workspace/项目/记忆等
    易变上下文由 builder 每轮追加到最后一条 user 消息末尾（见 build_context）。
    """
    from app.core.config import get_settings

    change_set = ChangeSet(project_file=project_file)
    settings = get_settings()
    command_executor = build_linux_command_executor(settings)

    tools, review_mgr = create_conversation_tools(
        llm, source_dir=source_dir, test_dir=test_dir, project_file=project_file,
        include_review=True, progress=progress,
        task_scope=task_scope or project_file,
        change_set=change_set,
        review_session_id=task_scope or "",
        review_project_id=os.path.splitext(os.path.basename(project_file))[0] if project_file else "",
        auto_approve_reviews=auto_approve_reviews,
        command_executor=command_executor,
        # The default chat path stays single-agent.  Dedicated orchestration
        # can opt into subagents explicitly without adding a task classifier.
        include_subagent=False,
    )

    workspace_roots = [source_dir, test_dir]
    if project_file:
        workspace_roots.append(os.path.dirname(project_file))
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=workspace_roots))
    for t in tools:
        registry.register_tool(t)

    memory_provider = load_memory(llm=llm, settings=settings)
    prompt_builder = DevPromptBuilder(
        memory=memory_provider,
        memory_recall_top_k=settings.agent_memory_recall_top_k,
        memory_recall_max_tokens=settings.agent_memory_recall_max_tokens,
    )

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=prompt_builder.system_prompt,
        max_steps=max_steps or settings.agent_max_steps,
        max_tool_calls=max_tool_calls or settings.agent_max_tool_calls,
        max_repeated_tool_calls=settings.agent_max_repeated_tool_calls,
        max_run_seconds=max_run_seconds or settings.agent_max_run_seconds,
        max_total_tokens=max_total_tokens or settings.agent_max_total_tokens,
        token_finalization_reserve_tokens=settings.agent_token_finalization_reserve_tokens,
        convergence_tool_steps=(
            convergence_tool_steps
            if convergence_tool_steps is not None
            else settings.agent_convergence_tool_steps
        ),
        convergence_budget_ratio=settings.agent_convergence_budget_ratio,
        convergence_keep_recent_steps=settings.agent_convergence_keep_recent_steps,
        evidence_max_records=settings.agent_evidence_max_records,
        force_final_summary_on_step_limit=settings.agent_force_final_summary_on_step_limit,
        final_summary_max_tokens=settings.agent_final_summary_max_tokens,
        llm_timeout_seconds=settings.agent_llm_timeout_seconds,
        use_native_fc=True,
        context_budget=ContextBudgetManager(budget=ContextBudget(
            max_context_tokens=settings.agent_context_max_tokens,
            output_reserve_tokens=settings.agent_context_output_reserve_tokens,
            max_history_tokens=settings.agent_context_max_history_tokens,
            max_history_turns=settings.agent_context_max_history_turns,
            max_summary_tokens=settings.agent_context_max_summary_tokens,
            max_react_steps=settings.agent_context_max_react_steps,
        )),
    )
    agent.change_set = change_set
    agent.memory_provider = memory_provider
    if restore_history:
        agent.restore_history(restore_history)
    return agent, review_mgr, prompt_builder


# ── 记忆系统（跨任务归档 + 注入） ───────────────────────────
# 主流程只依赖 MemoryPort；具体存储和提取策略由可插拔 provider 提供。
# 任务结束 (done) 后异步归档工具过程 + 结论，新任务开始时 recall 相关记忆。

def _todo_progress_state() -> dict:
    """Return the frontend-safe, authoritative TODO snapshot for this run."""
    runtime = get_runtime()
    todos: list[dict] = []
    for item in runtime.todos:
        if not isinstance(item, dict):
            continue
        snapshot = {
            key: item[key]
            for key in ("content", "status", "kind", "acceptance")
            if key in item
        }
        if snapshot:
            todos.append(snapshot)
    return {
        "todos": todos,
        "planning_mode": runtime.requires_acceptance_todos,
        "strategy_advised": runtime.strategy_subagent_used,
    }


def _checkpoint_answer(checkpoint: dict) -> str:
    if not checkpoint:
        return "当前会话没有可用的任务执行记录。"
    lines = [f"任务状态：{checkpoint.get('status', 'unknown')}"]
    for label, key in (
        ("已完成", "completed_items"), ("未完成", "pending_items"),
        ("已修改文件", "changed_files"), ("验证", "verification"),
    ):
        values = checkpoint.get(key) or []
        if values:
            lines.append(f"{label}：" + "；".join(map(str, values)))
    if checkpoint.get("stop_reason"):
        lines.append("停止原因：" + str(checkpoint["stop_reason"]))
    if checkpoint.get("last_error"):
        lines.append("最后错误：" + str(checkpoint["last_error"]))
    return "\n".join(lines)


def _latest_persisted_checkpoint(session_id: str) -> dict:
    """Read the newest run checkpoint for reconnects without invoking an LLM."""
    if not session_id:
        return {}
    try:
        for record in get_run_store().list(limit=20, session_id=session_id):
            checkpoint = record.metadata.get("checkpoint")
            if isinstance(checkpoint, dict) and checkpoint:
                return checkpoint
    except Exception:
        logger.warning("[RunState] Could not read checkpoint for %s", session_id, exc_info=True)
    return {}


_RESUME_REQUESTS = frozenset({
    "\u7ee7\u7eed", "\u7ee7\u7eed\u6267\u884c", "\u6062\u590d", "\u6062\u590d\u4efb\u52a1", "continue", "resume",
})


def _is_resume_request(message: str) -> bool:
    """Recognize an explicit reconnect/resume command."""
    return (message or "").strip().lower() in _RESUME_REQUESTS


def _latest_resumable_run(session_id: str):
    """Return the newest non-terminal run that has a resumable checkpoint."""
    if not session_id:
        return None
    resumable_statuses = {
        RunStatus.RUNNING.value,
        RunStatus.PAUSED.value,
        RunStatus.ORPHANED.value,
    }
    try:
        for record in get_run_store().list(limit=50, session_id=session_id):
            if record.status not in resumable_statuses:
                continue
            checkpoint = record.metadata.get("checkpoint")
            if not isinstance(checkpoint, dict) or not checkpoint:
                continue
            if record.status == RunStatus.RUNNING.value and not checkpoint.get("resume_available"):
                continue
            if checkpoint.get("resume_consumed"):
                continue
            return record, checkpoint
    except Exception:
        logger.warning("[RunState] Could not find resumable run for %s", session_id, exc_info=True)
    return None


def _resume_prompt(checkpoint: dict) -> str:
    """Turn a persisted checkpoint into an explicit continuation request."""
    original = str(checkpoint.get("request_summary") or checkpoint.get("message") or "")[:500]
    completed = checkpoint.get("completed_items") or []
    pending = checkpoint.get("pending_items") or []
    verification = checkpoint.get("verification") or []
    last_step = checkpoint.get("last_step") or ""
    return (
        "continue the previous unfinished task. Original request: " + original
        + ". Read the current files and existing changes first, skip completed steps, "
        "and continue from the pending step; do not treat this as a new task."
        + (" Completed: " + "; ".join(map(str, completed[-16:])) + "." if completed else "")
        + (" Pending: " + "; ".join(map(str, pending[-16:])) + "." if pending else "")
        + (" Last step: " + str(last_step) + "." if last_step else "")
        + (" Verification: " + "; ".join(map(str, verification[-16:])) + "." if verification else "")
    )[:1800]


def _persist_run_checkpoint(
    run_id: str,
    owner_id: str,
    checkpoint: dict,
    status: str | RunStatus = RunStatus.RUNNING,
    error: str = "",
) -> None:
    """Persist progress frequently enough that a transport loss is recoverable."""
    if not run_id:
        return
    try:
        get_run_store().transition(
            run_id,
            status,
            expected={RunStatus.RUNNING, RunStatus.WAITING_APPROVAL, RunStatus.PAUSED},
            owner_id=owner_id,
            error=error,
            metadata_patch={"checkpoint": dict(checkpoint)},
        )
    except RunStateError:
        logger.warning("[RunState] Could not persist checkpoint for run %s", run_id, exc_info=True)

def _should_archive_task_memory(
    checkpoint_status: str, tool_calls_detail: list[dict],
) -> bool:
    """Archive successful mutations based on observed tool facts, not wording."""
    mutating_tools = {"write_file", "edit_file"}
    return bool(
        checkpoint_status == "completed"
        and any(
            detail.get("name") in mutating_tools
            and detail.get("status") in {"success", "completed"}
            for detail in tool_calls_detail
            if isinstance(detail, dict)
        )
    )


def _terminal_checkpoint_status(final_answer: str, todos: list[dict]) -> tuple[str, str | None]:
    """Map an Agent terminal answer to an honest checkpoint state."""
    answer = (final_answer or "").lower()
    if "token 预算" in answer:
        return "budget_exceeded", "token budget exceeded"
    if "时间预算" in answer or "llm 调用超过时间" in answer:
        return "timed_out", "time budget exceeded"
    if "task is not complete" in answer:
        return "partial", "required task plan is incomplete"
    if any(isinstance(todo, dict) and todo.get("status") != "completed" for todo in todos):
        return "partial", "task checklist has pending items"
    return "completed", None


def _summary_excerpt(value: object, limit: int = 220) -> str:
    """Return a bounded single-line value for the next-turn checkpoint."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _build_task_execution_summary(
    tool_calls: list[dict],
    checkpoint: dict,
    status: str,
) -> str:
    """Build a deterministic, bounded summary of the completed task.

    The full tool stream remains in ChatTrace.  This checkpoint keeps the
    small set of facts useful for a later turn: status, changed files,
    verification, representative successes, and every distinct failure up to
    a fixed bound.
    """
    calls = [item for item in (tool_calls or ()) if isinstance(item, dict)]
    counts: dict[str, int] = {}
    success_lines: list[str] = []
    failure_lines: list[str] = []
    success_seen: set[tuple[str, str]] = set()
    failure_seen: set[tuple[str, str, str]] = set()

    for item in calls:
        name = str(item.get("name") or "tool")
        item_status = str(item.get("status") or "unknown")
        counts[item_status] = counts.get(item_status, 0) + 1
        arguments = item.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        target = (
            arguments.get("path")
            or arguments.get("command")
            or arguments.get("node_id")
            or arguments.get("cwd")
            or ""
        )
        target_text = _summary_excerpt(target, 150)
        evidence = item.get("evidence")
        facts = evidence.get("facts", []) if isinstance(evidence, dict) else []
        fact_text = "; ".join(
            _summary_excerpt(fact, 160) for fact in facts[:3]
            if str(fact or "").strip()
        )
        observation = _summary_excerpt(item.get("observation"), 240)
        is_success = item_status in {"success", "completed"}
        if is_success:
            signature = (name, target_text or fact_text)
            if signature not in success_seen and len(success_lines) < 12:
                success_seen.add(signature)
                detail = "; ".join(value for value in (target_text, fact_text) if value)
                success_lines.append(
                    f"- {name} succeeded" + (f" ({detail})" if detail else "")
                )
        else:
            signature = (
                name,
                str(item.get("error_code") or ""),
                observation,
            )
            if signature not in failure_seen and len(failure_lines) < 12:
                failure_seen.add(signature)
                detail = "; ".join(value for value in (
                    str(item.get("error_code") or ""), target_text, observation,
                ) if value)
                failure_lines.append(f"- {name} failed" + (f": {detail}" if detail else ""))

    lines = [
        "## Task execution checkpoint",
        f"- Status: {status}",
        f"- Tool calls: {len(calls)}" + (
            " (" + ", ".join(f"{key}:{value}" for key, value in sorted(counts.items())) + ")"
            if counts else ""
        ),
    ]
    changed_files = [str(item) for item in (checkpoint.get("changed_files") or []) if item]
    if changed_files:
        lines.append("- Changed files: " + "; ".join(changed_files[:16]))
    completed = [str(item) for item in (checkpoint.get("completed_items") or []) if item]
    if completed:
        lines.append("- Completed items: " + "; ".join(completed[-8:]))
    verification = [str(item) for item in (checkpoint.get("verification") or []) if item]
    if verification:
        lines.append("- Verification attempted: " + "; ".join(verification[-8:]))
    if success_lines:
        lines.append("- Successful execution flow:")
        lines.extend(success_lines)
    if failure_lines:
        lines.append("- Failed execution flow:")
        lines.extend(failure_lines)
    if len(success_lines) < sum(
        1 for item in calls if str(item.get("status") or "") in {"success", "completed"}
    ):
        lines.append("- Additional successful calls are available in Trace.")
    pending = [str(item) for item in (checkpoint.get("pending_items") or []) if item]
    if pending:
        lines.append("- Pending items: " + "; ".join(pending[-8:]))
    if checkpoint.get("stop_reason"):
        lines.append("- Stop reason: " + _summary_excerpt(checkpoint.get("stop_reason"), 260))
    return "\n".join(lines)


async def _archive_task_to_memory(
    memory: MemoryPort,
    project_id: str,
    user_message: str,
    final_answer: str,
    tool_calls_detail: list[dict],
    run_id: str = "",
    trace_id: str = "",
) -> None:
    """通过可选 MemoryPort 异步归档任务摘要；失败不影响主流程。"""
    try:
        result = await memory.archive(MemoryArchiveRequest(
            project_id=project_id,
            user_message=user_message,
            final_answer=final_answer,
            tool_steps=tuple(tool_calls_detail or ()),
            run_id=run_id,
            trace_id=trace_id,
        ))
        logger.info(
            "[Memory] Archived task to memory (project=%s, stored=%d)",
            project_id,
            result.stored_count,
        )
    except Exception:
        logger.warning("[Memory] Archive to memory failed (non-fatal)", exc_info=True)


async def _ws_send(websocket: WebSocket, payload: dict) -> bool:
    """发送 WebSocket 消息，连接已断开时返回 False 而非抛异常。

    前端可能在任何时刻断开（刷新/关闭面板），若继续在原连接上 send_json，
    会抛 "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'"
    并可能击穿 uvicorn 进程。这里把发送失败转化为返回值，让调用方优雅终止 agent 循环。
    """
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        logger.info("[AgentChat] WebSocket disconnected during send")
        return False
    except Exception:
        logger.warning("[AgentChat] WebSocket send failed (client likely closed)", exc_info=True)
        return False


def _consume_task_exception(task: asyncio.Task) -> None:
    """读取后台任务的异常，避免 "Task exception was never retrieved" 警告。"""
    if not task.cancelled():
        task.exception()


async def _handle_dev(
    agent: ReActAgent,
    review_mgr,
    user_message: str,
    websocket: WebSocket,
    stop_check,
    trace_log: ChatTraceLogger | None = None,
    project_file: str = "",
    source_dir: str = "",
    test_dir: str = "",
    progress: ProgressRelay | None = None,
    context: str = "",
    fallback_review_runs: dict[int, str] | None = None,
    run_id: str = "",
    run_owner: str = "",
    session_id: str = "",
    resume_checkpoint: dict | None = None,
    disconnect_check: Callable[[], bool] | None = None,
):
    """ReActAgent 执行 — 单 agent 承接所有消息，进度推送到前端。

    该函数同时服务闲聊与开发：agent 依据 system prompt 自行决定
    是否调用工具（闲聊直接文本回复，开发调工具）。

    progress (ProgressRelay): 若提供，则将其 design_element 事件转发
    到 WebSocket 供前端实时渲染（流式优化模式）。
    """

    # 本轮是否经过 submit_uml_review 审核（兜底检测用，见 is_final 分支）
    uml_review_seen = False
    resume_checkpoint = dict(resume_checkpoint or {})
    checkpoint_request_summary = str(
        resume_checkpoint.get("request_summary") or user_message
    )[:500]
    agent.last_run_checkpoint = {
        "run_id": run_id,
        "status": "running",
        "request_summary": checkpoint_request_summary,
        "completed_items": list(resume_checkpoint.get("completed_items") or []),
        "pending_items": list(resume_checkpoint.get("pending_items") or []),
        "changed_files": list(resume_checkpoint.get("changed_files") or []),
        "verification": list(resume_checkpoint.get("verification") or []),
        "last_error": None,
        "stop_reason": None,
        "resume_available": False,
        "resume_of": resume_checkpoint.get("run_id", ""),
        "project_file": project_file,
        "source_dir": source_dir,
        "test_dir": test_dir,
    }
    _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)

    async def _on_progress(ev: dict):
        """将 ProgressRelay 的 design_element / review 事件转发为 WebSocket 消息。"""
        nonlocal uml_review_seen
        if ev.get("event") == "design_element":
            await _ws_send(websocket, {
                "event": "design_element",
                "type": ev.get("type", ""),
                "data": ev.get("data", ""),
            })
        elif ev.get("event") == "review_timeout":
            await _ws_send(websocket, {
                "event": "review_timeout",
                "review_id": ev.get("review_id", 0),
                "review_type": ev.get("review_type", ""),
                "title": ev.get("title", ""),
                "timeout": ev.get("timeout", 0),
            })
        elif ev.get("event") == "review":
            review_type = ev.get("review_type", "code")
            if review_type == "uml_diff":
                uml_review_seen = True
            if trace_log:
                trace_log.review_request(
                    review_id=ev.get("review_id", 0),
                    review_type=review_type,
                    title=ev.get("title", ""),
                    question=ev.get("question", ""),
                    content=ev.get("content", ""),
                )
            if review_type == "uml_diff":
                metadata = ev.get("metadata", {}) or {}
                await _ws_send(websocket, {
                    "event": "uml_review",
                    "review_id": ev.get("review_id", 0),
                    "title": ev.get("title", ""),
                    "diagrams": metadata.get("diagrams", []),
                    "changed_diagrams": metadata.get("changed_diagrams"),
                    "original_diagrams": metadata.get("original_diagrams"),
                })
            else:
                await _ws_send(websocket, {
                    "event": "request_review",
                    "review_id": ev.get("review_id", 0),
                    "review_type": review_type,
                    "title": ev.get("title", ""),
                    "content": ev.get("content", ""),
                    "question": ev.get("question", ""),
                })

    if progress:
        progress.on_progress(_on_progress)

    # 捕获本任务的 before 快照（框架负责 before/after，模型只负责改设计）。
    # 存在 review_mgr 上（工具与 review_response 处理共享，可随 accept 刷新）。
    if review_mgr is not None and project_file and os.path.isfile(project_file):
        try:
            from app.services.file_service import load_project
            review_mgr.baseline = [d.model_dump() for d in load_project(project_file).diagrams]
        except Exception:
            review_mgr.baseline = None

    _runtime_token = set_runtime(AgentRuntime(
        stop_check=stop_check,
    ))
    task_tool_calls: list[dict] = []
    task_summary_written = False

    def _write_task_summary(status: str) -> None:
        """Persist one bounded summary for every terminal execution path."""
        nonlocal task_summary_written
        if task_summary_written:
            return
        task_summary = _build_task_execution_summary(
            task_tool_calls,
            agent.last_run_checkpoint,
            status,
        )
        agent.append_task_summary(task_summary)
        agent.last_run_checkpoint["task_summary"] = task_summary
        if trace_log:
            trace_log.task_summary(
                summary=task_summary,
                status=status,
                tool_call_count=len(task_tool_calls),
            )
        task_summary_written = True

    try:
        change_set = getattr(agent, "change_set", None)
        if change_set is not None:
            change_set.project_file = project_file or change_set.project_file
            change_set.begin()
        task_tool_calls: list[dict] = []  # 累计本任务所有工具调用（供记忆归档）
        agent.tool_registry.set_allowed_tools(None)
        context = "\n\n".join(filter(None, [
            context, _enabled_tools_context(),
        ]))
        orchestration_settings = get_settings()
        orchestrator = load_orchestrator(
            llm=agent.llm,
            settings=orchestration_settings,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
        )
        if trace_log:
            trace_log.event("orchestrator_phase", phase="plan", status="started")
        orchestration_result = await orchestrator.prepare(OrchestrationRequest(
            user_message=user_message,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
            previous_checkpoint=resume_checkpoint,
            available_tools=tuple(agent.tool_registry.list_tools()),
        ))
        if trace_log:
            trace_log.event(
                "orchestrator_plan",
                **orchestration_result.metadata,
                phase=orchestration_result.phase,
                token_overhead=orchestration_result.token_overhead,
            )
            if orchestration_result.phase == "explore":
                trace_log.event(
                    "orchestrator_phase",
                    phase="explore",
                    status="completed",
                    worker_tokens=orchestration_result.metadata.get("worker_tokens", 0),
                )
        apply_runtime_directives(orchestration_result.runtime_directives)
        if orchestration_result.context:
            context = "\n\n".join(filter(None, [context, orchestration_result.context]))
        main_allowed_tools = None
        if orchestration_result.excluded_tools:
            main_allowed_tools = exclude_tools(
                agent.tool_registry.list_tools(), orchestration_result.excluded_tools,
            )
        previous_compaction_callback = getattr(agent, "on_context_compacted", None)
        if trace_log:
            agent.on_context_compacted = lambda report: trace_log.context_compacted(
                summary=report.get("summary", ""),
                dropped_messages=report.get("dropped_messages", 0),
                dropped_tokens=report.get("dropped_tokens", 0),
                reason=report.get("reason", ""),
                triggered_by=report.get("triggered_by", []),
                tool_call_count=report.get("tool_call_count", 0),
                token_budget_used=report.get("token_budget_used", 0),
                keep_recent_steps=report.get("keep_recent_steps", 0),
            )
        async for step_progress in agent.arun_stream(
            user_message,
            context=context,
            initial_token_usage=orchestration_result.token_overhead,
            **({"allowed_tools": main_allowed_tools} if main_allowed_tools is not None else {}),
        ):
            d = step_progress.to_dict()
            task_tool_calls.extend(d.get("tool_calls_detail", []))
            todo_state = _todo_progress_state()
            todos = todo_state.get("todos", [])
            agent.last_run_checkpoint.update({
                "last_step": d.get("step", 0),
                "completed_items": [
                    item.get("content", "")
                    for item in todos
                    if isinstance(item, dict) and item.get("status") == "completed"
                ],
                "pending_items": [
                    item.get("content", "")
                    for item in todos
                    if isinstance(item, dict) and item.get("status") != "completed"
                ],
                "verification": [
                    detail.get("name", "")
                    for detail in d.get("tool_calls_detail", [])
                    if isinstance(detail, dict)
                    and detail.get("name", "").lower() in {"pytest", "run_tests", "test"}
                ],
                "tool_calls": [
                    {
                        "name": detail.get("name", ""),
                        "status": detail.get("status", ""),
                        "error_code": detail.get("error_code", ""),
                    }
                    for detail in task_tool_calls[-32:]
                    if isinstance(detail, dict)
                ],
            })
            _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)

            # 记录完整工具调用与返回（在截断发给前端之前）
            if trace_log:
                trace_log.agent_step(
                    step=d["step"], thought=step_progress.thought or "",
                    actions=d["actions"], is_final=d["is_final"],
                )
                for td in d.get("tool_calls_detail", []):
                    tool_span = trace_log.tool_call(
                        step=d["step"],
                        tool_name=td.get("name", ""),
                        arguments=td.get("arguments", {}),
                    )
                    trace_log.tool_result(
                        span_id=tool_span,
                        tool_name=td.get("name", ""),
                        observation=str(td.get("observation", "")),
                        error=(
                            str(td.get("error_code", ""))
                            if td.get("status") not in {"", "success", "completed"}
                            else ""
                        ),
                        fed_truncated=bool(td.get("fed_truncated", False)),
                        fed_length=int(td.get("fed_length") or 0),
                        duration_ms=float(td.get("duration_ms") or 0.0),
                        evidence=td.get("evidence") if isinstance(td.get("evidence"), dict) else None,
                    )

            ok = await _ws_send(websocket, {
                "event": "progress",
                "step": d["step"],
                "actions": d["actions"],
                "thought": d["thought"][:300],
                "tool_calls_detail": [
                    {
                        "name": td.get("name", ""),
                        "arguments": td.get("arguments", {}),
                        "observation": str(td.get("observation", ""))[:3000],
                    }
                    for td in d.get("tool_calls_detail", [])[:5]
                ],
                "is_final": d["is_final"],
                "final_answer": d["final_answer"] if d["is_final"] else "",
                **_todo_progress_state(),
            })
            if not ok:
                agent.last_run_checkpoint.update({
                    "status": "paused",
                    "resume_available": True,
                    "stop_reason": "websocket disconnected",
                })
                _persist_run_checkpoint(
                    run_id, run_owner, agent.last_run_checkpoint,
                    status=RunStatus.PAUSED,
                )
                if run_id:
                    _record_audit(
                        "run_paused", run_id=run_id, session_id=session_id,
                        reason="websocket disconnected",
                    )
                return

            if d["is_final"]:
                fallback_review_requested = False
                # ── 兜底审核：本轮改了 .umlproj 但没调 submit_uml_review ──
                # 审核靠 prompt 自觉，模型可能漏调；这里对比本轮前后磁盘状态，
                # 有变更则补推一次 uml_review（接受/拒绝语义与正常审核一致：
                # accept 刷新 baseline，reject 由主循环开启一轮修订）。
                if (
                    review_mgr is not None
                    and not uml_review_seen
                    and project_file
                    and os.path.isfile(project_file)
                    and review_mgr.baseline is not None
                ):
                    try:
                        from app.services.file_service import load_project
                        from app.services.diagram_diff import changed_diagrams
                        after = [d.model_dump() for d in load_project(project_file).diagrams]
                        changed = changed_diagrams(after, review_mgr.baseline)
                        if changed:
                            req = review_mgr.submit(
                                review_type="uml_diff",
                                title="检测到未审核的设计变更",
                                content="Agent 修改了设计文件但未提交 diff 审核",
                                question="设计文件已被修改但未经审核，请确认是否接受此变更。",
                                metadata={
                                    "diagrams": after,
                                    "changed_diagrams": changed,
                                    "original_diagrams": review_mgr.baseline,
                                },
                            )
                            if fallback_review_runs is not None:
                                fallback_review_runs[req.id] = run_id
                            fallback_review_requested = True
                            if trace_log:
                                trace_log.review_request(
                                    review_id=req.id,
                                    review_type="uml_diff",
                                    title=req.title,
                                    question=req.question,
                                    content=req.content,
                                )
                            logger.info("[AgentChat] 兜底审核补推: review_id=%d", req.id)
                            await _ws_send(websocket, {
                                "event": "uml_review",
                                "review_id": req.id,
                                "title": req.title,
                                "diagrams": after,
                                "changed_diagrams": changed,
                                "original_diagrams": review_mgr.baseline,
                                "auto": True,
                            })
                    except Exception:
                        logger.exception("[AgentChat] Fallback review check failed")

                if change_set is not None and change_set.has_changes:
                    manifest = change_set.commit()
                    logger.info("[ChangeSet] committed %d file changes", len(manifest))
                else:
                    manifest = []
                todos = get_runtime().todos or []
                terminal_status, stop_reason = _terminal_checkpoint_status(
                    d["final_answer"], todos,
                )
                agent.last_run_checkpoint = {
                    "run_id": run_id,
                    "status": "waiting_approval" if fallback_review_requested else terminal_status,
                    "request_summary": user_message[:500],
                    "completed_items": [
                        t.get("content", "") for t in todos
                        if isinstance(t, dict) and t.get("status") == "completed"
                    ],
                    "pending_items": [
                        t.get("content", "") for t in todos
                        if isinstance(t, dict) and t.get("status") != "completed"
                    ],
                    "changed_files": [m.get("path", "") for m in manifest],
                    "verification": [
                        td.get("name", "") for td in task_tool_calls
                        if td.get("name", "") in {"bash", "test", "pytest"}
                    ],
                    "last_error": None,
                    "stop_reason": stop_reason,
                }
                if fallback_review_requested:
                    agent.last_run_checkpoint.update({
                        "review_status": "pending",
                        "post_review_status": terminal_status,
                    })

                summary_status = (
                    "waiting_approval" if fallback_review_requested else terminal_status
                )
                _write_task_summary(summary_status)

                # A fallback review has no Agent future waiting on it.  Do
                # not announce success before the human has resolved it.
                if fallback_review_requested:
                    if run_id:
                        try:
                            get_run_store().transition(
                                run_id, RunStatus.WAITING_APPROVAL,
                                expected={RunStatus.RUNNING}, owner_id=run_owner,
                                metadata_patch={"checkpoint": agent.last_run_checkpoint},
                            )
                        except RunStateError:
                            logger.warning("[RunState] Could not mark run %s awaiting review", run_id, exc_info=True)
                    await _ws_send(websocket, {
                        "event": "awaiting_review",
                        "run_id": run_id,
                        "checkpoint": agent.last_run_checkpoint,
                    })
                    return

                run_status = (
                    RunStatus.SUCCEEDED if terminal_status == "completed" else RunStatus.FAILED
                )
                try:
                    from app.services.agent_metrics import get_agent_metrics
                    get_agent_metrics().record_run(
                        "success" if terminal_status == "completed" else terminal_status,
                    )
                except Exception:
                    pass
                if run_id:
                    try:
                        get_run_store().transition(
                            run_id, run_status,
                            expected={RunStatus.RUNNING}, owner_id=run_owner,
                            metadata_patch={"checkpoint": agent.last_run_checkpoint},
                        )
                    except RunStateError:
                        logger.warning("[RunState] Could not mark run %s succeeded", run_id, exc_info=True)
                    _record_audit(
                        "run_succeeded" if terminal_status == "completed" else "run_partial",
                        run_id=run_id, session_id=session_id,
                        tool_call_count=len(task_tool_calls),
                    )

                # 异步后台归档到记忆系统（不阻塞返回 done）
                project_id = os.path.splitext(os.path.basename(project_file))[0] if project_file else ""
                if project_id and _should_archive_task_memory(
                    terminal_status, task_tool_calls,
                ):
                    memory = getattr(agent, "memory_provider", None)
                    if memory is not None:
                        asyncio.create_task(_archive_task_to_memory(
                            memory=memory,
                            project_id=project_id,
                            user_message=user_message,
                            final_answer=d["final_answer"] or "",
                            tool_calls_detail=task_tool_calls,
                            run_id=run_id,
                            trace_id=trace_log.trace_id if trace_log else "",
                        ))

                # 历史由 _arun_with_fc_stream 内部统一写入，此处不再重复 add_message
                if trace_log:
                    report = getattr(agent, "last_context_report", {})
                    trace_log.done(answer=d["final_answer"], runtime={
                        "token_budget_used": report.get("token_budget_used", 0),
                        "token_budget_stop_reason": report.get("token_budget_stop_reason", "model_answer"),
                        "convergence_policy": report.get("convergence_policy", {}),
                        "convergence_evidence_compaction": report.get(
                            "convergence_evidence_compaction", {}
                        ),
                        "finalization_textual_tool_markup_blocked": report.get(
                            "finalization_textual_tool_markup_blocked", False
                        ),
                    })
                ok = await _ws_send(websocket, {
                    "event": "done",
                    "result": d["final_answer"],
                })
                if not ok:
                    return
                return

    except asyncio.CancelledError:
        disconnected = bool(disconnect_check and disconnect_check())
        is_paused = disconnected
        agent.last_run_checkpoint = {
            **getattr(agent, "last_run_checkpoint", {}),
            "run_id": run_id,
            "status": "paused" if is_paused else "stopped",
            "resume_available": is_paused,
            "stop_reason": (
                "websocket disconnected; send continue to resume"
                if is_paused else "agent task was canceled"
            ),
        }
        _write_task_summary(agent.last_run_checkpoint["status"])
        if run_id:
            _persist_run_checkpoint(
                run_id, run_owner, agent.last_run_checkpoint,
                status=RunStatus.PAUSED if is_paused else RunStatus.CANCELED,
                error="websocket disconnected" if is_paused else "agent task was canceled",
            )
            _record_audit(
                "run_paused" if is_paused else "run_canceled",
                run_id=run_id, session_id=session_id,
                reason="websocket disconnected" if is_paused else "agent task was canceled",
            )
        raise
    except AgentInterrupted:
        agent.last_run_checkpoint = {
            **getattr(agent, "last_run_checkpoint", {}),
            "run_id": run_id,
            "status": "stopped",
            "stop_reason": "user requested stop",
        }
        _write_task_summary("stopped")
        if run_id:
            try:
                get_run_store().transition(
                    run_id, RunStatus.CANCELED,
                    expected={RunStatus.RUNNING, RunStatus.WAITING_APPROVAL},
                    owner_id=run_owner, error="user requested stop",
                    metadata_patch={"checkpoint": agent.last_run_checkpoint},
                )
            except RunStateError:
                logger.warning("[RunState] Could not mark run %s canceled", run_id, exc_info=True)
            _record_audit(
                "run_canceled", run_id=run_id, session_id=session_id,
                reason="user requested stop",
            )
        await _ws_send(websocket, {
            "event": "stopped", "reason": "User requested stop",
        })
    except Exception as e:
        agent.last_run_checkpoint = {
            **getattr(agent, "last_run_checkpoint", {}),
            "run_id": run_id,
            "status": "failed",
            "last_error": f"{type(e).__name__}: {e}",
            "stop_reason": f"{type(e).__name__}: {e}",
        }
        _write_task_summary("failed")
        logger.exception("[AgentChat] Dev agent execution error")
        try:
            from app.services.agent_metrics import get_agent_metrics
            get_agent_metrics().record_run("error")
        except Exception:
            pass
        if run_id:
            try:
                get_run_store().transition(
                    run_id, RunStatus.FAILED,
                    expected={RunStatus.RUNNING, RunStatus.WAITING_APPROVAL},
                    owner_id=run_owner, error=f"{type(e).__name__}: {e}",
                    metadata_patch={"checkpoint": agent.last_run_checkpoint},
                )
            except RunStateError:
                logger.warning("[RunState] Could not mark run %s failed", run_id, exc_info=True)
            _record_audit(
                "run_failed", run_id=run_id, session_id=session_id,
                error=f"{type(e).__name__}: {e}",
            )
        if trace_log:
            trace_log.error(event_type="agent", message=f"Agent error: {type(e).__name__}: {e}")
        await _ws_send(websocket, {
            "event": "error", "message": f"Agent error: {type(e).__name__}: {e}",
        })
    finally:
        if 'previous_compaction_callback' in locals():
            agent.on_context_compacted = previous_compaction_callback
        reset_runtime(_runtime_token)


# ── WebSocket 端点 ──────────────────────────────────────

@router.websocket("/ws/chat")
async def agent_chat_ws(websocket: WebSocket):
    """Agent 对话 WebSocket — 流式双向通信。"""
    await websocket.accept()
    if not await require_ws_auth(websocket):
        return
    logger.info("[AgentChat] WebSocket connected")

    # 会话 id 来自前端（localStorage 持久化），跨连接复用 agent 历史与日志文件；
    # 旧前端未传时退化为按时间戳生成（等价于每次连接一个新会话）。
    session_id = websocket.query_params.get("session_id") or \
        datetime.now().strftime("%Y%m%d_%H%M%S")
    session = get_or_create(session_id)
    # 恢复历史会话：全新会话但磁盘上已有 trace → 重建对话历史，等 agent 创建时注入
    restore_history = None
    if session.agent is None:
        restore_history = reconstruct_history(session_id)
    if session.trace_log is None:
        trace_log = ChatTraceLogger(session_id=session_id)
        trace_log.start()  # 首次连接时写入会话开始边界（session_end 由 TTL 回收时 close 写入）
    else:
        trace_log = session.trace_log
    session.trace_log = trace_log

    llm: BaseAgentsLLM | None = None
    dev_agent: ReActAgent | None = session.agent
    review_mgr = session.review_mgr
    progress: ProgressRelay | None = session.progress
    prompt_builder = session.prompt_builder
    stop_requested = False
    run_task: asyncio.Task | None = None
    active_run = None
    connection_owner = uuid.uuid4().hex
    transport_disconnected = False
    # 兜底审核（run 结束后补推的 uml_review）与其原始 run 的映射。
    # 这些请求没有 agent 在 future 上阻塞，reject 时需要主循环代为开启修订轮。
    fallback_review_runs: dict[int, str] = {}
    source_dir = ""
    test_dir = ""
    project_file = ""
    _set_trace_bridge(trace_log)
    trace_hook_handler = _trace_hook_bridge
    push_trace_hook(trace_hook_handler)

    def _stop_check():
        return stop_requested

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type", "")

            # Status is an explicit transport command, not a natural-language
            # shortcut in the execution path.
            if msg_type == "task_status":
                checkpoint = getattr(dev_agent, "last_run_checkpoint", {}) if dev_agent else {}
                checkpoint = checkpoint or _latest_persisted_checkpoint(session_id)
                answer = _checkpoint_answer(checkpoint)
                await _ws_send(websocket, {
                    "event": "done",
                    "result": answer,
                    "checkpoint": checkpoint,
                })
                continue

            # ── 开始对话 ──
            if msg_type == "chat":
                user_message = msg.get("message", "")
                resume_record = None
                resume_checkpoint = {}
                if _is_resume_request(user_message):
                    resumable = _latest_resumable_run(session_id)
                    if resumable is not None:
                        resume_record, resume_checkpoint = resumable
                    else:
                        await _ws_send(websocket, {
                            "event": "done",
                            "result": "当前会话没有可恢复的未完成任务。",
                        })
                        continue
                requested_source = msg.get("source_dir") or (
                    resume_checkpoint.get("source_dir") or source_dir
                )
                requested_test = msg.get("test_dir") or (
                    resume_checkpoint.get("test_dir") or test_dir
                )
                requested_project = msg.get("project_file") or (
                    resume_checkpoint.get("project_file") or project_file
                )

                if not user_message:
                    await websocket.send_json({"event": "error", "message": "Empty message"})
                    continue

                validated = []
                for value, kind, label in (
                    (requested_source, "directory", "source_dir"),
                    (requested_test, "directory", "test_dir"),
                    (requested_project, "file", "project_file"),
                ):
                    normalized, error = validate_agent_workspace_path(value, kind=kind)
                    if error:
                        validated.append(f"{label}: {error}")
                    else:
                        validated.append(normalized)
                if any(item.startswith(("source_dir:", "test_dir:", "project_file:"))
                       for item in validated):
                    await websocket.send_json({
                        "event": "error",
                        "message": "Invalid workspace path: " + "; ".join(
                            item for item in validated if ": " in item
                        ),
                    })
                    continue
                source_dir, test_dir, project_file = validated
                effective_user_message = (
                    _resume_prompt(resume_checkpoint)
                    if resume_checkpoint else user_message
                )

                # 记录用户消息（trace）
                # One session uses one configured coding model.  Do not infer
                # model changes from a short follow-up: that makes behaviour
                # less predictable and breaks provider prompt-cache prefixes.
                if llm is None:
                    llm = BaseAgentsLLM.from_settings(temperature=0.3)
                    if dev_agent is not None:
                        dev_agent.llm = llm
                        subagent = dev_agent.tool_registry.get_tool("spawn_subagent")
                        if subagent is not None and hasattr(subagent, "llm"):
                            subagent.llm = llm

                stop_requested = False

                # ── 单 agent 承接所有消息：懒创建 + 跨轮复用 ──
                if dev_agent is None:
                    progress = ProgressRelay()
                    dev_agent, review_mgr, prompt_builder = await _create_dev_agent(
                        llm, source_dir, test_dir, project_file, effective_user_message,
                        progress=progress, restore_history=restore_history,
                        task_scope=session_id,
                    )
                    session.agent, session.review_mgr, session.progress = \
                        dev_agent, review_mgr, progress
                    session.prompt_builder = prompt_builder

                session.touch()

                # Bind the durable run before writing per-turn trace events.
                # This keeps user_message, prompt_context, model, tool, and
                # terminal events correlated to the same run_id.
                if run_task is not None and not run_task.done():
                    await websocket.send_json({
                        "event": "error",
                        "message": "Agent is still processing the previous request",
                    })
                    continue
                if not session.try_claim_run(connection_owner):
                    await websocket.send_json({
                        "event": "error",
                        "message": "This session is already running on another connection",
                    })
                    continue
                run = get_run_store().create(
                    kind="agent_chat",
                    session_id=session_id,
                    metadata={
                        "message": effective_user_message[:500],
                        "resume_of": resume_record.run_id if resume_record else "",
                        "source_dir": source_dir,
                        "test_dir": test_dir,
                        "project_file": project_file,
                    },
                    idempotency_key=(
                        f"{session_id}:{msg['request_id']}"
                        if msg.get("request_id") else ""
                    ),
                )
                if run.status != RunStatus.QUEUED.value:
                    session.release_run(connection_owner)
                    await _ws_send(websocket, {
                        "event": "error",
                        "message": "This request has already been accepted",
                        "run_id": run.run_id,
                    })
                    continue
                get_run_store().claim(run.run_id, connection_owner)
                if resume_record is not None:
                    old_status = resume_record.status
                    old_target = (
                        RunStatus.PAUSED.value
                        if old_status == RunStatus.RUNNING.value else old_status
                    )
                    consumed_checkpoint = dict(resume_checkpoint)
                    consumed_checkpoint.update({
                        "resume_consumed": True,
                        "resumed_by": run.run_id,
                    })
                    try:
                        get_run_store().transition(
                            resume_record.run_id, old_target,
                            expected={old_status},
                            metadata_patch={"checkpoint": consumed_checkpoint},
                        )
                    except RunStateError:
                        logger.warning(
                            "[RunState] Could not mark resumed run %s consumed",
                            resume_record.run_id, exc_info=True,
                        )
                active_run = run
                trace_log.set_run_id(run.run_id)
                trace_log.user_message(
                    user_message, project_file=project_file,
                    source_dir=source_dir, test_dir=test_dir,
                )
                trace_log.event(
                    "agent_model",
                    model=get_settings().deepseek_model,
                    policy="fixed_session_model",
                )
                _record_audit(
                    "run_started", run_id=run.run_id, session_id=session_id,
                    kind="agent_chat", project_file=project_file,
                )
                await _ws_send(websocket, {
                    "event": "run_started",
                    "run_id": run.run_id,
                    "status": RunStatus.RUNNING.value,
                })

                # 每轮按 live context 组装易变尾块（memo 化），追加到最后一条
                # user 消息末尾，最大化 system+history 前缀的 KV 缓存命中。
                context = ""
                if prompt_builder is not None:
                    context = await prompt_builder.build_context(
                        project_file, source_dir, test_dir, effective_user_message,
                    )
                    trace_log.event(
                        "prompt_context",
                        prompt_version=f"devagent-{prompt_builder.prompt_version}",
                        static_prompt=prompt_builder.static_prompt_report,
                        history_structure=_history_structure(dev_agent),
                        **prompt_builder.last_context_report,
                    )
                    from app.services.agent_metrics import get_agent_metrics
                    static_tokens = int(
                        (prompt_builder.static_prompt_report or {}).get("estimated_tokens", 0) or 0
                    )
                    dynamic_tokens = int(
                        (prompt_builder.last_context_report or {}).get("estimated_tokens", 0) or 0
                    )
                    get_agent_metrics().record_prompt(
                        static_tokens + dynamic_tokens,
                        prompt_version=f"devagent-{prompt_builder.prompt_version}",
                    )

                # _handle_dev 作为后台任务运行：审核工具会阻塞等待人类回复，
                # 收消息循环必须并发处理 review_response / stop 才能解阻塞。
                run_task = asyncio.create_task(_handle_dev(
                    dev_agent, review_mgr, effective_user_message, websocket, _stop_check,
                    trace_log=trace_log, project_file=project_file,
                    source_dir=source_dir, test_dir=test_dir,
                    progress=progress, context=context,
                    fallback_review_runs=fallback_review_runs,
                    run_id=run.run_id, run_owner=connection_owner,
                    resume_checkpoint=resume_checkpoint,
                    disconnect_check=lambda: transport_disconnected,
                    session_id=session_id,
                ))
                run_task.add_done_callback(_consume_task_exception)
                run_task.add_done_callback(
                    lambda _task: session.release_run(connection_owner)
                )

            # ── 停止对话 ──
            elif msg_type == "stop":
                stop_requested = True
                trace_log.error(event_type="user_stop", message="用户请求停止")
                await websocket.send_json({"event": "stopped", "reason": "User requested stop"})

            # ── 人工审核回复 ──
            elif msg_type == "review_response":
                logger.info("[AgentChat] review_response received: %s", raw[:200])
                review_id = msg.get("review_id", 0)
                # 新版协议：decision + feedback；旧版纯文本 response 仍兼容
                decision = msg.get("decision", "")
                if decision:
                    response = json.dumps({
                        "decision": decision,
                        "feedback": msg.get("feedback", ""),
                    }, ensure_ascii=False)
                else:
                    response = msg.get("response", "")
                if review_mgr:
                    resolved = review_mgr.resolve(review_id, response, session_id=session_id)
                    if not resolved:
                        # 待审核请求不存在（连接断开被清理/会话回收/重复回复）：
                        # 明确告知前端，避免用户以为审核已生效而 agent 实际没收到
                        logger.info("[AgentChat] Review %d 已失效，通知前端", review_id)
                        await websocket.send_json({
                            "event": "review_expired",
                            "review_id": review_id,
                        })
                        continue

                    # 接受时刷新 baseline：本轮后续设计修改的 before = 已接受态
                    if decision == "accept" and project_file:
                        try:
                            from app.services.file_service import load_project
                            review_mgr.baseline = [d.model_dump() for d in load_project(project_file).diagrams]
                        except Exception:
                            pass
                    trace_log.review_response(review_id=review_id, response=response)
                    logger.info("[AgentChat] Review %d resolved: %s", review_id, response[:80])

                    # ── 兜底审核：Agent 已结束，审核结果由编排层收口 ──
                    # accept 才把 waiting_approval 变为最终状态；reject 则把
                    # 用户反馈作为新一轮修订任务。此前绝不能宣称 completed。
                    if review_id in fallback_review_runs:
                        reviewed_run_id = fallback_review_runs.pop(review_id)
                        checkpoint = getattr(dev_agent, "last_run_checkpoint", {}) if dev_agent else {}
                        post_review_status = str(checkpoint.pop("post_review_status", "completed"))
                        checkpoint["review_status"] = "accepted" if decision == "accept" else "rejected"
                        if decision == "accept":
                            checkpoint["status"] = post_review_status
                            if dev_agent is not None:
                                dev_agent.last_run_checkpoint = checkpoint
                            resolved_run_status = (
                                RunStatus.SUCCEEDED
                                if post_review_status == "completed"
                                else RunStatus.FAILED
                            )
                            if reviewed_run_id:
                                try:
                                    get_run_store().transition(
                                        reviewed_run_id, resolved_run_status,
                                        expected={RunStatus.WAITING_APPROVAL},
                                        owner_id=connection_owner,
                                        metadata_patch={"checkpoint": checkpoint},
                                    )
                                except RunStateError:
                                    logger.warning(
                                        "[RunState] Could not finalize reviewed run %s",
                                        reviewed_run_id, exc_info=True,
                                    )
                            if post_review_status == "completed":
                                answer = "设计变更已通过审核，任务已完成。"
                            else:
                                answer = (
                                    "设计变更已通过审核；原任务未完整完成。"
                                    + (f"停止原因：{checkpoint.get('stop_reason')}" if checkpoint.get("stop_reason") else "")
                                )
                            trace_log.done(answer=answer)
                            await _ws_send(websocket, {
                                "event": "done",
                                "result": answer,
                                "checkpoint": checkpoint,
                            })
                            continue

                        checkpoint["status"] = "partial"
                        if dev_agent is not None:
                            dev_agent.last_run_checkpoint = checkpoint
                        if reviewed_run_id:
                            try:
                                get_run_store().transition(
                                    reviewed_run_id, RunStatus.FAILED,
                                    expected={RunStatus.WAITING_APPROVAL},
                                    owner_id=connection_owner,
                                    metadata_patch={"checkpoint": checkpoint},
                                )
                            except RunStateError:
                                logger.warning(
                                    "[RunState] Could not mark rejected review run %s partial",
                                    reviewed_run_id, exc_info=True,
                                )
                        if (
                            dev_agent is not None
                            and (run_task is None or run_task.done())
                        ):
                            feedback_text = msg.get("feedback", "") or msg.get("response", "")
                            followup = (
                                "用户拒绝了刚才的 UML 设计变更"
                                + (f"，反馈：{feedback_text}" if feedback_text else "")
                                + "。请据此修改设计文件，然后调用 submit_uml_review 重新提交审核。"
                            )
                            logger.info("[AgentChat] 兜底审核被拒，开启修订轮: %s", feedback_text[:80])
                            stop_requested = False
                            followup_context = ""
                            if prompt_builder is not None:
                                followup_context = await prompt_builder.build_context(
                                    project_file, source_dir, test_dir, followup,
                                )
                            parent_run_id = reviewed_run_id
                            followup_run = get_run_store().create(
                                kind="agent_chat",
                                session_id=session_id,
                                metadata={
                                    "message": followup[:500],
                                    "source_dir": source_dir,
                                    "test_dir": test_dir,
                                    "project_file": project_file,
                                    "parent_run_id": parent_run_id,
                                },
                            )
                            get_run_store().claim(followup_run.run_id, connection_owner)
                            active_run = followup_run
                            trace_log.set_run_id(followup_run.run_id)
                            trace_log.user_message(
                                followup, project_file=project_file,
                                source_dir=source_dir, test_dir=test_dir,
                            )
                            trace_log.event(
                                "agent_model",
                                model=get_settings().deepseek_model,
                                policy="fixed_session_model",
                            )
                            if prompt_builder is not None:
                                trace_log.event(
                                    "prompt_context",
                                    prompt_version=f"devagent-{prompt_builder.prompt_version}",
                                    static_prompt=prompt_builder.static_prompt_report,
                                    history_structure=_history_structure(dev_agent),
                                    **prompt_builder.last_context_report,
                                )
                            _record_audit(
                                "run_started", run_id=followup_run.run_id, session_id=session_id,
                                kind="agent_chat", project_file=project_file,
                                parent_run_id=parent_run_id,
                            )
                            await _ws_send(websocket, {
                                "event": "run_started",
                                "run_id": followup_run.run_id,
                                "status": RunStatus.RUNNING.value,
                            })
                            run_task = asyncio.create_task(_handle_dev(
                                dev_agent, review_mgr, followup, websocket, _stop_check,
                                trace_log=trace_log, project_file=project_file,
                                source_dir=source_dir, test_dir=test_dir,
                                progress=progress, context=followup_context,
                                fallback_review_runs=fallback_review_runs,
                                run_id=followup_run.run_id, run_owner=connection_owner,
                                disconnect_check=lambda: transport_disconnected,
                                session_id=session_id,
                            ))
                            run_task.add_done_callback(_consume_task_exception)
                            run_task.add_done_callback(
                                lambda _task: session.release_run(connection_owner)
                            )

            # ── 心跳 ──
            elif msg_type == "ping":
                await _ws_send(websocket, {"event": "pong"})

            else:
                await websocket.send_json({
                    "event": "error", "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        transport_disconnected = True
        stop_requested = True
        logger.info("[AgentChat] WebSocket disconnected")
    except RuntimeError as e:
        # 前端断开时 Starlette 会在 receive_text()/send_json() 抛这个错误；
        # 识别为正常断开，优雅收尾，不当作服务端错误处理。
        if "WebSocket is not connected" in str(e) or "not connected" in str(e):
            transport_disconnected = True
            stop_requested = True
            logger.info("[AgentChat] WebSocket closed (client disconnected)")
        else:
            logger.exception("[AgentChat] Unexpected error")
            trace_log.error(event_type="server", message=f"Server error: {e}")
    except Exception as e:
        logger.exception("[AgentChat] Unexpected error")
        trace_log.error(event_type="server", message=f"Server error: {e}")
        try:
            await websocket.send_json({"event": "error", "message": f"Server error: {e}"})
        except Exception:
            pass
    finally:
        # 取消未完成的 agent 后台任务，避免泄漏并确保 trace bridge 清理前任务已停。
        if run_task is not None and not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
        # 连接断开 → 该连接产生的待审核请求一并作废：被 cancel 的工具协程
        # 不会消费 future，若不清理，重连后补发的 review_response 会 resolve
        # 到无主 future 上（用户以为生效，实际无人继续）。
        # 仅当本连接跑过任务时才清理（review 只能由本连接的 run 产生），
        # 避免同 session 的其他空闲连接误杀进行中的审核。
        if run_task is not None and review_mgr is not None:
            review_mgr.reset()
        # 日志器不在此 close — 由 AgentSession 回收时统一 finalize，
        # 从而同一会话跨连接持续追加到同一 trace_*.jsonl。
        session.touch()
        session.release_run(connection_owner)
        pop_trace_hook(trace_hook_handler)
        _set_trace_bridge(None)
