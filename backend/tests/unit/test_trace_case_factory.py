from pathlib import Path

import asyncio
import json
from types import SimpleNamespace

import pytest

from extensions.evals.trace_cases import TraceCaseCaptureRequest, TraceCaseDraftRequest, TraceCasePublishRequest, TraceCaseReviewRequest
from extensions.evals import trace_cases
from extensions.evals.models import EVAL_TRACE_TOOL_NAMES, EvalCase, EvalResult
from extensions.evals.trace_cases import _case_from_extracted, _extract_trace


def test_extract_trace_preserves_turns_and_supported_tools():
    events = [
        {"event_type": "session_start", "user_message": "ignored fallback"},
        {"event_type": "user_message", "message": "add a file"},
        {"event_type": "tool_call", "tool_name": "apply_changes"},
        {"event_type": "tool_result", "tool_name": "apply_changes"},
        {"event_type": "done", "answer": "done"},
        {"event_type": "user_message", "message": "run the tests"},
        {"event_type": "tool_call", "tool_name": "run_task"},
        {"event_type": "tool_result", "tool_name": "run_task", "error": "failed"},
    ]

    extracted = _extract_trace(events)

    assert [turn["prompt"] for turn in extracted["turns"]] == ["add a file", "run the tests"]
    assert extracted["supported_tool_names"] == ["apply_changes", "run_task"]
    assert extracted["tool_errors"] == 1
    assert set(extracted["supported_tool_names"]).issubset(EVAL_TRACE_TOOL_NAMES)


def test_observed_tools_are_scoped_candidates_not_automatic_pass_conditions():
    extracted = _extract_trace([
        {"event_type": "user_message", "message": "fix code"},
        {"event_type": "tool_call", "tool_name": "apply_changes"},
        {"event_type": "done", "answer": "incorrect historical answer"},
        {"event_type": "user_message", "message": "run tests"},
        {"event_type": "tool_call", "tool_name": "run_task"},
        {"event_type": "user_message", "message": "thanks"},
    ])
    case, candidates, _ = _case_from_extracted(TraceCaseDraftRequest(session_id="scoped"), extracted, "hash")
    assert not case.checkers and not case.hard_checkers
    assert all(not turn.checkers and not turn.hard_checkers for turn in case.turns)
    assert [(item["_evidence"]["turn_index"], item["required_tools"]) for item in candidates] == [
        (1, ["apply_changes"]), (2, ["run_task"]),
    ]
    assert "incorrect historical answer" not in case.model_dump_json()


@pytest.fixture
def captured_case(tmp_path, monkeypatch):
    from extensions.evals import projects
    workspace = tmp_path / "workspace"
    for name in ["src", "test", "design"]:
        (workspace / name).mkdir(parents=True)
    (workspace / "src/main.py").write_text("broken", encoding="utf-8")
    (workspace / "test/test_main.py").write_text("def test_ok(): pass", encoding="utf-8")
    (workspace / "design/project.umlproj").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(trace_cases, "_draft_root", lambda: tmp_path / "drafts")
    monkeypatch.setattr(trace_cases, "_capture_root", lambda: tmp_path / "captures")
    monkeypatch.setattr(trace_cases, "_allowed_workspace_roots", lambda: [tmp_path])
    for module in [trace_cases, projects]:
        monkeypatch.setattr(module, "fixtures_dir", lambda: tmp_path / "fixtures")
        monkeypatch.setattr(module, "projects_dir", lambda: tmp_path / "projects")
    monkeypatch.setattr(trace_cases, "cases_dir", lambda: tmp_path / "cases")
    draft = trace_cases.TraceCaseDraft(
        draft_id="tcd_regression", created_at="2026-01-01", session_id="session", trace_sha256="hash",
        case=EvalCase(id="regression", prompt="fix code", hard_checkers=[
            {"type": "file_contains", "path": "src/main.py", "text": "fixed"},
        ]),
        workspace={"source_dir": str(workspace / "src"), "test_dir": str(workspace / "test"),
                   "project_file": str(workspace / "design/project.umlproj")},
    )
    trace_cases._save_draft(draft)
    factory = trace_cases.TraceCaseFactory(None)
    captured = factory.capture(draft.draft_id, TraceCaseCaptureRequest())
    factory.review(draft.draft_id, TraceCaseReviewRequest(
        project_id=captured["case"]["project_id"], hard_checkers=draft.case.hard_checkers,
        fixture_state_confirmed=True,
    ))
    return factory, draft.draft_id


