"""
MemoryManager — 记忆系统顶层接口

对外暴露的核心 API:
  - remember():    从 LLM 交互中提取并存储记忆
  - recall():      根据查询检索相关记忆 (BM25 / 向量预留 / 混合预留)
  - inject():      将记忆注入 system prompt
  - forget():      删除指定记忆
  - reinforce():   强化记忆 (标记为有用)
  - maintenance(): 执行衰减 + 淘汰
  - list():        列出项目记忆
  - stats():       获取统计信息

集成方式 (3 步):
  1. manager = MemoryManager(db_path="./data/memories.db")
  2. LLM 调用后: await manager.remember(...)
  3. LLM 调用前: results = await manager.recall(...) → manager.inject_memories(...)
"""

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from .database import MemoryDatabase
from .lifecycle import LifecycleManager
from .models import (
    MemoryEntry, MemoryType, MemoryConfig,
    RetrieveMode, RecallResult, _utc_now,
)
from .tokenizer import tokenize_for_fts, tokenize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 去重: Jaccard 相似度
# ---------------------------------------------------------------------------

def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """
    计算两个文本的 token 级 Jaccard 相似度.

    用于判断新提取的记忆是否和已有记忆重复.
    """
    tokens_a = set(tokenize(text_a))
    tokens_b = set(tokenize(text_b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)

# ---------------------------------------------------------------------------
# type alias
# ---------------------------------------------------------------------------

ExtractFn = Callable[[str], Any]
"""LLM 提取函数签名: 接收 prompt, 返回 JSON 字符串."""


# ---------------------------------------------------------------------------
# 默认的记忆提取 Prompt (改进版: 输出 summary + original_text 双字段)
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """你是一个知识提取助手。分析以下 LLM 交互, 提取 2-3 条对后续设计有价值的记忆。

## 上下文
用户在做什么: {context}
LLM 调用类型: {call_type}
用户输入摘要: {user_input}

## LLM 输出 (截取前 2000 字符)
{llm_output}

## 用户反馈
{user_feedback}

## 要求
返回 JSON 数组, 每条记忆包含:
- memory_type: "preference" | "decision" | "rejection" | "convention" | "insight"
- summary: 核心 insight 摘要 (1 句话, 简洁明确, 用于检索匹配)
- original_text: 原始上下文详情 (2-3 句话, 保留完整细节)
- tags: 2-4 个关键词标签
- importance: 0.0~1.0 重要性 (重要设计决策=0.9, 一般偏好=0.5, 临时备注=0.2)

只返回有效 JSON 数组, 不要额外解释.

## 示例
```json
[
  {{
    "memory_type": "preference",
    "summary": "用户偏好使用组合模式而非继承来复用代码",
    "original_text": "在优化 Blog 系统类图时, 用户明确表示偏好组合模式, 认为继承链过深难以维护",
    "tags": ["设计模式", "组合优于继承", "类图"],
    "importance": 0.8
  }}
]
```"""


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------

class MemoryManager:
    """
    记忆系统管理器 — 顶层接口.

    Parameters:
        db_path:          SQLite 数据库文件路径
        config:           系统配置 (MemoryConfig), None 使用默认值
        embedding_service: 嵌入服务实例 (None = 仅 BM25, 后续接入后启用向量检索)

    Usage:
        mgr = MemoryManager(db_path="./memories.db")

        # 记录
        entries = await mgr.remember(
            project_id="blog_system",
            context="优化类图",
            llm_call_type="optimize",
            user_input="提高可扩展性",
            llm_output="...",
            extract_fn=my_chat_fn,
        )

        # 检索
        results = await mgr.recall("blog_system", "如何优化类图设计")

        # 注入
        prompt = mgr.inject_memories(system_prompt, results)

        # 强化 (记忆被实际使用后)
        mgr.reinforce(results, project_id="blog_system")

        # 定期维护 (衰减 + 淘汰)
        mgr.maintenance("blog_system")
    """

    __slots__ = ("db", "config", "lifecycle", "_embedding_service")

    def __init__(
        self,
        db_path: str = "./memories.db",
        config: Optional[MemoryConfig] = None,
        embedding_service = None,  # EmbeddingService | None (预留)
    ):
        self.config = config or MemoryConfig(db_path=db_path)
        self.db = MemoryDatabase(db_path)
        self.lifecycle = LifecycleManager(self.db, self.config)
        self._embedding_service = embedding_service

    # ==================================================================
    # Public API
    # ==================================================================

    # ── remember ──────────────────────────────────────────────────────

    async def remember(
        self,
        project_id: str,
        context: str,
        llm_call_type: str,
        user_input: str = "",
        llm_output: str = "",
        user_feedback: Optional[str] = None,
        extract_fn: Optional[ExtractFn] = None,
    ) -> List[MemoryEntry]:
        """
        LLM 调用后提取并存储记忆.

        Args:
            project_id:    项目标识
            context:       触发上下文描述
            llm_call_type: LLM 调用类型 (optimize | generate | pipeline_stage)
            user_input:    用户输入或原始 prompt (截断到 1000 字符)
            llm_output:    LLM 返回内容 (截断到 2000 字符)
            user_feedback: 用户反馈 (accepted | rejected | modified | None)
            extract_fn:    外部 LLM 调用函数, 用于自动提取记忆.
                           为 None 时跳过自动提取, 返回空列表.

        Returns:
            新创建的记忆条目列表
        """
        if extract_fn is None:
            logger.info(f"[MemoryManager] extract_fn is None, skipping auto-extract for {project_id}")
            return []

        # 1. 构建提取 prompt
        prompt = EXTRACT_PROMPT.format(
            context=context,
            call_type=llm_call_type,
            user_input=user_input[:1000],
            llm_output=llm_output[:2000],
            user_feedback=user_feedback or "未确认",
        )

        # 2. 调用外部 LLM 提取
        try:
            raw = await extract_fn(prompt)
            if asyncio.iscoroutine(raw):
                raw = await raw
        except Exception as exc:
            logger.error(f"[MemoryManager] extract_fn failed: {exc}")
            return []

        # 3. 解析 JSON
        items = self._parse_extract_result(raw)
        if not items:
            logger.info("[MemoryManager] No insights extracted from LLM response")
            return []

        # 4. 创建 MemoryEntry（带去重检查）并存储
        new_entries: List[MemoryEntry] = []
        dup_count = 0

        for item in items:
            try:
                metadata = {
                    "context": context,
                    "call_type": llm_call_type,
                    "extracted_at": _utc_now(),
                }
                if "metadata" in item and isinstance(item["metadata"], dict):
                    metadata.update(item["metadata"])

                entry = MemoryEntry(
                    project_id=project_id,
                    memory_type=MemoryType(item.get("memory_type", "insight")),
                    summary=item.get("summary", item.get("content", "")),
                    original_text=item.get("original_text", item.get("context", context)),
                    metadata=metadata,
                    tags=item.get("tags", []),
                    importance_score=float(item.get("importance", 0.5)),
                    user_feedback=user_feedback,
                    source=llm_call_type,
                )

                # ── 去重: FTS5 检索已有记忆, 计算 Jaccard 相似度 ──
                candidates = self.db.find_similar(project_id, entry.summary, top_k=3)
                best_sim = 0.0
                best_match: Optional[MemoryEntry] = None

                for rr in candidates:
                    sim = _jaccard_similarity(entry.summary, rr.entry.summary)
                    if sim > best_sim:
                        best_sim = sim
                        best_match = rr.entry

                if best_match and best_sim >= self.config.dedup_threshold:
                    # 更新已有记忆: 提升重要性, 更新原文, 合并 tags
                    best_match.importance_score = min(1.0, best_match.importance_score + 0.05)
                    best_match.original_text = entry.original_text
                    best_match.access_count += 1
                    best_match.last_accessed_at = _utc_now()
                    best_match.tags = list(set(best_match.tags + entry.tags))
                    best_match.user_feedback = user_feedback or best_match.user_feedback
                    self.db.update(best_match)
                    dup_count += 1
                    logger.debug(
                        f"[MemoryManager] Merged similar memory {best_match.id[:8]}... "
                        f"(sim={best_sim:.2f}, imp={best_match.importance_score:.2f})"
                    )
                else:
                    self.db.add(entry)
                    new_entries.append(entry)

            except (ValueError, KeyError) as exc:
                logger.warning(f"[MemoryManager] Skipping invalid memory item: {exc}")

        logger.info(
            f"[MemoryManager] Remembered {len(new_entries)} new + {dup_count} merged "
            f"insight(s) for project '{project_id}'"
        )
        return new_entries

    # ── recall ────────────────────────────────────────────────────────

    async def recall(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
        max_tokens: int = 800,
        mode: RetrieveMode = RetrieveMode.BM25,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> List[RecallResult]:
        """
        LLM 调用前检索相关记忆.

        Args:
            project_id:   项目标识
            query:        查询文本 (通常是用户需求描述)
            top_k:        返回的最大记忆数
            max_tokens:   总 token 预算上限 (1 token ≈ 2 chars for Chinese)
            mode:         检索模式 (当前仅 BM25 可用, vector/hybrid 预留)
            memory_types: 按类型过滤 (None = 所有类型)

        Returns:
            RecallResult 列表, 按相关性得分降序排列
        """
        if mode == RetrieveMode.VECTOR:
            logger.warning("[MemoryManager] Vector retrieval not yet implemented, falling back to BM25")
            mode = RetrieveMode.BM25
        if mode == RetrieveMode.HYBRID:
            logger.warning("[MemoryManager] Hybrid retrieval not yet implemented, falling back to BM25")
            mode = RetrieveMode.BM25

        # BM25 检索
        results: List[RecallResult] = []
        char_budget = max_tokens * 2  # 1 token ≈ 2 chars

        if memory_types and len(memory_types) == 1:
            # 单类型过滤
            results = self.db.search_bm25(
                project_id, query, top_k=top_k,
                memory_type=memory_types[0],
            )
        elif memory_types:
            # 多类型过滤: 分别检索后合并排序
            all_results: List[RecallResult] = []
            for mt in memory_types:
                partial = self.db.search_bm25(
                    project_id, query, top_k=top_k,
                    memory_type=mt,
                )
                all_results.extend(partial)
            all_results.sort(key=lambda r: r.score, reverse=True)
            results = all_results[:top_k]
        else:
            results = self.db.search_bm25(project_id, query, top_k=top_k)

        # Token 预算保护
        filtered: List[RecallResult] = []
        total_chars = 0
        for rr in results:
            total_chars += len(rr.entry.summary) + len(rr.entry.original_text)
            filtered.append(rr)
            if total_chars >= char_budget:
                break

        logger.info(
            f"[MemoryManager] Recalled {len(filtered)} memories for '{project_id}' "
            f"(query: {query[:50]}..., mode={mode.value})"
        )
        return filtered

    # ── inject ────────────────────────────────────────────────────────

    @staticmethod
    def inject_memories(
        system_prompt: str,
        recall_results: List[RecallResult],
        section_title: str = "## 项目历史记忆",
    ) -> str:
        """
        将检索到的记忆注入 system prompt.

        Args:
            system_prompt:  原始 system prompt
            recall_results: recall() 返回的检索结果
            section_title:  记忆章节的标题

        Returns:
            拼接后的 system prompt
        """
        if not recall_results:
            return system_prompt

        lines = [
            "",
            section_title,
            "以下是从过往交互中提取的设计上下文, 请在回答时参考:",
            "",
        ]
        for i, rr in enumerate(recall_results, 1):
            type_label = {
                MemoryType.PREFERENCE:  "偏好",
                MemoryType.DECISION:    "决策",
                MemoryType.REJECTION:   "拒绝",
                MemoryType.CONVENTION:  "规范",
                MemoryType.INSIGHT:     "洞察",
            }.get(rr.entry.memory_type, "其他")

            tags_str = f" [{', '.join(rr.entry.tags)}]" if rr.entry.tags else ""
            # 注入 summary (简洁) 而非 original_text (过长)
            lines.append(
                f"{i}. [{type_label}]{tags_str} {rr.entry.summary} "
                f"_(相关性: {rr.score:.2f})_"
            )

        memory_section = "\n".join(lines)
        return system_prompt.rstrip() + "\n" + memory_section

    # ── reinforce ─────────────────────────────────────────────────────

    def reinforce(
        self,
        results_or_ids,
        project_id: Optional[str] = None,
        delta: Optional[float] = None,
    ) -> int:
        """
        强化记忆 (被检索使用后调用).

        支持两种调用方式:
          - mgr.reinforce(recall_results, project_id="xxx")
          - mgr.reinforce(memory_id, project_id="xxx")

        Args:
            results_or_ids: RecallResult 列表, 或单个 memory_id 字符串
            project_id:    项目 ID (results_or_ids 为 RecallResult 列表时可省略)
            delta:         重要性增量 (默认使用 config.reinforce_delta)

        Returns:
            成功强化的数量
        """
        # 统一处理
        if isinstance(results_or_ids, str):
            # 单个 memory_id
            ok = self.lifecycle.reinforce(
                results_or_ids, project_id, delta=delta,
            )
            return 1 if ok else 0

        if isinstance(results_or_ids, list):
            ids: List[str] = []
            for item in results_or_ids:
                if isinstance(item, RecallResult):
                    ids.append(item.entry.id)
                    if project_id is None:
                        project_id = item.entry.project_id
                elif isinstance(item, str):
                    ids.append(item)
            if project_id is None:
                logger.warning("[MemoryManager] reinforce: project_id is required for id list")
                return 0
            return self.lifecycle.reinforce_batch(ids, project_id, delta=delta)

        return 0

    # ── forget ────────────────────────────────────────────────────────

    async def forget(self, project_id: str, memory_id: str) -> bool:
        """
        删除一条记忆.

        Returns:
            True 若删除成功.
        """
        ok = self.db.delete(project_id, memory_id)
        if ok:
            logger.info(f"[MemoryManager] Forgot memory {memory_id[:8]}... from '{project_id}'")
        return ok

    # ── list ──────────────────────────────────────────────────────────

    async def list_memories(
        self,
        project_id: str,
        memory_type: Optional[MemoryType] = None,
    ) -> List[MemoryEntry]:
        """
        列出项目的所有记忆.

        Args:
            project_id:  项目标识
            memory_type: 按类型过滤 (None = 所有)

        Returns:
            MemoryEntry 列表 (按创建时间降序)
        """
        return self.db.list_by_project(project_id, memory_type=memory_type)

    # ── stats ─────────────────────────────────────────────────────────

    async def stats(self, project_id: str) -> Dict[str, Any]:
        """获取项目记忆统计."""
        return self.db.stats(project_id)

    # ── maintenance ───────────────────────────────────────────────────

    def maintenance(self, project_id: str) -> Dict[str, int]:
        """
        执行一次完整维护: 衰减 + 淘汰.

        建议通过定时任务调用 (如每天一次).
        """
        return self.lifecycle.maintenance(project_id)

    # ── pin / unpin ───────────────────────────────────────────────────

    def pin(self, memory_id: str, project_id: str) -> bool:
        """固定记忆 (不参与淘汰)."""
        return self.lifecycle.pin(memory_id, project_id)

    def unpin(self, memory_id: str, project_id: str) -> bool:
        """取消固定."""
        return self.lifecycle.unpin(memory_id, project_id)

    # ── clear ─────────────────────────────────────────────────────────

    def clear_project(self, project_id: str) -> int:
        """清除项目所有记忆."""
        return self.db.clear_project(project_id)

    # ── close ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接."""
        self.db.close()

    # ==================================================================
    # Internal helpers
    # ==================================================================

    @staticmethod
    def _parse_extract_result(raw: str) -> List[Dict[str, Any]]:
        """
        解析 LLM 返回的 JSON 提取结果.

        支持:
          - 纯 JSON 数组: [{"memory_type": ...}, ...]
          - Markdown code block: ```json [...] ```
          - 额外文字包裹
        """
        if not raw or not raw.strip():
            return []

        # 尝试提取 ```json ``` 代码块
        m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", raw, re.IGNORECASE)
        if m:
            raw = m.group(1)

        # 尝试找到第一个 [ 和最后一个 ]
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError as exc:
            logger.warning(f"[MemoryManager] Failed to parse extract JSON: {exc}")
            logger.debug(f"Raw extract response: {raw[:500]}")

        return []
