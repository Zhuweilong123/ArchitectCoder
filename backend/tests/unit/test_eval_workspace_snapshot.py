"""Evaluation snapshots remain valid while SQLite WAL databases are open."""

import sqlite3

from extensions.evals import workspace_snapshot as snapshot


def test_snapshot_includes_committed_wal_without_copying_locked_sidecars(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    state = workspace / ".architectcoder"
    state.mkdir(parents=True)
    (workspace / "source.py").write_text("print('hello')", encoding="utf-8")
    database = state / "knowledge_graph.db"
    connection = sqlite3.connect(database)
    destination = tmp_path / "archive"
    copy2 = snapshot.shutil.copy2

    def reject_sidecars(source, target):
        assert not str(source).endswith((".db-wal", ".db-shm"))
        return copy2(source, target)

    monkeypatch.setattr(snapshot.shutil, "copy2", reject_sidecars)
    try:
        connection.execute("PRAGMA journal_mode=WAL").fetchall()
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE probe(value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('committed')")
        connection.commit()
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        # The snapshot must not include an unfinished transaction or commit it.
        connection.execute("INSERT INTO probe VALUES ('unfinished')")
        snapshot.copy_workspace_snapshot(workspace, destination)
        assert connection.in_transaction
        assert (destination / "source.py").read_text() == "print('hello')"
        archived = destination / ".architectcoder" / "knowledge_graph.db"
        reader = sqlite3.connect(archived)
        try:
            assert reader.execute("SELECT value FROM probe").fetchall() == [("committed",)]
            assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            reader.close()
        assert not archived.with_name(archived.name + "-wal").exists()
        assert not archived.with_name(archived.name + "-shm").exists()
    finally:
        connection.close()


def test_snapshot_preserves_non_database_files_with_sidecar_names(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("notes", "notes-wal", "notes-shm", "empty.db"):
        (workspace / name).write_text("ordinary file", encoding="utf-8")
    destination = tmp_path / "archive"
    snapshot.copy_workspace_snapshot(workspace, destination)
    assert {p.name for p in destination.iterdir()} == {p.name for p in workspace.iterdir()}


def test_runner_keeps_result_and_reports_invalid_sqlite_snapshot(tmp_path, monkeypatch):
    from extensions.evals import runner
    from extensions.evals.models import EvalResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "invalid.db").write_bytes(b"SQLite format 3\x00" + b"invalid" * 100)
    monkeypatch.setattr(runner, "evaluation_root", lambda: tmp_path / "evals")
    result = EvalResult(run_id="eval_snapshot", case_id="case", status="passed", passed=True)
    runner.EvalRunner._persist_workspace_snapshot(workspace, result.run_id, result)
    assert result.passed and result.status == "passed"
    assert result.workspace == ""
    assert result.metadata["workspace_snapshot_error"]