def test_review_can_select_turns_and_remove_legacy_per_turn_policies(captured_case):
    from extensions.evals.models import EvalTurn
    factory, draft_id = captured_case
    draft = trace_cases._load_draft(draft_id)
    draft.case.turns = [EvalTurn(prompt="fix", checkers=[{"type": "trace_policy", "required_tools": ["apply_changes"]}]),
                        EvalTurn(prompt="thanks")]
    trace_cases._save_draft(draft)
    updated = factory.review(draft_id, TraceCaseReviewRequest(
        project_id=draft.case.project_id,
        turns=[EvalTurn(prompt="check correction", hard_checkers=[{"type": "answer_contains_all", "texts": ["fixed"]}],
                        metadata={"source_turn_index": 1})],
    ))
    assert updated["case"]["prompt"] == ""
    assert len(updated["case"]["turns"]) == 1
    assert updated["case"]["turns"][0]["checkers"] == []
    with pytest.raises(ValueError, match="blank"):
        factory.review(draft_id, TraceCaseReviewRequest(turns=[EvalTurn(prompt=" ")]))


def test_validate_requires_explicit_criteria_and_confirmed_starting_state(captured_case):
    factory, draft_id = captured_case
    draft = trace_cases._load_draft(draft_id)
    draft.case.hard_checkers = []
    trace_cases._save_draft(draft)
    with pytest.raises(ValueError, match="hard checker"):
        asyncio.run(factory.validate(draft_id))
    draft.case.hard_checkers = [{"type": "file_exists", "path": "src/main.py"}]
    draft.capture["baseline_confirmed"] = False
    trace_cases._save_draft(draft)
    with pytest.raises(ValueError, match="starting state"):
        asyncio.run(factory.validate(draft_id))


@pytest.mark.parametrize("changed", ["fixture", "case", "manifest", "missing_fixture"])
def test_publish_rejects_changes_after_validation(captured_case, changed):
    factory, draft_id = captured_case
    class PassingRunner:
        async def run_case(self, case, **kwargs):
            return EvalResult(run_id="run", case_id=case.id, status="passed", passed=True)
    factory.runner = PassingRunner()
    asyncio.run(factory.validate(draft_id))
    draft = trace_cases._load_draft(draft_id)
    if changed == "fixture":
        (Path(draft.capture["staging_path"]) / "src/main.py").write_text("changed", encoding="utf-8")
    elif changed == "case":
        draft.case.prompt = "a different task"
    elif changed == "missing_fixture":
        draft.capture["staging_path"] = str(Path(draft.capture["staging_path"]).parent / "missing")
    else:
        draft.capture["manifest"]["source_dir"] = "."
    trace_cases._save_draft(draft)
    with pytest.raises(ValueError, match="changed since validation"):
        factory.publish(draft_id, TraceCasePublishRequest())
    assert not trace_cases.cases_dir().exists()
    assert factory.get(draft_id)["validation"] is None


def test_validation_does_not_overwrite_review_saved_while_running(captured_case):
    factory, draft_id = captured_case
    class EditingRunner:
        async def run_case(self, case, **kwargs):
            factory.review(draft_id, TraceCaseReviewRequest(
                name="New review", project_id=case.project_id, hard_checkers=case.hard_checkers,
            ))
            return EvalResult(run_id="run", case_id=case.id, status="passed", passed=True)
    factory.runner = EditingRunner()
    with pytest.raises(ValueError, match="during validation"):
        asyncio.run(factory.validate(draft_id))
    assert factory.get(draft_id)["case"]["name"] == "New review"
    assert factory.get(draft_id)["validation"] is None


def test_base_fixture_changes_invalidate_validation(captured_case, tmp_path):
    factory, draft_id = captured_case
    base = tmp_path / "fixtures/base"
    base.mkdir(parents=True)
    (base / "config.json").write_text("{}", encoding="utf-8")
    draft = trace_cases._load_draft(draft_id)
    draft.capture["manifest"]["base_fixture"] = "base"
    trace_cases._save_draft(draft)
    before = factory._validation_fingerprint(draft)
    (base / "config.json").write_text('{"changed": true}', encoding="utf-8")
    assert factory._validation_fingerprint(draft) != before


