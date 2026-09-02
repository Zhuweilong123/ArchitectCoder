"""
Memory System — 基于 SQLite + FTS5 + jieba 的 Agent 记忆系统

核心组件:
  - MemoryManager:   顶层 API (remember / recall / inject / forget / reinforce)
  - MemoryDatabase:  SQLite + FTS5 存储层
  - LifecycleManager: 记忆生命周期 (强化 / 衰减 / 淘汰)
  - MemoryEntry:     记忆数据模型
  - MemoryConfig:    全局配置
  - tokenize / tokenize_for_fts: jieba 分词工具
  - EmbeddingService: 嵌入服务协议 (预留)

检索模式:
  - BM25 (FTS5 + jieba):  当前可用
  - Vector (Embedding):   接口预留, 后续接入
  - Hybrid (RRF 融合):    接口预留, 后续接入

快速使用:
    from memory_system import MemoryManager, MemoryConfig

    manager = MemoryManager(db_path="./memories.db")

    # LLM 调用后记录
    entries = await manager.remember(
        project_id="blog_app",
        context="优化类图设计",
        llm_call_type="optimize",
        user_input="提高系统可扩展性",
        llm_output=llm_response,
        extract_fn=my_chat_fn,
    )

    # LLM 调用前检索
    results = await manager.recall("blog_app", "如何优化类图")

    # 注入 system prompt
    prompt = manager.inject_memories(system_prompt, results)

    # 强化 (记忆被使用后)
    manager.reinforce(results)

    # 定期维护
    manager.maintenance("blog_app")
"""

from .manager import MemoryManager
from .models import (
    MemoryEntry, MemoryType, MemoryConfig,
    RecallResult, RetrieveMode,
)
from .policy import MemoryRecallPolicy, MemoryWriteDecision, MemoryWritePolicy
from .policy import MemoryWriteDecision, MemoryWritePolicy
from .database import MemoryDatabase
from .lifecycle import LifecycleManager
from .tokenizer import tokenize, tokenize_for_index, tokenize_for_fts, is_jieba_available
from .embedding import EmbeddingService, cosine_similarity, normalize_vector

__all__ = [
    # Core
    "MemoryManager",
    "MemoryDatabase",
    "LifecycleManager",
    # Models
    "MemoryEntry",
    "MemoryType",
    "MemoryConfig",
    "RecallResult",
    "RetrieveMode",
    # Tokenizer
    "tokenize",
    "tokenize_for_index",
    "tokenize_for_fts",
    "is_jieba_available",
    # Embedding (reserved)
    "EmbeddingService",
    "cosine_similarity",
    "normalize_vector",
]

__version__ = "2.0.0"
