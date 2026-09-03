"""评测 MVP 的最小端到端闭环。"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from app.agent_base.agents.react_agent import ReActProgress
from app.evals.models import EvalCase
from app.evals.checkers import build_checkers
from app.evals.projects import load_projects, resolve_fixture
from app.evals.registry import load_cases
from app.evals.runner import EvalRunner
from app.evals.batches import EvalArchiveRequest, EvalBatch, EvalBatchManager, summarize
from app.api.evals import BASELINE_PATH, get_baseline, get_repository


class _FakeAgent:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.llm = type("FakeLLM", (), {"model": "fake-model"})()

    async def arun_stream(self, prompt: str):
        (self.workspace / "result.txt").write_text("evaluation passed", encoding="utf-8")
        yield ReActProgress(step=1, thought="done", is_final=True, final_answer="done")


async def _factory(workspace, case):
    return _FakeAgent(workspace)


def test_eval_runner_fixture_checker_trace_and_result(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "input.txt").write_text("input", encoding="utf-8")
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))

    case = EvalCase(
        id="file-change",
        prompt="create result",
        fixture=str(fixture),
        checkers=[{"type": "file_contains", "path": "result.txt", "text": "passed"}],
    )
    result = __import__("asyncio").run(
        EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory)
    )

    assert result.status == "passed"
    assert result.passed is True
    assert result.score == 1.0
    assert result.trace_path.endswith(f"{result.run_id}.jsonl")
    assert (tmp_path / "results.jsonl").is_file()
    assert result.workspace == ""


def test_eval_runner_reuses_agent_for_all_natural_language_turns(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))

    class _MultiAgent:
        def __init__(self, workspace):
            self.workspace = workspace
            self.llm = type("FakeLLM", (), {"model": "deepseek-v4-flash"})()
            self.tool_registry = __import__("app.agent_base.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry()
            self.last_run_checkpoint = {}
            self.seen = []

        async def arun_stream(self, prompt, **kwargs):
            self.seen.append(prompt)
            (self.workspace / "result.txt").write_text("evaluation passed", encoding="utf-8")
            yield ReActProgress(step=1, is_final=True, final_answer=f"done: {prompt}")

    agents = []

    async def _factory(workspace, case):
        agent = _MultiAgent(workspace)
        agents.append(agent)
        return agent

    case = EvalCase(
        id="multiturn-status",
        turns=[
            {"prompt": "先完成第一步"},
            {"prompt": "再完成第二步"},
            {"prompt": "上面的任务执行完成了吗？"},
        ],
        checkers=[{"type": "file_contains", "path": "result.txt", "text": "passed"}],
    )
    result = asyncio.run(EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory))

    assert result.status == "passed"
    assert agents[0].seen == ["先完成第一步", "再完成第二步", "上面的任务执行完成了吗？"]
    assert [turn["status"] for turn in result.metadata["turns"]] == [
        "completed", "completed", "completed",
    ]


def test_eval_runner_persists_missing_fixture_result(tmp_path):
    case = EvalCase(
        id="missing-fixture",
        prompt="run with missing fixture",
        fixture=str(tmp_path / "missing"),
    )
    results_path = tmp_path / "results.jsonl"
    result = __import__("asyncio").run(EvalRunner(results_path).run_case(case, _factory))

    assert result.status == "error"
    assert "fixture not found" in result.error
    assert results_path.read_text(encoding="utf-8").count("missing-fixture") == 1


async def _run_checkers(workspace, configs):
    return await asyncio.gather(*(checker.check(workspace) for checker in build_checkers(configs)))


def test_radar_eval_catalog_and_uml_checkers():
    cases = load_cases()
    projects = load_projects()
    assert len(cases) == 18
    assert "radar-base-001" in cases
    assert "radar_sim_v1" in projects
    assert "radar_sim_validation_v1" in projects
    assert "radar_sim_noise_seed_v1" in projects
    assert "radar_trace_remove_v1" in projects
    trace_case = cases["trace-3-1-component-element-multiturn-001"]
    assert len(trace_case.turns) == 3
    assert trace_case.metadata["require_auto_approval"] is True
    continuous_case = cases["trace-3-1-component-element-continuous-remove-001"]
    assert len(continuous_case.turns) == 7
    assert continuous_case.metadata["reference_turn_count"] == 7
    assert continuous_case.metadata["baseline_comparable"] is True

    fixture, manifest = resolve_fixture(cases["radar-base-001"])
    assert fixture is not None and fixture.is_dir()
    assert manifest is not None
    assert manifest.entry_file == "design/radar_sim_design.umlproj"

    configs = [
        {"type": "uml_valid", "path": "design/radar_sim_design.umlproj"},
        {"type": "uml_contains", "path": "design/radar_sim_design.umlproj", "kind": "component", "name": "EchoSimulation", "diagram": "Radar System Architecture"},
        {"type": "uml_relation", "path": "design/radar_sim_design.umlproj", "source": "PulseCompression", "target": "EchoSimulation", "relation_type": "dependency", "diagram": "Radar System Architecture"},
        {"type": "uml_method", "path": "design/radar_sim_design.umlproj", "class_name": "PeakDetector", "method": "detect", "diagram": "Pulse Compression"},
        {"type": "uml_sequence", "path": "design/radar_sim_design.umlproj", "labels": ["setMode", "generateEcho", "compress", "detect"], "diagram": "Full Radar Signal Processing Flow"},
    ]
    results = asyncio.run(_run_checkers(fixture, configs))
    assert all(item.passed for item in results), [(item.checker, item.passed, item.message) for item in results]


def test_uml_component_names_checker_matches_exact_component_set(tmp_path):
    project = {
        "diagrams": [{
            "name": "Components",
            "diagram_type": "component",
            "components": [{"name": "ModeControl"}, {"name": "EchoSimulation"}],
        }],
    }
    (tmp_path / "project.umlproj").write_text(
        __import__("json").dumps(project), encoding="utf-8",
    )
    checker = build_checkers([{
        "type": "uml_component_names",
        "path": "project.umlproj",
        "diagram": "Components",
        "names": ["EchoSimulation", "ModeControl"],
    }])[0]

    result = asyncio.run(checker.check(tmp_path))

    assert result.passed is True


def test_file_absent_checker_requires_explicit_path_to_be_removed(tmp_path):
    target = tmp_path / "temporary.txt"
    target.write_text("temporary", encoding="utf-8")
    checker = build_checkers([{"type": "file_absent", "path": "temporary.txt"}])[0]

    before = asyncio.run(checker.check(tmp_path))
    target.unlink()
    after = asyncio.run(checker.check(tmp_path))

    assert before.passed is False
    assert after.passed is True


def test_file_not_contains_checker_rejects_stale_text(tmp_path):
    target = tmp_path / "source.py"
    target.write_text("NewComponent component", encoding="utf-8")
    checker = build_checkers([{
        "type": "file_not_contains",
        "path": "source.py",
        "text": "OldComponent component",
    }])[0]

    clean = asyncio.run(checker.check(tmp_path))
    target.write_text("OldComponent component", encoding="utf-8")
    stale = asyncio.run(checker.check(tmp_path))

    assert clean.passed is True
    assert stale.passed is False


def test_turn_hard_checker_failure_cannot_be_masked_by_later_turns(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))

    class _ProductionLikeAgent:
        def __init__(self, workspace):
            self.workspace = workspace
            self.llm = type("FakeLLM", (), {"model": "fake-model"})()
            self.tool_registry = object()
            self.last_context_report = {}

        async def arun_stream(self, prompt, **kwargs):
            (self.workspace / "result.txt").write_text("done", encoding="utf-8")
            yield ReActProgress(step=1, is_final=True, final_answer="done")

    async def _factory(workspace, case):
        return _ProductionLikeAgent(workspace)

    case = EvalCase(
        id="turn-hard-failure",
        turns=[
            {
                "prompt": "完成源码同步",
                "hard_checkers": [{
                    "type": "file_contains",
                    "path": "result.txt",
                    "text": "required source change",
                }],
            },
            {"prompt": "继续执行后续任务"},
        ],
    )
    result = asyncio.run(
        EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory)
    )

    assert result.status == "failed"
    assert result.passed is False
    assert any(
        item.checker == "file_contains" and not item.passed
        for item in result.checker_results
    )


def test_multiturn_eval_keeps_independent_token_budgets(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))
    traced_usage = iter([30, 60, 60])
    monkeypatch.setattr(
        "app.evals.runner._trace_total_tokens",
        lambda trace_path: next(traced_usage),
    )

    class _ProductionLikeAgent:
        def __init__(self, workspace):
            self.llm = type("FakeLLM", (), {"model": "fake-model"})()
            self.tool_registry = object()
            self.last_context_report = {}
            self.initial_usage = []

        async def arun_stream(self, prompt, **kwargs):
            self.initial_usage.append(kwargs.get("initial_token_usage"))
            yield ReActProgress(step=1, is_final=True, final_answer="done")

    agents = []

    async def _factory(workspace, case):
        agent = _ProductionLikeAgent(workspace)
        agents.append(agent)
        return agent

    case = EvalCase(
        id="cumulative-token-budget",
        turns=[{"prompt": "第一轮"}, {"prompt": "第二轮"}],
        hard_checkers=[{"type": "file_exists", "path": "missing.txt"}],
    )
    result = asyncio.run(
        EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory)
    )

    # Prior-turn usage is report-only; each task starts its own Agent budget.
    assert agents[0].initial_usage == [None, None]
    assert result.total_tokens == 60
    events = [
        json.loads(line)
        for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()
    ]
    summaries = [event for event in events if event.get("event_type") == "task_summary"]
    assert [event.get("turn") for event in summaries] == [1, 2]


def test_eval_runner_records_hard_budget_exhaustion_separately(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))

    class _BudgetAgent:
        def __init__(self, workspace):
            self.workspace = workspace
            self.llm = type("FakeLLM", (), {"model": "fake-model"})()
            self.tool_registry = object()
            self.last_context_report = {}

        async def arun_stream(self, prompt, **kwargs):
            (self.workspace / "result.txt").write_text("done", encoding="utf-8")
            self.last_context_report = {
                "token_budget_used": 100,
                "token_budget_stop_reason": "hard_limit_before_next_llm",
            }
            yield ReActProgress(step=1, is_final=True, final_answer="done")

    async def _factory(workspace, case):
        return _BudgetAgent(workspace)

    case = EvalCase(
        id="hard-budget-status",
        prompt="完成任务",
        checkers=[{"type": "file_contains", "path": "result.txt", "text": "done"}],
    )
    result = asyncio.run(
        EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory)
    )

    assert result.status == "budget_exceeded"
    assert result.passed is False
    assert result.error == "evaluation stopped after a hard execution budget was exhausted"
    assert result.metadata["token_budget_stop_reasons"][0]["reason"] == (
        "hard_limit_before_next_llm"
    )


def test_eval_runner_records_budget_finalization_separately(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("app.services.chat_trace._chat_log_dir", lambda: str(trace_dir))

    class _FinalizingAgent:
        def __init__(self, workspace):
            self.llm = type("FakeLLM", (), {"model": "fake-model"})()
            self.tool_registry = object()
            self.last_context_report = {}

        async def arun_stream(self, prompt, **kwargs):
            (self.workspace / "result.txt").write_text("done", encoding="utf-8")
            self.last_context_report = {
                "token_budget_used": 90,
                "token_budget_stop_reason": "reserve_finalization",
            }
            yield ReActProgress(step=1, is_final=True, final_answer="done")

    async def _factory(workspace, case):
        agent = _FinalizingAgent(workspace)
        agent.workspace = workspace
        return agent

    case = EvalCase(
        id="budget-finalized-status",
        prompt="完成任务",
        checkers=[{"type": "file_contains", "path": "result.txt", "text": "done"}],
    )
    result = asyncio.run(
        EvalRunner(tmp_path / "results.jsonl").run_case(case, _factory)
    )

    assert result.status == "budget_finalized"
    assert result.passed is True


def test_eval_cases_are_pinned_to_devagent():
    with pytest.raises(ValueError):
        EvalCase(id="legacy-agent", prompt="legacy", agent="legacy")


def test_devagent_baseline_snapshot_is_available():
    baseline = asyncio.run(get_baseline())

    assert BASELINE_PATH.is_file()
    assert baseline["agent"] == "devagent"
    assert baseline["case_count"] == 16
    assert baseline["passed"] == 10
    assert baseline["pass_rate"] == 0.625
    assert len(baseline["groups"]) == 6


def test_devagent_repository_version_is_available():
    repository = asyncio.run(get_repository())

    assert repository["branch"]
    assert repository["commit"]
    assert repository["version"].endswith(repository["commit"])
    assert isinstance(repository["dirty"], bool)


def test_paths_unchanged_detects_mutation(tmp_path):
    protected = tmp_path / "protected.txt"
    protected.write_text("before", encoding="utf-8")
    baseline = {"protected.txt": hashlib.sha256(protected.read_bytes()).hexdigest()}
    checker = build_checkers(
        [{"type": "paths_unchanged", "paths": ["protected.txt"]}], baseline
    )[0]

    protected.write_text("after", encoding="utf-8")
    result = asyncio.run(checker.check(tmp_path))
    assert result.passed is False


def test_eval_batch_summary_aggregates_runtime_metrics():
    results = [
        type("Result", (), {"status": "passed", "score": 1.0, "duration_ms": 100.0, "total_tokens": 10, "tool_calls": 2})(),
        type("Result", (), {"status": "failed", "score": 0.5, "duration_ms": 300.0, "total_tokens": 30, "tool_calls": 4})(),
        type("Result", (), {"status": "timeout", "score": 0.0, "duration_ms": 500.0, "total_tokens": 20, "tool_calls": 1})(),
    ]

    summary = summarize(results, total=4)

    assert summary.total == 4
    assert summary.completed == 3
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.timeout == 1
    assert summary.pass_rate == 0.3333
    assert summary.average_score == 0.5
    assert summary.total_tokens == 60
    assert summary.total_tool_calls == 7


def test_eval_batch_summary_counts_budget_statuses():
    results = [
        type("Result", (), {"status": "budget_exceeded", "passed": False, "score": 0.0, "duration_ms": 100.0, "total_tokens": 10, "tool_calls": 2})(),
        type("Result", (), {"status": "budget_finalized", "passed": True, "score": 1.0, "duration_ms": 200.0, "total_tokens": 20, "tool_calls": 3})(),
    ]

    summary = summarize(results)

    assert summary.passed == 1
    assert summary.budget_exceeded == 1
    assert summary.budget_finalized == 1
    assert summary.failed == 0
    assert summary.timeout == 0
    assert summary.errors == 0


def test_eval_batch_archive_writes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("app.evals.batches._eval_root", lambda: tmp_path / "evals")
    manager = EvalBatchManager()
    batch = EvalBatch(
        batch_id="batch_test",
        suite="baseline",
        version="v3.0-test",
        case_ids=["case-1"],
        status="completed",
        started_at="2026-08-31T00:00:00+00:00",
        finished_at="2026-08-31T00:01:00+00:00",
    )
    manager._batches[batch.batch_id] = batch

    archive = manager.archive(EvalArchiveRequest(batch_id=batch.batch_id, note="test"))

    assert archive["batch_id"] == batch.batch_id
    archive_path = tmp_path / "evals" / "archives" / f"{archive['archive_id']}.json"
    assert archive_path.is_file()
    assert '"v3.0-test"' in archive_path.read_text(encoding="utf-8")


def test_eval_batch_archive_baseline_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("app.evals.batches._eval_root", lambda: tmp_path / "evals")
    manager = EvalBatchManager()

    archive = manager.archive_baseline(
        {
            "agent": "devagent",
            "label": "DevAgent 3.0 基线",
            "version": "a1122e8",
            "captured_at": "2026-09-01T03:10:58+00:00",
            "case_count": 16,
            "passed": 10,
            "failed": 1,
            "timeout": 5,
            "pass_rate": 0.625,
            "average_score": 0.6667,
            "total_duration_ms": 1930200,
            "total_tokens": 6639458,
            "total_tool_calls": 602,
        },
        note="baseline",
    )

    archive_path = tmp_path / "evals" / "archives" / f"{archive['archive_id']}.json"
    assert archive_path.is_file()
    assert '"DevAgent 3.0 基线"' in archive_path.read_text(encoding="utf-8")
