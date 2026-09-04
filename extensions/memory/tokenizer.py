"""
中英文混合分词器 (jieba 优先, bigram 兜底)

策略:
  - jieba 可用时: 使用 jieba.cut_for_search (搜索引擎模式, 召回优先)
  - jieba 不可用时: 回退到字符级 bigram + 英文空格分词
  - FTS5 输出: 空格连接的 token 串, 供 FTS5 默认 tokenizer 使用
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# jieba 检测
# ---------------------------------------------------------------------------

_JIEBA_AVAILABLE = False
_jieba = None

try:
    import jieba
    _JIEBA_AVAILABLE = True
    _jieba = jieba
    # 首次使用时设置静默日志
    jieba.setLogLevel(logging.WARNING)
    logger.info("[tokenizer] jieba 分词已启用")
except ImportError:
    logger.info("[tokenizer] jieba 未安装, 使用 bigram 兜底分词")


# ---------------------------------------------------------------------------
# Bigram 兜底实现
# ---------------------------------------------------------------------------

CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
]

_CHINESE_CHAR_PATTERN = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def _is_chinese(ch: str) -> bool:
    return bool(_CHINESE_CHAR_PATTERN.match(ch))


def _tokenize_chinese_bigram(text: str) -> List[str]:
    """字符级 bigram 分词 (jieba 不可用时的兜底)."""
    tokens: List[str] = []
    chars = [ch for ch in text if _is_chinese(ch)]
    n = len(chars)
    if n == 0:
        return tokens
    for i in range(n - 1):
        tokens.append(chars[i] + chars[i + 1])
    tokens.extend(chars)  # unigram 兜底
    return tokens


def _tokenize_english(text: str) -> List[str]:
    """英文空格 + 标点分割分词."""
    words = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return [w for w in words if len(w) >= 2 and not w.isdigit()]


def _tokenize_bigram(text: str) -> List[str]:
    """bigram 混合分词 (中英文分别处理)."""
    if not text or not text.strip():
        return []

    tokens: List[str] = []
    segments = _split_segments(text)
    for seg_text, is_cj in segments:
        if is_cj:
            tokens.extend(_tokenize_chinese_bigram(seg_text))
        else:
            tokens.extend(_tokenize_english(seg_text))
    return _dedupe(tokens)


def _split_segments(text: str) -> List[tuple]:
    """将文本切分为 [("中文段", True), ("英文段", False), ...]."""
    segments: List[tuple] = []
    current_chars: List[str] = []
    current_is_chinese: bool | None = None

    for ch in text:
        is_cj = _is_chinese(ch)
        if current_is_chinese is None:
            current_is_chinese = is_cj
        if is_cj == current_is_chinese:
            current_chars.append(ch)
        else:
            segments.append(("".join(current_chars), current_is_chinese))
            current_chars = [ch]
            current_is_chinese = is_cj
    if current_chars:
        segments.append(("".join(current_chars), current_is_chinese))
    return segments


def _dedupe(tokens: List[str]) -> List[str]:
    """去重保持顺序."""
    seen: set = set()
    result: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tokenize(text: str) -> List[str]:
    """
    通用分词 (去重, 用于展示/分析).

    Args:
        text: 输入文本 (中英文混合)

    Returns:
        去重后的 token 列表

    Example:
        >>> tokenize("用户偏好使用组合模式")
        ['用户', '偏好', '使用', '组合', '模式']   # jieba
        ['用户', '户偏', '偏好', ...]              # bigram 兜底
    """
    if not text or not text.strip():
        return []

    if _JIEBA_AVAILABLE:
        tokens = list(_jieba.cut_for_search(text))
        # 过滤纯空格和单字符英文
        tokens = [t.strip() for t in tokens if t.strip() and len(t.strip()) >= 1]
        return _dedupe(tokens)
    else:
        return _tokenize_bigram(text)


def tokenize_for_index(text: str) -> List[str]:
    """
    用于索引的分词 (保留重复, 用于 TF 计算).

    Args:
        text: 输入文本

    Returns:
        保留频率信息的 token 列表
    """
    if not text or not text.strip():
        return []

    if _JIEBA_AVAILABLE:
        tokens = list(_jieba.cut_for_search(text))
        return [t.strip() for t in tokens if t.strip()]
    else:
        # bigram fallback (no dedupe)
        segments = _split_segments(text)
        tokens: List[str] = []
        for seg_text, is_cj in segments:
            if is_cj:
                tokens.extend(_tokenize_chinese_bigram(seg_text))
            else:
                tokens.extend(_tokenize_english(seg_text))
        return tokens


def tokenize_for_fts(text: str) -> str:
    """
    用于 FTS5 的查询/索引文本 —— 空格连接的 token 串.

    FTS5 默认 tokenizer 按空格和标点分割, 我们预分好词后
    用空格连接, 每个 jieba/bigram token 就成为 FTS5 的一个 term.

    Args:
        text: 输入文本

    Returns:
        空格连接的 token 串

    Example:
        >>> tokenize_for_fts("用户偏好组合模式")
        "用户 偏好 组合 模式"     # jieba
        "用户 户偏 偏好 ..."      # bigram
    """
    tokens = tokenize_for_index(text)
    return " ".join(tokens)


def is_jieba_available() -> bool:
    """检测 jieba 是否可用."""
    return _JIEBA_AVAILABLE
