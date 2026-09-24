"""Durable, hash-checked snapshots for rejected Agent candidates.

Candidate artifacts are deliberately separate from the project workspace. A
contract-blocked run can therefore be resumed without exposing unapproved
source changes to the active workspace before the user accepts the design.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from backend.config.paths import runtime_root
from backend.config.settings import Settings, get_settings
from app.services.change_set import ChangeSet


class CandidateArtifactError(RuntimeError):
    """The candidate is missing, stale, or conflicts with workspace edits."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".candidate", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class CandidateArtifactStore:
    def __init__(self, settings: Settings | None = None, root: str | Path | None = None):
        self.root = Path(root).resolve() if root else runtime_root(settings or get_settings()) / "data" / "candidate_artifacts"

    def capture(self, change_set: ChangeSet, run_id: str) -> dict | None:
        if not run_id or not change_set.has_changes:
            return None
        try:
            entries = change_set.candidate_entries()
        except Exception as exc:
            raise CandidateArtifactError(str(exc)) from exc
        if not entries:
            return None

        artifact_dir = self.root / run_id
        files_dir = artifact_dir / "files"
        if artifact_dir.exists():
            raise CandidateArtifactError(f"candidate artifact already exists: {run_id}")
        files_dir.mkdir(parents=True, exist_ok=False)
        manifest_entries = []
        for index, entry in enumerate(entries):
            relative = f"files/{index}.txt"
            if entry["after_exists"]:
                _atomic_write(artifact_dir / relative, entry["content"])
            manifest_entries.append({
                key: value for key, value in entry.items()
                if key != "content"
            } | {"artifact_file": relative})
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "entries": manifest_entries,
        }
        manifest_path = artifact_dir / "manifest.json"
        _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
        return {
            "artifact_id": run_id,
            "artifact_path": str(manifest_path),
            "file_count": len(manifest_entries),
        }

    def _load(self, reference: dict | str) -> tuple[Path, dict]:
        raw = reference.get("artifact_path") if isinstance(reference, dict) else reference
        if not raw:
            raise CandidateArtifactError("candidate artifact reference is empty")
        manifest_path = Path(str(raw)).resolve()
        try:
            manifest_path.relative_to(self.root)
        except ValueError as exc:
            raise CandidateArtifactError("candidate artifact is outside artifact root") from exc
        if not manifest_path.is_file():
            raise CandidateArtifactError(f"candidate artifact not found: {manifest_path}")
        try:
            payload = json.loads(_read_text(manifest_path))
        except (OSError, ValueError) as exc:
            raise CandidateArtifactError("candidate artifact is unreadable") from exc
        if payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
            raise CandidateArtifactError("unsupported candidate artifact format")
        return manifest_path.parent, payload

    @staticmethod
    def _under(path: Path, roots: Iterable[str | Path]) -> bool:
        resolved = path.resolve()
        return any(
            resolved == (Path(root).resolve())
            or Path(resolved).is_relative_to(Path(root).resolve())
            for root in roots if root
        )

    @staticmethod
    def _excluded(path: Path, excludes: Iterable[str | Path]) -> bool:
        resolved = path.resolve()
        for root in excludes:
            if not root:
                continue
            candidate = Path(root).resolve()
            if resolved == candidate or candidate.is_dir() and resolved.is_relative_to(candidate):
                return True
        return False

    def restore(
        self,
        reference: dict | str,
        change_set: ChangeSet,
        *,
        allowed_roots: Iterable[str | Path] = (),
        exclude_paths: Iterable[str | Path] = (),
    ) -> dict:
        artifact_dir, payload = self._load(reference)
        entries = payload["entries"]
        allowed = tuple(allowed_roots)
        excludes = tuple(exclude_paths)
        prepared = []
        for item in entries:
            path = Path(str(item.get("path", ""))).resolve()
            if not path:
                raise CandidateArtifactError("candidate entry has no path")
            if allowed and not self._under(path, allowed):
                raise CandidateArtifactError(f"candidate path outside workspace: {path}")
            if self._excluded(path, excludes):
                continue
            current_exists = path.is_file()
            current = _read_text(path) if current_exists else ""
            expected_exists = bool(item.get("before_exists"))
            expected_sha = str(item.get("before_sha256", ""))
            if current_exists != expected_exists or (current_exists and _sha256(current) != expected_sha):
                raise CandidateArtifactError(f"workspace changed since candidate rollback: {path}")
            after_exists = bool(item.get("after_exists"))
            after = _read_text(artifact_dir / str(item.get("artifact_file", ""))) if after_exists else ""
            if _sha256(after) != str(item.get("after_sha256", "")):
                raise CandidateArtifactError(f"candidate content hash mismatch: {path}")
            prepared.append((path, current_exists, current, after_exists, after))

        for path, current_exists, current, after_exists, after in prepared:
            change_set.record(str(path), current_exists, current, after if after_exists else "")
            if after_exists:
                _atomic_write(path, after)
            elif path.exists():
                path.unlink()
        return {
            "artifact_id": payload.get("run_id", ""),
            "restored_count": len(prepared),
            "skipped_count": len(entries) - len(prepared),
        }


__all__ = ["CandidateArtifactError", "CandidateArtifactStore"]
