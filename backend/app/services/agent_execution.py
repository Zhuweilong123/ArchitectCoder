"""Transport-neutral Agent execution coordinator.

The execution service owns one Agent run and reports domain events through an
injected async sender. WebSocket is only one possible transport adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from backend.config import get_settings

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.assembly import enabled_tools_context
from app.agent_base.host_api.errors import AgentInterrupted
from app.agent_base.adapters.analysis import ReadOnlyAnalysisAdapter
from app.agent_base.adapters.review import ReviewAdapter
from app.agent_base.adapters.execution import dispatch_execution
from app.agent_base.host_api.execution import ExecutionRequest, ExecutionSlots
from app.agent_base.core.hooks import (
    AgentRuntime,
    get_hooks,
    get_runtime,
    reset_runtime,
    set_runtime,
)
from app.agent_base.core.extension_context import extension_request, publish_task_result
from app.agent_base.core.plugin_runtime import pin_plugins
from app.agent_base.execution_summary import build_task_execution_summary
from app.agent_base.evidence import update_checkpoint_evidence
from app.agent_base.outcome import RunOutcome
from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
from app.services.audit_log import record_audit as _record_audit
from app.services.candidate_artifact import CandidateArtifactError, CandidateArtifactStore
from app.services.run_state import (
    RunStateError,
    RunStatus,
    get_run_store,
    run_status_for_completion,
)
from app.agent_base.host_api.tracing import TraceSink

logger = logging.getLogger(__name__)

_TASK_BIND_TIMEOUT_SECONDS = 5.0
_REVIEW_BASELINE_TIMEOUT_SECONDS = 5.0


class _ExecutionProgressForwarder:
    """Translate domain progress events into transport events for one run."""

    def __init__(self, send: Callable[[dict], Awaitable[bool]], trace_log: TraceSink | None):
        self._send = send
        self._trace_log = trace_log
        self.uml_review_seen = False

    async def __call__(self, event: dict) -> None:
        event_type = event.get("event")
        if event_type == "design_element":
            await self._send({
                "event": "design_element",
                "type": event.get("type", ""),
                "data": event.get("data", ""),
            })
        elif event_type == "review_timeout":
            await self._send({
                "event": "review_timeout",
                "review_id": event.get("review_id", 0),
                "review_type": event.get("review_type", ""),
                "title": event.get("title", ""),
                "timeout": event.get("timeout", 0),
            })
        elif event_type == "review":
            review_type = event.get("review_type", "code")
            if review_type == "uml_diff":
                self.uml_review_seen = True
            if self._trace_log:
                self._trace_log.review_request(
                    review_id=event.get("review_id", 0),
                    review_type=review_type,
                    title=event.get("title", ""),
                    question=event.get("question", ""),
                    content=event.get("content", ""),
                    metadata=event.get("metadata", {}) or {},
                )
            if review_type == "uml_diff":
                metadata = event.get("metadata", {}) or {}
                await self._send({
                    "event": "uml_review",
                    "review_id": event.get("review_id", 0),
                    "title": event.get("title", ""),
                    "diagrams": metadata.get("diagrams", []),
                    "changed_diagrams": metadata.get("changed_diagrams"),
                    "original_diagrams": metadata.get("original_diagrams"),
                })
            else:
                await self._send({
                    "event": "request_review",
                    "review_id": event.get("review_id", 0),
                    "review_type": review_type,
                    "title": event.get("title", ""),
                    "content": event.get("content", ""),
                    "question": event.get("question", ""),
                })


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

def _terminal_checkpoint_status(
    outcome: RunOutcome | None, todos: list[dict],
) -> tuple[str, str | None]:
    if outcome is not None and outcome.status != "completed":
        return outcome.status, outcome.stop_reason
    if any(
        isinstance(todo, dict) and todo.get("status") != "completed"
        for todo in todos
    ):
        return "partial", "task checklist has pending items"
    return "completed", None


def _sync_checkpoint_outcome(
    checkpoint: dict,
    *,
    status: str,
    stop_reason: str | None,
    final_answer: str | None = None,
    preserve_as: str | None = None,
) -> None:
    """Keep the durable outcome aligned with the terminal checkpoint.

    The streamed Agent can report a successful model turn before a later
    verification or contract gate changes the run's terminal state. Store the
    pre-gate outcome separately, then expose one canonical terminal outcome.
    """
    previous = checkpoint.get("outcome")
    if isinstance(previous, dict) and preserve_as and previous.get("stop_reason") != stop_reason:
        checkpoint[preserve_as] = dict(previous)
    outcome = dict(previous) if isinstance(previous, dict) else {}
    outcome["status"] = status
    if stop_reason:
        outcome["stop_reason"] = stop_reason
    if final_answer is not None:
        outcome["final_answer"] = final_answer
    outcome.setdefault("total_tokens", 0)
    checkpoint["outcome"] = outcome


def _finalize_terminal_checkpoint(
    agent: ReActAgent,
    *,
    outcome: RunOutcome | None,
    run_id: str,
    task_id: str,
    request_summary: str,
    fallback_review_requested: bool,
    review_manager: Any,
) -> tuple[str, list[dict]]:
    """Shape the durable checkpoint for a completed streamed Agent run."""
    todos = get_runtime().todos or []
    terminal_status, stop_reason = _terminal_checkpoint_status(outcome, todos)
    if terminal_status == "completed" and any(
        not item["passed"]
        for item in agent.last_run_checkpoint.get("verification_results", [])
    ):
        terminal_status, stop_reason = "partial", "verification_failed"
    agent.last_run_checkpoint = {
        **agent.last_run_checkpoint,
        "run_id": run_id,
        "task_id": task_id,
        "status": "waiting_approval" if fallback_review_requested else terminal_status,
        "request_summary": request_summary,
        "completed_items": [
            todo.get("content", "") for todo in todos
            if isinstance(todo, dict) and todo.get("status") == "completed"
        ],
        "pending_items": [
            todo.get("content", "") for todo in todos
            if isinstance(todo, dict) and todo.get("status") != "completed"
        ],
        "last_error": None,
        "stop_reason": stop_reason,
    }
    if outcome is not None:
        agent.last_run_checkpoint["outcome"] = outcome.to_dict()
    _sync_checkpoint_outcome(
        agent.last_run_checkpoint,
        status=terminal_status,
        stop_reason=stop_reason,
    )
    if fallback_review_requested:
        agent.last_run_checkpoint.update({
            "review_status": "pending",
            "post_review_status": terminal_status,
            "review_baseline": review_manager.baseline,
        })
    return terminal_status, todos


def recent_conversation_history(
    agent: ReActAgent,
    *,
    turns: int = 4,
    exclude_latest_turn: bool = False,
) -> tuple[dict[str, str], ...]:
    """Return the latest complete user/assistant turns without truncating text."""
    messages: list[tuple[str, str]] = []
    for item in getattr(agent, "_history", ()) or ():
        if isinstance(item, dict):
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
        else:
            role = str(getattr(item, "role", "") or "")
            content = str(getattr(item, "content", "") or "")
        if role in {"user", "assistant"}:
            messages.append((role, content))

    completed_turns: list[tuple[tuple[str, str], tuple[str, str]]] = []
    pending_user: tuple[str, str] | None = None
    for message in messages:
        if message[0] == "user":
            pending_user = message
        elif pending_user is not None:
            completed_turns.append((pending_user, message))
            pending_user = None

    if exclude_latest_turn and completed_turns:
        completed_turns = completed_turns[:-1]
    selected = completed_turns[-max(0, turns):] if turns > 0 else []
    return tuple(
        {"role": role, "content": content}
        for turn in selected
        for role, content in turn
    )

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


async def _request_fallback_uml_review(
    *,
    review_manager: Any,
    uml_review_seen: bool,
    project_file: str,
    trace_log: TraceSink | None,
    send: Callable[[dict], Awaitable[bool]],
    fallback_review_runs: dict[int, str] | None,
    run_id: str,
) -> bool:
    """Request review when a changed UML project bypassed the review tool."""
    if (
        review_manager is None
        or uml_review_seen
        or not project_file
        or not os.path.isfile(project_file)
        or review_manager.baseline is None
    ):
        return False
    fallback_requested = False
    try:
        from app.services.file_service import load_project
        from app.services.diagram_diff import changed_diagrams

        after = [diagram.model_dump() for diagram in load_project(project_file).diagrams]
        changed = changed_diagrams(after, review_manager.baseline)
        if not changed:
            return False
        request = review_manager.submit(
            review_type="uml_diff",
            title="检测到未审核的设计变更",
            content="Agent 修改了设计文件但未提交 diff 审核",
            question="设计文件已被修改但未经审核，请确认是否接受此变更。",
            metadata={
                "diagrams": after,
                "changed_diagrams": changed,
                "original_diagrams": review_manager.baseline,
            },
        )
        fallback_requested = True
        if fallback_review_runs is not None:
            fallback_review_runs[request.id] = run_id
        if trace_log:
            trace_log.review_request(
                review_id=request.id,
                review_type="uml_diff",
                title=request.title,
                question=request.question,
                content=request.content,
            )
        logger.info("[AgentChat] 兜底审核补推: review_id=%d", request.id)
        await send({
            "event": "uml_review",
            "review_id": request.id,
            "title": request.title,
            "diagrams": after,
            "changed_diagrams": changed,
            "original_diagrams": review_manager.baseline,
            "auto": True,
        })
        return True
    except Exception:
        logger.exception("[AgentChat] Fallback review check failed")
        return fallback_requested


async def _publish_terminal_execution(
    *,
    agent: ReActAgent,
    terminal_status: str,
    fallback_review_requested: bool,
    task_tool_calls: list[dict],
    user_message: str,
    final_answer: str,
    project_file: str,
    run_id: str,
    run_owner: str,
    session_id: str,
    conversation_history: tuple[dict[str, str], ...],
    trace_log: TraceSink | None,
    send: Callable[[dict], Awaitable[bool]],
    write_task_summary: Callable[[str], None],
) -> None:
    """Publish a terminal run as awaiting approval or a completed outcome."""
    summary_status = (
        "waiting_approval" if fallback_review_requested else terminal_status
    )
    agent.last_run_checkpoint["task_summary"] = build_task_execution_summary(
        task_tool_calls, agent.last_run_checkpoint, summary_status,
    )
    # A fallback review has no Agent future waiting on it. Do not announce
    # success until the human has resolved the review request.
    if fallback_review_requested:
        if run_id:
            get_run_store().transition(
                run_id,
                RunStatus.WAITING_APPROVAL,
                expected={RunStatus.RUNNING},
                owner_id=run_owner,
                metadata_patch={"checkpoint": agent.last_run_checkpoint},
            )
        write_task_summary(summary_status)
        await send({
            "event": "awaiting_review",
            "run_id": run_id,
            "checkpoint": agent.last_run_checkpoint,
        })
        return

    try:
        from app.services.agent_metrics import get_agent_metrics

        get_agent_metrics().record_run(
            "success" if terminal_status == "completed" else terminal_status,
        )
    except Exception:
        pass
    if run_id:
        get_run_store().transition(
            run_id,
            run_status_for_completion(terminal_status),
            expected={RunStatus.RUNNING},
            owner_id=run_owner,
            metadata_patch={"checkpoint": agent.last_run_checkpoint},
        )
        _record_audit(
            "run_succeeded" if terminal_status == "completed" else "run_partial",
            run_id=run_id,
            session_id=session_id,
            tool_call_count=len(task_tool_calls),
        )
    write_task_summary(summary_status)

    from backend.config.project_storage import project_id_for
    await publish_task_result(
        agent, run_id=run_id, project_id=project_id_for(project_file) if project_file else "",
        status=terminal_status, user_message=user_message, final_answer=final_answer,
        tool_steps=task_tool_calls, checkpoint=agent.last_run_checkpoint,
        conversation_history=conversation_history, trace_id=trace_log.trace_id if trace_log else "",
    )

    if trace_log:
        report = getattr(agent, "last_context_report", {})
        trace_log.done(answer=final_answer, runtime={
            "token_budget_used": report.get("token_budget_used", 0),
            "token_budget_stop_reason": report.get(
                "token_budget_stop_reason", "model_answer",
            ),
            "convergence_policy": report.get("convergence_policy", {}),
            "context_budget_compaction": report.get(
                "context_budget_compaction", {},
            ),
            "finalization_textual_tool_markup_blocked": report.get(
                "finalization_textual_tool_markup_blocked", False,
            ),
        })
    await send({
        "event": "done",
        "result": final_answer,
        "run_id": run_id,
        "checkpoint": agent.last_run_checkpoint,
    })


def _update_stream_checkpoint(
    agent: ReActAgent,
    step: dict,
    task_tool_calls: list[dict],
    *,
    run_id: str,
    run_owner: str,
) -> dict:
    """Persist one stream step and return its current todo projection."""
    todo_state = _todo_progress_state()
    todos = todo_state.get("todos", [])
    agent.last_run_checkpoint.update({
        "last_step": step.get("step", 0),
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
        "tool_calls": [
            {
                "name": detail.get("name", ""),
                "status": detail.get("status", ""),
                "error_code": detail.get("error_code", ""),
                "changes": detail.get("changes", []),
                "verification": detail.get("verification"),
            }
            for detail in task_tool_calls[-32:]
            if isinstance(detail, dict)
        ],
    })
    update_checkpoint_evidence(agent.last_run_checkpoint, step.get("tool_calls_detail", []))
    _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
    return todo_state


def _record_stream_step(trace_log: TraceSink | None, step: dict, thought: str) -> None:
    """Write the stream step to the optional trace sink.

    Tool spans are emitted by ``ToolRoundExecutor`` at the actual execution
    boundary.  They must not be reconstructed here: a blocking tool (for
    example ``submit_uml_review``) can suspend the loop before this progress
    snapshot is produced, and reconstructing spans here reverses the real
    review/tool ordering.
    """
    if trace_log is None:
        return
    trace_log.agent_step(
        step=step["step"], thought=thought or "",
        actions=step["actions"], is_final=step["is_final"],
    )


def _stream_progress_event(step: dict, todo_state: dict) -> dict:
    """Build the bounded transport payload for a stream step."""
    return {
        "event": "progress",
        "step": step["step"],
        "actions": step["actions"],
        "thought": step["thought"][:300],
        "tool_calls_detail": [
            {
                "name": detail.get("name", ""),
                "arguments": detail.get("arguments", {}),
                "observation": str(detail.get("observation", ""))[:3000],
            }
            for detail in step.get("tool_calls_detail", [])[:5]
        ],
        "is_final": step["is_final"],
        "final_answer": step["final_answer"] if step["is_final"] else "",
        **todo_state,
    }

@pin_plugins
@extension_request
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
    design_dir: str = "",
    workspace_root: str = "",
    progress: ProgressRelay | None = None,
    context: str = "",
    fallback_review_runs: dict[int, str] | None = None,
    run_id: str = "",
    run_owner: str = "",
    session_id: str = "",
    resume_checkpoint: dict | None = None,
    disconnect_check: Callable[[], bool] | None = None,
    design_contract_enabled: bool | None = None,
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
    progress_forwarder = _ExecutionProgressForwarder(send, trace_log)
    resume_checkpoint = dict(resume_checkpoint or {})
    checkpoint_request_summary = str(
        resume_checkpoint.get("request_summary") or user_message
    )[:500]
    agent.last_run_checkpoint = {
        "run_id": run_id,
        "plugin_plan_id": get_hooks().plan_id,
        "status": "running",
        "request_summary": checkpoint_request_summary,
        "completed_items": list(resume_checkpoint.get("completed_items") or []),
        "pending_items": list(resume_checkpoint.get("pending_items") or []),
        "changed_files": list(resume_checkpoint.get("changed_files") or []),
        "verification": list(resume_checkpoint.get("verification") or []),
        "verification_results": list(resume_checkpoint.get("verification_results") or []),
        "mutation_evidence": dict(resume_checkpoint.get("mutation_evidence") or {}),
        "last_error": None,
        "stop_reason": None,
        "resume_available": False,
        "resume_of": resume_checkpoint.get("run_id", ""),
        "candidate_artifact": resume_checkpoint.get("candidate_artifact"),
        "candidate_recovery": bool(resume_checkpoint.get("candidate_artifact")),
        "project_file": project_file,
        "source_dir": source_dir,
        "test_dir": test_dir,
        "design_dir": design_dir,
    }
    _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
    logger.info("[AgentExecution] initial checkpoint persisted run=%s", run_id)
    if review_mgr is not None:
        # The UML review must happen while the source candidate is still
        # rolled back.  The chat transport restores the candidate only after
        # this review is accepted.
        review_mgr.candidate_recovery = (
            dict(resume_checkpoint.get("candidate_artifact"))
            if isinstance(resume_checkpoint.get("candidate_artifact"), dict)
            else None
        )

    logger.info("[AgentExecution] installing runtime context run=%s", run_id)
    _runtime_token = set_runtime(AgentRuntime(
        stop_check=stop_check,
        run_id=run_id,
    ))
    logger.info("[AgentExecution] runtime context installed run=%s", run_id)
    task_binding = None
    task_tool_calls: list[dict] = []
    task_summary_written = False
    stream = None

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
        if progress:
            logger.info("[AgentExecution] registering progress callback run=%s", run_id)
            progress.on_progress(progress_forwarder)
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
                review_mgr.baseline = (
                    resume_checkpoint["review_baseline"]
                    if "review_baseline" in resume_checkpoint
                    else await _load_review_baseline_async(project_file)
                )
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
        change_set = getattr(agent, "change_set", None)
        if change_set is not None:
            change_set.project_file = project_file or change_set.project_file
            change_set.begin()
        if review_mgr is not None:
            review_mgr.candidate_restore_callback = None
            candidate_reference = resume_checkpoint.get("candidate_artifact")
            if change_set is not None and isinstance(candidate_reference, dict):
                allowed_roots = tuple(
                    value for value in (workspace_root, source_dir, test_dir, design_dir, project_file)
                    if value
                )

                def _restore_candidate_after_design(reference: dict):
                    if getattr(agent, "candidate_recovery_applied", False):
                        return agent.last_run_checkpoint.get("candidate_restore", {})
                    restored = CandidateArtifactStore(get_settings()).restore(
                        reference,
                        change_set,
                        allowed_roots=allowed_roots,
                        # The accepted design is already in the active ChangeSet;
                        # restore only source/test candidate files.
                        exclude_paths=(design_dir, project_file),
                    )
                    agent.candidate_recovery_applied = True
                    agent.last_run_checkpoint["candidate_restore"] = restored
                    agent.last_run_checkpoint["candidate_recovery_phase"] = "candidate_restored"
                    if trace_log:
                        trace_log.event(
                            "candidate_restore",
                            phase="after_design_review",
                            artifact_id=reference.get("artifact_id", ""),
                            restored_count=restored.get("restored_count", 0),
                            skipped_count=restored.get("skipped_count", 0),
                        )
                    return restored

                review_mgr.candidate_restore_callback = _restore_candidate_after_design
        task_tool_calls: list[dict] = []
        agent.tool_registry.set_allowed_tools(None)
        context = "\n\n".join(filter(None, [
            context, enabled_tools_context(),
        ]))
        preparation = await dispatch_execution(ExecutionRequest(
            ExecutionSlots.PREPARE, run_id=run_id, context=context,
            checkpoint=agent.last_run_checkpoint, data={
                "user_message": user_message, "project_file": project_file,
                "source_dir": source_dir, "test_dir": test_dir,
                "previous_checkpoint": resume_checkpoint,
                "available_tools": tuple(agent.tool_registry.list_tools()),
                "record_event": trace_log.event if trace_log else None,
            }), agent_name=getattr(agent, "name", "Agent"))
        context, main_allowed_tools = preparation.context, preparation.allowed_tools
        _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
        logger.info("[AgentExecution] entering agent stream run=%s", run_id)
        archive_conversation_history = recent_conversation_history(agent, turns=4)
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
                context_target_tokens=report.get("context_target_tokens", 0),
            )
        stream = agent.arun_stream(
            user_message,
            context=context,
            **({"allowed_tools": main_allowed_tools} if main_allowed_tools is not None else {}),
        )
        async for step_progress in stream:
            d = step_progress.to_dict()
            task_tool_calls.extend(d.get("tool_calls_detail", []))
            todo_state = _update_stream_checkpoint(
                agent, d, task_tool_calls, run_id=run_id, run_owner=run_owner,
            )
            _sync_task_execution()

            _record_stream_step(trace_log, d, step_progress.thought or "")
            ok = await send(_stream_progress_event(d, todo_state))
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
                fallback_review_requested = await _request_fallback_uml_review(
                    review_manager=review_mgr,
                    uml_review_seen=progress_forwarder.uml_review_seen,
                    project_file=project_file,
                    trace_log=trace_log,
                    send=send,
                    fallback_review_runs=fallback_review_runs,
                    run_id=run_id,
                )

                execution_check = await dispatch_execution(ExecutionRequest(
                    ExecutionSlots.CHECK, run_id=run_id,
                    checkpoint=agent.last_run_checkpoint, allowed_tools=main_allowed_tools,
                    data={
                        "workspace_manifest": dict(getattr(agent, "workspace_manifest", {}) or {}),
                        "changed_paths": change_set.manifest() if change_set is not None else (),
                        "design_contract_enabled": design_contract_enabled,
                        "record_event": trace_log.event if trace_log else None,
                    }, capabilities={
                        "emit": send,
                        "request_review": ReviewAdapter(review_mgr, progress_forwarder).ask if review_mgr else None,
                        "analyze": ReadOnlyAnalysisAdapter(agent).invoke,
                    }), agent_name=getattr(agent, "name", "Agent"))
                _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
                if not execution_check.allowed:
                    rollback_completed = False
                    candidate_artifact = None
                    if change_set is not None and change_set.has_changes:
                        try:
                            candidate_artifact = CandidateArtifactStore(
                                get_settings()
                            ).capture(change_set, run_id)
                        except CandidateArtifactError as exc:
                            logger.warning(
                                "[Candidate] Could not persist rejected candidate run=%s: %s",
                                run_id, exc,
                            )
                            agent.last_run_checkpoint["candidate_artifact_error"] = str(exc)
                        if candidate_artifact:
                            agent.last_run_checkpoint["candidate_artifact"] = candidate_artifact
                            agent.last_run_checkpoint["candidate_recovery"] = True
                            if trace_log:
                                trace_log.event(
                                    "candidate_artifact",
                                    phase="check_rejected",
                                    artifact_id=candidate_artifact.get("artifact_id", ""),
                                    file_count=candidate_artifact.get("file_count", 0),
                                )
                            await send({
                                **execution_check.recovery_event,
                                "run_id": run_id,
                                "file_count": candidate_artifact.get("file_count", 0),
                            })
                        change_set.rollback()
                        rollback_completed = True
                        if trace_log:
                            trace_log.event(
                                "candidate_rollback",
                                phase="check_rejected",
                                candidate_saved=bool(candidate_artifact),
                            )
                    execution_check.interface_id = ExecutionSlots.REJECTED
                    execution_check.data["rollback_completed"] = rollback_completed
                    await dispatch_execution(execution_check, agent_name=getattr(agent, "name", "Agent"))
                    failure_analysis = execution_check.message
                    terminal_status, todos = _finalize_terminal_checkpoint(
                        agent,
                        outcome=step_progress.outcome,
                        run_id=run_id,
                        task_id=task_binding.task_id if task_binding else "",
                        request_summary=checkpoint_request_summary,
                        fallback_review_requested=False,
                        review_manager=review_mgr,
                    )
                    terminal_status = "partial"
                    agent.last_run_checkpoint.update({
                        "status": terminal_status,
                        "stop_reason": execution_check.stop_reason,
                    })
                    _sync_checkpoint_outcome(
                        agent.last_run_checkpoint,
                        status=terminal_status,
                        stop_reason=execution_check.stop_reason,
                        final_answer=failure_analysis,
                        preserve_as="pre_gate_outcome",
                    )
                    _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
                    await _publish_terminal_execution(
                        agent=agent,
                        terminal_status=terminal_status,
                        fallback_review_requested=False,
                        task_tool_calls=task_tool_calls,
                        user_message=user_message,
                        final_answer=failure_analysis,
                        project_file=project_file,
                        run_id=run_id,
                        run_owner=run_owner,
                        session_id=session_id,
                        conversation_history=archive_conversation_history,
                        trace_log=trace_log,
                        send=send,
                        write_task_summary=_write_task_summary,
                    )
                    return

                if change_set is not None and change_set.has_changes:
                    manifest = change_set.commit()
                    logger.info("[ChangeSet] committed %d file changes", len(manifest))
                    from app.services.project_repository import ProjectRepository
                    project_repository = ProjectRepository()
                    for item in manifest:
                        committed_path = str(item.get("path", ""))
                        if committed_path.lower().endswith(".umlproj") and os.path.isfile(committed_path):
                            await send({
                                "event": "project_committed",
                                "filepath": committed_path,
                                "revision": project_repository.revision(committed_path),
                            })
                    if trace_log:
                        trace_log.event(
                            "changes_committed",
                            phase="post_execution_check",
                            file_count=len(manifest),
                            paths=[
                                str(item.get("path", ""))
                                for item in manifest
                                if isinstance(item, dict) and item.get("path")
                            ],
                        )
                    execution_check.interface_id = ExecutionSlots.COMMITTED
                    execution_check.data["changed_paths"] = manifest
                    await dispatch_execution(execution_check, agent_name=getattr(agent, "name", "Agent"))
                    _persist_run_checkpoint(run_id, run_owner, agent.last_run_checkpoint)
                else:
                    manifest = []
                terminal_status, todos = _finalize_terminal_checkpoint(
                    agent,
                    outcome=step_progress.outcome,
                    run_id=run_id,
                    task_id=task_binding.task_id if task_binding else "",
                    request_summary=checkpoint_request_summary,
                    fallback_review_requested=fallback_review_requested,
                    review_manager=review_mgr,
                )

                await _publish_terminal_execution(
                    agent=agent,
                    terminal_status=terminal_status,
                    fallback_review_requested=fallback_review_requested,
                    task_tool_calls=task_tool_calls,
                    user_message=user_message,
                    final_answer=d["final_answer"] or "",
                    project_file=project_file,
                    run_id=run_id,
                    run_owner=run_owner,
                    session_id=session_id,
                    conversation_history=archive_conversation_history,
                    trace_log=trace_log,
                    send=send,
                    write_task_summary=_write_task_summary,
                )
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
                "websocket disconnected; next message can resume"
                if disconnected else (
                    "user requested stop; next message can resume"
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
        try:
            if stream is not None:
                await stream.aclose()
        finally:
            if 'previous_compaction_callback' in locals():
                agent.on_context_compacted = previous_compaction_callback
            reset_runtime(_runtime_token)

__all__ = ["handle_agent_execution"]
