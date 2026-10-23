"""Corrupt persisted records must not break catalogs or erase neighboring data."""
import json

import pytest
from fastapi import HTTPException

from extensions.evals.batches import EvalBatch, EvalBatchManager
from extensions.evals.full_api import _provider_baseline
from extensions.evals.paths import baseline_path
from extensions.evals.performance import _archive_sources, _load_rows
from extensions.evals.persistence import PersistedDataError, read_baseline
from extensions.evals.provider import LocalEvalProvider
from extensions.evals.runner import EvalRunner


@pytest.mark.parametrize("payload", [
    [], None, {},
    {"case_count": "16"}, {"case_count": True}, {"case_count": -1},
    {"case_count": 1, "case_ids": [1]},
    {"case_count": 1, "pass_rate": 1.5},
    {"case_count": 1, "groups": [{"total": "one"}]},
    {"case_count": 1, "failure_categories": {"tool_failure": -1}},
    {"case_count": 1, "extra": {"metric": float("nan")}},
    {"case_count": 1, "extra": {"metric": float("inf")}},
])
def test_baseline_rejects_malformed_fields_without_rewriting(tmp_path, payload):
    path = tmp_path / "baseline.json"
    original = json.dumps(payload).encode()
    path.write_bytes(original)
    with pytest.raises(PersistedDataError, match="invalid evaluation baseline"):
        read_baseline(path)
    assert path.read_bytes() == original


def test_tracked_and_legacy_baselines_keep_all_fields(tmp_path):
    assert read_baseline(baseline_path()) == json.loads(baseline_path().read_text(encoding="utf-8"))
    path = tmp_path / "legacy.json"
    # Older snapshots omit case IDs and grouping; extra producer fields survive.
    legacy = {"case_count": 16, "version": "3.0", "passed": 10,
              "producer": {"extra_counts": [1, None]}}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert read_baseline(path) == legacy


def test_baseline_http_boundary_distinguishes_missing_and_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "baseline.json"
    monkeypatch.setattr("extensions.evals.paths.baseline_path", lambda: path)
    provider = LocalEvalProvider()
    with pytest.raises(HTTPException) as missing:
        _provider_baseline(provider)
    assert missing.value.status_code == 404
    for contents in (b"[", b"[]", b'{"case_count": "bad"}', b"\xff"):
        path.write_bytes(contents)
        with pytest.raises(HTTPException) as invalid:
            _provider_baseline(provider)
        assert invalid.value.status_code == 500
        assert invalid.value.detail == "evaluation baseline is invalid"


@pytest.mark.parametrize("contents", [
    b"[]", b"null", b'{"batch": []}', b'{"batch": "bad"}',
    b'{"note": {}}', b'{"batch": {"summary": {"total": "bad"}}}',
    b'{"batch": {"source_result_id": []}}', b"{", b"\xff",
])
def test_bad_archive_does_not_break_either_catalog(tmp_path, monkeypatch, caplog, contents):
    monkeypatch.setattr("extensions.evals.batches._eval_root", lambda: tmp_path)
    root = tmp_path / "archives"
    root.mkdir()
    (root / "archive_bad.json").write_bytes(contents)
    # Legacy archives may identify their result only in the note, with no batch.
    (root / "archive_old.json").write_text(json.dumps({
        "note": "temp/evals/performance-old.jsonl",
        "batch": None,
    }), encoding="utf-8")

    rows = EvalBatchManager().list_archives()
    assert [row["archive_id"] for row in rows] == ["archive_old"]
    assert _archive_sources(tmp_path) == {"temp/evals/performance-old.jsonl"}
    assert "archive_bad.json" in caplog.text


def test_results_skip_bad_rows_preserve_old_fields_and_fill_limit(tmp_path, caplog):
    path = tmp_path / "results.jsonl"
    first = {"run_id": "one", "case_id": "case", "status": "passed", "passed": True,
             "producer_field": {"version": "old"}}
    second = {**first, "run_id": "two"}
    path.write_bytes(b"\n".join([
        json.dumps(first).encode(), b"[]", b"\xff", b'{"run_id": "incomplete"}',
        json.dumps(second).encode(), b"{", b"null", b"",
    ]))
    original = path.read_bytes()
    assert EvalRunner(path).list_results(limit=1) == [second]
    assert EvalRunner(path).list_results(limit=2) == [first, second]
    assert [row.run_id for row in _load_rows(path)] == ["one", "two"]
    assert f"{path}:3" in caplog.text
    assert path.read_bytes() == original


def test_batch_catalog_skips_bad_rows_but_delete_refuses_to_drop_them(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("extensions.evals.batches._eval_root", lambda: tmp_path)
    path = tmp_path / "batches.jsonl"
    legacy = {"batch_id": "old", "version": "3.0", "case_ids": [], "status": "completed",
              "created_at": "2026-01-01", "producer_field": {"kept": True}}
    path.write_text("\n".join([
        json.dumps(legacy), json.dumps({**legacy, "batch_id": ["invalid"]}),
        json.dumps({**legacy, "batch_id": "bad-time", "started_at": 123}),
        json.dumps({**legacy, "batch_id": "bad-legacy-time", "created_at": 123}),
    ]), encoding="utf-8")
    original = path.read_bytes()
    manager = EvalBatchManager()
    assert manager.list_batches() == [legacy]
    assert manager.get("old").version == "3.0"
    manager._batches["old"] = EvalBatch.model_validate(legacy)

    with pytest.raises(PersistedDataError, match="invalid evaluation history record"):
        manager.delete("old")
    assert "old" in manager._batches
    assert path.read_bytes() == original
    assert f"{path}:2" in caplog.text


def test_deleting_valid_batch_retains_other_legacy_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("extensions.evals.batches._eval_root", lambda: tmp_path)
    path = tmp_path / "batches.jsonl"
    old = {"batch_id": "old", "version": "3.0", "case_ids": [], "status": "completed"}
    kept = {**old, "batch_id": "kept", "producer_field": {"keep": [1, None]}}
    path.write_text(json.dumps(old) + "\n" + json.dumps(kept), encoding="utf-8")
    assert EvalBatchManager().delete("old") == {"batch_id": "old"}
    assert json.loads(path.read_text(encoding="utf-8")) == kept
