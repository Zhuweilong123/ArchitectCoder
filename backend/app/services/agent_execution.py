"""Transport-neutral Agent execution coordinator.

The execution service owns one Agent run and reports domain events through an
injected async sender. WebSocket is only one possible transport adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from backend.config import get_settings

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.assembly import enabled_tools_context
from app.agent_base.core.exceptions import AgentInterrupted
from app.agent_base.core.hooks import (
    AgentRuntime,
    get_runtime,
    reset_runtime,
    set_runtime,
)
from app.agent_base.core.memory import MemoryArchiveRequest, MemoryPort
from app.agent_base.core.orchestration import (
    OrchestrationRequest,
    apply_runtime_directives,
    exclude_tools,
    load_orchestrator,
)
from app.agent_base.execution_summary import build_task_execution_summary
from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
from app.agent_base.tools.my_tools.subagent_tool import SpawnSubagentTool
from app.services.audit_log import get_audit_logger
from app.services.run_state import (
    RunStateError,
    RunStatus,
    get_run_store,
    run_status_for_completion,
)
from app.trace.tracing import TraceSink

logger = logging.getLogger(__name__)

_TASK_BIND_TIMEOUT_SECONDS = 5.0
_REVIEW_BASELINE_TIMEOUT_SECONDS = 5.0


def _record_audit(event_type: str, *, run_id: str, session_id: str, **payload) -> None:
    try:
        get_audit_logger().record(
            event_type, run_id=run_id, session_id=session_id, **payload,
        )
    except Exception:
        logger.exception("[Audit] Could not persist %s for run %s", event_type, run_id)


def _todo_progress_state() -> dict:
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


def _persist_run_checkpoint(
    run_id: str,
    owner_id: str,
    checkpoint: dict,
    status: str | RunStatus = RunStatus.RUNNING,
    error: str = "",
) -> None:
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


def _terminal_checkpoint_status(
    final_answer: str, todos: list[dict],
) -> tuple[str, str | None]:
    answer = (final_answer or "").lower()
    if "token 预算" in answer:
        return "budget_exceeded", "token budget exceeded"
    if "时间预算" in answer or "llm 调用超过时间" in answer:
        return "timed_out", "time budget exceeded"
    if "task is not complete" in answer:
        return "partial", "required task plan is incomplete"
    if any(
        isinstance(todo, dict) and todo.get("status") != "completed"
        for todo in todos
    ):
        return "partial", "task checklist has pending items"
    return "completed", None


async def _archive_task_to_memory(
    memory: MemoryPort,
    project_id: str,
    user_message: str,
    final_answer: str,
    tool_calls_detail: list[dict],
    run_id: str = "",
    trace_id: str = "",
) -> None:
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


async def _create_task_execution_async(
    *,
    scope: str,
    run_id: str,
    owner: str,
    subject: str,
    description: str,
):
    """Create the optional durable task binding off the Agent event loop.

    Task persistence is an execution aid, not part of the response-critical
    Agent path.  File-system stalls or an import-time lock must not prevent
    the main Agent from reaching its first LLM call.
    """
    def _bind():
        logger.info(
            "[AgentExecution] task binding worker started run=%s scope=%s",
            run_id,
            scope,
        )
        from app.agent_base.tools.task_system import create_task_execution

        logger.info("[AgentExecution] task_system imported run=%s", run_id)
        binding = create_task_execution(
            scope=scope,
            run_id=run_id,
            owner=owner,
            subject=subject,
            description=description,
        )
        logger.info(
            "[AgentExecution] task binding worker completed run=%s task=%s",
            run_id,
            binding.task_id,
        )
        return binding

    logger.info("[AgentExecution] scheduling task binding run=%s", run_id)
    return await asyncio.wait_for(
        asyncio.to_thread(_bind),
        timeout=_TASK_BIND_TIMEOUT_SECONDS,
    )


async def _load_review_baseline_async(project_file: str):
    """Read the review baseline without blocking the Agent event loop."""
    def _load():
        logger.info("[AgentExecution] review baseline worker started")
        if not os.path.isfile(project_file):
            logger.info("[AgentExecution] review baseline file is unavailable")
            return None
        from app.services.file_service import load_project

        baseline = [
            diagram.model_dump()
            for diagram in load_project(project_file).diagrams
        ]
        logger.info(
            "[AgentExecution] review baseline worker completed diagrams=%d",
            len(baseline),
        )
        return baseline

    return await asyncio.wait_for(
        asyncio.to_thread(_load),
        timeout=_REVIEW_BASELINE_TIMEOUT_SECONDS,
    )


async def handle_agent_execution(
    agent: ReActAgent,
    review_mgr,
    user_message: str,
    send: Callable[[dict], Awaitable[bool]],
    stop_check,
    trace_log: TraceSink | None = None,
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

    logger.info(
        "[AgentExecution] started run=%s session=%s",
        run_id,
        session_id,
    )

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
    logger.info("[AgentExecution] initial checkpoint persisted run=%s", run_id)

    async def _on_progress(ev: dict):
        """将 ProgressRelay 的 design_element / review 事件转发为 WebSocket 消息。"""
        nonlocal uml_review_seen
        if ev.get("event") == "design_element":
            await send( {
                "event": "design_element",
                "type": ev.get("type", ""),
                "data": ev.get("data", ""),
            })
        elif ev.get("event") == "review_timeout":
            await send( {
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
                await send( {
                    "event": "uml_review",
                    "review_id": ev.get("review_id", 0),
                    "title": ev.get("title", ""),
                    "diagrams": metadata.get("diagrams", []),
                    "changed_diagrams": metadata.get("changed_diagrams"),
                    "original_diagrams": metadata.get("original_diagrams"),
                })
            else:
                await send( {
                    "event": "request_review",
                    "review_id": ev.get("review_id", 0),
                    "review_type": review_type,
                    "title": ev.get("title", ""),
                    "content": ev.get("content", ""),
                    "question": ev.get("question", ""),
                })

    if progress:
        logger.info("[AgentExecution] registering progress callback run=%s", run_id)
        progress.on_progress(_on_progress)
        logger.info("[AgentExecution] progress callback registered run=%s", run_id)

    # 捕获本任务的 before 快照（框架负责 before/after，模型只负责改设计）。
    # 存在 review_mgr 上（工具与 review_response 处理共享，可随 accept 刷新）。
    logger.info(
        "[AgentExecution] checking review baseline run=%s has_review=%s has_project=%s",
        run_id,
        review_mgr is not None,
        bool(project_file),
    )
    if review_mgr is not None and project_file:
        logger.info("[AgentExecution] loading review baseline run=%s", run_id)
        try:
            review_mgr.baseline = await _load_review_baseline_async(project_file)
            logger.info(
                "[AgentExecution] review baseline loaded run=%s available=%s",
                run_id,
                review_mgr.baseline is not None,
            )
        except asyncio.TimeoutError:
            review_mgr.baseline = None
            logger.error(
                "[AgentExecution] review baseline timed out after %.1fs; continuing run=%s",
                _REVIEW_BASELINE_TIMEOUT_SECONDS,
                run_id,
            )
        except Exception:
            review_mgr.baseline = None
            logger.warning("[AgentExecution] review baseline unavailable run=%s", run_id, exc_info=True)

    logger.info("[AgentExecution] installing runtime context run=%s", run_id)
    _runtime_token = set_runtime(AgentRuntime(
        stop_check=stop_check,
    ))
    logger.info("[AgentExecution] runtime context installed run=%s", run_id)
    task_binding = None
    if run_id:
        try:
            task_binding = await _create_task_execution_async(
                scope=session_id or project_file or "default",
                run_id=run_id,
                owner=f"run:{run_id}",
                subject=user_message,
                description="Durable task state for one DevAgent execution.",
            )
            logger.info(
                "[AgentExecution] task binding ready run=%s task=%s",
                run_id,
                task_binding.task_id,
            )
            agent.last_run_checkpoint["task_id"] = task_binding.task_id
            _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
            if trace_log:
                trace_log.event(
                    "task_binding",
                    task_id=task_binding.task_id,
                    status="bound",
                )
        except asyncio.TimeoutError:
            logger.error(
                "[TaskSystem] task binding timed out after %.1fs; continuing without binding run=%s",
                _TASK_BIND_TIMEOUT_SECONDS,
                run_id,
            )
        except Exception:
            # Task persistence is an execution aid. A store failure must not
            # turn an otherwise usable chat run into a false tool failure.
            logger.warning("[TaskSystem] Could not bind run %s", run_id, exc_info=True)
    task_tool_calls: list[dict] = []
    task_summary_written = False

    def _sync_task_execution() -> None:
        if task_binding is None:
            return
        try:
            task_binding.sync(
                todos=list(get_runtime().todos or []),
                checkpoint=agent.last_run_checkpoint,
            )
        except Exception:
            logger.warning(
                "[TaskSystem] Could not sync task %s",
                task_binding.task_id,
                exc_info=True,
            )

    def _write_task_summary(status: str) -> None:
        """Persist one bounded summary for every terminal execution path."""
        nonlocal task_summary_written
        if task_summary_written:
            return
        task_summary = build_task_execution_summary(
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
        if task_binding is not None:
            try:
                task_binding.finalize(
                    status,
                    checkpoint=agent.last_run_checkpoint,
                )
            except Exception:
                logger.warning(
                    "[TaskSystem] Could not finalize task %s as %s",
                    task_binding.task_id,
                    status,
                    exc_info=True,
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
            context, enabled_tools_context(),
        ]))
        logger.info("[AgentExecution] loading orchestrator run=%s", run_id)
        orchestration_settings = get_settings()
        orchestrator = load_orchestrator(
            llm=agent.llm,
            settings=orchestration_settings,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
            explorer_factory=SpawnSubagentTool,
        )
        logger.info(
            "[AgentExecution] orchestrator loaded run=%s type=%s",
            run_id,
            type(orchestrator).__name__,
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
        logger.info(
            "[AgentExecution] entering agent stream run=%s phase=%s",
            run_id,
            orchestration_result.phase,
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
            # Planner/explorer usage is tracked as orchestration overhead and
            # must remain separate from the main Agent's per-task budget.
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
            _sync_task_execution()

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

            ok = await send( {
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
                            await send( {
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
                    "task_id": task_binding.task_id if task_binding else "",
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
                    await send( {
                        "event": "awaiting_review",
                        "run_id": run_id,
                        "checkpoint": agent.last_run_checkpoint,
                    })
                    return

                run_status = run_status_for_completion(terminal_status)
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
                ok = await send( {
                    "event": "done",
                    "result": d["final_answer"],
                })
                if not ok:
                    return
                return

    except asyncio.CancelledError:
        disconnected = bool(disconnect_check and disconnect_check())
        user_stopped = bool(stop_check())
        is_paused = disconnected or user_stopped
        agent.last_run_checkpoint = {
            **getattr(agent, "last_run_checkpoint", {}),
            "run_id": run_id,
            "status": "paused" if is_paused else "stopped",
            "resume_available": is_paused,
            "stop_reason": (
                "websocket disconnected; send continue to resume"
                if disconnected else (
                    "user requested stop; send continue to resume"
                    if user_stopped else "agent task was canceled"
                )
            ),
        }
        _write_task_summary(agent.last_run_checkpoint["status"])
        if run_id:
            _persist_run_checkpoint(
                run_id, run_owner, agent.last_run_checkpoint,
                status=RunStatus.PAUSED if is_paused else RunStatus.CANCELED,
                error=(
                    "websocket disconnected" if disconnected else (
                        "user requested stop" if user_stopped
                        else "agent task was canceled"
                    )
                ),
            )
            _record_audit(
                "run_paused" if is_paused else "run_canceled",
                run_id=run_id, session_id=session_id,
                reason=(
                    "websocket disconnected" if disconnected else (
                        "user requested stop" if user_stopped
                        else "agent task was canceled"
                    )
                ),
            )
        raise
    except AgentInterrupted:
        # An explicit user stop is a recoverable pause.  A true task cancel
        # remains canceled; the stop hook itself is only raised for the
        # user-controlled stop path.
        is_paused = True
        agent.last_run_checkpoint = {
            **getattr(agent, "last_run_checkpoint", {}),
            "run_id": run_id,
            "status": "paused",
            "resume_available": True,
            "stop_reason": "user requested stop",
        }
        _write_task_summary("paused")
        if run_id:
            try:
                get_run_store().transition(
                    run_id, RunStatus.PAUSED,
                    expected={RunStatus.RUNNING, RunStatus.WAITING_APPROVAL},
                    owner_id=run_owner, error="user requested stop",
                    metadata_patch={"checkpoint": agent.last_run_checkpoint},
                )
            except RunStateError:
                logger.warning("[RunState] Could not mark run %s paused", run_id, exc_info=True)
            _record_audit(
                "run_paused", run_id=run_id, session_id=session_id,
                reason="user requested stop",
            )
        await send( {
            "event": "stopped", "reason": "User requested stop",
            "status": "paused", "resume_available": is_paused,
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
        await send( {
            "event": "error", "message": f"Agent error: {type(e).__name__}: {e}",
        })
    finally:
        if 'previous_compaction_callback' in locals():
            agent.on_context_compacted = previous_compaction_callback
        reset_runtime(_runtime_token)





__all__ = ["handle_agent_execution"]
