"""隔离评测 Runner：fixture → Agent → checker → trace/result。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.llm import BaseAgentsLLM
from app.core.config import get_settings
from app.services.agent_metrics import get_agent_metrics
from app.services.chat_trace import TraceSession

from .checkers import build_checkers
from .fixture_materializer import materialize_fixture
from .models import CheckerResult, EvalCase, EvalResult
from .projects import load_projects, resolve_fixture

AgentFactory = Callable[[Path, EvalCase], Awaitable[Any]]
logger = logging.getLogger(__name__)

_HARD_BUDGET_STOP_REASONS = {
    "hard_limit_before_next_llm",
    "hard_limit_after_current_tools",
    "tool_call_limit",
}
_FINALIZATION_BUDGET_STOP_REASONS = {
    "reserve_finalization",
    "reserve_finalization_empty_response",
}


def _trace_total_tokens(trace_path: str) -> int:
    if not trace_path:
        return 0
    total = 0
    try:
        for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("event_type") != "llm_response":
                continue
            usage = event.get("usage") or {}
            total += int(usage.get("total_tokens") or 0)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return 0
    return total


def _default_results_path() -> Path:
    settings = get_settings()
    return Path(settings.uml_dir).resolve().parent / "evals" / "results.jsonl"


async def dev_agent_factory(workspace: Path, case: EvalCase) -> ReActAgent:
    """Build the production DevAgent inside the isolated evaluation workspace.

    The interactive WebSocket path and this factory share the same agent
    assembly function. Evaluation only changes the workspace, run budgets and
    approval policy; it does not replace the production prompt/tool chain.
    """
    from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
    from app.services.agent_chat_ws import _create_dev_agent, _enabled_tools_context

    settings = get_settings()
    first_prompt = case.prompts()[0]
    llm = BaseAgentsLLM.from_settings(temperature=0.3)
    manifest = load_projects().get(case.project_id) if case.project_id else None
    source_dir = workspace / manifest.source_dir if manifest else workspace
    test_dir = workspace / manifest.test_dir if manifest else workspace
    project_file = workspace / manifest.entry_file if manifest and manifest.entry_file else workspace / "evaluation.umlproj"
    progress = ProgressRelay()
    agent, review_mgr, prompt_builder = await _create_dev_agent(
        llm,
        source_dir=str(source_dir),
        test_dir=str(test_dir),
        project_file=str(project_file),
        user_message=first_prompt,
        progress=progress,
        task_scope=f"eval_{case.id}",
        auto_approve_reviews=True,
        max_steps=case.max_tool_calls,
        max_tool_calls=case.max_tool_calls,
        max_run_seconds=case.max_seconds,
        max_total_tokens=min(settings.agent_max_total_tokens, case.max_total_tokens),
        convergence_tool_steps=(
            int(case.metadata["convergence_tool_steps"])
            if case.metadata.get("convergence_tool_steps") is not None else None
        ),
    )
    agent._eval_context = await prompt_builder.build_context(
        str(project_file), str(source_dir), str(test_dir), first_prompt,
    )
    agent._eval_prompt_builder = prompt_builder
    agent._eval_source_dir = str(source_dir)
    agent._eval_test_dir = str(test_dir)
    agent._eval_project_file = str(project_file)
    agent._eval_progress = progress
    agent._eval_review_manager = review_mgr
    agent._eval_agent_mode = "devagent"
    agent._eval_context = "\n\n".join(filter(None, [
        agent._eval_context, _enabled_tools_context(),
    ]))
    return agent


class EvalRunner:
    def __init__(self, results_path: str | Path | None = None):
        self.results_path = Path(results_path) if results_path else _default_results_path()

    async def run_case(self, case: EvalCase, agent_factory: AgentFactory | None = None) -> EvalResult:
        run_id = f"eval_{uuid.uuid4().hex[:16]}"
        result = EvalResult.started(run_id, case.id)
        started = time.monotonic()
        # All official evaluations use the production DevAgent assembly.
        # ``agent_factory`` remains only as a dependency-injection seam for
        # unit tests and local harnesses.
        factory = agent_factory or dev_agent_factory
        result.metadata["project_id"] = case.project_id

        try:
            fixture, manifest = resolve_fixture(case)
        except ValueError as exc:
            finished = self._finish(result, "error", str(exc), started)
            self._append_result(finished)
            get_agent_metrics().record_run("eval_error")
            return finished

        # Agent tools may create POSIX-style links (for example ``venv/lib64``)
        # inside the Windows-backed fixture. Cleanup must not erase the result
        # record or hide the actual evaluation outcome when such a link cannot
        # be removed by Windows.
        with tempfile.TemporaryDirectory(
            prefix=f"{run_id}_", ignore_cleanup_errors=True
        ) as temp_dir:
            workspace = Path(temp_dir).resolve()
            result.workspace = str(workspace)
            result.metadata["workspace_ephemeral"] = True
            if fixture is not None:
                if not fixture.is_dir():
                    finished = self._finish(result, "error", f"fixture not found: {fixture}", started)
                    finished.workspace = ""
                    self._append_result(finished)
                    get_agent_metrics().record_run("eval_error")
                    return finished
                materialize_fixture(fixture, workspace, manifest)

            baseline_hashes: dict[str, str | None] = {}
            for config in [*case.hard_checkers, *case.checkers]:
                if config.get("type") != "paths_unchanged":
                    continue
                for relative_path in config.get("paths", []):
                    candidate = (workspace / relative_path).resolve()
                    if not candidate.is_relative_to(workspace):
                        baseline_hashes[relative_path] = None
                    elif candidate.is_file():
                        baseline_hashes[relative_path] = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    else:
                        baseline_hashes[relative_path] = None

            try:
                source_dir = workspace / manifest.source_dir if manifest else workspace
                test_dir = workspace / manifest.test_dir if manifest else workspace
                first_prompt = case.prompts()[0]
                async with TraceSession(
                    session_id=run_id, user_message=first_prompt,
                    source_dir=str(source_dir), test_dir=str(test_dir),
                    env_snapshot={"eval_case": case.id},
                ) as tracer:
                    result.trace_id = tracer.trace_id
                    agent = await factory(workspace, case)
                    result.model = getattr(getattr(agent, "llm", None), "model", "")
                    result.metadata["agent"] = "devagent"
                    change_set = getattr(agent, "change_set", None)
                    if change_set is not None:
                        change_set.begin()
                    task_binding = None
                    task_finalized = False
                    if hasattr(agent, "tool_registry"):
                        try:
                            from app.agent_base.tools.task_system import create_task_execution

                            task_subject = (case.name or "").strip() or first_prompt
                            task_binding = create_task_execution(
                                scope=f"eval_{case.id}",
                                run_id=run_id,
                                owner=f"run:{run_id}",
                                subject=task_subject,
                                description="Durable task state for one evaluation execution.",
                            )
                            result.metadata["task_id"] = task_binding.task_id
                            tracer.event(
                                "task_binding",
                                task_id=task_binding.task_id,
                                status="bound",
                            )
                            agent.last_run_checkpoint = {
                                **dict(getattr(agent, "last_run_checkpoint", {}) or {}),
                                "task_id": task_binding.task_id,
                            }
                        except Exception:
                            # Task persistence is an observability aid; an unavailable
                            # task store must not prevent the evaluation itself.
                            logger.warning(
                                "[TaskSystem] Could not bind evaluation run %s",
                                run_id,
                                exc_info=True,
                            )
                    review_mgr = getattr(agent, "_eval_review_manager", None)
                    progress_relay = getattr(agent, "_eval_progress", None)
                    turn_hard_checker_results: list[CheckerResult] = []
                    from app.services.agent_chat_ws import _build_task_execution_summary
                    active_turn: dict[str, Any] = {
                        "turn": 0,
                        "details": [],
                        "checkpoint": {},
                        "summary_written": False,
                    }

                    def finalize_task(
                        status: str,
                        checkpoint: dict[str, Any] | None = None,
                    ) -> None:
                        nonlocal task_finalized
                        if task_binding is None:
                            return
                        if task_finalized:
                            return
                        try:
                            effective_checkpoint = dict(
                                checkpoint or getattr(
                                    agent, "last_run_checkpoint", {}
                                ) or {}
                            )
                            effective_checkpoint["status"] = status
                            task_binding.finalize(
                                status,
                                checkpoint=effective_checkpoint,
                            )
                            tracer.event(
                                "task_binding",
                                task_id=task_binding.task_id,
                                status=status,
                            )
                            task_finalized = True
                        except Exception:
                            logger.warning(
                                "[TaskSystem] Could not finalize evaluation task %s as %s",
                                task_binding.task_id,
                                status,
                                exc_info=True,
                            )

                    async def consume() -> None:
                        """Run a legacy single prompt or a shared multi-turn script."""
                        from app.agent_base.core.hooks import (
                            AgentRuntime, get_runtime, set_runtime, reset_runtime,
                        )
                        from app.services.agent_chat_ws import (
                            _build_task_execution_summary,
                            _enabled_tools_context,
                        )

                        prompts = case.prompts()
                        turn_specs = case.turn_specs()
                        turn_records: list[dict[str, Any]] = []
                        # Budget semantics: every user task gets an independent
                        # ReAct budget. Keep cumulative usage for reporting only;
                        # never pass it into the next turn's budget.
                        reported_total_tokens = 0
                        traced_total_tokens = 0
                        review_offset = 0
                        approval_offset = 0
                        prompt_builder = getattr(agent, "_eval_prompt_builder", None)
                        production_agent = hasattr(agent, "tool_registry")

                        def sync_task_binding(checkpoint: dict[str, Any]) -> None:
                            if task_binding is None:
                                return
                            try:
                                task_binding.sync(
                                    todos=list(get_runtime().todos or []),
                                    checkpoint=checkpoint,
                                )
                            except Exception:
                                logger.warning(
                                    "[TaskSystem] Could not sync evaluation task %s",
                                    task_binding.task_id,
                                    exc_info=True,
                                )

                        def sync_progress_checkpoint(
                            step: int,
                            details: list[dict],
                        ) -> None:
                            """Persist a bounded checkpoint before a turn can be interrupted."""
                            if task_binding is None:
                                return
                            checkpoint = dict(
                                getattr(agent, "last_run_checkpoint", {}) or {}
                            )
                            checkpoint.update({
                                "run_id": run_id,
                                "task_id": task_binding.task_id,
                                "turn": active_turn["turn"],
                                "last_step": step,
                                "last_tools": [
                                    {
                                        "name": str(detail.get("name") or ""),
                                        "status": str(detail.get("status") or ""),
                                        "error_code": str(detail.get("error_code") or ""),
                                    }
                                    for detail in details[-8:]
                                ],
                            })
                            active_turn["checkpoint"] = checkpoint
                            sync_task_binding(checkpoint)

                        def write_turn_summary(status: str) -> None:
                            """Write one trace checkpoint for the active eval task."""
                            if not active_turn["turn"] or active_turn["summary_written"]:
                                return
                            checkpoint = dict(active_turn.get("checkpoint") or {})
                            summary = _build_task_execution_summary(
                                active_turn.get("details") or [], checkpoint, status,
                            )
                            tracer.task_summary(
                                summary=summary,
                                status=status,
                                tool_call_count=len(active_turn.get("details") or []),
                                turn=active_turn["turn"],
                            )
                            active_turn["summary_written"] = True

                        def record_tool_details(
                            step: int,
                            details: list[dict],
                            collector: list[dict] | None = None,
                        ) -> None:
                            """Mirror streamed tool details into the evaluation trace."""
                            if collector is not None:
                                collector.extend(details)
                            for detail in details:
                                name = str(detail.get("name") or "tool")
                                arguments = detail.get("arguments")
                                if not isinstance(arguments, dict):
                                    arguments = {"raw": str(arguments or "")}
                                span_id = tracer.tool_call(
                                    step=step, tool_name=name, arguments=arguments,
                                )
                                status = str(detail.get("status") or "")
                                tracer.tool_result(
                                    span_id=span_id,
                                    tool_name=name,
                                    observation=str(detail.get("observation") or ""),
                                    error=(
                                        str(detail.get("error_code") or "")
                                        if status not in {"", "success", "completed"} else ""
                                    ),
                                    fed_truncated=bool(detail.get("fed_truncated")),
                                    fed_length=int(detail.get("fed_length") or 0),
                                )

                        for turn_index, (prompt, turn_spec) in enumerate(
                            zip(prompts, turn_specs), 1
                        ):
                            active_turn.update({
                                "turn": turn_index,
                                "details": [],
                                "checkpoint": {},
                                "summary_written": False,
                            })
                            tracer.user_message(
                                prompt,
                                project_file=getattr(agent, "_eval_project_file", ""),
                                source_dir=getattr(agent, "_eval_source_dir", ""),
                                test_dir=getattr(agent, "_eval_test_dir", ""),
                            )
                            tracer.event(
                                "agent_model", model=getattr(getattr(agent, "llm", None), "model", ""),
                                policy="fixed_session_model", turn=turn_index,
                            )

                            if not production_agent:
                                turn_tool_calls = 0
                                turn_tokens = 0
                                final_answer = ""
                                turn_tool_details: list[dict] = active_turn["details"]
                                async for progress in agent.arun_stream(prompt):
                                    details = progress.tool_calls_detail or []
                                    record_tool_details(progress.step, details, turn_tool_details)
                                    sync_progress_checkpoint(progress.step, details)
                                    delta_tool_calls = sum(
                                        detail.get("status") != "blocked" for detail in details
                                    )
                                    turn_tool_calls += delta_tool_calls
                                    result.tool_calls += delta_tool_calls
                                    for detail in details:
                                        tokens = int(detail.get("total_tokens") or 0)
                                        turn_tokens += tokens
                                        result.total_tokens += tokens
                                    if progress.is_final:
                                        final_answer = progress.final_answer or ""
                                        tracer.done(answer=final_answer)
                                turn_records.append({
                                    "turn": turn_index,
                                    "prompt": prompt,
                                    "model": getattr(getattr(agent, "llm", None), "model", ""),
                                    "status": "completed",
                                    "tool_calls": turn_tool_calls,
                                    "total_tokens": turn_tokens,
                                    "answer": final_answer[:500],
                                })
                                active_turn["checkpoint"] = dict(
                                    getattr(agent, "last_run_checkpoint", {}) or {}
                                )
                                sync_task_binding(active_turn["checkpoint"])
                                write_turn_summary("completed")
                                continue

                            context = ""
                            if prompt_builder is not None:
                                context = await prompt_builder.build_context(
                                    getattr(agent, "_eval_project_file", ""),
                                    getattr(agent, "_eval_source_dir", ""),
                                    getattr(agent, "_eval_test_dir", ""),
                                    prompt,
                                )
                                tracer.event(
                                    "prompt_context",
                                    prompt_version=f"devagent-{prompt_builder.prompt_version}",
                                    static_prompt=prompt_builder.static_prompt_report,
                                    **prompt_builder.last_context_report,
                                    turn=turn_index,
                                )
                            context = "\n\n".join(filter(None, [
                                context,
                                _enabled_tools_context(),
                            ]))
                            stream_kwargs: dict[str, Any] = {}
                            if context:
                                stream_kwargs["context"] = context
                            # Each turn is a separate user task with its own
                            # main-Agent budget. Cumulative usage is report-only.
                            runtime_token = set_runtime(AgentRuntime(
                            ))
                            turn_tool_calls = 0
                            turn_tokens = 0
                            final_answer = ""
                            turn_tool_details: list[dict] = active_turn["details"]
                            tool_budget_exceeded = False
                            try:
                                stream = agent.arun_stream(prompt, **stream_kwargs)
                                async for progress in stream:
                                    details = progress.tool_calls_detail or []
                                    tool_budget_exceeded = tool_budget_exceeded or any(
                                        "Tool-call budget exceeded" in str(
                                            detail.get("observation") or ""
                                        )
                                        for detail in details
                                    )
                                    record_tool_details(progress.step, details, turn_tool_details)
                                    sync_progress_checkpoint(progress.step, details)
                                    turn_tool_calls += sum(
                                        detail.get("status") != "blocked" for detail in details
                                    )
                                    result.tool_calls += sum(
                                        detail.get("status") != "blocked" for detail in details
                                    )
                                    for detail in details:
                                        tokens = int(detail.get("total_tokens") or 0)
                                        turn_tokens += tokens
                                        result.total_tokens += tokens
                                    if progress.is_final:
                                        final_answer = progress.final_answer or ""
                                        tracer.done(answer=final_answer)
                            finally:
                                reset_runtime(runtime_token)

                            # Tool-call details do not carry LLM usage. Use the
                            # trace delta and this turn's report for aggregation,
                            # without feeding prior-turn usage back into Agent.
                            traced_tokens = _trace_total_tokens(tracer.path)
                            traced_delta = max(0, traced_tokens - traced_total_tokens)
                            traced_total_tokens = max(traced_total_tokens, traced_tokens)
                            reported_tokens = int(
                                getattr(agent, "last_context_report", {}).get(
                                    "token_budget_used", 0
                                ) or 0
                            )
                            turn_tokens = max(traced_delta, reported_tokens)
                            reported_total_tokens += turn_tokens
                            result.total_tokens = max(result.total_tokens, reported_total_tokens)
                            budget_stop_reason = str(
                                getattr(agent, "last_context_report", {}).get(
                                    "token_budget_stop_reason", ""
                                ) or ""
                            )
                            if tool_budget_exceeded and not budget_stop_reason:
                                budget_stop_reason = "tool_call_limit"

                            # Mirror the production transport's terminal
                            # checkpoint for explicit task-status requests.
                            agent.last_run_checkpoint = {
                                "status": "completed",
                                "run_id": run_id,
                                "turn": turn_index,
                                "request": prompt[:500],
                                "verification": [],
                            }
                            if task_binding is not None:
                                agent.last_run_checkpoint["task_id"] = task_binding.task_id
                            active_turn["checkpoint"] = dict(agent.last_run_checkpoint)
                            sync_task_binding(active_turn["checkpoint"])
                            turn_records.append({
                                "turn": turn_index,
                                "prompt": prompt,
                                "model": getattr(getattr(agent, "llm", None), "model", ""),
                                "status": "completed",
                                "tool_calls": turn_tool_calls,
                                "total_tokens": turn_tokens,
                                "answer": final_answer[:500],
                            })

                            turn_hard_configs = turn_spec.hard_checkers
                            turn_score_configs = turn_spec.checkers
                            if turn_hard_configs or turn_score_configs:
                                turn_hard_results = await asyncio.gather(*(
                                    checker.check(workspace)
                                    for checker in build_checkers(
                                        turn_hard_configs, baseline_hashes
                                    )
                                ))
                                turn_score_results = await asyncio.gather(*(
                                    checker.check(workspace)
                                    for checker in build_checkers(
                                        turn_score_configs, baseline_hashes
                                    )
                                ))
                                turn_results = [*turn_hard_results, *turn_score_results]
                                result.checker_results.extend(turn_results)
                                # A turn-local hard checker is an acceptance
                                # gate, not merely diagnostic evidence. Keep
                                # it in the final aggregate so a later turn
                                # cannot mask an incomplete earlier task.
                                turn_hard_checker_results.extend(turn_hard_results)
                                turn_records[-1]["checker_results"] = [
                                    item.model_dump() for item in turn_results
                                ]
                            if budget_stop_reason:
                                turn_records[-1]["token_budget_stop_reason"] = budget_stop_reason
                                result.metadata.setdefault("token_budget_stop_reasons", []).append({
                                    "turn": turn_index,
                                    "reason": budget_stop_reason,
                                    "used": reported_tokens,
                                })

                            if progress_relay is not None:
                                for event in progress_relay.events[review_offset:]:
                                    if event.get("event") != "review":
                                        continue
                                    tracer.review_request(
                                        review_id=event.get("review_id", 0),
                                        review_type=event.get("review_type", ""),
                                        title=event.get("title", ""),
                                        question=event.get("question", ""),
                                        content=event.get("content", ""),
                                    )
                                review_offset = len(progress_relay.events)
                            if review_mgr is not None:
                                for event in review_mgr.approval_events[approval_offset:]:
                                    if event.get("event") == "review_response":
                                        tracer.review_response(
                                            review_id=event.get("review_id", 0),
                                            response=json.dumps(event, ensure_ascii=False),
                                        )
                                approval_offset = len(review_mgr.approval_events)
                            write_turn_summary(
                                "budget_exceeded"
                                if budget_stop_reason in _HARD_BUDGET_STOP_REASONS
                                else "partial"
                                if budget_stop_reason
                                else "completed"
                            )

                        result.metadata["turns"] = turn_records

                    try:
                        await asyncio.wait_for(consume(), timeout=case.max_seconds)
                    except asyncio.TimeoutError:
                        timeout_checkpoint = dict(
                            active_turn.get("checkpoint") or {}
                        )
                        timeout_checkpoint["status"] = "timed_out"
                        timeout_checkpoint["stop_reason"] = (
                            f"evaluation exceeded {case.max_seconds}s"
                        )
                        # Keep the terminal task event inside TraceSession. The
                        # outer handler still owns rollback and result shaping.
                        finalize_task("timed_out", timeout_checkpoint)
                        raise
                    if change_set is not None:
                        result.metadata["change_set"] = change_set.commit()
                    if review_mgr is not None:
                        result.metadata["approval_events"] = review_mgr.approval_events
                    if case.metadata.get("require_auto_approval"):
                        approval_events = review_mgr.approval_events if review_mgr else []
                        responses = [
                            event for event in approval_events
                            if event.get("event") == "review_response"
                        ]
                        approval_passed = bool(responses) and all(
                            event.get("approval_mode") == "auto_stub"
                            and event.get("decision") == "accept"
                            for event in responses
                        )
                        result.checker_results.append(CheckerResult(
                            checker="review_auto_stub",
                            passed=approval_passed,
                            score=1.0 if approval_passed else 0.0,
                            message=(
                                "all reviews accepted by auto stub"
                                if approval_passed else
                                "review approval was not fully auto-stub accepted"
                            ),
                            details={"responses": len(responses)},
                        ))
                    if progress_relay is not None:
                        result.metadata["progress_events"] = progress_relay.events
                    hard_results = await asyncio.gather(*(
                        checker.check(workspace) for checker in build_checkers(case.hard_checkers, baseline_hashes)
                    ))
                    score_results = await asyncio.gather(*(
                        checker.check(workspace) for checker in build_checkers(case.checkers, baseline_hashes)
                    ))
                    # Keep non-file execution checkers (notably
                    # review_auto_stub).  Turn-local file checkers are
                    # diagnostic evidence and are rerun below against final
                    # workspace state, so including them again would skew the
                    # final score.
                    execution_results = [
                        item for item in result.checker_results
                        if item.checker == "review_auto_stub"
                    ]
                    checker_results = [
                        *execution_results,
                        *turn_hard_checker_results,
                        *hard_results,
                        *score_results,
                    ]
                    result.checker_results = list(checker_results)
                    result.score = (
                        sum(item.score for item in checker_results) / len(checker_results)
                        if checker_results else 1.0
                    )
                    result.passed = bool(checker_results) and all(item.passed for item in checker_results)
                    budget_events = result.metadata.get("token_budget_stop_reasons", [])
                    budget_reasons = {
                        str(event.get("reason") or "")
                        for event in budget_events
                    }
                    if budget_reasons & _HARD_BUDGET_STOP_REASONS:
                        result.status = "budget_exceeded"
                        result.passed = False
                        result.error = "evaluation stopped after a hard execution budget was exhausted"
                    elif budget_reasons & _FINALIZATION_BUDGET_STOP_REASONS:
                        result.status = "budget_finalized"
                    else:
                        result.status = "passed" if result.passed else "failed"
                    finalize_task(
                        "budget_exceeded"
                        if budget_reasons & _HARD_BUDGET_STOP_REASONS
                        else "completed"
                        if result.passed
                        else "failed"
                    )
            except asyncio.TimeoutError:
                if (
                    "tracer" in locals()
                    and "active_turn" in locals()
                    and active_turn["turn"]
                    and not active_turn["summary_written"]
                ):
                    active_turn["checkpoint"] = {
                        **dict(active_turn.get("checkpoint") or {}),
                        "stop_reason": f"evaluation exceeded {case.max_seconds}s",
                    }
                    summary = _build_task_execution_summary(
                        active_turn.get("details") or [],
                        active_turn.get("checkpoint") or {},
                        "partial",
                    )
                    tracer.task_summary(
                        summary=summary,
                        status="partial",
                        tool_call_count=len(active_turn.get("details") or []),
                        turn=active_turn["turn"],
                    )
                    active_turn["summary_written"] = True
                change_set = locals().get("change_set")
                if change_set is not None:
                    change_set.rollback()
                result.status = "timeout"
                result.error = f"evaluation exceeded {case.max_seconds}s"
                timeout_checkpoint = dict(
                    active_turn.get("checkpoint") if "active_turn" in locals() else {}
                )
                timeout_checkpoint["status"] = "timed_out"
                timeout_checkpoint["stop_reason"] = result.error
                finalize_task("timed_out", timeout_checkpoint)
            except Exception as exc:
                if (
                    "tracer" in locals()
                    and "active_turn" in locals()
                    and active_turn["turn"]
                    and not active_turn["summary_written"]
                ):
                    active_turn["checkpoint"] = {
                        **dict(active_turn.get("checkpoint") or {}),
                        "stop_reason": f"{type(exc).__name__}: {exc}",
                    }
                    summary = _build_task_execution_summary(
                        active_turn.get("details") or [],
                        active_turn.get("checkpoint") or {},
                        "failed",
                    )
                    tracer.task_summary(
                        summary=summary,
                        status="failed",
                        tool_call_count=len(active_turn.get("details") or []),
                        turn=active_turn["turn"],
                    )
                    active_turn["summary_written"] = True
                change_set = locals().get("change_set")
                if change_set is not None:
                    change_set.rollback()
                result.status = "error"
                result.error = f"{type(exc).__name__}: {exc}"
                failed_checkpoint = dict(
                    active_turn.get("checkpoint") if "active_turn" in locals() else {}
                )
                failed_checkpoint["status"] = "failed"
                failed_checkpoint["stop_reason"] = result.error
                finalize_task("failed", failed_checkpoint)
            finally:
                result.trace_path = str(Path(tracer.path)) if "tracer" in locals() else ""

        result.workspace = ""
        result.total_tokens = max(result.total_tokens, _trace_total_tokens(result.trace_path))
        result.duration_ms = round((time.monotonic() - started) * 1000, 1)
        self._append_result(result)
        get_agent_metrics().record_run(f"eval_{result.status}")
        return result

    @staticmethod
    def _finish(result: EvalResult, status: str, error: str, started: float) -> EvalResult:
        result.status = status
        result.error = error
        result.duration_ms = round((time.monotonic() - started) * 1000, 1)
        return result

    def _append_result(self, result: EvalResult) -> None:
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with self.results_path.open("a", encoding="utf-8") as handle:
            handle.write(result.model_dump_json() + "\n")

    def list_results(self, limit: int = 100) -> list[dict]:
        if not self.results_path.is_file():
            return []
        lines = self.results_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 1000)):]
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result
