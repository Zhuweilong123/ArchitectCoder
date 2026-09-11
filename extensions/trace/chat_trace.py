"""
ChatTrace — 会话级结构化 trace 层 (JSONL).

专为智能体 trace 追踪与问题复现设计。

核心特性:
  - JSONL 每行一个事件，程序化可解析、可重放
  - trace_id / span_id / parent_span_id 组成因果链
  - ts_ms（墙上毫秒）+ monotonic_ns（单调纳秒）支撑时序重建与延迟测量
  - 记录 LLM 原始往返（prompt/completion/model/tokens）、工具调用参数与完整返回
  - 记录环境快照（agent/prompt 版本、KG 状态），便于复现现场

文件命名: trace_{session_id}.jsonl。普通聊天位于 temp/chat_log/，评测 Trace
由调用方指定到 temp/evals/traces/。
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime

from . import format as trace_format
from .format import (
    EVT_AGENT_STEP,
    EVT_CONTEXT_COMPACTED,
    EVT_DONE,
    EVT_ERROR,
    EVT_KG_INJECT,
    EVT_LLM_REQUEST,
    EVT_LLM_RESPONSE,
    EVT_REVIEW_REQUEST,
    EVT_REVIEW_RESPONSE,
    EVT_SESSION_END,
    EVT_SESSION_START,
    EVT_TASK_SUMMARY,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    EVT_USER_MESSAGE,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    """墙上时钟毫秒（用于人类可读时间戳与跨会话对齐）。"""
    return int(time.time() * 1000)


def _now_ns() -> int:
    """单调时钟纳秒（用于精确延迟与严格时序，不受系统时间跳变影响）。"""
    return time.monotonic_ns()


def new_trace_id() -> str:
    """生成短 trace/span id。"""
    return uuid.uuid4().hex[:16]


def _split_system_prompt(messages: list) -> tuple[str, list]:
    """拆分 system prompt 与对话流。

    system 消息作为独立字段记录，messages 只保留 user/assistant/tool 对话流，
    避免同一份 system 内容在 trace 中重复出现。

    复现约定：`[{"role": "system", "content": system_prompt}] + messages`
    即重建当时的完整请求。无 system 消息时返回空串与原始列表。
    """
    if not messages:
        return "", []
    system_prompt = ""
    system_index = None
    for index, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content")
            system_prompt = content if isinstance(content, str) else ""
            system_index = index
            break
    # The first system message is the stable prompt.  Later system messages
    # are runtime constraints/checkpoints and must remain replayable in the
    # conversation stream (for example budget finalization instructions).
    stripped = [
        msg for index, msg in enumerate(messages)
        if index != system_index
    ]
    return system_prompt, stripped


def _prompt_structure(messages: list, system_prompt: str, conversation: list) -> dict:
    """Return non-content prompt shape metadata for trace inspection."""
    roles = [
        str(message.get("role") or "unknown")
        for message in messages
        if isinstance(message, dict)
    ]
    contents = [
        str(message.get("content") or "")
        for message in messages
        if isinstance(message, dict)
    ]
    return {
        "total_messages": len(messages),
        "stable_system_prompt": bool(system_prompt),
        "conversation_messages": len(conversation),
        "role_sequence": roles,
        "task_execution_summary_count": sum(
            content.startswith("## Task execution checkpoint")
            for content in contents
        ),
        "conversation_checkpoint_count": sum(
            content.startswith("## Conversation checkpoint")
            for content in contents
        ),
        "tool_call_message_count": sum(
            isinstance(message, dict)
            and message.get("role") == "assistant"
            and bool(message.get("tool_calls"))
            for message in messages
        ),
        "tool_result_message_count": sum(
            role == "tool" for role in roles
        ),
    }


def _json_safe(value):
    """Normalize trace payloads so every physical line is strict JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _event(
    session_id: str,
    event_type: str,
    *,
    trace_id: str = "",
    parent_span_id: str = "",
    **payload,
) -> dict:
    """构造统一结构的事件 dict（不含 env 快照，snapshot 单独处理）。"""
    return {
        "schema_version": 1,
        "session_id": session_id,
        "trace_id": trace_id or new_trace_id(),
        "span_id": new_trace_id(),
        "parent_span_id": parent_span_id or None,
        "event_type": event_type,
        "ts_ms": _now_ms(),
        "monotonic_ns": _now_ns(),
        **payload,
    }


