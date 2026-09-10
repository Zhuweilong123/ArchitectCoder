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
    monkeypatch.setattr(trace_cases, "load_trace", lambda: SimpleNamespace(
        query=lambda: SimpleNamespace(read_trace=lambda _session_id: {"events": [
            {"event_type": "session_start", "user_message": "build it", "project_file": str(design / "project.umlproj"), "source_dir": str(source), "test_dir": str(tests)},
            {"event_type": "user_message", "message": "build it", "project_file": str(design / "project.umlproj"), "source_dir": str(source), "test_dir": str(tests)},
            {"event_type": "done", "answer": "done"},
        ]})
    ))

    class FakeRunner:
        async def run_case(self, case, result_metadata=None):
            assert case.fixture
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