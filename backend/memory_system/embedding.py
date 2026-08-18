"""
嵌入服务接口 (预留)

当前仅定义抽象接口, 后续可实现:
  - LocalEmbedding: 本地模型 (sentence-transformers / text2vec 等)
  - RemoteEmbedding: 远程 API (DeepSeek / OpenAI embedding 等)

使用方式:
    # 后续接入后:
    emb_service = LocalEmbedding(model_name="bge-small-zh-v1.5")
    manager = MemoryManager(embedding_service=emb_service)

    # 当前 (纯 BM25 模式):
    manager = MemoryManager(embedding_service=None)
"""

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingService(Protocol):
    """
    嵌入服务协议.

    实现类只需提供 encode() 方法和 dimension 属性.
    """

    def encode(self, text: str) -> List[float]:
        """
        将文本编码为浮点向量.

        Args:
            text: 输入文本

        Returns:
            浮点向量 (长度 = self.dimension)
        """
        ...

    @property
    def dimension(self) -> int:
        """向量维度."""
        ...


# ---------------------------------------------------------------------------
# 向量工具函数 (后续混合检索时使用)
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """
    计算两个归一化向量的余弦相似度.

    若向量已归一化 (L2 norm = 1), 等同于点积.

    Args:
        vec_a, vec_b: 等长浮点向量

    Returns:
        余弦相似度 [-1, 1], 越接近 1 越相似
    """
    if len(vec_a) != len(vec_b):
        raise ValueError(f"向量维度不匹配: {len(vec_a)} vs {len(vec_b)}")

    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def normalize_vector(vec: List[float]) -> List[float]:
    """L2 归一化."""
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]