def test_recapture_resets_confirmation_and_uses_same_plan_as_preview(captured_case):
    factory, draft_id = captured_case
    draft = trace_cases._load_draft(draft_id)
    old_staging = Path(draft.capture["staging_path"])
    source = Path(draft.workspace["source_dir"])
    (source / ".env.local").write_text("secret", encoding="utf-8")
    (source / ".architectcoder").mkdir()
    (source / ".architectcoder/state.json").write_text("state", encoding="utf-8")
    preview = factory.preview(draft_id, TraceCaseCaptureRequest())
    captured = factory.capture(draft_id, TraceCaseCaptureRequest())
    staging = Path(captured["capture"]["staging_path"])
    actual = sorted(path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file())
    assert actual == [item["path"] for item in preview["files"]]
    assert not old_staging.exists()
    assert captured["capture"]["baseline_confirmed"] is False
    assert captured["validation"] is None


def test_changed_staging_must_be_recaptured_before_validation(captured_case):
    factory, draft_id = captured_case
    draft = trace_cases._load_draft(draft_id)
    (Path(draft.capture["staging_path"]) / "src/main.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="capture a new snapshot"):
        asyncio.run(factory.validate(draft_id))


def test_real_runner_uses_same_manifest_before_and_after_publish(captured_case, tmp_path, monkeypatch):
    from extensions.evals import runner, projects
    from app.agent_base.agents.react_agent import ReActProgress
    factory, draft_id = captured_case
    seen = []
    class Agent:
        llm = SimpleNamespace(model="fake-model")
        def __init__(self, workspace, manifest):
            self.workspace, self.manifest = workspace, manifest
        async def arun_stream(self, prompt):
            (self.workspace / self.manifest.source_dir / "main.py").write_text("fixed", encoding="utf-8")
            yield ReActProgress(step=1, is_final=True, final_answer="fixed")
    async def agent_factory(workspace, case, *, manifest_override=None):
        manifest = manifest_override or projects.load_projects()[case.project_id]
        seen.append(manifest.model_dump())
        assert (workspace / manifest.entry_file).is_file()
        assert (workspace / manifest.test_dir).is_dir()
        return Agent(workspace, manifest)
    monkeypatch.setattr(runner, "dev_agent_factory", agent_factory)
    monkeypatch.setattr(runner, "get_settings", lambda: original_settings)
    from config.settings import get_settings
    original_settings = get_settings().model_copy(update={"agent_max_run_seconds": 0})
    factory.runner = runner.EvalRunner(tmp_path / "results.jsonl")
    validated = asyncio.run(factory.validate(draft_id))
    assert validated["validation"]["passed"] is True
    assert validated["validation"]["metadata"]["project_manifest"]["entry_file"] == "design/project.umlproj"
    published = factory.publish(draft_id, TraceCasePublishRequest())
    result = asyncio.run(factory.runner.run_case(EvalCase.model_validate(published["case"])))
    assert result.passed is True
    assert seen[0] == seen[1]


def test_real_runner_rejects_invalid_captured_layout_before_agent(captured_case, tmp_path):
    from extensions.evals.runner import EvalRunner
    factory, draft_id = captured_case
    draft = trace_cases._load_draft(draft_id)
    draft.capture["manifest"]["entry_file"] = "design/missing.umlproj"
    trace_cases._save_draft(draft)
    factory.runner = EvalRunner(tmp_path / "results.jsonl")
    validated = asyncio.run(factory.validate(draft_id))
    assert validated["status"] == "validation_failed"
    assert "entry_file not found" in validated["validation"]["error"]


def test_case_from_trace_is_draft_safe_without_project_binding():
    request = TraceCaseDraftRequest(
        session_id="session-1",
        name="Trace derived case",
        include_trace_policy=True,
    )
    extracted = {
        "turns": [{"prompt": "add a file"}],
        "supported_tool_names": ["apply_changes"],
        "tool_errors": 0,
        "event_counts": {"user_message": 1, "tool_call": 1},
    }

    case, candidate_checkers, warnings = _case_from_extracted(request, extracted, "sha256")

    assert case.id == "trace-session-1"
    assert case.prompt == "add a file"
    assert case.project_id == ""
    assert candidate_checkers[0]["type"] == "trace_policy"
    assert candidate_checkers[0]["required_tools"] == ["apply_changes"]
    assert candidate_checkers[0]["_evidence"]["tool_calls"] == 1
    assert any("bind a project" in warning for warning in warnings)

def test_review_trace_case_draft_revalidates_checker_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_cases, "_draft_root", lambda: tmp_path)
    draft = trace_cases.TraceCaseDraft(
        draft_id="tcd_review",
        created_at="2026-01-01T00:00:00+00:00",
        case=EvalCase(id="trace-review", prompt="review this"),
        session_id="session-review",
        trace_sha256="sha256",
    )
    trace_cases._save_draft(draft)
    factory = trace_cases.TraceCaseFactory(runner=None)

    updated = factory.review(
        "tcd_review",
        TraceCaseReviewRequest(
            name="Reviewed case",
            checkers=[{"type": "file_exists", "path": "src/main.py"}],
            hard_checkers=[{"type": "file_contains", "path": "src/main.py", "text": "ok"}],
        ),
    )

    assert updated["status"] == "draft_created"
    assert updated["case"]["name"] == "Reviewed case"
    assert updated["case"]["hard_checkers"][0]["type"] == "file_contains"

    with pytest.raises(ValueError, match="unsupported checker type"):
        factory.review(
            "tcd_review",
            TraceCaseReviewRequest(checkers=[{"type": "not_a_checker"}]),
        )

