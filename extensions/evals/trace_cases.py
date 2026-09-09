"""Trace-to-Case conversion owned by the evaluation provider.

This module only reads Trace evidence through the stable Trace query port. It
does not subscribe to Agent execution, alter Trace storage, or participate in
Trace replay. Drafts stay in runtime artifacts until an explicit publish.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

class TraceCaseDraftRequest(BaseModel):
    """Request for deriving an evaluation-case draft from a Trace session."""

    session_id: str = Field(min_length=1, max_length=200)
    case_id: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=200)
    project_id: str = Field(default="", max_length=100)
    suite: str = Field(default="trace-derived", max_length=100)
    include_trace_policy: bool = True


class TraceCaseCaptureRequest(BaseModel):
    """Request for previewing or capturing the source Trace workspace."""

    project_id: str = Field(default="", max_length=100)
    version: str = Field(default="1.0.0", max_length=50)


class TraceCaseReviewRequest(BaseModel):
    """Editable review fields for a Trace case draft."""

    name: str = Field(default="", max_length=200)
    project_id: str = Field(default="", max_length=100)
    checkers: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    hard_checkers: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class TraceCasePublishRequest(BaseModel):
    """Request for explicitly publishing a validated Trace case draft."""

    case_id: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=200)
    suite: str = Field(default="", max_length=100)

from backend.config import evaluation_root, get_settings
from app.trace.tracing import load_trace

from .models import EVAL_TRACE_TOOL_NAMES, EvalCase, EvalTurn, ProjectManifest
from .paths import cases_dir, fixtures_dir, projects_dir
from .projects import load_projects


TRACE_CASE_DRAFT_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


class TraceCaseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    schema_version: str = TRACE_CASE_DRAFT_SCHEMA_VERSION
    status: str = "draft_created"
    created_at: str
    case: EvalCase
    session_id: str
    trace_sha256: str
    trace_summary: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, str] = Field(default_factory=dict)
    candidate_checkers: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    capture: dict[str, Any] | None = None


def _draft_root() -> Path:
    return evaluation_root() / "trace-case-drafts"


_CAPTURE_IGNORED_NAMES = frozenset({
    ".git", ".env", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
    "temp", "tmp", "coverage", ".coverage",
})


def _capture_root() -> Path:
    return evaluation_root() / "trace-case-captures"


def _allowed_workspace_roots() -> list[Path]:
    settings = get_settings()
    repo_root = Path(__file__).resolve().parents[2]
    roots = [repo_root, Path(settings.uml_dir).resolve(), Path(settings.uml_dir).resolve().parent]
    roots.extend(Path(item.strip()).resolve() for item in settings.workspace_roots.split(",") if item.strip())
    return roots


def _validated_workspace_path(value: str, *, kind: str) -> Path:
    if not value:
        raise ValueError(f"Trace workspace {kind} path is missing")
    path = Path(value).resolve()
    if not any(path == root or path.is_relative_to(root) for root in _allowed_workspace_roots()):
        raise ValueError(f"Trace workspace path is outside configured roots: {path}")
    if kind == "directory" and not path.is_dir():
        raise ValueError(f"Trace workspace directory not found: {path}")
    if kind == "file" and not path.is_file():
        raise ValueError(f"Trace workspace file not found: {path}")
    return path


def _copy_capture_tree(source: Path, target: Path) -> None:
    if source.name in _CAPTURE_IGNORED_NAMES or source.is_symlink():
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_capture_tree(child, target / child.name)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _capture_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(path.read_bytes())
        count += 1
    return digest.hexdigest(), count


def _iter_capture_files(source: Path):
    if source.is_symlink() or source.name in _CAPTURE_IGNORED_NAMES:
        return
    if source.is_file():
        yield source
        return
    if not source.is_dir():
        return
    for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
        yield from _iter_capture_files(child)


def _capture_plan(draft: TraceCaseDraft, request: TraceCaseCaptureRequest) -> dict[str, Any]:
    source_dir = _validated_workspace_path(draft.workspace.get("source_dir", ""), kind="directory")
    test_dir_value = draft.workspace.get("test_dir", "")
    test_dir = _validated_workspace_path(test_dir_value, kind="directory") if test_dir_value else None
    project_file_value = draft.workspace.get("project_file", "")
    project_file = _validated_workspace_path(project_file_value, kind="file") if project_file_value else None
    roots = [source_dir, *([test_dir] if test_dir else []), *([project_file.parent] if project_file else [])]
    try:
        capture_workspace = Path(os.path.commonpath([str(path) for path in roots])).resolve()
    except ValueError as exc:
        raise ValueError("Trace workspace directories must share one filesystem root") from exc
    if not any(capture_workspace == root or capture_workspace.is_relative_to(root) for root in _allowed_workspace_roots()):
        raise ValueError(f"Trace workspace root is outside configured roots: {capture_workspace}")

    project_id = _safe_case_id(request.project_id, draft.draft_id)
    manifest = ProjectManifest(
        id=project_id,
        version=request.version.strip() or "1.0.0",
        fixture=project_id,
        entry_file=_relative_or_dot(project_file, capture_workspace) if project_file else "",
        source_dir=_relative_or_dot(source_dir, capture_workspace),
        test_dir=_relative_or_dot(test_dir, capture_workspace) if test_dir else ".",
        protected_paths=([_relative_or_dot(project_file, capture_workspace)] if project_file else []),
        allowed_write_paths=[
            item for item in {
                _relative_or_dot(source_dir, capture_workspace),
                _relative_or_dot(test_dir, capture_workspace) if test_dir else "",
            } if item
        ],
    )
    selected_paths = [source_dir, *([test_dir] if test_dir else []), *([project_file] if project_file else [])]
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for selected in selected_paths:
        for path in _iter_capture_files(selected):
            relative = path.relative_to(capture_workspace).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            files.append({"path": relative, "size": path.stat().st_size})
    files.sort(key=lambda item: item["path"])
    return {
        "capture_workspace": capture_workspace,
        "selected_paths": selected_paths,
        "project_id": project_id,
        "manifest": manifest,
        "files": files,
        "total_bytes": sum(item["size"] for item in files),
        "project_exists": project_id in load_projects(),
    }


def _relative_or_dot(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."

def _draft_path(draft_id: str) -> Path:
    safe_id = _SAFE_ID.sub("", draft_id)
    if not safe_id or safe_id != draft_id:
        raise ValueError("invalid Trace case draft id")
    return _draft_root() / f"{safe_id}.json"


def _save_draft(draft: TraceCaseDraft) -> dict[str, Any]:
    path = _draft_path(draft.draft_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return draft.model_dump(mode="json")


def _load_draft(draft_id: str) -> TraceCaseDraft | None:
    path = _draft_path(draft_id)
    if not path.is_file():
        return None
    return TraceCaseDraft.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _safe_case_id(value: str, session_id: str) -> str:
    candidate = _SAFE_ID.sub("-", value.strip()).strip("-").lower()
    if not candidate:
        candidate = f"trace-{_SAFE_ID.sub('-', session_id).strip('-').lower()}"
    return candidate[:100].rstrip("-") or f"trace-{uuid.uuid4().hex[:12]}"


def _trace_digest(events: list[dict[str, Any]]) -> str:
    payload = json.dumps(events, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _first_value(events: list[dict[str, Any]], key: str) -> str:
    for event in events:
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    tool_names: set[str] = set()
    tool_errors = 0
    event_counts: dict[str, int] = {}

    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "user_message":
            if current is not None:
                turns.append(current)
            current = {
                "prompt": str(event.get("message") or "").strip(),
                "tool_names": [],
                "answer": "",
            }
        elif current is not None and event_type == "tool_call":
            tool_name = str(event.get("tool_name") or "").strip()
            if tool_name:
                tool_names.add(tool_name)
                current["tool_names"].append(tool_name)
        elif current is not None and event_type == "tool_result":
            if event.get("error"):
                tool_errors += 1
        elif current is not None and event_type == "done":
            current["answer"] = str(event.get("answer") or "")

    if current is not None:
        turns.append(current)

    if not turns:
        session_prompt = _first_value(events, "user_message")
        if session_prompt:
            turns.append({"prompt": session_prompt, "tool_names": [], "answer": ""})

    workspace = {
        "project_file": _first_value(events, "project_file"),
        "source_dir": _first_value(events, "source_dir"),
        "test_dir": _first_value(events, "test_dir"),
    }
    return {
        "turns": turns,
        "tool_names": sorted(tool_names),
        "supported_tool_names": sorted(tool_names & EVAL_TRACE_TOOL_NAMES),
        "tool_errors": tool_errors,
        "event_counts": event_counts,
        "workspace": workspace,
    }


def _candidate_checkers(extracted: dict[str, Any], include_trace_policy: bool) -> list[dict[str, Any]]:
    if not include_trace_policy:
        return []
    supported = extracted["supported_tool_names"]
    if not supported:
        return []
    return [{
        "type": "trace_policy",
        "required_tools": supported,
        "_generated": True,
        "_confidence": "observed",
        "_reason": "observed in the source Trace; review before promoting to hard_checkers",
        "_evidence": {
            "source": "trace",
            "observed_tools": supported,
            "tool_calls": extracted["event_counts"].get("tool_call", 0),
            "tool_errors": extracted["tool_errors"],
        },
    }]


def _case_from_extracted(
    request: TraceCaseDraftRequest,
    extracted: dict[str, Any],
    trace_sha256: str,
) -> tuple[EvalCase, list[dict[str, Any]], list[str]]:
    raw_turns = extracted["turns"]
    warnings: list[str] = []
    if not raw_turns:
        raise ValueError("Trace contains no user message and cannot become an evaluation case")
    if extracted["tool_errors"]:
        warnings.append(f"source Trace contains {extracted['tool_errors']} tool errors")
    if not request.project_id:
        warnings.append("bind a project manifest before validation or publishing")
    elif request.project_id not in load_projects():
        raise ValueError(f"evaluation project not found: {request.project_id}")

    candidate_checkers = _candidate_checkers(extracted, request.include_trace_policy)
    turns = [
        EvalTurn(prompt=str(item["prompt"]), checkers=list(candidate_checkers))
        for item in raw_turns
        if str(item.get("prompt") or "").strip()
    ]
    if not turns:
        raise ValueError("Trace contains no non-empty user message")

    metadata = {
        "suite": request.suite or "trace-derived",
        "source": "trace",
        "trace_case_status": "draft",
        "trace_session_id": request.session_id,
        "trace_sha256": trace_sha256,
        "candidate_checkers": candidate_checkers,
        "source_event_counts": extracted["event_counts"],
    }
    if len(turns) == 1:
        case = EvalCase(
            id=_safe_case_id(request.case_id, request.session_id),
            name=request.name.strip() or turns[0].prompt[:120],
            prompt=turns[0].prompt,
            project_id=request.project_id,
            checkers=candidate_checkers,
            metadata=metadata,
        )
    else:
        case = EvalCase(
            id=_safe_case_id(request.case_id, request.session_id),
            name=request.name.strip() or turns[0].prompt[:120],
            turns=turns,
            project_id=request.project_id,
            metadata=metadata,
        )
    return case, candidate_checkers, warnings


class TraceCaseFactory:
    """Local implementation of the optional Evals Trace Case capability."""

    def __init__(self, runner):
        self.runner = runner

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {
                "id": manifest.id,
                "version": manifest.version,
                "fixture": manifest.fixture,
                "source_dir": manifest.source_dir,
                "test_dir": manifest.test_dir,
            }
            for manifest in load_projects().values()
        ]

    def list_drafts(self) -> list[dict[str, Any]]:
        root = _draft_root()
        if not root.is_dir():
            return []
        drafts: list[dict[str, Any]] = []
        for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                draft = TraceCaseDraft.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            drafts.append(draft.model_dump(mode="json"))
        return drafts

    def delete_draft(self, draft_id: str) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft.status == "published":
            raise ValueError("published Trace case drafts cannot be deleted")
        capture = draft.capture or {}
        staging = Path(str(capture.get("staging_path") or "")).resolve() if capture else None
        if staging and staging.is_relative_to(_capture_root().resolve()):
            shutil.rmtree(staging, ignore_errors=True)
        path = _draft_path(draft_id)
        path.unlink(missing_ok=True)
        return {"draft_id": draft_id, "deleted": True}
    async def create(self, request: TraceCaseDraftRequest) -> dict[str, Any]:
        data = load_trace().query().read_trace(request.session_id)
        if data is None:
            raise ValueError("Trace not found")
        events = data.get("events") or []
        if not isinstance(events, list):
            raise ValueError("Trace events are invalid")
        extracted = _extract_trace(events)
        digest = _trace_digest(events)
        case, candidate_checkers, warnings = _case_from_extracted(request, extracted, digest)
        draft = TraceCaseDraft(
            draft_id=f"tcd_{uuid.uuid4().hex[:16]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            case=case,
            session_id=request.session_id,
            trace_sha256=digest,
            trace_summary={
                "events": len(events),
                "turns": len(extracted["turns"]),
                "tool_calls": extracted["event_counts"].get("tool_call", 0),
                "tool_errors": extracted["tool_errors"],
                "tool_names": extracted["tool_names"],
            },
            workspace=extracted["workspace"],
            candidate_checkers=candidate_checkers,
            warnings=warnings,
        )
        return _save_draft(draft)

    def get(self, draft_id: str) -> dict[str, Any] | None:
        draft = _load_draft(draft_id)
        return draft.model_dump(mode="json") if draft else None

    def review(self, draft_id: str, request: TraceCaseReviewRequest) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft.status == "published":
            raise ValueError("published Trace case drafts cannot be edited")
        captured_project_id = str((draft.capture or {}).get("project_id") or "")
        if request.project_id and request.project_id not in load_projects() and request.project_id != captured_project_id:
            raise ValueError(f"evaluation project not found: {request.project_id}")

        metadata = dict(draft.case.metadata)
        metadata["trace_case_status"] = "review_ready" if request.project_id else "draft"
        metadata["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        updated_case = draft.case.model_copy(update={
            "name": request.name.strip() or draft.case.name,
            "project_id": request.project_id,
            "checkers": request.checkers,
            "hard_checkers": request.hard_checkers,
            "metadata": metadata,
        })
        # Re-validate the complete EvalCase contract, including checker types,
        # required fields and tool protocol constraints, before saving review.
        draft.case = EvalCase.model_validate(updated_case.model_dump(mode="json"))
        draft.status = "review_ready" if request.project_id else "draft_created"
        draft.validation = None
        return _save_draft(draft)
    def capture(self, draft_id: str, request: TraceCaseCaptureRequest) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft.status == "published":
            raise ValueError("published Trace case drafts cannot capture a new fixture")

        source_dir = _validated_workspace_path(draft.workspace.get("source_dir", ""), kind="directory")
        test_dir_value = draft.workspace.get("test_dir", "")
        test_dir = _validated_workspace_path(test_dir_value, kind="directory") if test_dir_value else None
        project_file_value = draft.workspace.get("project_file", "")
        project_file = _validated_workspace_path(project_file_value, kind="file") if project_file_value else None
        roots = [source_dir, *( [test_dir] if test_dir else [] ), *([project_file.parent] if project_file else [])]
        try:
            capture_workspace = Path(os.path.commonpath([str(path) for path in roots])).resolve()
        except ValueError as exc:
            raise ValueError("Trace workspace directories must share one filesystem root") from exc
        if not any(capture_workspace == root or capture_workspace.is_relative_to(root) for root in _allowed_workspace_roots()):
            raise ValueError(f"Trace workspace root is outside configured roots: {capture_workspace}")

        project_id = _safe_case_id(request.project_id, draft.draft_id)
        if project_id in load_projects():
            raise ValueError(f"evaluation project already exists: {project_id}")
        capture_id = f"tcc_{uuid.uuid4().hex[:16]}"
        staging = (_capture_root() / capture_id).resolve()
        capture_root = _capture_root().resolve()
        if not staging.is_relative_to(capture_root):
            raise ValueError("fixture capture path escapes staging root")
        manifest = ProjectManifest(
            id=project_id,
            version=request.version.strip() or "1.0.0",
            fixture=project_id,
            entry_file=_relative_or_dot(project_file, capture_workspace) if project_file else "",
            source_dir=_relative_or_dot(source_dir, capture_workspace),
            test_dir=_relative_or_dot(test_dir, capture_workspace) if test_dir else ".",
            protected_paths=([_relative_or_dot(project_file, capture_workspace)] if project_file else []),
            allowed_write_paths=[
                item for item in {
                    _relative_or_dot(source_dir, capture_workspace),
                    _relative_or_dot(test_dir, capture_workspace) if test_dir else "",
                } if item
            ],
        )
        selected_paths = [source_dir, *([test_dir] if test_dir else []), *([project_file] if project_file else [])]
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for selected in selected_paths:
                relative = selected.relative_to(capture_workspace)
                _copy_capture_tree(selected, staging / relative)
            digest, file_count = _capture_digest(staging)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        metadata = dict(draft.case.metadata)
        metadata.update({
            "trace_case_status": "fixture_bound",
            "fixture_capture_id": capture_id,
            "fixture_sha256": digest,
            "fixture_file_count": file_count,
        })
        draft.case = draft.case.model_copy(update={
            "project_id": project_id,
            "fixture": "",
            "metadata": metadata,
        })
        draft.case = EvalCase.model_validate(draft.case.model_dump(mode="json"))
        draft.status = "fixture_bound"
        draft.validation = None
        draft.capture = {
            "capture_id": capture_id,
            "staging_path": str(staging),
            "project_id": project_id,
            "fixture": project_id,
            "root": str(capture_workspace),
            "manifest": manifest.model_dump(mode="json"),
            "sha256": digest,
            "file_count": file_count,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        return _save_draft(draft)

    def preview(self, draft_id: str, request: TraceCaseCaptureRequest) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft.status == "published":
            raise ValueError("published Trace case drafts cannot preview a new fixture")
        plan = _capture_plan(draft, request)
        preview_limit = 2000
        return {
            "draft_id": draft_id,
            "project_id": plan["project_id"],
            "version": plan["manifest"].version,
            "root": str(plan["capture_workspace"]),
            "manifest": plan["manifest"].model_dump(mode="json"),
            "files": plan["files"][:preview_limit],
            "file_count": len(plan["files"]),
            "total_bytes": plan["total_bytes"],
            "truncated": len(plan["files"]) > preview_limit,
            "project_exists": plan["project_exists"],
            "warnings": [
                "当前预览仅展示前 2000 个文件，确认捕获时仍会按规则完整复制。"
            ] if len(plan["files"]) > preview_limit else [],
        }

    async def validate(self, draft_id: str) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if not draft.case.project_id:
            raise ValueError("bind project_id before validating a Trace case draft")
        validation_case = draft.case
        capture = draft.capture or {}
        if capture:
            staging = Path(str(capture.get("staging_path") or "")).resolve()
            if not staging.is_dir() or not staging.is_relative_to(_capture_root().resolve()):
                raise ValueError("captured fixture staging area is missing or invalid")
            # Validate against the staged snapshot without publishing it first.
            validation_case = draft.case.model_copy(update={
                "project_id": "",
                "fixture": str(staging),
            })
        result = await self.runner.run_case(
            validation_case,
            result_metadata={
                "trace_case_draft_id": draft.draft_id,
                "trace_case_source_session_id": draft.session_id,
                "trace_case_fixture_capture_id": capture.get("capture_id", ""),
            },
        )
        draft.status = "validated" if result.passed else "validation_failed"
        draft.validation = result.model_dump(mode="json")
        _save_draft(draft)
        return draft.model_dump(mode="json")

    def publish(self, draft_id: str, request: TraceCasePublishRequest) -> dict[str, Any]:
        draft = _load_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if draft.status != "validated" or not draft.validation or not draft.validation.get("passed"):
            raise ValueError("Trace case draft must pass isolated validation before publishing")
        if not draft.case.project_id:
            raise ValueError("bind project_id before publishing a Trace case draft")

        case_id = _safe_case_id(request.case_id or draft.case.id, draft.session_id)
        path = cases_dir() / f"{case_id}.json"
        if path.exists():
            raise ValueError(f"evaluation case already exists: {case_id}")
        metadata = dict(draft.case.metadata)
        metadata["suite"] = request.suite.strip() or metadata.get("suite", "trace-derived")
        metadata["trace_case_status"] = "published"
        case = draft.case.model_copy(update={
            "id": case_id,
            "name": request.name.strip() or draft.case.name,
            "metadata": metadata,
        })
        case = EvalCase.model_validate(case.model_dump(mode="json"))
        published_paths: list[Path] = []
        temporary_paths: list[Path] = []
        try:
            capture = draft.capture or {}
            if capture:
                staging = Path(str(capture.get("staging_path") or "")).resolve()
                capture_root = _capture_root().resolve()
                if not staging.is_dir() or not staging.is_relative_to(capture_root):
                    raise ValueError("captured fixture staging area is missing or invalid")
                manifest = ProjectManifest.model_validate(capture.get("manifest") or {})
                if manifest.id != case.project_id:
                    raise ValueError("captured fixture manifest does not match the evaluation case")
                fixture_target = (fixtures_dir() / manifest.fixture).resolve()
                projects_target = (projects_dir() / f"{manifest.id}.json").resolve()
                if not fixture_target.is_relative_to(fixtures_dir().resolve()):
                    raise ValueError("captured fixture escapes fixtures directory")
                if not projects_target.is_relative_to(projects_dir().resolve()):
                    raise ValueError("captured project manifest escapes projects directory")
                if fixture_target.exists() or projects_target.exists():
                    raise ValueError(f"captured fixture or project already exists: {manifest.id}")
                fixtures_dir().mkdir(parents=True, exist_ok=True)
                projects_dir().mkdir(parents=True, exist_ok=True)
                fixture_temporary = fixtures_dir() / f".{manifest.fixture}.{uuid.uuid4().hex}.tmp"
                manifest_temporary = projects_dir() / f".{manifest.id}.{uuid.uuid4().hex}.tmp"
                temporary_paths.extend([fixture_temporary, manifest_temporary])
                shutil.copytree(staging, fixture_temporary)
                fixture_temporary.replace(fixture_target)
                published_paths.append(fixture_target)
                manifest_temporary.write_text(
                    json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                manifest_temporary.replace(projects_target)
                published_paths.append(projects_target)

            cases_dir().mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary_paths.append(temporary)
            temporary.write_text(
                json.dumps(case.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
            published_paths.append(path)
        except Exception:
            for temporary_path in temporary_paths:
                if temporary_path.is_dir():
                    shutil.rmtree(temporary_path, ignore_errors=True)
                else:
                    temporary_path.unlink(missing_ok=True)
            for published_path in reversed(published_paths):
                if published_path.is_dir():
                    shutil.rmtree(published_path, ignore_errors=True)
                else:
                    published_path.unlink(missing_ok=True)
            raise
        draft.status = "published"
        draft.case = case
        _save_draft(draft)
        return {
            "draft_id": draft.draft_id,
            "case_id": case.id,
            "path": str(path),
            "case": case.model_dump(mode="json"),
        }