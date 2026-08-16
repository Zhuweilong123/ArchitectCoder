"""
记忆生命周期管理 — 强化 / 衰减 / 淘汰

三个核心机制:
  1. 强化 (Reinforcement):
     - 被检索并注入 context 时, importance_score += delta
     - 被用户明确标记为重要时, 大幅提升
     - access_count++ 记录热度

  2. 衰减 (Decay):
     - 定期将非 pinned 记忆的 importance_score × decay_factor
     - 模拟"不访问则遗忘", 防止记忆堆积
     - 不低于 importance_min 硬下限

  3. 淘汰 (Pruning):
     - 当项目记忆数超过阈值时触发
     - LFU 变体: 优先淘汰低重要性 + 长期未访问
     - 保护: pinned / 高访问量记忆不参与
     - 分批淘汰: 每次最多 batch_ratio 比例

Usage:
    from .database import MemoryDatabase
    from .lifecycle import LifecycleManager

    lcm = LifecycleManager(db, config)
    lcm.reinforce(memory_id, project_id)
    lcm.decay(project_id)
    lcm.prune(project_id)
"""

import logging
from typing import Dict, List, Optional

from .database import MemoryDatabase
from .models import MemoryConfig

logger = logging.getLogger(__name__)


class LifecycleManager:
    """
    记忆生命周期管理器.

    与存储层解耦, 仅通过 MemoryDatabase 接口操作.
    """

    __slots__ = ("db", "config")

    def __init__(self, db: MemoryDatabase, config: MemoryConfig):
        self.db = db
        self.config = config

    # ── 强化 ──────────────────────────────────────────────────────────

    def reinforce(
        self,
        memory_id: str,
        project_id: str,
        delta: Optional[float] = None,
        record_access: bool = True,
    ) -> bool:
        """
        强化一条记忆.

        触发时机:
          - 检索后被注入 system prompt
          - 用户显式标记为重要

        Args:
            memory_id:    记忆 ID
            project_id:   项目 ID
            delta:        重要性增量 (默认使用 config.reinforce_delta)
            record_access: 是否记录访问 (access_count++ / last_accessed_at)

        Returns:
            True 若成功
        """
        delta = delta if delta is not None else self.config.reinforce_delta

        # 更新 importance_score
        ok = self.db.reinforce(memory_id, project_id, delta)

        # 记录访问
        if record_access:
            entry = self.db.get(project_id, memory_id)
            if entry is not None:
                # 通过 rowid 更新 access
                row = self.db.conn.execute(
                    "SELECT rowid FROM memories WHERE id = ? AND project_id = ?",
                    (memory_id, project_id),
                ).fetchone()
                if row:
                    self.db.update_access(row["rowid"])

        if ok:
            logger.debug(
                f"[Lifecycle] Reinforced memory {memory_id[:8]}... "
                f"(delta={delta:.2f}, access={record_access})"
            )
        return ok

    def reinforce_batch(
        self,
        memory_ids: List[str],
        project_id: str,
        delta: Optional[float] = None,
    ) -> int:
        """
        批量强化多条记忆.

        Returns:
            成功强化的数量.
        """
        count = 0
        for mid in memory_ids:
            if self.reinforce(mid, project_id, delta=delta):
                count += 1
        return count

    # ── 衰减 ──────────────────────────────────────────────────────────

    def decay(self, project_id: str) -> int:
        """
        对项目所有非 pinned 记忆施加一次衰减.

        importance_score = max(importance_min, importance_score * decay_factor)

        Returns:
            受影响的记忆数量.
        """
        affected = self.db.apply_decay(
            project_id,
            self.config.insight_decay_factor,
            self.config.decay_factor,
            self.config.importance_min,
        )
        if affected > 0:
            logger.info(
                f"[Lifecycle] Decayed {affected} memories in '{project_id}' "
                f"(factor={self.config.decay_factor})"
            )
        return affected

    # ── 淘汰 ──────────────────────────────────────────────────────────

    def prune(self, project_id: str) -> int:
        """
        淘汰低价值记忆.

        流程:
          1. 获取候选列表 (低重要性 + 非 pinned + 低访问量)
          2. 批量删除
          3. 日志记录

        Returns:
            淘汰的记忆数量.
        """
        candidates = self.db.get_prune_candidates(
            project_id=project_id,
            importance_threshold=self.config.importance_min * 2,  # 略高于最低阈值
            max_entries=self.config.max_entries_per_project,
            batch_ratio=self.config.prune_batch_ratio,
            pin_access_threshold=self.config.pin_access_threshold,
        )

        if not candidates:
            logger.debug(f"[Lifecycle] No prune candidates for '{project_id}'")
            return 0

        # 在删除前获取摘要 (用于日志)
        entries_map = self.db.get_memories_by_rowids(candidates)

        deleted = self.db.delete_by_rowids(candidates)

        for rowid in candidates:
            entry = entries_map.get(rowid)
            if entry:
                logger.info(
                    f"[Lifecycle] Pruned memory {entry.id[:8]}... "
                    f"type={entry.memory_type.value}, "
                    f"imp={entry.importance_score:.3f}, "
                    f"age={entry.age_days:.0f}d, "
                    f"accessed={entry.access_count}"
                )

        logger.info(
            f"[Lifecycle] Pruned {deleted} memories from '{project_id}' "
            f"(candidates={len(candidates)})"
        )
        return deleted

    # ── 完整维护周期 ──────────────────────────────────────────────────

    def maintenance(self, project_id: str) -> Dict[str, int]:
        """
        执行一次完整维护: 衰减 + 淘汰.

        Returns:
            {"decayed": N, "pruned": M}
        """
        decayed = self.decay(project_id)
        pruned = self.prune(project_id)
        return {"decayed": decayed, "pruned": pruned}

    # ── Pin 管理 ──────────────────────────────────────────────────────

    def pin(self, memory_id: str, project_id: str) -> bool:
        """固定一条记忆 (标记为受保护)."""
        cursor = self.db.conn.execute(
            "UPDATE memories SET is_pinned = 1 WHERE id = ? AND project_id = ?",
            (memory_id, project_id),
        )
        self.db.conn.commit()
        ok = cursor.rowcount > 0
        if ok:
            logger.info(f"[Lifecycle] Pinned memory {memory_id[:8]}...")
        return ok

    def unpin(self, memory_id: str, project_id: str) -> bool:
        """取消固定."""
        cursor = self.db.conn.execute(
            "UPDATE memories SET is_pinned = 0 WHERE id = ? AND project_id = ?",
            (memory_id, project_id),
        )
        self.db.conn.commit()
        ok = cursor.rowcount > 0
        if ok:
            logger.info(f"[Lifecycle] Unpinned memory {memory_id[:8]}...")
        return ok
