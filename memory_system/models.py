"""
数据模型 — 支持 SQLite 存储、BM25/向量混合检索、记忆生命周期管理

记忆结构:
  - summary:       摘要化总结 (用于检索与上下文注入)
  - original_text: 原始文本 (回溯细节)
  - metadata:      JSON 元数据 (时间、来源、类型等)
  - embedding:     BLOB 向量 (预留, 后续接入)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MemoryType(str, Enum):
    """记忆分类 (强类型约束)."""
    PREFERENCE  = "preference"   # 用户偏好: "用户喜欢用组合而非继承"
    DECISION    = "decision"     # 设计决策: "UserService 使用了策略模式"
    REJECTION   = "rejection"    # 被拒绝的建议: "不要加 Observer 模式"
    CONVENTION  = "convention"   # 代码/设计规范: "项目统一使用 MVC 分层"
    INSIGHT     = "insight"      # LLM 总结的通用 insight


class RetrieveMode(str, Enum):
    """检索模式."""
    BM25   = "bm25"
    VECTOR = "vector"    # 预留
    HYBRID = "hybrid"    # 预留, RRF 融合


# ---------------------------------------------------------------------------
# 时间工具 (避免 datetime.now() 在各处重复)
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    """
    一条记忆记录.

    Attributes:
        id:               唯一标识 (UUID hex)
        project_id:       所属项目标识
        memory_type:      记忆分类 (强类型枚举)
        summary:          摘要化总结, 用于 BM25 检索和上下文注入
        original_text:    原始文本, 可回溯完整细节
        metadata:         JSON 元数据 (时间、来源、上下文、自定义字段)
        embedding:        向量 BLOB (预留, 后续接入 embedding 服务后启用)
        embedding_model:  向量化模型名称/版本 (便于迁移)
        importance_score: 重要性/强度 (0.0~1.0), 支持强化与衰减
        access_count:     被检索使用的次数
        last_accessed_at: 最后访问时间 (ISO timestamp)
        created_at:       创建时间 (ISO timestamp)
        tags:             扁平标签列表 (便于过滤)
        source:           来源标记 (llm_call_type: optimize|generate|pipeline_stage)
        user_feedback:    用户反馈 (accepted|rejected|modified|None)
        is_pinned:        是否固定 (受保护, 不参与淘汰)
    """
    project_id: str
    memory_type: MemoryType
    summary: str
    original_text: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[bytes] = None
    embedding_model: str = ""
    importance_score: float = 0.5
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    tags: List[str] = field(default_factory=list)
    source: str = ""
    user_feedback: Optional[str] = None
    is_pinned: bool = False

    # ── computed properties ─────────────────────────────────────────

    @property
    def age_days(self) -> float:
        """记忆年龄 (天)."""
        try:
            ts = datetime.fromisoformat(self.created_at)
            delta = _utc_now_dt() - ts
            return max(delta.total_seconds() / 86400.0, 0.0)
        except (ValueError, TypeError):
            return 365.0

    @property
    def days_since_access(self) -> float:
        """距上次访问的天数 (从未访问返回大值)."""
        if not self.last_accessed_at:
            return max(self.age_days, 0.0)
        try:
            ts = datetime.fromisoformat(self.last_accessed_at)
            delta = _utc_now_dt() - ts
            return max(delta.total_seconds() / 86400.0, 0.0)
        except (ValueError, TypeError):
            return 365.0

    # ── serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["memory_type"] = self.memory_type.value
        # embedding BLOB 不序列化到 JSON
        d.pop("embedding", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=data.get("id", uuid4().hex),
            project_id=data["project_id"],
            memory_type=MemoryType(data.get("memory_type", "insight")),
            summary=data.get("summary", data.get("content", "")),
            original_text=data.get("original_text", data.get("context", "")),
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding"),
            embedding_model=data.get("embedding_model", ""),
            importance_score=float(data.get("importance_score", data.get("importance", 0.5))),
            access_count=int(data.get("access_count", 0)),
            last_accessed_at=data.get("last_accessed_at"),
            created_at=data.get("created_at", _utc_now()),
            tags=data.get("tags", []),
            source=data.get("source", ""),
            user_feedback=data.get("user_feedback"),
            is_pinned=bool(data.get("is_pinned", False)),
        )

    def __repr__(self) -> str:
        return (
            f"MemoryEntry(id={self.id[:8]}..., type={self.memory_type.value}, "
            f"project={self.project_id}, imp={self.importance_score:.2f}, "
            f"accessed={self.access_count})"
        )


@dataclass
class RecallResult:
    """
    检索结果 —— 记忆条目 + 相关性得分.

    Attributes:
        entry:    记忆条目
        score:    BM25 相关性得分 (越高越相关)
        source:   检索来源 ("bm25" | "vector" | "hybrid")
    """
    entry: MemoryEntry
    score: float
    source: str = "bm25"

    def __repr__(self) -> str:
        return f"RecallResult(score={self.score:.4f}, src={self.source}, entry={self.entry!r})"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class MemoryConfig:
    """
    记忆系统全局配置.

    所有参数可单独调优, 通过 MemoryManager(config=...) 传入.
    """

    # ── 存储 ──
    db_path: str = "./data/memories.db"
    max_entries_per_project: int = 100

    # ── 检索 ──
    enable_bm25: bool = True
    enable_vector: bool = False     # 预留
    enable_hybrid: bool = False     # 预留 (同时开启 bm25 + vector 后可用)
    bm25_top_k: int = 10
    vector_top_k: int = 10          # 预留
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    rrf_k: int = 60                 # RRF 常数 (预留)

    # ── 去重 ──
    dedup_threshold: float = 0.55  # summary token Jaccard 相似度阈值 (超过则更新而非新建)

    # ── 生命周期 ──
    reinforce_delta: float = 0.1
    decay_factor: float = 0.98      # 每次衰减 multiplier
    decay_interval_hours: int = 24
    importance_min: float = 0.1     # 低于此值可被淘汰
    prune_batch_ratio: float = 0.1  # 每次最多淘汰的比例
    pin_access_threshold: int = 5   # access_count 达到此值自动 pin