def test_capture_fixture_uses_allowlisted_workspace_and_omits_generated_dirs(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    tests = workspace / "test"
    design = workspace / "design"
    source.mkdir(parents=True)
    tests.mkdir()
    design.mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    (tests / "test_main.py").write_text("def test_ok(): pass", encoding="utf-8")
    (design / "project.umlproj").write_text("{}", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")

    monkeypatch.setattr(trace_cases, "_allowed_workspace_roots", lambda: [tmp_path])
    monkeypatch.setattr(trace_cases, "_capture_root", lambda: tmp_path / "captures")
    monkeypatch.setattr(trace_cases, "_draft_root", lambda: tmp_path / "drafts")
    draft = trace_cases.TraceCaseDraft(
        draft_id="tcd_capture",
        created_at="2026-01-01T00:00:00+00:00",
        case=EvalCase(id="trace-capture", prompt="capture this"),
        session_id="session-capture",
        trace_sha256="sha256",
        workspace={
            "source_dir": str(source),
            "test_dir": str(tests),
            "project_file": str(design / "project.umlproj"),
        },
    )
    trace_cases._save_draft(draft)

    captured = trace_cases.TraceCaseFactory(runner=None).capture(
        "tcd_capture", TraceCaseCaptureRequest()
    )

    staging = Path(captured["capture"]["staging_path"])
    assert captured["status"] == "fixture_bound"
    assert captured["case"]["project_id"].startswith("trace-tcd_capture")
    assert (staging / "src" / "main.py").is_file()
    assert (staging / "test" / "test_main.py").is_file()
    assert (staging / "design" / "project.umlproj").is_file()
    assert not (staging / "src" / "__pycache__").exists()
    assert trace_cases.TraceCaseFactory(runner=None).list_drafts()[0]["draft_id"] == "tcd_capture"
    deleted = trace_cases.TraceCaseFactory(runner=None).delete_draft("tcd_capture")
    assert deleted["deleted"] is True
    assert not staging.exists()

def test_trace_case_factory_end_to_end_publish_catalog(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    tests = workspace / "test"
    design = workspace / "design"
    source.mkdir(parents=True)
    tests.mkdir()
    design.mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    (tests / "test_main.py").write_text("def test_ok(): pass", encoding="utf-8")
    (design / "project.umlproj").write_text("{}", encoding="utf-8")

    draft_root = tmp_path / "drafts"
    capture_root = tmp_path / "captures"
    cases_root = tmp_path / "cases"
    fixtures_root = tmp_path / "fixtures"
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(trace_cases, "_draft_root", lambda: draft_root)
    monkeypatch.setattr(trace_cases, "_capture_root", lambda: capture_root)
    monkeypatch.setattr(trace_cases, "_allowed_workspace_roots", lambda: [tmp_path])
    monkeypatch.setattr(trace_cases, "load_projects", lambda: {})
    monkeypatch.setattr(trace_cases, "cases_dir", lambda: cases_root)
    monkeypatch.setattr(trace_cases, "fixtures_dir", lambda: fixtures_root)
    monkeypatch.setattr(trace_cases, "projects_dir", lambda: projects_root)
    monkeypatch.setattr(trace_cases, "get_host_services", lambda: SimpleNamespace(resolve_provider=lambda slot: SimpleNamespace(
        query=lambda: SimpleNamespace(read_trace=lambda _session_id: {"events": [
            {"event_type": "session_start", "user_message": "build it", "project_file": str(design / "project.umlproj"), "source_dir": str(source), "test_dir": str(tests)},
            {"event_type": "user_message", "message": "build it", "project_file": str(design / "project.umlproj"), "source_dir": str(source), "test_dir": str(tests)},
            {"event_type": "done", "answer": "done"},
        ]})
    )))

    class FakeRunner:
        async def run_case(self, case, result_metadata=None, *, fixture_override=None):
            assert case.project_id
            assert fixture_override[1].entry_file == "design/project.umlproj"
            return EvalResult(
                run_id="eval_trace_factory",
                case_id=case.id,
                status="passed",
                passed=True,
                score=1.0,
                metadata=result_metadata or {},
            )

    factory = trace_cases.TraceCaseFactory(FakeRunner())
    draft = asyncio.run(factory.create(TraceCaseDraftRequest(session_id="session-e2e")))
    reviewed = factory.review(
        draft["draft_id"],
        TraceCaseReviewRequest(
            name="Published Trace case",
            project_id="",
            checkers=[{"type": "file_exists", "path": "src/main.py"}],
            hard_checkers=[{"type": "file_contains", "path": "src/main.py", "text": "ok"}],
        ),
    )
    captured = factory.capture(reviewed["draft_id"], TraceCaseCaptureRequest())
    factory.review(captured["draft_id"], TraceCaseReviewRequest(
        project_id=captured["case"]["project_id"],
        hard_checkers=reviewed["case"]["hard_checkers"], fixture_state_confirmed=True,
    ))
    validated = asyncio.run(factory.validate(captured["draft_id"]))
    published = factory.publish(validated["draft_id"], TraceCasePublishRequest())

    assert validated["status"] == "validated"
    assert published["case"]["project_id"] == captured["case"]["project_id"]
    assert (cases_root / f"{published['case_id']}.json").is_file()
    assert (fixtures_root / published["case"]["project_id"] / "src" / "main.py").is_file()
    manifest = json.loads((projects_root / f"{published['case']['project_id']}.json").read_text(encoding="utf-8"))
    assert manifest["source_dir"] == "src"

    from extensions.evals import registry
    monkeypatch.setattr(registry, "cases_dir", lambda: cases_root)
    loaded = registry.load_cases()
    assert published["case_id"] in loaded
    assert loaded[published["case_id"]].hard_checkers[0]["type"] == "file_contains"
def test_fixture_preview_lists_selected_files_without_creating_staging(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    tests = workspace / "test"
    design = workspace / "design"
    source.mkdir(parents=True)
    tests.mkdir()
    design.mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    (tests / "test_main.py").write_text("def test_ok(): pass", encoding="utf-8")
    (design / "project.umlproj").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(trace_cases, "_allowed_workspace_roots", lambda: [tmp_path])
    monkeypatch.setattr(trace_cases, "_draft_root", lambda: tmp_path / "drafts")
    monkeypatch.setattr(trace_cases, "_capture_root", lambda: tmp_path / "captures")
    monkeypatch.setattr(trace_cases, "load_projects", lambda: {})
    draft = trace_cases.TraceCaseDraft(
        draft_id="tcd_preview",
        created_at="2026-01-01T00:00:00+00:00",
        case=EvalCase(id="trace-preview", prompt="preview this"),
        session_id="session-preview",
        trace_sha256="sha256",
        workspace={
            "source_dir": str(source),
            "test_dir": str(tests),
            "project_file": str(design / "project.umlproj"),
        },
    )
    trace_cases._save_draft(draft)

    preview = trace_cases.TraceCaseFactory(runner=None).preview(
        "tcd_preview", TraceCaseCaptureRequest(version="2.0.0")
    )

    assert preview["file_count"] == 3
    assert preview["total_bytes"] > 0
    assert [item["path"] for item in preview["files"]] == [
        "design/project.umlproj", "src/main.py", "test/test_main.py"
    ]
    assert preview["manifest"]["version"] == "2.0.0"
    assert not (tmp_path / "captures").exists()