class ChatTraceLogger:
    """JSONL trace 写入器 — 每连接一个文件，事件即时追加，线程安全。

    写入 temp/chat_log/trace_{session_id}.jsonl（机器回放 JSONL）。
    """

    def __init__(self, session_id: str = "", log_dir: str | None = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = ""
        self._trace_id = new_trace_id()
        self._lock = threading.Lock()
        self._path: str | None = None
        self._log_dir = log_dir
        self._closed = False
        self._n = 0

    def set_run_id(self, run_id: str) -> None:
        """Associate subsequent events with one durable harness Run."""
        self.run_id = run_id or ""

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def path(self) -> str:
        if self._path is None:
            log_dir = self._log_dir or _chat_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            self._path = os.path.join(log_dir, f"trace_{self.session_id}.jsonl")
        return self._path

    # ── 底层写入 ─────────────────────────────────────

    def _write(self, evt: dict) -> None:
        if self._closed:
            return
        try:
            line = json.dumps(
                _json_safe(evt), ensure_ascii=False, default=str,
                allow_nan=False,
            )
            # Keep the JSONL promise explicit: one strict JSON object per
            # physical line, including tool observations with newlines.
            json.loads(line)
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                self._n += 1
        except Exception:
            logger.exception("[Trace] Failed to append to %s", self.path)

    def event(self, event_type: str, **payload) -> dict:
        """通用事件写入（供外部扩展）。返回已写入的事件 dict。"""
        evt = _event(
            self.session_id, event_type,
            trace_id=self._trace_id,
            run_id=self.run_id,
            **payload,
        )
        self._write(evt)
        return evt

    # ── 生命周期 ─────────────────────────────────────

    def start(self, *, user_message: str = "",
              project_file: str = "", source_dir: str = "",
              test_dir: str = "", env_snapshot: dict | None = None) -> None:
        """会话开始事件 — 记录环境快照便于复现。"""
        payload = {
            "user_message": user_message,
            "project_file": project_file,
            "source_dir": source_dir,
            "test_dir": test_dir,
        }
        if env_snapshot:
            payload["env_snapshot"] = env_snapshot
        self.event(EVT_SESSION_START, **payload)

    def close(self) -> None:
        if self._closed:
            return
        try:
            # 先写结束事件，再标记 closed。旧实现先置位，导致 event() 内部
            # 被 _write() 直接短路，trace 永远没有 session_end。
            self.event(EVT_SESSION_END, total_events=self._n)
            logger.info("[Trace] Session trace → %s (%d events)", self.path, self._n)
        except Exception:
            logger.exception("[Trace] Failed to finalize %s", self.path)
        finally:
            self._closed = True

    # ── 事件记录方法 ─────────────────────────────────

    def user_message(self, message: str, project_file: str = "",
                     source_dir: str = "", test_dir: str = "") -> None:
        """记录用户消息及当时的工作区目录。

        source_dir / test_dir 供 live 回放（真实工具执行）重建 safe_path 守卫的
        workspace root；旧 trace 无此二字段时回放侧回退从 context 文本解析。
        """
        self.event(
            EVT_USER_MESSAGE,
            message=message,
            project_file=project_file,
            source_dir=source_dir,
            test_dir=test_dir,
        )

    def kg_inject(self, context: str, query: str = "") -> None:
        """记录注入给模型的知识图谱上下文（去隐私/去敏感后）。"""
        self.event(EVT_KG_INJECT, query=query, context_length=len(context))

    def context_compacted(
        self,
        *,
        summary: str,
        dropped_messages: int = 0,
        dropped_tokens: int = 0,
        reason: str = "",
        triggered_by: list[str] | None = None,
        tool_call_count: int = 0,
        token_budget_used: int = 0,
        context_target_tokens: int = 0,
    ) -> None:
        """Persist the checkpoint used to restore a compacted session."""
        self.event(
            EVT_CONTEXT_COMPACTED,
            summary=summary,
            dropped_messages=dropped_messages,
            dropped_tokens=dropped_tokens,
            reason=reason,
            triggered_by=triggered_by or [],
            tool_call_count=tool_call_count,
            token_budget_used=token_budget_used,
            context_target_tokens=context_target_tokens,
        )

    def task_summary(
        self,
        *,
        summary: str,
        status: str = "",
        tool_call_count: int = 0,
        turn: int | None = None,
    ) -> None:
        """Persist the bounded task checkpoint used by later chat turns."""
        payload = {
            "summary": str(summary or ""),
            "status": status,
            "tool_call_count": tool_call_count,
        }
        if turn is not None:
            payload["turn"] = turn
        self.event(
            EVT_TASK_SUMMARY,
            **payload,
        )

    def llm_request(self, *, provider: str, model: str, messages: list,
                    temperature: float | None, max_tokens: int | None,
                    tools: list | None = None, tool_choice: str | None = None,
                    response_format: dict | None = None, timeout: int | None = None,
                    request_context: dict | None = None,
                    span_id: str = "", span_path: str = "") -> str:
        """记录 LLM 请求（原始 prompt）。返回 span_id 供 response 关联。

        字段排序约定: 系统提示词置顶（system_prompt），tools 沉底，便于人工翻阅 trace。
        system_prompt 从 messages 中拆出独立记录，messages 只留对话流，避免重复。
        复现请求: `[{"role":"system","content":system_prompt}] + messages`。

        span_path 是子 Agent 调用栈路径（如 "UmlOptimizer/reflect"），
        由全局 hook 自动注入，用于区分 LLM 调用来源。
        """
        sid = span_id or new_trace_id()
        system_prompt, stripped = _split_system_prompt(messages)
        self._write({
            **_event(self.session_id, EVT_LLM_REQUEST,
                     trace_id=self._trace_id, span_id=sid, run_id=self.run_id),
            "system_prompt": system_prompt,
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tool_choice": tool_choice,
            "response_format": response_format,
            "timeout": timeout,
            "span_path": span_path,
            "request_context": request_context or {},
            "prompt_structure": _prompt_structure(messages, system_prompt, stripped),
            "messages": stripped,
            "tools": tools,
        })
        return sid

    def llm_response(self, *, span_id: str, content: str,
                     tool_calls: list | None = None,
                     usage: dict | None = None, error: str = "",
                     duration_ms: float = 0.0,
                     span_path: str = "") -> None:
        """记录 LLM 响应（完整 completion/tool_calls/usage）。

        span_path 由全局 hook 自动注入，与对应 llm_request 一致。
        """
        self._write({
            **_event(self.session_id, EVT_LLM_RESPONSE,
                     trace_id=self._trace_id, span_id=span_id,
                     parent_span_id=span_id, run_id=self.run_id),
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "error": error,
            "duration_ms": round(duration_ms, 1),
            "span_path": span_path,
        })

    def agent_step(self, *, step: int, thought: str = "", actions: list | None = None,
                   is_final: bool = False) -> None:
        """记录 ReAct 单步（不含工具详情，详情单独 tool_call/tool_result）。"""
        self.event(
            EVT_AGENT_STEP,
            step=step, thought=thought, actions=actions or [], is_final=is_final,
        )

    def tool_call(self, *, step: int, tool_name: str, arguments: dict,
                  parent_span_id: str = "", span_path: str = "") -> str:
        """记录工具调用。返回 span_id 供 tool_result 关联。"""
        sid = new_trace_id()
        self._write({
            **_event(self.session_id, EVT_TOOL_CALL,
                     trace_id=self._trace_id, span_id=sid,
                     parent_span_id=parent_span_id, run_id=self.run_id),
            "step": step,
            "tool_name": tool_name,
            "arguments": arguments,
            "span_path": span_path,
        })
        return sid

    def tool_result(self, *, span_id: str, tool_name: str, observation: str,
                    duration_ms: float = 0.0, error: str = "",
                    fed_truncated: bool = False, fed_length: int = 0,
                    evidence: dict | None = None, span_path: str = "") -> None:
        """记录工具返回（完整 observation，不截断）。

        fed_truncated / fed_length 标记该返回喂回模型前是否被截断，
        用于区分「模型实际看到的口径」与「工具完整返回的口径」。``evidence``
        是运行时从原文提取的有界结构化事实，供评估压缩质量使用。
        """
        self._write({
            **_event(self.session_id, EVT_TOOL_RESULT,
                     trace_id=self._trace_id, span_id=span_id,
                     parent_span_id=span_id, run_id=self.run_id),
            "tool_name": tool_name,
            "observation": observation,
            "fed_truncated": fed_truncated,
            "fed_length": fed_length,
            "evidence": evidence or {},
            "duration_ms": round(duration_ms, 1),
            "error": error,
            "span_path": span_path,
        })

    def review_request(self, *, review_id: int, review_type: str,
                       title: str, question: str, content: str = "") -> None:
        self.event(EVT_REVIEW_REQUEST, review_id=review_id,
                   review_type=review_type, title=title,
                   question=question, content=content)

    def review_response(self, *, review_id: int, response: str) -> None:
        self.event(EVT_REVIEW_RESPONSE, review_id=review_id, response=response)

    def done(self, *, answer: str, runtime: dict | None = None) -> None:
        self.event(EVT_DONE, answer=answer, runtime=runtime or {})

    def error(self, *, event_type: str, message: str) -> None:
        self.event(EVT_ERROR, source=event_type, message=message)


def _chat_log_dir() -> str:
    """Compatibility seam for callers that patch the writer's log directory."""
    return trace_format.chat_log_dir()


class JsonlTraceProvider:
    """Default trace provider backed by the existing JSONL sink."""

    def create(self, request):
        return ChatTraceLogger(
            session_id=request.session_id,
            log_dir=getattr(request, "trace_dir", "") or None,
        )

    def query(self):
        return JsonlTraceQuery()

    async def replay(
        self,
        session_id: str,
        *,
        mode: str = "mock",
        until_turn: int | None = None,
        tool_policy: str = "readonly",
    ) -> dict:
        from .replay import replay_agent_session

        return await replay_agent_session(
            session_id,
            mode=mode,
            until_turn=until_turn,
            tool_policy=tool_policy,
        )


class JsonlTraceQuery:
    """Read-side adapter kept beside the JSONL storage implementation."""

    def list_traces(self):
        from extensions.trace.trace_reader import list_traces
        return list_traces()

    def read_trace(self, session_id: str, trace_type: str | None = None):
        from extensions.trace.trace_reader import read_trace
        return read_trace(session_id, trace_type=trace_type)

    def summarize_trace(self, session_id: str):
        from extensions.trace.trace_reader import summarize_trace
        return summarize_trace(session_id)

    def reconstruct_history(self, session_id: str):
        from extensions.trace.trace_reader import reconstruct_history
        return reconstruct_history(session_id)


def create(*, settings=None, **kwargs):
    """Provider factory loaded through ``agent_trace_provider``."""
    return JsonlTraceProvider()

