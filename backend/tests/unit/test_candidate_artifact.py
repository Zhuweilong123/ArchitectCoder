from pathlib import Path

import pytest

from app.services.candidate_artifact import CandidateArtifactError, CandidateArtifactStore
from app.services.change_set import ChangeSet


def _candidate_change_set(source: Path, design: Path) -> ChangeSet:
    changes = ChangeSet()
    changes.begin()
    source_before = source.read_text(encoding="utf-8")
    design_before = design.read_text(encoding="utf-8")
    source_after = "candidate source\n"
    design_after = "candidate design\n"
    changes.record(str(source), True, source_before, source_after)
    changes.record(str(design), True, design_before, design_after)
    source.write_text(source_after, encoding="utf-8", newline="")
    design.write_text(design_after, encoding="utf-8", newline="")
    return changes


def test_candidate_artifact_survives_rollback_and_restores_with_hash_checks(tmp_path):
    source = tmp_path / "src.py"
    design = tmp_path / "design.txt"
    source.write_text("original source\n", encoding="utf-8")
    design.write_text("original design\n", encoding="utf-8")
    changes = _candidate_change_set(source, design)

    store = CandidateArtifactStore(root=tmp_path / "artifacts")
    reference = store.capture(changes, "run-1")
    assert reference and reference["file_count"] == 2
    changes.rollback()
    assert source.read_text(encoding="utf-8") == "original source\n"

    resumed = ChangeSet()
    resumed.begin()
    restored = store.restore(
        reference,
        resumed,
        allowed_roots=(tmp_path,),
        exclude_paths=(design,),
    )
    assert restored["restored_count"] == 1
    assert restored["skipped_count"] == 1
    assert source.read_text(encoding="utf-8") == "candidate source\n"
    assert design.read_text(encoding="utf-8") == "original design\n"


def test_candidate_restore_rejects_workspace_drift(tmp_path):
    source = tmp_path / "src.py"
    design = tmp_path / "design.txt"
    source.write_text("original source\n", encoding="utf-8")
    design.write_text("original design\n", encoding="utf-8")
    changes = _candidate_change_set(source, design)
    store = CandidateArtifactStore(root=tmp_path / "artifacts")
    reference = store.capture(changes, "run-2")
    changes.rollback()
    source.write_text("user edited after rollback\n", encoding="utf-8")

    resumed = ChangeSet()
    resumed.begin()
    with pytest.raises(CandidateArtifactError, match="workspace changed"):
        store.restore(reference, resumed, allowed_roots=(tmp_path,))
