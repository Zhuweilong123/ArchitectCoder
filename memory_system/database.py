"""
SQLite 数据库层 — 记忆持久化与索引

特性:
  - SQLite + FTS5 全文索引
  - jieba 分词集成 (不可用时 bigram 兜底)
  - WAL 模式, 支持并发读写
  - embedding BLOB 字段预留
  - 线程安全 (asyncio.Lock 保护写操作)

Schema:
  memories 表: 记忆主表 (summary + original_text + metadata + 生命周期字段)
  memories_fts:  FTS5 虚拟表 (独立管理, 非 content-synced, 支持预分词)
"""

import json
import logging
import sqlite3
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import MemoryEntry, MemoryType, RecallResult, _utc_now
from .tokenizer import tokenize_for_fts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

CREATE_MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    rowid            INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT UNIQUE NOT NULL,
    project_id       TEXT NOT NULL,
    memory_type      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    original_text    TEXT NOT NULL DEFAULT '',
    subject          TEXT NOT NULL DEFAULT '',
    metadata         TEXT NOT NULL DEFAULT '{}',
    embedding        BLOB,
    embedding_model  TEXT NOT NULL DEFAULT '',
    importance_score REAL NOT NULL DEFAULT 0.5,
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL DEFAULT '',
    tags             TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT '',
    user_feedback    TEXT,
    is_pinned        INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary
);
"""

# 索引
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(project_id, memory_type);",
    "CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(project_id, memory_type, subject);",
    "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(project_id, importance_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_memories_accessed ON memories(project_id, last_accessed_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories(project_id, is_pinned);",
]

# Schema version (for future migrations)
PRAGMA_INIT = [
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA cache_size=-8000;",  # 8MB cache
]


def _search_text(summary: str, tags: List[str]) -> str:
    """构造 FTS 索引文本: summary + tags(含检索别名), 让标签/别名参与 BM25 召回."""
    parts = [summary or ""]
    parts.extend(str(t).strip() for t in (tags or []) if str(t).strip())
    return " ".join(parts)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class MemoryDatabase:
    """
    SQLite 数据库封装.

    Usage:
        db = MemoryDatabase("./memories.db")
        db.add(entry)
        results = db.search_bm25("project_1", "组合模式 设计")
        db.close()
    """

    __slots__ = ("db_path", "_conn", "_lock")

    def __init__(self, db_path: str = "./memories.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """懒初始化连接."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """建表 + 迁移列 + 索引."""
        conn = self._conn
        conn.execute(CREATE_MEMORIES_TABLE)
        self._migrate_columns(conn)
        # FTS5 表可能已存在, 忽略错误
        try:
            conn.execute(CREATE_FTS_TABLE)
        except sqlite3.OperationalError:
            pass
        for idx_sql in CREATE_INDEXES:
            conn.execute(idx_sql)
        conn.commit()

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        """幂等加列: 旧库缺 subject / updated_at 时补上."""
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "subject" not in existing:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN subject TEXT NOT NULL DEFAULT ''"
            )
        if "updated_at" not in existing:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        """关闭数据库连接."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, entry: MemoryEntry) -> int:
        """
        插入一条记忆, 同时更新 FTS5 索引.

        Returns:
            新插入行的 rowid (用于 FTS5 关联).
        """
        conn = self.conn
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        metadata_json = json.dumps(entry.metadata, ensure_ascii=False)

        sql = """
            INSERT INTO memories (
                id, project_id, memory_type, summary, original_text, subject,
                metadata, embedding, embedding_model,
                importance_score, access_count, last_accessed_at,
                created_at, updated_at, tags, source, user_feedback, is_pinned
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
        """
        params = (
            entry.id,
            entry.project_id,
            entry.memory_type.value,
            entry.summary,
            entry.original_text,
            entry.subject,
            metadata_json,
            entry.embedding,
            entry.embedding_model,
            entry.importance_score,
            entry.access_count,
            entry.last_accessed_at,
            entry.created_at,
            entry.updated_at or entry.created_at,
            tags_json,
            entry.source,
            entry.user_feedback,
            1 if entry.is_pinned else 0,
        )

        cursor = conn.execute(sql, params)

        # FTS5: 预分词后插入独立 FTS 表 (rowid 与 memories 表一致)
        rowid = cursor.lastrowid
        fts_tokens = tokenize_for_fts(_search_text(entry.summary, entry.tags))
        if fts_tokens.strip():
            conn.execute(
                "INSERT INTO memories_fts(rowid, summary) VALUES (?, ?)",
                (rowid, fts_tokens),
            )

        conn.commit()
        logger.debug(
            f"[Database] Added memory {entry.id[:8]}... (rowid={rowid}, "
            f"fts_tokens={len(fts_tokens.split())})"
        )
        return rowid

    def get(self, project_id: str, memory_id: str) -> Optional[MemoryEntry]:
        """按 ID 获取单条记忆."""
        conn = self.conn
        row = conn.execute(
            "SELECT * FROM memories WHERE project_id = ? AND id = ?",
            (project_id, memory_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def get_by_subject(
        self, project_id: str, memory_type: MemoryType, subject: str,
    ) -> Optional[MemoryEntry]:
        """按 (project_id, memory_type, subject) 获取一条记忆。

        用于 insight 类记忆的"后写覆盖"：同主题最新观察顶替旧观察。
        """
        if not subject:
            return None
        conn = self.conn
        row = conn.execute(
            "SELECT * FROM memories WHERE project_id = ? AND memory_type = ? "
            "AND subject = ? LIMIT 1",
            (project_id, memory_type.value, subject),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def update(self, entry: MemoryEntry) -> bool:
        """
        更新一条记忆 (按 id 匹配), 同步更新 FTS5.

        Returns:
            True 若更新成功, False 若记忆不存在.
        """
        conn = self.conn

        # 先查 rowid
        existing = conn.execute(
            "SELECT rowid FROM memories WHERE id = ? AND project_id = ?",
            (entry.id, entry.project_id),
        ).fetchone()
        if existing is None:
            return False

        rowid = existing["rowid"]
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        metadata_json = json.dumps(entry.metadata, ensure_ascii=False)

        sql = """
            UPDATE memories SET
                memory_type = ?, summary = ?, original_text = ?, subject = ?,
                metadata = ?, embedding = ?, embedding_model = ?,
                importance_score = ?, access_count = ?, last_accessed_at = ?,
                updated_at = ?,
                tags = ?, source = ?, user_feedback = ?, is_pinned = ?
            WHERE id = ? AND project_id = ?
        """
        conn.execute(sql, (
            entry.memory_type.value,
            entry.summary,
            entry.original_text,
            entry.subject,
            metadata_json,
            entry.embedding,
            entry.embedding_model,
            entry.importance_score,
            entry.access_count,
            entry.last_accessed_at,
            entry.updated_at or entry.created_at,
            tags_json,
            entry.source,
            entry.user_feedback,
            1 if entry.is_pinned else 0,
            entry.id,
            entry.project_id,
        ))

        # 更新 FTS5
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (rowid,))
        fts_tokens = tokenize_for_fts(_search_text(entry.summary, entry.tags))
        if fts_tokens.strip():
            conn.execute(
                "INSERT INTO memories_fts(rowid, summary) VALUES (?, ?)",
                (rowid, fts_tokens),
            )

        conn.commit()
        return True

    def delete(self, project_id: str, memory_id: str) -> bool:
        """
        删除一条记忆 (同时清除 FTS5 索引).

        Returns:
            True 若删除成功, False 若不存在.
        """
        conn = self.conn

        row = conn.execute(
            "SELECT rowid FROM memories WHERE id = ? AND project_id = ?",
            (memory_id, project_id),
        ).fetchone()
        if row is None:
            return False

        rowid = row["rowid"]
        conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (rowid,))
        conn.execute("DELETE FROM memories WHERE rowid = ?", (rowid,))
        conn.commit()
        return True

    def list_by_project(
        self,
        project_id: str,
        memory_type: Optional[MemoryType] = None,
        order_by: str = "created_at DESC",
        limit: Optional[int] = None,
    ) -> List[MemoryEntry]:
        """
        列出项目记忆.

        Args:
            project_id:  项目标识
            memory_type: 按类型过滤 (None = 所有)
            order_by:    排序字段
            limit:       最大返回数

        Returns:
            MemoryEntry 列表
        """
        conn = self.conn
        where = "WHERE project_id = ?"
        params: List[Any] = [project_id]

        if memory_type is not None:
            where += " AND memory_type = ?"
            params.append(memory_type.value)

        # 安全 order_by: 只允许已知列
        allowed_orders = {
            "created_at DESC", "created_at ASC",
            "importance_score DESC", "importance_score ASC",
            "access_count DESC", "last_accessed_at DESC",
            "rowid DESC",
        }
        if order_by not in allowed_orders:
            order_by = "created_at DESC"

        sql = f"SELECT * FROM memories {where} ORDER BY {order_by}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self, project_id: str, memory_type: Optional[MemoryType] = None) -> int:
        """项目记忆数量."""
        conn = self.conn
        if memory_type is None:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE project_id = ? AND memory_type = ?",
                (project_id, memory_type.value),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_memories_by_rowids(
        self, rowids: List[int]
    ) -> Dict[int, MemoryEntry]:
        """批量按 rowid 获取记忆."""
        if not rowids:
            return {}
        conn = self.conn
        placeholders = ",".join("?" for _ in rowids)
        rows = conn.execute(
            f"SELECT * FROM memories WHERE rowid IN ({placeholders})",
            rowids,
        ).fetchall()
        return {r["rowid"]: self._row_to_entry(r) for r in rows}

    # ------------------------------------------------------------------
    # BM25 检索 (FTS5)
    # ------------------------------------------------------------------

    def search_bm25(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        memory_type: Optional[MemoryType] = None,
    ) -> List[RecallResult]:
        """
        BM25 全文检索 (通过 FTS5).

        Args:
            project_id:  项目标识
            query:       自然语言查询
            top_k:       返回的最大记忆数
            memory_type: 按类型过滤 (None = 所有)

        Returns:
            RecallResult 列表, 按 BM25 得分降序排列
        """
        conn = self.conn

        # 将查询文本用 jieba/bigram 分词, 转为 FTS5 查询
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        # FTS5 BM25 检索, JOIN memories 获取完整数据
        where_clause = "WHERE m.project_id = ?"
        params: List[Any] = [project_id]

        if memory_type is not None:
            where_clause += " AND m.memory_type = ?"
            params.append(memory_type.value)

        sql = f"""
            SELECT m.*, bm25(memories_fts) AS bm25_score
            FROM memories_fts
            JOIN memories m ON m.rowid = memories_fts.rowid
            {where_clause}
              AND memories_fts MATCH ?
            ORDER BY bm25_score
            LIMIT ?
        """
        params.append(fts_query)
        params.append(top_k)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning(f"[Database] FTS5 search failed: {exc}")
            return []

        results: List[RecallResult] = []
        for row in rows:
            entry = self._row_to_entry(row)
            # bm25() 返回负值, 越小越相关; 取反使高分=高相关
            raw_score = row["bm25_score"]
            score = -raw_score if raw_score is not None else 0.0
            results.append(RecallResult(entry=entry, score=score, source="bm25"))

        return results

    def _build_fts_query(self, query: str) -> str:
        """
        构建 FTS5 查询字符串.

        用 jieba/bigram 预分词后, 各 term 用 OR 连接 (召回优先).
        对包含特殊字符的 term 用双引号包裹.
        """
        tokens = tokenize_for_fts(query).split()
        if not tokens:
            return ""

        # 过滤并转义
        safe_tokens: List[str] = []
        for t in tokens:
            # 去掉纯标点
            if not t.strip():
                continue
            # FTS5 特殊字符处理: 用双引号包裹
            safe = t.replace('"', '""')
            safe_tokens.append(f'"{safe}"')

        if not safe_tokens:
            return ""

        return " OR ".join(safe_tokens)

    # ------------------------------------------------------------------
    # 相似检索 (用于去重)
    # ------------------------------------------------------------------

    def find_similar(
        self,
        project_id: str,
        text: str,
        top_k: int = 3,
    ) -> List[RecallResult]:
        """
        FTS5 检索与 text 语义相近的已有记忆.

        Args:
            project_id: 项目标识
            text:       待比较的文本 (新记忆的 summary)
            top_k:      返回的最大候选数

        Returns:
            按 BM25 得分降序排列的 RecallResult 列表
        """
        return self.search_bm25(project_id, text, top_k=top_k)

    # ------------------------------------------------------------------
    # 生命周期操作 (供 lifecycle 模块调用)
    # ------------------------------------------------------------------

    def update_access(self, rowid: int) -> None:
        """记录一次访问 (access_count + 1)."""
        conn = self.conn
        conn.execute(
            """UPDATE memories
               SET access_count = access_count + 1,
                   last_accessed_at = ?
               WHERE rowid = ?""",
            (_utc_now(), rowid),
        )
        conn.commit()

    def reinforce(self, memory_id: str, project_id: str, delta: float) -> bool:
        """
        强化一条记忆 (增加 importance_score).

        Returns:
            True 若成功.
        """
        conn = self.conn
        cursor = conn.execute(
            """UPDATE memories
               SET importance_score = MIN(1.0, importance_score + ?)
               WHERE id = ? AND project_id = ?""",
            (delta, memory_id, project_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def apply_decay(
        self,
        project_id: str,
        insight_factor: float,
        durable_factor: float,
        importance_min: float,
    ) -> int:
        """对项目所有非 pinned 记忆施加衰减 (insight 类衰减更快).

        Returns:
            受影响的记忆数量.
        """
        conn = self.conn
        affected = 0
        # insight 类: 状态观察, 随时间更快淡出
        cur = conn.execute(
            """UPDATE memories
               SET importance_score = MAX(?, importance_score * ?)
               WHERE project_id = ? AND is_pinned = 0 AND memory_type = 'insight'""",
            (importance_min, insight_factor, project_id),
        )
        affected += cur.rowcount
        # 耐久类: preference/decision/rejection/convention
        cur = conn.execute(
            """UPDATE memories
               SET importance_score = MAX(?, importance_score * ?)
               WHERE project_id = ? AND is_pinned = 0 AND memory_type != 'insight'""",
            (importance_min, durable_factor, project_id),
        )
        affected += cur.rowcount
        conn.commit()
        return affected

    def get_prune_candidates(
        self,
        project_id: str,
        importance_threshold: float,
        max_entries: int,
        batch_ratio: float,
        pin_access_threshold: int,
    ) -> List[int]:
        """
        获取待淘汰记忆的 rowid 列表.

        策略:
          1. 仅当项目记忆数 > max_entries 时才选候选
          2. 排除 pinned 记忆
          3. 排除 access_count >= pin_access_threshold 的记忆 (热记忆保护)
          4. 按 (importance_score, last_accessed_at) 升序排列
          5. 每次最多淘汰 batch_ratio 比例

        Returns:
            待淘汰的 rowid 列表
        """
        conn = self.conn

        total = self.count(project_id)
        if total <= max_entries:
            return []

        # 计算可淘汰的批量大小
        excess = total - max_entries
        batch_limit = max(1, int(total * batch_ratio))
        prune_count = min(excess, batch_limit)

        rows = conn.execute(
            """SELECT rowid FROM memories
               WHERE project_id = ?
                 AND is_pinned = 0
                 AND access_count < ?
                 AND importance_score < ?
               ORDER BY importance_score ASC, last_accessed_at ASC NULLS FIRST
               LIMIT ?""",
            (project_id, pin_access_threshold, importance_threshold, prune_count),
        ).fetchall()

        return [r["rowid"] for r in rows]

    def delete_by_rowids(self, rowids: List[int]) -> int:
        """
        批量删除记忆 (同时清除 FTS5).

        Returns:
            实际删除的数量.
        """
        if not rowids:
            return 0

        conn = self.conn
        placeholders = ",".join("?" for _ in rowids)
        conn.execute(
            f"DELETE FROM memories_fts WHERE rowid IN ({placeholders})",
            rowids,
        )
        cursor = conn.execute(
            f"DELETE FROM memories WHERE rowid IN ({placeholders})",
            rowids,
        )
        conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # 统计 / 工具
    # ------------------------------------------------------------------

    def stats(self, project_id: str) -> Dict[str, Any]:
        """项目记忆统计."""
        conn = self.conn

        total = self.count(project_id)

        type_rows = conn.execute(
            """SELECT memory_type, COUNT(*) as cnt
               FROM memories WHERE project_id = ?
               GROUP BY memory_type""",
            (project_id,),
        ).fetchall()
        by_type = {r["memory_type"]: r["cnt"] for r in type_rows}

        fts_doc_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM memories_fts"
        ).fetchone()["cnt"]

        avg_importance = conn.execute(
            "SELECT AVG(importance_score) as avg FROM memories WHERE project_id = ?",
            (project_id,),
        ).fetchone()["avg"]

        return {
            "project_id": project_id,
            "total_memories": total,
            "by_type": by_type,
            "fts_docs": fts_doc_count,
            "avg_importance": round(avg_importance, 4) if avg_importance else 0.0,
        }

    def clear_project(self, project_id: str) -> int:
        """清除项目的所有记忆, 返回清除数量."""
        conn = self.conn

        rows = conn.execute(
            "SELECT rowid FROM memories WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        rowids = [r["rowid"] for r in rows]

        if rowids:
            placeholders = ",".join("?" for _ in rowids)
            conn.execute(
                f"DELETE FROM memories_fts WHERE rowid IN ({placeholders})",
                rowids,
            )
            conn.execute(
                "DELETE FROM memories WHERE project_id = ?",
                (project_id,),
            )
            conn.commit()

        return len(rowids)

    # ------------------------------------------------------------------
    # Internal: row → MemoryEntry
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        """将 sqlite3.Row 转换为 MemoryEntry."""
        tags = json.loads(row["tags"]) if row["tags"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        return MemoryEntry(
            id=row["id"],
            project_id=row["project_id"],
            memory_type=MemoryType(row["memory_type"]),
            summary=row["summary"],
            original_text=row["original_text"] or "",
            subject=row["subject"] or "",
            metadata=metadata,
            embedding=row["embedding"],
            embedding_model=row["embedding_model"] or "",
            importance_score=row["importance_score"],
            access_count=row["access_count"] or 0,
            last_accessed_at=row["last_accessed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"] or "",
            tags=tags,
            source=row["source"] or "",
            user_feedback=row["user_feedback"],
            is_pinned=bool(row["is_pinned"]),
        )
