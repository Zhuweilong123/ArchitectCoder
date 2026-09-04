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
from fastapi import WebSocket, WebSocketDisconnect
from app.core.security import validate_agent_workspace_path
from backend.config import get_settings

from app.agent_base.assembly import (
    DevPromptBuilder,
    create_dev_agent,
    enabled_tools_context,
)
from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.my_tools.conversation_tools import (
    ProgressRelay,
)
from app.trace.tracing import (
    TraceSessionRequest,
    TraceSink,
    current_trace_spans,
    load_trace,
    pop_trace_hook,
    push_trace_hook,
)
from app.runtime.agent_runtime import get_or_create, runtime as agent_runtime
from app.services.run_state import (
    RunStateError, RunStatus, get_run_store, run_status_for_completion,
)
from app.services.audit_log import get_audit_logger

logger = logging.getLogger(__name__)

def _record_audit(event_type: str, *, run_id: str, session_id: str, **payload) -> None:
    """Keep audit failures observable without breaking the Agent response."""
    try:
        get_audit_logger().record(
            event_type, run_id=run_id, session_id=session_id, **payload,
        )
    except Exception:
        logger.exception("[Audit] Could not persist %s for run %s", event_type, run_id)



def _trace_hook_bridge(kind: str, *args, **kwargs):
    """全局 LLM trace hook 处理器 — 转发到当前会话的 ChatTraceLogger。

    由 llm.py 的 _trace_hook() 调用，签名: (kind, **kwargs)。
    kind: 'llm_request' | 'llm_response'
    """
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



_TRACE_BRIDGE: contextvars.ContextVar[TraceSink | None] = contextvars.ContextVar(
    "agent_chat_trace_bridge", default=None,
)


def _set_trace_bridge(tracer: TraceSink | None):
    _TRACE_BRIDGE.set(tracer)


from app.agent_base.execution_summary import build_task_execution_summary
from app.services.agent_execution import (
    _archive_task_to_memory,
    _persist_run_checkpoint,
    _should_archive_task_memory,
    _terminal_checkpoint_status,
    _todo_progress_state,
    handle_agent_execution,
)

# Compatibility exports for existing application and test callers.  New
# entry points must import these capabilities from agent_base, never from this
# WebSocket transport module.
_create_dev_agent = create_dev_agent
_enabled_tools_context = enabled_tools_context
_build_task_execution_summary = build_task_execution_summary


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


def _latest_persisted_checkpoint(session_id: str, *, store_factory=get_run_store) -> dict:
    """Read the newest run checkpoint for reconnects without invoking an LLM."""
    if not session_id:
        return {}
    try:
        for record in store_factory().list(limit=20, session_id=session_id):
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
    return _resume_supplement(message) is not None


def _resume_supplement(message: str) -> str | None:
    """Return optional guidance attached to an explicit resume command.

    A delimiter is required for the extended form so ordinary messages such
    as ``继续一下`` are not accidentally treated as recovery requests.
    """
    text = (message or "").strip()
    normalized = text.lower()
    if normalized in _RESUME_REQUESTS:
        return ""
    for command in sorted(_RESUME_REQUESTS, key=len, reverse=True):
        if not normalized.startswith(command):
            continue
        suffix = text[len(command):]
        if suffix and suffix[0] in " \u3000:,\uff0c\uff1a":
            supplement = suffix[1:].strip()
            if supplement:
                return supplement
    return None


def _latest_resumable_run(session_id: str, *, store_factory=get_run_store):
    """Return the newest non-terminal run that has a resumable checkpoint."""
    if not session_id:
        return None
    resumable_statuses = {
        RunStatus.RUNNING.value,
        RunStatus.PAUSED.value,
        RunStatus.ORPHANED.value,
    }
    try:
        for record in store_factory().list(limit=50, session_id=session_id):
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


