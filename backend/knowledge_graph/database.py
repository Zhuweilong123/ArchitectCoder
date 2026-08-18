"""
SQLite 图数据库层 — kg_nodes + kg_edges + FTS5 全文索引.

Follows memory_system/database.py pattern:
  - WAL mode + NORMAL synchronous
  - Lazy connection via property
  - _row_to_node / _row_to_edge static methods
  - Natural key upsert for idempotent builds
  - FTS5 content-sync mode (auto-sync via triggers)

Reuses memory_system.tokenizer.tokenize_for_fts() for query tokenization.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Optional

from .models import (
    GraphNode, GraphEdge, NodeType, EdgeType, NodeResult, PathResult, _utc_now,
)

logger = logging.getLogger(__name__)

# ── FTS5 query builder ────────────────────────────────────────
# 复用 memory_system tokenizer, 仅在可用时导入

try:
    from memory_system.tokenizer import tokenize_for_fts as _tokenize_for_fts
except ImportError:
    def _tokenize_for_fts(text: str) -> str:
        """Fallback: 直接用原始文本做 FTS 查询."""
        return text


def _build_fts_query(pattern: str) -> str:
    """将用户查询转换为 FTS5 MATCH 表达式.

    Uses jieba/bigram tokenization then joins with OR for BM25 scoring.
    No phrase-quote wrapping — FTS5 content-sync mode re-tokenizes CJK into
    individual characters, so phrase matching doesn't work on pre-tokenized
    content_text.  OR-connected character/word tokens give effective BM25
    ranking for Chinese.
    """
    tokens = _tokenize_for_fts(pattern)
    if not tokens or not tokens.strip():
        return _escape_fts(pattern)
    # No double-quote wrapping — let FTS5 match individual tokens
    parts = [t for t in tokens.split() if t and len(t) >= 2]
    if not parts:
        # All tokens are single-char, include them anyway
        parts = [t for t in tokens.split() if t]
    return " OR ".join(parts)


def _escape_fts(text: str) -> str:
    """Escape FTS5 special characters and wrap."""
    safe = text.replace('"', '""')
    return f'"{safe}"'


# ═══════════════════════════════════════════════════════════════
# DDL
# ═══════════════════════════════════════════════════════════════

DDL_SCRIPT = """
-- 节点主表
CREATE TABLE IF NOT EXISTS kg_nodes (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    id           TEXT UNIQUE NOT NULL,
    node_type    TEXT NOT NULL,
    name         TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'design',
    properties   TEXT NOT NULL DEFAULT '{}',
    content_text TEXT NOT NULL DEFAULT '',
    embedding    BLOB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 边表
CREATE TABLE IF NOT EXISTS kg_edges (
    rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT UNIQUE NOT NULL,
    source_id  TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    edge_type  TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    weight     REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL
);

-- FTS5 虚拟表 (content-sync: 触发器自动同步 kg_nodes.content_text)
CREATE VIRTUAL TABLE IF NOT EXISTS kg_node_fts USING fts5(
    content_text,
    content='kg_nodes',
    content_rowid='rowid'
);

-- ── 索引 ──

-- 节点: 自然键唯一索引 (幂等 upsert)
CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_nodes_natural
    ON kg_nodes(project_id, node_type, name, source);

-- 节点: 常用查询加速
CREATE INDEX IF NOT EXISTS idx_kg_nodes_project   ON kg_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type      ON kg_nodes(project_id, node_type);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_source    ON kg_nodes(project_id, source);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_name      ON kg_nodes(project_id, name);

-- 边: 查询加速
CREATE INDEX IF NOT EXISTS idx_kg_edges_source    ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target    ON kg_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_type      ON kg_edges(source_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_kg_edges_pair      ON kg_edges(source_id, target_id);

-- 边去重: (source_id, target_id, edge_type) + properties hash
CREATE UNIQUE INDEX IF NOT EXISTS idx_kg_edges_unique
    ON kg_edges(source_id, target_id, edge_type, properties);

-- FTS5 同步触发器
CREATE TRIGGER IF NOT EXISTS kg_nodes_ai AFTER INSERT ON kg_nodes BEGIN
    INSERT INTO kg_node_fts(rowid, content_text) VALUES (new.rowid, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS kg_nodes_ad AFTER DELETE ON kg_nodes BEGIN
    INSERT INTO kg_node_fts(kg_node_fts, rowid, content_text)
        VALUES ('delete', old.rowid, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS kg_nodes_au AFTER UPDATE ON kg_nodes BEGIN
    INSERT INTO kg_node_fts(kg_node_fts, rowid, content_text)
        VALUES ('delete', old.rowid, old.content_text);
    INSERT INTO kg_node_fts(rowid, content_text)
        VALUES (new.rowid, new.content_text);
END;
"""

DDL_INDEXES = [stmt.strip() for stmt in DDL_SCRIPT.strip().split(";") if "INDEX" in stmt.upper()]
DDL_TABLES  = [stmt.strip() for stmt in DDL_SCRIPT.strip().split(";") if "TABLE" in stmt.upper()]


# ═══════════════════════════════════════════════════════════════
# KnowledgeGraphDB
# ═══════════════════════════════════════════════════════════════

class KnowledgeGraphDB:
    """SQLite 图存储封装 — 仿 MemoryDatabase 模式.

    Usage:
        db = KnowledgeGraphDB("./data/knowledge_graph.db")
        rowid = db.upsert_node(node)
        db.upsert_edge(edge)
        results = db.search_bm25("proj", "User login", top_k=10)
        db.close()
    """

    __slots__ = ("db_path", "_conn")

    def __init__(self, db_path: str = "./data/knowledge_graph.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Connection management ──────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA cache_size=-8000")
            self._init_schema()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _init_schema(self) -> None:
        self.conn.executescript(DDL_SCRIPT)

    # ── Node CRUD ──────────────────────────────────────────

    def upsert_node(self, node: GraphNode) -> int:
        """INSERT 或 REPLACE 一个节点 (by natural key).

        Returns:
            该行的 rowid.
        """
        node.updated_at = _utc_now()
        sql = """\
            INSERT INTO kg_nodes (id, node_type, name, project_id, source,
                                  properties, content_text, embedding,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, node_type, name, source) DO UPDATE SET
                properties = excluded.properties,
                content_text = excluded.content_text,
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
        """
        cur = self.conn.execute(sql, (
            node.id,
            node.node_type.value,
            node.name,
            node.project_id,
            node.source,
            json.dumps(node.properties, ensure_ascii=False),
            node.content_text,
            node.embedding,
            node.created_at,
            node.updated_at,
        ))
        self.conn.commit()
        return cur.lastrowid or 0

    def upsert_nodes_batch(self, nodes: list[GraphNode]) -> int:
        """批量 upsert 节点."""
        now = _utc_now()
        rows = []
        for node in nodes:
            node.updated_at = now
            rows.append((
                node.id, node.node_type.value, node.name,
                node.project_id, node.source,
                json.dumps(node.properties, ensure_ascii=False),
                node.content_text, node.embedding,
                node.created_at, node.updated_at,
            ))
        sql = """\
            INSERT INTO kg_nodes (id, node_type, name, project_id, source,
                                  properties, content_text, embedding,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, node_type, name, source) DO UPDATE SET
                properties = excluded.properties,
                content_text = excluded.content_text,
                embedding = excluded.embedding,
                updated_at = excluded.updated_at
        """
        with self.conn:
            self.conn.executemany(sql, rows)
        return len(rows)

    def delete_node(self, node_id: str) -> bool:
        """删除一个节点 (FTS5 触发器自动清理)."""
        # 先删关联边
        self.conn.execute(
            "DELETE FROM kg_edges WHERE source_id = ? OR target_id = ?",
            (node_id, node_id),
        )
        cur = self.conn.execute("DELETE FROM kg_nodes WHERE id = ?", (node_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_nodes_by_project_source(self, project_id: str,
                                       source: str = "design") -> int:
        """批量删除某项目某 source 的所有节点 (及关联边).

        用于声明式重建前的清理.
        """
        # 先找到要删除的节点 ID
        ids = [
            row[0] for row in
            self.conn.execute(
                "SELECT id FROM kg_nodes WHERE project_id = ? AND source = ?",
                (project_id, source),
            ).fetchall()
        ]
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        # 删边
        self.conn.execute(
            f"DELETE FROM kg_edges WHERE source_id IN ({placeholders}) "
            f"OR target_id IN ({placeholders})",
            ids + ids,
        )
        # 删节点
        cur = self.conn.execute(
            f"DELETE FROM kg_nodes WHERE id IN ({placeholders})", ids,
        )
        self.conn.commit()
        return cur.rowcount

    def get_descendant_ids(self, node_id: str,
                           edge_type: str = "contains") -> list[str]:
        """BFS 递归收集指定节点的所有后代节点 ID (通过指定边类型).

        用于增量重建某张图时, 找出该图下的全部旧实体 (含多级嵌套,
        如 DIAGRAM → CLASS → METHOD/ATTRIBUTE), 避免只删一层留下悬空节点.
        """
        all_ids: set[str] = set()
        frontier: list[str] = [node_id]
        while frontier:
            placeholders = ",".join("?" * len(frontier))
            rows = self.conn.execute(
                f"SELECT target_id FROM kg_edges "
                f"WHERE source_id IN ({placeholders}) AND edge_type = ?",
                [*frontier, edge_type],
            ).fetchall()
            nxt: list[str] = []
            for (tid,) in rows:
                if tid not in all_ids and tid != node_id:
                    all_ids.add(tid)
                    nxt.append(tid)
            frontier = nxt
        return list(all_ids)

    def delete_nodes_by_ids(self, ids: list[str]) -> int:
        """批量删除指定 ID 的节点及所有关联边 (FTS5 触发器自动清理索引)."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(
            f"DELETE FROM kg_edges WHERE source_id IN ({placeholders}) "
            f"OR target_id IN ({placeholders})",
            ids + ids,
        )
        cur = self.conn.execute(
            f"DELETE FROM kg_nodes WHERE id IN ({placeholders})", ids,
        )
        self.conn.commit()
        return cur.rowcount

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        row = self.conn.execute(
            "SELECT * FROM kg_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, GraphNode]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM kg_nodes WHERE id IN ({placeholders})", ids,
        ).fetchall()
        return {self._row_to_node(r).id: self._row_to_node(r) for r in rows}

    def find_nodes(
        self,
        project_id: str,
        node_type: Optional[str] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> list[GraphNode]:
        """精确查找节点."""
        clauses = ["project_id = ?"]
        params: list = [project_id]
        if node_type:
            clauses.append("node_type = ?")
            params.append(node_type)
        if name:
            clauses.append("name = ?")
            params.append(name)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT * FROM kg_nodes WHERE {where} LIMIT ?",
            params + [limit],
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    # ── Edge CRUD ──────────────────────────────────────────

    def upsert_edge(self, edge: GraphEdge) -> int:
        """INSERT 或 REPLACE 一条边."""
        sql = """\
            INSERT INTO kg_edges (id, source_id, target_id, edge_type,
                                  properties, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, edge_type, properties) DO UPDATE SET
                id = excluded.id,
                weight = excluded.weight,
                created_at = excluded.created_at
        """
        cur = self.conn.execute(sql, (
            edge.id,
            edge.source_id,
            edge.target_id,
            edge.edge_type.value,
            json.dumps(edge.properties, ensure_ascii=False),
            edge.weight,
            edge.created_at or _utc_now(),
        ))
        self.conn.commit()
        return cur.lastrowid or 0

    def upsert_edges_batch(self, edges: list[GraphEdge]) -> int:
        """批量 upsert 边."""
        now = _utc_now()
        rows = []
        for edge in edges:
            if not edge.created_at:
                edge.created_at = now
            rows.append((
                edge.id, edge.source_id, edge.target_id,
                edge.edge_type.value,
                json.dumps(edge.properties, ensure_ascii=False),
                edge.weight, edge.created_at,
            ))
        sql = """\
            INSERT INTO kg_edges (id, source_id, target_id, edge_type,
                                  properties, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, edge_type, properties) DO UPDATE SET
                id = excluded.id,
                weight = excluded.weight,
                created_at = excluded.created_at
        """
        with self.conn:
            self.conn.executemany(sql, rows)
        return len(rows)

    def delete_edge(self, edge_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM kg_edges WHERE id = ?", (edge_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_edges_by_nodes(self, source_id: str = "",
                               target_id: str = "") -> int:
        """删除与指定节点相关的所有边."""
        if source_id and target_id:
            cur = self.conn.execute(
                "DELETE FROM kg_edges WHERE source_id = ? AND target_id = ?",
                (source_id, target_id),
            )
        elif source_id:
            cur = self.conn.execute(
                "DELETE FROM kg_edges WHERE source_id = ?", (source_id,),
            )
        elif target_id:
            cur = self.conn.execute(
                "DELETE FROM kg_edges WHERE target_id = ?", (target_id,),
            )
        else:
            return 0
        self.conn.commit()
        return cur.rowcount

    def find_edges(
        self,
        source_id: str = "",
        target_id: str = "",
        edge_type: Optional[str] = None,
        limit: int = 500,
    ) -> list[GraphEdge]:
        """查找边."""
        clauses: list[str] = []
        params: list = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if edge_type:
            clauses.append("edge_type = ?")
            params.append(edge_type)
        if not clauses:
            rows = self.conn.execute(
                "SELECT * FROM kg_edges LIMIT ?", (limit,)
            ).fetchall()
        else:
            where = " AND ".join(clauses)
            rows = self.conn.execute(
                f"SELECT * FROM kg_edges WHERE {where} LIMIT ?",
                params + [limit],
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # ── BM25 search ────────────────────────────────────────

    def search_bm25(
        self,
        project_id: str,
        query: str,
        top_k: int = 20,
        node_type: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[NodeResult]:
        """BM25 全文检索 + 名称模糊匹配.

        FTS5 默认分词器把驼峰类名（如 ``WaveformGenerator``）当作单个 token，
        搜 ``Waveform`` 命中不了。因此在 BM25 之外补充 name 的大小写不敏感
        包含匹配（``name LIKE '%query%'``），与 BM25 结果取并集，保证按类名
        检索总能命中。查询过短（<3 字符）时跳过 LIKE，避免返回过多噪音。

        Args:
            project_id: 项目标识
            query:      用户查询文本 (中文/英文混合)
            top_k:      最大返回数
            node_type:  节点类型过滤 (None = 全部)
            source:     来源过滤 ("design"|"code"|"test", None = 全部)

        Returns:
            NodeResult 列表, score 为 BM25 正相关得分 (越高越相关).
        """
        fts_query = _build_fts_query(query)

        # 构建 WHERE 子句
        extra_clauses = ["n.project_id = ?"]
        extra_params: list = [project_id]
        if node_type:
            extra_clauses.append("n.node_type = ?")
            extra_params.append(node_type)
        if source:
            extra_clauses.append("n.source = ?")
            extra_params.append(source)
        extra_where = " AND ".join(extra_clauses)

        sql = f"""\
            SELECT n.*, bm25(kg_node_fts) AS bm25_score
            FROM kg_node_fts
            JOIN kg_nodes n ON n.rowid = kg_node_fts.rowid
            WHERE kg_node_fts MATCH ?
              AND {extra_where}
            ORDER BY bm25_score
            LIMIT ?
        """
        rows = self.conn.execute(
            sql, [fts_query] + extra_params + [top_k],
        ).fetchall()

        # bm25() 返回负值为更相关, 取绝对值后反转
        results: list[NodeResult] = []
        for row in rows:
            bm25_val = row["bm25_score"]
            if bm25_val is not None:
                score = abs(bm25_val) + 1.0  # +1 避免除零
                score = round(1.0 / score, 4)  # 反转为正相关
            else:
                score = 0.0
            results.append(NodeResult(
                node=self._row_to_node(row),
                score=score,
            ))

        # ── 名称模糊匹配兜底: 命中 BM25 索引不了的驼峰/部分类名 ──
        name_like = query.strip()
        if len(name_like) >= 3:
            like_clauses = ["project_id = ?", "name LIKE ?"]
            like_params: list = [project_id, f"%{name_like}%"]
            if node_type:
                like_clauses.append("node_type = ?")
                like_params.append(node_type)
            if source:
                like_clauses.append("source = ?")
                like_params.append(source)
            like_where = " AND ".join(like_clauses)
            like_rows = self.conn.execute(
                f"SELECT * FROM kg_nodes WHERE {like_where} LIMIT ?",
                like_params + [top_k],
            ).fetchall()
            seen: set[str] = {r.node.id for r in results}
            for row in like_rows:
                if row["id"] in seen:
                    continue
                # 名称精确包含的节点给予较高相关度
                results.append(NodeResult(
                    node=self._row_to_node(row),
                    score=0.9,
                ))
                seen.add(row["id"])

        return results

    # ── Neighbor expansion ─────────────────────────────────

    def get_neighbors(
        self,
        node_ids: list[str],
        edge_types: Optional[list[str]] = None,
        direction: str = "outgoing",
    ) -> dict[str, list[tuple[str, str, str, dict]]]:
        """批量获取邻居 (用于 BFS 展开).

        Args:
            node_ids:   起点节点 ID 列表
            edge_types: 边类型过滤
            direction:  "outgoing" | "incoming" | "both"

        Returns:
            {origin_node_id: [(neighbor_node_id, edge_type, edge_id, edge_properties), ...]}
        """
        if not node_ids:
            return {}

        placeholders = ",".join("?" * len(node_ids))
        params: list = node_ids + node_ids  # for both directions

        conditions: list[str] = []
        if direction == "outgoing":
            conditions.append(f"source_id IN ({placeholders})")
            params = node_ids
        elif direction == "incoming":
            conditions.append(f"target_id IN ({placeholders})")
            params = node_ids
        else:  # "both"
            conditions.append(
                f"(source_id IN ({placeholders}) OR target_id IN ({placeholders}))"
            )

        if edge_types:
            et_placeholders = ",".join("?" * len(edge_types))
            conditions.append(f"edge_type IN ({et_placeholders})")
            params = params + edge_types

        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"SELECT source_id, target_id, edge_type, id, properties "
            f"FROM kg_edges WHERE {where}",
            params,
        ).fetchall()

        result: dict[str, list[tuple[str, str, str, dict]]] = {nid: [] for nid in node_ids}
        for row in rows:
            src = row["source_id"]
            tgt = row["target_id"]
            et = row["edge_type"]
            eid = row["id"]
            props = _parse_json(row["properties"])
            if direction == "outgoing" or direction == "both":
                if src in result:
                    result[src].append((tgt, et, eid, props))
            if direction == "incoming" or direction == "both":
                if tgt in result:
                    result[tgt].append((src, et, eid, props))

        return result

    # ── Path trace (recursive CTE) ─────────────────────────

    def trace_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 10,
        edge_types: Optional[list[str]] = None,
    ) -> list[PathResult]:
        """使用 SQLite recursive CTE 查找两节点间所有路径.

        Args:
            source_id:  起点节点 ID
            target_id:  终点节点 ID
            max_depth:  最大搜索深度
            edge_types: 边类型过滤

        Returns:
            找到的路径列表, 按长度升序.
        """
        # 构建边类型过滤条件
        edge_filter = ""
        if edge_types:
            et_placeholders = ",".join(("?," * len(edge_types))[:-1])
            et_placeholders = ",".join(f"'{et}'" for et in edge_types)
            edge_filter = f" AND e.edge_type IN ({et_placeholders})"

        # 注意: recursive CTE 在 UNION ALL 的 recursive arm 中
        # 不能直接使用绑定参数, 需要用字符串拼接 max_depth.
        sql = f"""\
            WITH RECURSIVE path_search AS (
                -- Base: direct edge from source
                SELECT
                    e.id AS edge_id,
                    e.source_id,
                    e.target_id,
                    e.edge_type,
                    e.properties,
                    e.source_id || '|' || e.target_id AS path_nodes,
                    e.id || '' AS path_edges,
                    1 AS depth
                FROM kg_edges e
                WHERE e.source_id = ?
                  {edge_filter}

                UNION ALL

                -- Recursive: extend from last target
                SELECT
                    e.id AS edge_id,
                    e.source_id,
                    e.target_id,
                    e.edge_type,
                    e.properties,
                    ps.path_nodes || '|' || e.target_id,
                    ps.path_edges || '|' || e.id,
                    ps.depth + 1
                FROM kg_edges e
                JOIN path_search ps ON e.source_id = ps.target_id
                WHERE ps.depth < {int(max_depth)}
                  AND ps.path_nodes NOT LIKE '%|' || e.target_id || '|%'
                  AND ps.path_nodes NOT LIKE e.target_id || '|%'
                  {edge_filter.replace('e.', 'e.')}
            )
            SELECT * FROM path_search
            WHERE target_id = ?
            ORDER BY depth;
        """
        rows = self.conn.execute(sql, (source_id, target_id)).fetchall()

        # 收集所有出现的节点 ID 并批量加载
        all_node_ids: set[str] = {source_id, target_id}
        for row in rows:
            for nid in row["path_nodes"].split("|"):
                all_node_ids.add(nid)

        node_map = self.get_nodes_by_ids(list(all_node_ids))

        # 组装结果
        results: list[PathResult] = []
        for row in rows:
            node_ids = row["path_nodes"].split("|")
            edge_id_list = row["path_edges"].split("|")
            path_nodes = [node_map.get(nid) for nid in node_ids]

            edges: list[dict] = []
            for eid in edge_id_list:
                # 从 CTE 行中获取当前边的属性
                edges.append({
                    "edge_id": row["edge_id"],
                    "source_id": row["source_id"],
                    "target_id": row["target_id"],
                    "edge_type": row["edge_type"],
                    "properties": _parse_json(row["properties"]),
                })
                break  # CTE 每行只包含最后一条边的信息

            # 为每条路径重新获取所有边的详细信息
            full_edges: list[dict] = []
            for i in range(len(node_ids) - 1):
                # 批量查边以获取完整信息
                edge_rows = self.conn.execute(
                    "SELECT * FROM kg_edges WHERE source_id = ? AND target_id = ?",
                    (node_ids[i], node_ids[i + 1]),
                ).fetchall()
                for er in edge_rows:
                    full_edges.append({
                        "edge_id": er["id"],
                        "edge_type": er["edge_type"],
                        "source_id": er["source_id"],
                        "target_id": er["target_id"],
                        "properties": _parse_json(er["properties"]),
                    })

            results.append(PathResult(
                node_ids=node_ids,
                edges=full_edges,
                length=len(node_ids) - 1,
                nodes=[n for n in path_nodes if n is not None],
            ))

        return results

    # ── Diff ───────────────────────────────────────────────

    def get_design_classes_without_implementation(
        self, project_id: str,
    ) -> list[GraphNode]:
        """返回设计层中有但代码层中未实现的 CLASS 节点."""
        rows = self.conn.execute("""\
            SELECT d.* FROM kg_nodes d
            WHERE d.project_id = ?
              AND d.node_type = 'class'
              AND d.source = 'design'
              AND d.id NOT IN (
                  SELECT target_id FROM kg_edges WHERE edge_type = 'implements'
              )
            ORDER BY d.name
        """, (project_id,)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_code_classes_without_design(
        self, project_id: str,
    ) -> list[GraphNode]:
        """返回代码层中有但设计层中没有的 CLASS 节点."""
        rows = self.conn.execute("""\
            SELECT c.* FROM kg_nodes c
            WHERE c.project_id = ?
              AND c.node_type = 'class'
              AND c.source = 'code'
              AND c.id NOT IN (
                  SELECT e.source_id FROM kg_edges e
                  WHERE e.edge_type = 'implements'
                    AND e.target_id IN (
                        SELECT id FROM kg_nodes
                        WHERE project_id = ? AND node_type = 'class' AND source = 'design'
                    )
              )
            ORDER BY c.name
        """, (project_id, project_id)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_implemented_pairs(
        self, project_id: str,
    ) -> list[tuple[GraphNode, GraphNode]]:
        """返回 (code_node, design_node) 的 IMPLEMENTS 配对."""
        rows = self.conn.execute("""\
            SELECT c.*, d.*
            FROM kg_edges e
            JOIN kg_nodes c ON c.id = e.source_id
            JOIN kg_nodes d ON d.id = e.target_id
            WHERE e.edge_type = 'implements'
              AND c.project_id = ?
              AND d.project_id = ?
            ORDER BY d.name
        """, (project_id, project_id)).fetchall()
        # 需要拆分列 — sqlite3.Row 有重复列名时只保留最后一个
        # 改用两次查询
        pairs: list[tuple[GraphNode, GraphNode]] = []
        edge_rows = self.conn.execute("""\
            SELECT e.source_id, e.target_id
            FROM kg_edges e
            WHERE e.edge_type = 'implements'
        """).fetchall()
        code_ids = [r["source_id"] for r in edge_rows]
        design_ids = [r["target_id"] for r in edge_rows]
        code_map = self.get_nodes_by_ids(code_ids)
        design_map = self.get_nodes_by_ids(design_ids)
        for er in edge_rows:
            cn = code_map.get(er["source_id"])
            dn = design_map.get(er["target_id"])
            if cn and dn and cn.project_id == project_id and dn.project_id == project_id:
                pairs.append((cn, dn))
        return pairs

    # ── Stats ──────────────────────────────────────────────

    def stats(self, project_id: str) -> dict[str, Any]:
        """获取项目知识图谱统计."""
        node_count = self.conn.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE project_id = ?", (project_id,),
        ).fetchone()[0]
        edge_count = self.conn.execute(
            "SELECT COUNT(*) FROM kg_edges e "
            "INNER JOIN kg_nodes n ON n.id = e.source_id "
            "WHERE n.project_id = ?", (project_id,),
        ).fetchone()[0]
        type_counts = self.conn.execute(
            "SELECT node_type, COUNT(*) as cnt FROM kg_nodes "
            "WHERE project_id = ? GROUP BY node_type ORDER BY cnt DESC",
            (project_id,),
        ).fetchall()

        return {
            "project_id": project_id,
            "total_nodes": node_count,
            "total_edges": edge_count,
            "by_type": {r["node_type"]: r["cnt"] for r in type_counts},
        }

    def clear_project(self, project_id: str) -> int:
        """清除项目所有节点和边."""
        ids = [row[0] for row in self.conn.execute(
            "SELECT id FROM kg_nodes WHERE project_id = ?", (project_id,),
        ).fetchall()]
        if not ids:
            return 0
        ph = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM kg_edges WHERE source_id IN ({ph}) OR target_id IN ({ph})", ids + ids)
        cur = self.conn.execute(f"DELETE FROM kg_nodes WHERE id IN ({ph})", ids)
        self.conn.commit()
        return cur.rowcount

    # ── Serialization helpers ──────────────────────────────

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> GraphNode:
        return GraphNode(
            id=row["id"],
            node_type=NodeType(row["node_type"]),
            name=row["name"],
            project_id=row["project_id"],
            source=row["source"],
            properties=_parse_json(row["properties"]),
            content_text=row["content_text"] or "",
            embedding=row["embedding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> GraphEdge:
        return GraphEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(row["edge_type"]),
            properties=_parse_json(row["properties"]),
            weight=row["weight"],
            created_at=row["created_at"],
        )


# ── Utility ────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """安全解析 JSON 字符串."""
    if not raw or raw == "{}":
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
