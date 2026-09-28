"""SQLite-backed adapter for the core Agent ``MemoryPort``."""

from __future__ import annotations

import json
import os
from typing import Any

from backend.config.project_storage import project_storage

from app.agent_base.core.memory import (
    MemoryArchiveRequest,
    MemoryArchiveResult,
    MemoryRecallRequest,
    MemoryRecallResult,
)

from .manager import MemoryManager


def _memory_db_path(settings, project_file: str = "", workspace_root: str = "") -> str:
    configured = str(getattr(settings, "agent_memory_db_path", "") or "").strip()
    if configured:
        return os.path.normpath(os.path.abspath(configured))
    storage = project_storage(project_file, workspace_root=workspace_root)
    return str(storage.memory_db) if storage else ""


def _format_tool_steps(tool_steps: tuple[dict[str, Any], ...]) -> str:
    steps: list[dict[str, Any]] = []
    for item in tool_steps or ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "?"))[:80]
        args = item.get("arguments", {})
        observation = str(item.get("observation", ""))
        args_text = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        step = {"tool": name, "arguments": args_text, "result": observation}

        # Bound each serialized step, including JSON escaping and field names.
        serialized = json.dumps(step, ensure_ascii=False)
        while len(serialized) > 500:
            field = max(("arguments", "result", "tool"), key=lambda key: len(step[key]))
            excess = len(serialized) - 500
            step[field] = step[field][:max(0, len(step[field]) - max(1, excess))]
            serialized = json.dumps(step, ensure_ascii=False)

        steps.append(step)
    return json.dumps(steps, ensure_ascii=False)


class SQLiteMemoryProvider:
    """Keep the existing MemoryManager implementation behind the core port."""

    def __init__(self, *, llm, settings, **kwargs):
        self.llm = llm
        self.settings = settings
        self.db_path = _memory_db_path(
            settings, str(kwargs.get("project_file") or ""),
            str(kwargs.get("workspace_root") or ""),
        )
        self.recall_top_k = max(1, int(getattr(settings, "agent_memory_recall_top_k", 3)))
        self.recall_max_tokens = max(1, int(getattr(settings, "agent_memory_recall_max_tokens", 500)))

    def _manager(self) -> MemoryManager:
        if not self.db_path:
            raise ValueError("project path is required for local memory")
        return MemoryManager(db_path=self.db_path)

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        if not self.db_path:
            return MemoryRecallResult()
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
        if not self.db_path:
            return MemoryArchiveResult(metadata={"provider": "sqlite", "skipped": "no_project"})
        if self.llm is None:
            return MemoryArchiveResult(metadata={"provider": "sqlite", "skipped": "no_llm"})
        manager = self._manager()
        try:
            tool_execution_summary = _format_tool_steps(request.tool_steps)
            conversation_history = json.dumps([
                {"role": item["role"], "content": item.get("content", "")}
                for item in request.conversation_history
                if item.get("role") in {"user", "assistant"}
            ], ensure_ascii=False)

            async def extract(prompt: str) -> str:
                # Keep background extraction visible in trace without making
                # it part of the foreground Agent turn.
                from app.trace.tracing import trace_span
                with trace_span("MemoryArchive"):
                    return await self.llm.ainvoke(
                        [{"role": "user", "content": prompt}],
                        max_tokens=None,
                    )

            entries = await manager.remember(
                project_id=request.project_id,
                context=f"对话 Agent 任务: {request.user_message[:100]}",
                llm_call_type="agent_task",
                user_input=request.user_message,
                tool_execution_summary=tool_execution_summary,
                final_answer=request.final_answer,
                conversation_history=conversation_history,
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
        if not self.db_path or not memory_ids or not project_id:
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
