"""Compatibility exports; shared.tokenization owns the implementation."""
from shared.tokenization import (
    is_jieba_available, tokenize, tokenize_for_fts, tokenize_for_index,
)

__all__ = ["is_jieba_available", "tokenize", "tokenize_for_fts", "tokenize_for_index"]
