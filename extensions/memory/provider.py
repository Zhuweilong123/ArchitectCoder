"""SQLite-backed adapter for the core Agent ``MemoryPort``."""

from __future__ import annotations

import json
import os
from typing import Any

from app.agent_base.core.memory import (
    MemoryArchiveRequest,
    MemoryArchiveResult,
    MemoryRecallRequest,
    MemoryRecallResult,
)

from .manager import MemoryManager


def _memory_db_path(settings) -> str:
    configured = str(getattr(settings, "agent_memory_db_path", "") or "").strip()
    if configured:
        return os.path.normpath(os.path.abspath(configured))
    uml_dir = str(getattr(settings, "uml_dir", "../temp/uml_files") or "../temp/uml_files")
    return os.path.normpath(os.path.abspath(
        os.path.join(os.path.dirname(uml_dir), "data", "memories.db"),
    ))


def _format_tool_steps(tool_steps: tuple[dict[str, Any], ...], max_steps: int = 8) -> str:
    lines: list[str] = []
    for item in (tool_steps or ())[:max_steps]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "?"))
        args = item.get("arguments", {})
        observation = str(item.get("observation", ""))[:300]
        args_text = json.dumps(args, ensure_ascii=False)[:150] if isinstance(args, dict) else str(args)[:150]
        lines.append(f"[{name}] 参数:{args_text}\n返回:{observation}")
    return "\n".join(lines)


class SQLiteMemoryProvider:
    """Keep the existing MemoryManager implementation behind the core port."""

    def __init__(self, *, llm, settings, **kwargs):
        self.llm = llm
        self.settings = settings
        self.db_path = _memory_db_path(settings)
        self.recall_top_k = max(1, int(getattr(settings, "agent_memory_recall_top_k", 3)))
        self.recall_max_tokens = max(1, int(getattr(settings, "agent_memory_recall_max_tokens", 500)))
        self.archive_max_tokens = max(1, int(getattr(settings, "agent_memory_archive_max_tokens", 3000)))

    def _manager(self) -> MemoryManager:
        return MemoryManager(db_path=self.db_path)

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        manager = self._manager()
        try:
            results = await manager.recall(
                request.project_id,
                request.query,
                top_k=request.top_k or self.recall_top_k,
                max_tokens=request.max_tokens or self.recall_max_tokens,
            )
            context = manager.inject_memories("", results).strip()
            return MemoryRecallResult(
                context_block=context,
                memory_ids=tuple(result.entry.id for result in results),
                token_count=max(0, len(context) // 2),
                metadata={"provider": "sqlite", "count": len(results)},
            )
        finally:
            manager.close()

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        if self.llm is None:
            return MemoryArchiveResult(metadata={"provider": "sqlite", "skipped": "no_llm"})
        manager = self._manager()
        try:
            steps = _format_tool_steps(request.tool_steps)
            combined = f"## 工具执行过程\n{steps}\n\n## 最终结论\n{request.final_answer}"

            async def extract(prompt: str) -> str:
                # Keep background extraction visible in trace without making
                # it part of the foreground Agent turn.
                from app.trace.tracing import trace_span
                with trace_span("MemoryArchive"):
                    return await self.llm.ainvoke(
                        [{"role": "user", "content": prompt}],
                        max_tokens=self.archive_max_tokens,
                    )

            entries = await manager.remember(
                project_id=request.project_id,
                context=f"对话 Agent 任务: {request.user_message[:100]}",
                llm_call_type="agent_task",
                user_input=request.user_message,
                llm_output=combined[:2000],
                extract_fn=extract,
                source_run_id=request.run_id,
                source_trace_id=request.trace_id,
            )
            return MemoryArchiveResult(
                stored_count=len(entries),
                metadata={"provider": "sqlite"},
            )
        finally:
            manager.close()

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        if not memory_ids or not project_id:
            return
        manager = self._manager()
        try:
            manager.reinforce(list(memory_ids), project_id=project_id)
        finally:
            manager.close()

    def close(self) -> None:
        # Managers are scoped to individual operations; retained for the port's
        # optional lifecycle hook.
        return None


def create(*, llm, settings, **kwargs):
    return SQLiteMemoryProvider(llm=llm, settings=settings, **kwargs)