def _resume_prompt(checkpoint: dict, supplement: str = "") -> str:
    """Turn a persisted checkpoint into an explicit continuation request."""
    original = str(checkpoint.get("request_summary") or checkpoint.get("message") or "")[:500]
    completed = checkpoint.get("completed_items") or []
    pending = checkpoint.get("pending_items") or []
    verification = checkpoint.get("verification") or []
    last_step = checkpoint.get("last_step") or ""
    prompt = (
        "continue the previous unfinished task. Original request: " + original
        + ". Read the current files and existing changes first, skip completed steps, "
        "and continue from the pending step; do not treat this as a new task."
        + (" Completed: " + "; ".join(map(str, completed[-16:])) + "." if completed else "")
        + (" Pending: " + "; ".join(map(str, pending[-16:])) + "." if pending else "")
        + (" Last step: " + str(last_step) + "." if last_step else "")
        + (" Verification: " + "; ".join(map(str, verification[-16:])) + "." if verification else "")
    )
    if supplement:
        prompt += " User supplement for this continuation: " + str(supplement)[:500] + "."
    return prompt[:1800]











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




class ChatSessionCoordinator:
    """Coordinate one agent chat session independently from the WebSocket adapter."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def run(self) -> None:
        websocket = self.websocket
        # 会话 id 来自前端（localStorage 持久化），跨连接复用 agent 历史与日志文件；
        # 旧前端未传时退化为按时间戳生成（等价于每次连接一个新会话）。
        session_id = websocket.query_params.get("session_id") or \
            datetime.now().strftime("%Y%m%d_%H%M%S")
        session = get_or_create(session_id)
        # 恢复历史会话：全新会话但磁盘上已有 trace → 重建对话历史，等 agent 创建时注入
        restore_history = None
        trace_provider = None
        if session.agent is None or session.trace_log is None:
            trace_provider = load_trace(settings=get_settings())
        if session.agent is None:
            restore_history = trace_provider.query().reconstruct_history(session_id)
        if session.trace_log is None:
            assert trace_provider is not None
            trace_log = trace_provider.create(TraceSessionRequest(session_id=session_id))
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
                    resume_supplement = ""
                    if _is_resume_request(user_message):
                        resume_supplement = _resume_supplement(user_message) or ""
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
                        _resume_prompt(resume_checkpoint, resume_supplement)
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
                        dev_agent, review_mgr, prompt_builder = await create_dev_agent(
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
                    if not agent_runtime.try_claim_run(session_id, connection_owner):
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
                        agent_runtime.release_run(session_id, connection_owner)
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
                                # Paused/orphaned runs no longer own a worker
                                # lease; only a still-running run has an owner.
                                owner_id=(resume_record.owner_id if old_status == RunStatus.RUNNING.value else ""),
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

    # Agent execution runs as a background task: review tools can wait for a human
                    # 收消息循环必须并发处理 review_response / stop 才能解阻塞。
                    run_task = asyncio.create_task(handle_agent_execution(
                        dev_agent, review_mgr, effective_user_message,
                        lambda payload: _ws_send(websocket, payload), _stop_check,
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
                        lambda _task: agent_runtime.release_run(session_id, connection_owner)
                    )

                # ── 停止对话 ──
                elif msg_type == "stop":
                    stop_requested = True
                    trace_log.error(event_type="user_stop", message="用户请求停止")
                    # Cancel the background task as well as setting the hook flag.
                    # This is necessary when the Agent is waiting for a review
                    # future; otherwise it cannot observe the stop hook and the
                    # session remains locked until a review response arrives.
                    if run_task is not None and not run_task.done():
                        run_task.cancel()
                        try:
                            await run_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            logger.warning(
                                "[AgentChat] Stopped run raised during cancellation",
                                exc_info=True,
                            )
                        await websocket.send_json({
                            "event": "stopped",
                            "reason": "User requested stop",
                            "status": "paused",
                            "resume_available": True,
                        })
                    else:
                        await websocket.send_json({
                            "event": "stopped",
                            "reason": "User requested stop",
                            "status": "paused",
                            "resume_available": True,
                        })

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
                                resolved_run_status = run_status_for_completion(post_review_status)
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
                                        reviewed_run_id, RunStatus.PARTIAL,
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
                                run_task = asyncio.create_task(handle_agent_execution(
                                    dev_agent, review_mgr, followup,
                                    lambda payload: _ws_send(websocket, payload), _stop_check,
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
                                    lambda _task: agent_runtime.release_run(session_id, connection_owner)
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
            agent_runtime.release_run(session_id, connection_owner)
            pop_trace_hook(trace_hook_handler)
            _set_trace_bridge(None)
