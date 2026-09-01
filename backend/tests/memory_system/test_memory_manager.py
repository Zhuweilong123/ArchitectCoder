"""记忆系统单元测试（无需真实 LLM）。"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from memory_system.manager import MemoryManager, _normalize_subject
from memory_system.models import MemoryEntry, MemoryType, RecallResult, MemoryConfig
from memory_system.policy import MemoryWritePolicy


def _extract(items):
    async def fn(prompt):
        return json.dumps(items, ensure_ascii=False)
    return fn


def test_memory_write_policy_rejects_transient_and_low_confidence_candidates():
    policy = MemoryWritePolicy(min_confidence=0.6)
    assert not policy.evaluate({
        "memory_type": "insight", "summary": "temporary result", "temporary": True,
    }).allowed
    assert not policy.evaluate({
        "memory_type": "decision", "summary": "unverified guess", "confidence": 0.2,
    }).allowed
    assert policy.evaluate({
        "memory_type": "decision", "summary": "use SQLite", "confidence": 0.8,
    }).allowed


def test_remember_records_provenance_and_governance_metadata(tmp_path):
    async def _run():
        mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
        await mgr.remember(
            "p", "ctx", "agent_task", "u", "o",
            extract_fn=_extract([{
                "memory_type": "decision", "summary": "use SQLite",
                "confidence": 0.9, "importance": 0.8,
            }]),
            source_run_id="run-1", source_trace_id="trace-1",
            source_message_id="message-1", scope="project",
        )
        entry = mgr.db.list_by_project("p")[0]
        assert entry.metadata["provenance"]["run_id"] == "run-1"
        assert entry.metadata["provenance"]["trace_id"] == "trace-1"
        assert entry.metadata["scope"] == "project"
        assert entry.metadata["governance"]["confidence"] == 0.9
        assert entry.metadata["governance"]["status"] == "active"
        mgr.close()

    asyncio.run(_run())


def test_insight_subject_supersession(tmp_path):
    """同 subject 的 insight 后写覆盖，且不继承旧重要度。"""
    async def _run():
        mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
        await mgr.remember("p", "ctx", "explore", "u", "o", extract_fn=_extract([
            {"memory_type": "insight", "summary": "没有类图", "subject": "uml:class_diagram",
             "importance": 0.9, "tags": []},
        ]))
        await mgr.remember("p", "ctx", "explore", "u", "o", extract_fn=_extract([
            {"memory_type": "insight", "summary": "有类图", "subject": "uml:class_diagram",
             "importance": 0.6, "tags": []},
        ]))

        rows = mgr.db.list_by_project("p", memory_type=MemoryType.INSIGHT)
        assert len(rows) == 1
        assert rows[0].summary == "有类图"
        assert abs(rows[0].importance_score - 0.6) < 1e-6  # 不继承旧值 0.9
        mgr.close()

    asyncio.run(_run())


def test_durable_preference_merge(tmp_path):
    """耐久类相似合并：importance 提升，summary 更新。"""
    async def _run():
        mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
        fn = _extract([{"memory_type": "preference", "summary": "用户偏好组合模式",
                        "importance": 0.8, "tags": ["设计"]}])
        await mgr.remember("p", "ctx", "opt", "u", "o", extract_fn=fn)
        await mgr.remember("p", "ctx", "opt", "u", "o", extract_fn=fn)

        rows = mgr.db.list_by_project("p", memory_type=MemoryType.PREFERENCE)
        assert len(rows) == 1
        assert abs(rows[0].importance_score - 0.85) < 1e-6  # 0.8 + 0.05
        mgr.close()

    asyncio.run(_run())


def test_normalize_subject():
    assert _normalize_subject("  Uml:Class_Diagram  ") == "uml:class_diagram"
    assert _normalize_subject("Class   Diagram") == "class diagram"
    assert _normalize_subject("") == ""
    assert _normalize_subject(None) == ""


def test_recency_decay_only_for_insight(tmp_path):
    """recency 只衰减 insight，且越旧衰减越多；耐久类不受影响。"""
    mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(hours=24)).isoformat()

    fresh = RecallResult(entry=MemoryEntry(
        project_id="p", memory_type=MemoryType.INSIGHT, summary="fresh",
        created_at=now.isoformat(), updated_at=now.isoformat()), score=10.0)
    old = RecallResult(entry=MemoryEntry(
        project_id="p", memory_type=MemoryType.INSIGHT, summary="old",
        created_at=old_ts, updated_at=old_ts), score=10.0)
    durable = RecallResult(entry=MemoryEntry(
        project_id="p", memory_type=MemoryType.PREFERENCE, summary="durable",
        created_at=old_ts, updated_at=old_ts), score=10.0)

    mgr._apply_recency([fresh, old, durable])

    assert fresh.score > 9.9     # age≈0 不衰减
    assert durable.score > 9.9   # 耐久类不衰减
    assert old.score < 5.0       # 24h 半衰 ≈ 3.68
    mgr.close()


def test_type_aware_decay(tmp_path):
    """maintenance 衰减：insight 快于耐久类。"""
    mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.INSIGHT,
                           summary="i", importance_score=1.0))
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.PREFERENCE,
                           summary="p", importance_score=1.0))

    mgr.maintenance("p")

    ins = mgr.db.list_by_project("p", memory_type=MemoryType.INSIGHT)[0]
    pref = mgr.db.list_by_project("p", memory_type=MemoryType.PREFERENCE)[0]
    assert abs(ins.importance_score - 0.93) < 1e-6
    assert abs(pref.importance_score - 0.98) < 1e-6
    assert ins.importance_score < pref.importance_score
    mgr.close()


def test_migration_adds_columns_idempotently(tmp_path):
    """旧库缺 subject/updated_at 列时，_migrate_columns 幂等补齐且不丢数据。"""
    db_path = str(tmp_path / "old.db")
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE memories (
        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT UNIQUE NOT NULL,
        project_id TEXT NOT NULL,
        memory_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        original_text TEXT NOT NULL DEFAULT '',
        metadata TEXT NOT NULL DEFAULT '{}',
        embedding BLOB,
        embedding_model TEXT NOT NULL DEFAULT '',
        importance_score REAL NOT NULL DEFAULT 0.5,
        access_count INTEGER NOT NULL DEFAULT 0,
        last_accessed_at TEXT,
        created_at TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT '',
        user_feedback TEXT,
        is_pinned INTEGER NOT NULL DEFAULT 0
    )""")
    con.execute("INSERT INTO memories (id, project_id, memory_type, summary, "
                "original_text, created_at) VALUES ('m1', 'p', 'insight', 'old', '', '2026-01-01')")
    con.commit()
    con.close()

    from memory_system.database import MemoryDatabase
    db = MemoryDatabase(db_path)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(memories)")}
    assert "subject" in cols
    assert "updated_at" in cols
    assert db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    db.close()


