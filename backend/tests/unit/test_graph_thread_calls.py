"""Graph workers release resources before cancellation returns to their owner."""
import asyncio
import threading

import pytest

from extensions.orchestration.architecture_aware.thread_calls import graph_call


@pytest.mark.parametrize("fails", [False, True])
def test_cancellation_drains_worker_even_when_cancelled_again(tmp_path, fails):
    from extensions.knowledge_graph.database import KnowledgeGraphDB

    release = threading.Event()
    closed = threading.Event()
    path = tmp_path / "knowledge_graph.db"

    async def scenario():
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def query():
            db = KnowledgeGraphDB(str(path))
            try:
                db.conn.execute("SELECT 1").fetchall()
                loop.call_soon_threadsafe(started.set)
                if not release.wait(5):
                    raise RuntimeError("test worker was not released")
                if fails:
                    raise ValueError("query failed")
                return "answer"
            finally:
                db.close()
                closed.set()

        task = asyncio.create_task(graph_call(query))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not closed.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set()
        assert not path.with_name(path.name + "-wal").exists()
        path.unlink()

    asyncio.run(scenario())


def test_graph_call_preserves_result_and_exception():
    def fail():
        raise ValueError("query failed")

    async def scenario():
        assert await graph_call(lambda value: value, "answer") == "answer"
        with pytest.raises(ValueError, match="query failed"):
            await graph_call(fail)

    asyncio.run(scenario())


def test_database_close_does_not_commit_unfinished_transaction(tmp_path):
    import sqlite3
    from extensions.knowledge_graph.database import KnowledgeGraphDB

    path = tmp_path / "knowledge_graph.db"
    db = KnowledgeGraphDB(str(path))
    db.conn.execute("CREATE TABLE probe(value TEXT)")
    db.conn.commit()
    db.conn.execute("INSERT INTO probe VALUES (?)", ("unfinished",))
    db.close()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 0
    finally:
        connection.close()


def test_failed_code_rebuild_closes_builder(tmp_path, monkeypatch):
    from extensions.knowledge_graph import retriever as module

    closed = []

    class FailingBuilder:
        def __init__(self, path):
            pass

        def rebuild_code_layer(self, *args):
            raise ValueError("rebuild failed")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(module, "GraphBuilder", FailingBuilder)
    retriever = module.GraphRetriever(str(tmp_path / "knowledge_graph.db"))
    try:
        with pytest.raises(ValueError, match="rebuild failed"):
            retriever.diff("project", source_dir=str(tmp_path), force_rebuild=True)
        assert closed == [True]
    finally:
        retriever.close()
