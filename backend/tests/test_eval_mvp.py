"""评测 MVP 的最小端到端闭环。"""

import asyncio
import hashlib
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
    assert len(cases) == 16
    assert "radar-base-001" in cases
    assert "radar_sim_v1" in projects
    assert "radar_sim_validation_v1" in projects
    assert "radar_sim_noise_seed_v1" in projects

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
