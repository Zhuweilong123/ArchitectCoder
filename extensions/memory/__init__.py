"""SQLite memory extension.

The implementation lives entirely in this package.  The old
``memory_system`` package is only a compatibility facade.
"""

from .database import MemoryDatabase
from .embedding import EmbeddingService, cosine_similarity, normalize_vector
from .lifecycle import LifecycleManager
from .manager import MemoryManager
from .models import MemoryConfig, MemoryEntry, MemoryType, RecallResult, RetrieveMode
from .policy import MemoryRecallPolicy, MemoryWriteDecision, MemoryWritePolicy
from .provider import create
from .tokenizer import is_jieba_available, tokenize, tokenize_for_fts, tokenize_for_index

__all__ = [
    "create", "MemoryManager", "MemoryDatabase", "LifecycleManager",
    "MemoryEntry", "MemoryType", "MemoryConfig", "RecallResult", "RetrieveMode",
    "MemoryRecallPolicy", "MemoryWriteDecision", "MemoryWritePolicy",
    "tokenize", "tokenize_for_index", "tokenize_for_fts", "is_jieba_available",
    "EmbeddingService", "cosine_similarity", "normalize_vector",
]
