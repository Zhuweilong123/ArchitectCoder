"""隔离评测 Runner：fixture → Agent → checker → trace/result。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
from app.agent_base.tools.registry import ToolRegistry
from app.core.config import get_settings
from app.services.agent_metrics import get_agent_metrics
from app.services.chat_trace import TraceSession
from app.services.model_router import choose_model

from .checkers import build_checkers
from .models import CheckerResult, EvalCase, EvalResult
from .projects import load_projects, resolve_fixture

AgentFactory = Callable[[Path, EvalCase], Awaitable[Any]]


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


async def react_agent_factory(workspace: Path, case: EvalCase) -> ReActAgent:
    settings = get_settings()
    route = choose_model(case.prompt, settings)
    llm = BaseAgentsLLM.from_settings(model=route.model, temperature=0.2)
    manifest = load_projects().get(case.project_id) if case.project_id else None
    # An eval fixture is its own isolated project.  Its source, tests, design
    # and migration artifacts (for example ``legacy/``) are all task resources,
    # so make the copied fixture root the tool sandbox.  Restricting tools to
    # ``src/`` here made valid sibling resources unreachable and encouraged
    # temporary diagnostic scripts instead of the requested edit.
    source_dir = workspace
    test_dir = workspace / manifest.test_dir if manifest else workspace
    project_file = workspace / manifest.entry_file if manifest and manifest.entry_file else workspace / "evaluation.umlproj"
    tools, _ = create_conversation_tools(
        llm, source_dir=str(source_dir), test_dir=str(test_dir),
        project_file=str(project_file), include_review=False,
        task_scope=f"eval_{case.id}",
    )
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return ReActAgent(
        name=f"EvalAgent:{case.id}", llm=llm, tool_registry=registry,
        # Eval cases own their explicit budget. The interactive-agent default
        # is intentionally lower, but capping diagnostics at that value makes
        # focused cases fail before they can perform the requested edit.
        max_steps=case.max_tool_calls,
        max_tool_calls=case.max_tool_calls,
        max_total_tokens=min(settings.agent_max_total_tokens, case.max_total_tokens),
        max_run_seconds=case.max_seconds,
        llm_timeout_seconds=settings.agent_llm_timeout_seconds,
        use_native_fc=True,
    )


async def dev_agent_factory(workspace: Path, case: EvalCase) -> ReActAgent:
    """Build the production DevAgent inside the isolated evaluation workspace.

    The interactive WebSocket path and this factory share the same agent
    assembly function. Evaluation only changes the workspace, run budgets and
    approval policy; it does not replace the production prompt/tool chain.
    """
    from app.agent_base.tools.my_tools.conversation_tools import ProgressRelay
    from app.services.agent_chat_ws import (
        _create_dev_agent, _enabled_tools_context, _needs_strategy_planning,
        _requires_todo_plan, _select_tools_for_message, _todo_plan_context,
    )

    settings = get_settings()
    route = choose_model(case.prompt, settings)
    llm = BaseAgentsLLM.from_settings(model=route.model, temperature=0.3)
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
        user_message=case.prompt,
        progress=progress,
        task_scope=f"eval_{case.id}",
        auto_approve_reviews=True,
        max_steps=case.max_tool_calls,
        max_tool_calls=case.max_tool_calls,
        max_run_seconds=case.max_seconds,
        max_total_tokens=min(settings.agent_max_total_tokens, case.max_total_tokens),
    )
    agent._eval_context = await prompt_builder.build_context(
        str(project_file), str(source_dir), str(test_dir), case.prompt,
    )
    agent._eval_progress = progress
    agent._eval_review_manager = review_mgr
    agent._eval_agent_mode = "devagent"
    agent._eval_allowed_tools = _select_tools_for_message(agent.tool_registry, case.prompt)
    agent._eval_requires_acceptance_todos = _needs_strategy_planning(case.prompt)
    agent._eval_requires_todo_plan = _requires_todo_plan(case.prompt)
    agent._eval_context = "\n\n".join(filter(None, [
        agent._eval_context, _enabled_tools_context(agent._eval_allowed_tools),
        _todo_plan_context(
            agent._eval_requires_todo_plan,
            agent._eval_requires_acceptance_todos,
        ),
    ]))
    return agent


async def default_agent_factory(workspace: Path, case: EvalCase) -> Any:
    if case.agent.lower() in {"react", "reactagent", "legacy"}:
        return await react_agent_factory(workspace, case)
    return await dev_agent_factory(workspace, case)


class EvalRunner:
    def __init__(self, results_path: str | Path | None = None):
        self.results_path = Path(results_path) if results_path else _default_results_path()

    async def run_case(self, case: EvalCase, agent_factory: AgentFactory | None = None) -> EvalResult:
        run_id = f"eval_{uuid.uuid4().hex[:16]}"
        result = EvalResult.started(run_id, case.id)
        started = time.monotonic()
        factory = agent_factory or default_agent_factory
        result.metadata["project_id"] = case.project_id

        try:
            fixture, manifest = resolve_fixture(case)
        except ValueError as exc:
            finished = self._finish(result, "error", str(exc), started)
            self._append_result(finished)
            get_agent_metrics().record_run("eval_error")
            return finished

        with tempfile.TemporaryDirectory(prefix=f"{run_id}_") as temp_dir:
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
                for child in fixture.iterdir():
                    if child.name in {".pytest_cache", "__pycache__", "temp_pytest.txt"}:
                        continue
                    target = workspace / child.name
                    if child.is_dir():
                        shutil.copytree(child, target)
                    else:
                        shutil.copy2(child, target)

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
                async with TraceSession(
                    session_id=run_id, user_message=case.prompt,
                    source_dir=str(source_dir), test_dir=str(test_dir),
                    env_snapshot={"eval_case": case.id},
                ) as tracer:
                    result.trace_id = tracer.trace_id
                    agent = await factory(workspace, case)
                    result.model = getattr(getattr(agent, "llm", None), "model", "")
                    result.metadata["agent"] = getattr(agent, "_eval_agent_mode", "react")
                    change_set = getattr(agent, "change_set", None)
                    if change_set is not None:
                        change_set.begin()

                    async def consume() -> None:
                        context = getattr(agent, "_eval_context", "")
                        allowed_tools = getattr(agent, "_eval_allowed_tools", None)
                        if context:
                            stream_kwargs = {"context": context}
                            if allowed_tools is not None:
                                stream_kwargs["allowed_tools"] = allowed_tools
                            stream = agent.arun_stream(case.prompt, **stream_kwargs)
                        elif allowed_tools is not None:
                            stream = agent.arun_stream(case.prompt, allowed_tools=allowed_tools)
                        else:
                            # Preserve the original ``arun_stream(prompt)``
                            # contract for lightweight test doubles.
                            stream = agent.arun_stream(case.prompt)
                        async for progress in stream:
                            result.tool_calls += sum(
                                detail.get("status") != "blocked"
                                for detail in progress.tool_calls_detail or []
                            )
                            for detail in progress.tool_calls_detail or []:
                                result.total_tokens += int(detail.get("total_tokens") or 0)
                            if progress.is_final:
                                tracer.done(answer=progress.final_answer or "")

                    from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
                    runtime_token = set_runtime(AgentRuntime(
                        requires_todo_plan=bool(
                            getattr(agent, "_eval_requires_todo_plan", False)
                        ),
                        requires_acceptance_todos=bool(
                            getattr(agent, "_eval_requires_acceptance_todos", False)
                        ),
                    ))
                    try:
                        await asyncio.wait_for(consume(), timeout=case.max_seconds)
                    finally:
                        reset_runtime(runtime_token)
                    if change_set is not None:
                        result.metadata["change_set"] = change_set.commit()
                    review_mgr = getattr(agent, "_eval_review_manager", None)
                    progress_relay = getattr(agent, "_eval_progress", None)
                    if progress_relay is not None:
                        for event in progress_relay.events:
                            if event.get("event") != "review":
                                continue
                            tracer.review_request(
                                review_id=event.get("review_id", 0),
                                review_type=event.get("review_type", ""),
                                title=event.get("title", ""),
                                question=event.get("question", ""),
                                content=event.get("content", ""),
                            )
                    if review_mgr is not None:
                        result.metadata["approval_events"] = review_mgr.approval_events
                        for event in review_mgr.approval_events:
                            if event.get("event") == "review_response":
                                tracer.review_response(
                                    review_id=event.get("review_id", 0),
                                    response=json.dumps(event, ensure_ascii=False),
                                )
                    if progress_relay is not None:
                        result.metadata["progress_events"] = progress_relay.events
                    hard_results = await asyncio.gather(*(
                        checker.check(workspace) for checker in build_checkers(case.hard_checkers, baseline_hashes)
                    ))
                    score_results = await asyncio.gather(*(
                        checker.check(workspace) for checker in build_checkers(case.checkers, baseline_hashes)
                    ))
                    checker_results = [*hard_results, *score_results]
                    result.checker_results = list(checker_results)
                    result.score = (
                        sum(item.score for item in checker_results) / len(checker_results)
                        if checker_results else 1.0
                    )
                    result.passed = bool(checker_results) and all(item.passed for item in checker_results)
                    result.status = "passed" if result.passed else "failed"
            except asyncio.TimeoutError:
                change_set = locals().get("change_set")
                if change_set is not None:
                    change_set.rollback()
                result.status = "timeout"
                result.error = f"evaluation exceeded {case.max_seconds}s"
            except Exception as exc:
                change_set = locals().get("change_set")
                if change_set is not None:
                    change_set.rollback()
                result.status = "error"
                result.error = f"{type(exc).__name__}: {exc}"
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