def test_recall_hits_chinese_and_alias(tmp_path):
    """recall 端到端：中文命中 + 英文别名命中；别名并入 tags。"""
    async def _run():
        mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
        await mgr.remember("p", "ctx", "explore", "u", "o", extract_fn=_extract([
            {"memory_type": "insight", "summary": "当前项目包含类图时序图组件图",
             "subject": "uml:diagrams", "importance": 0.7,
             "tags": ["UML", "类图"], "aliases": ["class diagram", "class"]},
        ]))

        stored = mgr.db.list_by_project("p", memory_type=MemoryType.INSIGHT)[0]
        assert "class diagram" in stored.tags  # 别名并入 tags

        r1 = await mgr.recall("p", "类图 有哪些", top_k=5)
        assert len(r1) >= 1
        r2 = await mgr.recall("p", "class diagram", top_k=5)
        assert len(r2) >= 1
        assert r2[0].entry.summary == "当前项目包含类图时序图组件图"
        mgr.close()

    asyncio.run(_run())


def test_inject_memories_format():
    results = [RecallResult(entry=MemoryEntry(
        project_id="p", memory_type=MemoryType.INSIGHT,
        summary="记忆摘要", tags=["tag1"]), score=0.9)]
    prompt = MemoryManager.inject_memories("你是助手", results)
    assert "项目历史记忆" in prompt
    assert "记忆摘要" in prompt
    assert "tag1" in prompt
    assert "相关性" in prompt


def test_prune_removes_lowest_importance(tmp_path):
    """超阈值时淘汰最低重要度；pin 与高重要度保留。"""
    config = MemoryConfig(max_entries_per_project=2)
    mgr = MemoryManager(db_path=str(tmp_path / "m.db"), config=config)
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.INSIGHT,
                           summary="low1", importance_score=0.15))
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.INSIGHT,
                           summary="low2", importance_score=0.16))
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.INSIGHT,
                           summary="high", importance_score=0.9))
    mgr.db.add(MemoryEntry(project_id="p", memory_type=MemoryType.INSIGHT,
                           summary="pinned", importance_score=0.14, is_pinned=True))

    mgr.maintenance("p")

    names = {e.summary for e in mgr.db.list_by_project("p")}
    assert "high" in names
    assert "pinned" in names
    assert "low2" in names
    assert "low1" not in names  # 最低重要度被淘汰
    mgr.close()


def test_reinforce_boosts_importance(tmp_path):
    mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
    entry = MemoryEntry(project_id="p", memory_type=MemoryType.PREFERENCE,
                        summary="pref", importance_score=0.5)
    mgr.db.add(entry)
    mgr.reinforce(entry.id, project_id="p")
    got = mgr.db.get("p", entry.id)
    assert abs(got.importance_score - 0.6) < 1e-6  # 0.5 + 0.1
    assert got.access_count == 1
    mgr.close()


def test_dedup_below_threshold_creates_new(tmp_path):
    """不同主题的耐久类记忆低于相似度阈值 → 新建而非合并。"""
    async def _run():
        mgr = MemoryManager(db_path=str(tmp_path / "m.db"))
        await mgr.remember("p", "ctx", "opt", "u", "o", extract_fn=_extract([
            {"memory_type": "preference", "summary": "用户偏好组合模式", "importance": 0.7, "tags": []},
        ]))
        await mgr.remember("p", "ctx", "opt", "u", "o", extract_fn=_extract([
            {"memory_type": "preference", "summary": "项目使用微服务架构", "importance": 0.7, "tags": []},
        ]))
        rows = mgr.db.list_by_project("p", memory_type=MemoryType.PREFERENCE)
        assert len(rows) == 2
        mgr.close()

    asyncio.run(_run())
