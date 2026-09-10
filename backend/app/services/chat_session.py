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
        {"event": "request_review", "review_id": 0, "review_type": "shell_command", "title": "...", "question": "..."}
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
from fastapi import WebSocket, WebSocketDisconnect
from app.core.security import validate_agent_workspace_path
from backend.config import get_settings

from app.agent_base.assembly import create_dev_agent
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
from app.services.run_state import RunStateError, RunStatus, get_run_store
from app.services.audit_log import record_audit as _record_audit
from app.services.run_lifecycle import RunLifecycle
from app.services.session_compression import SessionContextCompressor
from app.runtime.agent_runtime import SessionBusyError

logger = logging.getLogger(__name__)

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
                request_context=kwargs.get("request_context"),
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


from app.services.agent_execution import (
    _archive_task_to_memory,
    _should_archive_task_memory,
    handle_agent_execution,
)


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


def _latest_persisted_checkpoint(session_id: str, *, store_factory=None) -> dict:
    """Read the newest run checkpoint for reconnects without invoking an LLM."""
    store_factory = store_factory or get_run_store
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


def _latest_resumable_run(session_id: str, *, store_factory=None):
    """Return the newest non-terminal run that has a resumable checkpoint."""
    store_factory = store_factory or get_run_store
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

        async def _start_run(message: str, *, parent_run_id: str = "",
                             resume_record=None, resume_checkpoint=None, request_id=""):
            nonlocal run_task, active_run
            lifecycle = RunLifecycle(get_run_store(), agent_runtime)
            try:
                run = lifecycle.start(
                    session_id=session_id, owner=connection_owner,
                    metadata={
                        "message": message[:500], "parent_run_id": parent_run_id,
                        "resume_of": resume_record.run_id if resume_record else "",
                        "source_dir": source_dir, "test_dir": test_dir,
                        "project_file": project_file,
                    },
                    idempotency_key=f"{session_id}:{request_id}" if request_id else "",
                )
            except (SessionBusyError, RunStateError) as exc:
                await _ws_send(websocket, {"event": "error", "message": str(exc)})
                return
            active_run = run
            try:
                if resume_record is not None:
                    old_status = resume_record.status
                    consumed = dict(resume_checkpoint or {})
                    consumed.update(resume_consumed=True, resumed_by=run.run_id)
                    get_run_store().transition(
                        resume_record.run_id,
                        RunStatus.PAUSED if old_status == RunStatus.RUNNING.value else old_status,
                        expected={old_status},
                        owner_id=resume_record.owner_id if old_status == RunStatus.RUNNING.value else "",
                        metadata_patch={"checkpoint": consumed},
                    )
                trace_log.set_run_id(run.run_id)
                trace_log.user_message(
                    message, project_file=project_file,
                    source_dir=source_dir, test_dir=test_dir,
                )
                trace_log.event("agent_model", model=get_settings().llm_model_id,
                                policy="fixed_session_model")
                _record_audit("run_started", run_id=run.run_id, session_id=session_id,
                              kind="agent_chat", project_file=project_file,
                              parent_run_id=parent_run_id)
                if not await _ws_send(websocket, {
                    "event": "run_started", "run_id": run.run_id,
                    "status": RunStatus.RUNNING.value,
                }):
                    raise ConnectionError("Transport disconnected before execution")
                context = ""
                if prompt_builder is not None:
                    context = await prompt_builder.build_context(
                        project_file, source_dir, test_dir, message,
                    )
                    trace_log.event(
                        "prompt_context",
                        prompt_version=f"devagent-{prompt_builder.prompt_version}",
                        static_prompt=prompt_builder.static_prompt_report,
                        history_structure=_history_structure(dev_agent),
                        **prompt_builder.last_context_report,
                    )
                    from app.services.agent_metrics import get_agent_metrics
                    get_agent_metrics().record_prompt(
                        int(prompt_builder.static_prompt_report.get("estimated_tokens", 0) or 0)
                        + int(prompt_builder.last_context_report.get("estimated_tokens", 0) or 0),
                        prompt_version=f"devagent-{prompt_builder.prompt_version}",
                    )
                async def execute():
                    try:
                        await handle_agent_execution(
                            dev_agent, review_mgr, message,
                            lambda payload: _ws_send(websocket, payload), _stop_check,
                            trace_log=trace_log, project_file=project_file,
                            source_dir=source_dir, test_dir=test_dir,
                            progress=progress, context=context,
                            fallback_review_runs=fallback_review_runs,
                            run_id=run.run_id, run_owner=connection_owner,
                            resume_checkpoint=resume_checkpoint,
                            disconnect_check=lambda: transport_disconnected,
                            session_id=session_id,
                        )
                    finally:
                        agent_runtime.release_run(session_id, connection_owner)
                run_task = asyncio.create_task(execute())
                run_task.add_done_callback(_consume_task_exception)
            except BaseException as exc:
                try:
                    get_run_store().transition(
                        run.run_id, RunStatus.CANCELED if isinstance(exc, asyncio.CancelledError)
                        else RunStatus.FAILED,
                        expected={RunStatus.RUNNING}, owner_id=connection_owner,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    agent_runtime.release_run(session_id, connection_owner)
                raise

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
                    if run_task is not None and not run_task.done():
                        await _ws_send(websocket, {
                            "event": "error", "message": "Agent is still processing the previous request",
                        })
                        continue
                    if session.run_owner not in (None, connection_owner):
                        await _ws_send(websocket, {
                            "event": "error", "message": "This session is already running on another connection",
                        })
                        continue
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

                    # Session compression belongs between turns. The active
                    # ReAct loop only compacts tool history; user/assistant
                    # conversation is summarized here before the next turn.
                    if dev_agent is not None and llm is not None:
                        settings = get_settings()
                        compression = SessionContextCompressor(
                            llm,
                            model=settings.agent_session_compression_model,
                            hard_limit_tokens=settings.agent_context_hard_limit_tokens,
                            trigger_ratio=settings.agent_session_compression_trigger_ratio,
                            max_output_tokens=settings.agent_session_compression_max_tokens,
                        )
                        compression_result = await compression.maybe_compress(
                            dev_agent,
                            session_id=session_id,
                            trace_log=trace_log,
                        )
                        if compression_result.error:
                            trace_log.event(
                                "session_context_compression_error",
                                session_id=session_id,
                                estimated_session_tokens=compression_result.estimated_tokens,
                                error=compression_result.error,
                            )

                    await _start_run(
                        effective_user_message, resume_record=resume_record,
                        resume_checkpoint=resume_checkpoint, request_id=msg.get("request_id", ""),
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
                        try:
                            parsed_response = json.loads(response)
                        except (ValueError, TypeError):
                            parsed_response = None
                        if isinstance(parsed_response, dict):
                            decision = parsed_response.get("decision", "")
                        elif str(response).strip().lower() in {"accept", "approve", "approved", "批准", "同意", "接受"}:
                            decision = "accept"
                        else:
                            decision = "reject"
                    if review_mgr:
                        reviewed_checkpoint = None
                        if review_id in fallback_review_runs:
                            if decision not in {"accept", "reject"}:
                                await _ws_send(websocket, {"event": "error", "message": "Invalid review decision"})
                                continue
                            # Validate the live request before committing its durable resolution.
                            if not any(item["id"] == review_id for item in review_mgr.get_pending()):
                                await _ws_send(websocket, {"event": "review_expired", "review_id": review_id})
                                continue
                            try:
                                reviewed_checkpoint = RunLifecycle(
                                    get_run_store(), agent_runtime,
                                ).resolve_review(
                                    run_id=fallback_review_runs[review_id],
                                    owner=connection_owner, accepted=decision == "accept",
                                )
                            except RunStateError as exc:
                                await _ws_send(websocket, {"event": "error", "message": str(exc)})
                                continue
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
                        if decision == "accept" and project_file and (
                            reviewed_checkpoint is None
                            or dev_agent.last_run_checkpoint.get("run_id") == reviewed_checkpoint.get("run_id")
                        ):
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
                            checkpoint = reviewed_checkpoint
                            _record_audit(
                                "review_accepted" if decision == "accept" else "review_rejected",
                                run_id=reviewed_run_id, session_id=session_id,
                            )
                            if dev_agent is not None and (
                                dev_agent.last_run_checkpoint.get("run_id") == reviewed_run_id
                            ):
                                dev_agent.last_run_checkpoint = checkpoint
                            if dev_agent is not None:
                                dev_agent.append_task_summary(checkpoint["task_summary"])
                            if decision == "accept":
                                status = checkpoint["status"]
                                memory = getattr(dev_agent, "memory_provider", None)
                                reviewed_project = checkpoint.get("project_file") or ""
                                if memory is not None and reviewed_project and _should_archive_task_memory(
                                    status, checkpoint.get("tool_calls", []), checkpoint,
                                ):
                                    asyncio.create_task(_archive_task_to_memory(
                                        memory=memory,
                                        project_id=os.path.splitext(os.path.basename(reviewed_project))[0],
                                        user_message=checkpoint.get("request_summary", ""),
                                        final_answer=(checkpoint.get("outcome") or {}).get("final_answer", ""),
                                        tool_calls_detail=checkpoint.get("tool_calls", []),
                                        run_id=reviewed_run_id, trace_id=trace_log.trace_id,
                                    ))
                                answer = (
                                    "设计变更已通过审核，任务已完成。"
                                    if status == "completed"
                                    else "设计变更已通过审核；原任务未完整完成。"
                                    + str(checkpoint.get("stop_reason") or "")
                                )
                                await _ws_send(websocket, {
                                    "event": "done", "run_id": reviewed_run_id,
                                    "result": answer, "checkpoint": checkpoint,
                                })
                                continue
                            if dev_agent is not None and (run_task is None or run_task.done()):
                                feedback_text = msg.get("feedback", "") or msg.get("response", "")
                                followup = (
                                    "用户拒绝了刚才的 UML 设计变更"
                                    + (f"，反馈：{feedback_text}" if feedback_text else "")
                                    + "。请据此修改设计文件，然后调用 submit_uml_review 重新提交审核。"
                                )
                                stop_requested = False
                                await _start_run(followup, parent_run_id=reviewed_run_id)

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
                for reviewed_run_id in fallback_review_runs.values():
                    try:
                        checkpoint = RunLifecycle(get_run_store(), agent_runtime).pause_review(
                            run_id=reviewed_run_id, owner=connection_owner,
                        )
                        if dev_agent is not None and dev_agent.last_run_checkpoint.get("run_id") == reviewed_run_id:
                            dev_agent.last_run_checkpoint = checkpoint
                    except RunStateError:
                        logger.warning("[RunState] Could not pause pending review %s", reviewed_run_id, exc_info=True)
                review_mgr.reset()
            # 日志器不在此 close — 由 AgentSession 回收时统一 finalize，
            # 从而同一会话跨连接持续追加到同一 trace_*.jsonl。
            session.touch()
            agent_runtime.release_run(session_id, connection_owner)
            pop_trace_hook(trace_hook_handler)
            _set_trace_bridge(None)
