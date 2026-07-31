"""
ChatTrace — 会话级结构化 trace 层 (JSONL).

与 markdown 日志（ChatSessionLogger）并行，专为智能体 trace 追踪与问题复现设计。

核心特性:
  - JSONL 每行一个事件，程序化可解析、可重放
  - trace_id / span_id / parent_span_id 组成因果链
  - ts_ms（墙上毫秒）+ monotonic_ns（单调纳秒）支撑时序重建与延迟测量
  - 记录 LLM 原始往返（prompt/completion/model/tokens）、工具调用参数与完整返回
  - 记录环境快照（agent/prompt 版本、KG 状态），便于复现现场

文件命名: trace_{session_id}.jsonl，与 chat_{session_id}.md 同目录（temp/chat_log/）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime

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


# ── 事件类型常量 ─────────────────────────────────────

EVT_SESSION_START = "session_start"
EVT_SESSION_END = "session_end"
EVT_USER_MESSAGE = "user_message"
EVT_LLM_REQUEST = "llm_request"
EVT_LLM_RESPONSE = "llm_response"
EVT_AGENT_STEP = "agent_step"
EVT_TOOL_CALL = "tool_call"
EVT_TOOL_RESULT = "tool_result"
EVT_REVIEW_REQUEST = "review_request"
EVT_REVIEW_RESPONSE = "review_response"
EVT_DONE = "done"
EVT_ERROR = "error"
EVT_INTENT = "intent"
EVT_KG_INJECT = "kg_inject"


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

    与 ChatSessionLogger 共享 temp/chat_log/ 目录：
      chat_{session_id}.md    — 人读 markdown
      trace_{session_id}.jsonl — 机器回放 JSONL
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._trace_id = new_trace_id()
        self._lock = threading.Lock()
        self._path: str | None = None
        self._closed = False
        self._n = 0

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def path(self) -> str:
        if self._path is None:
            log_dir = _chat_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            self._path = os.path.join(log_dir, f"trace_{self.session_id}.jsonl")
        return self._path

    # ── 底层写入 ─────────────────────────────────────

    def _write(self, evt: dict) -> None:
        if self._closed:
            return
        try:
            line = json.dumps(evt, ensure_ascii=False, default=str)
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
            **payload,
        )
        self._write(evt)
        return evt

    # ── 生命周期 ─────────────────────────────────────

    def start(self, *, mode: str = "", user_message: str = "",
              project_file: str = "", source_dir: str = "",
              test_dir: str = "", env_snapshot: dict | None = None) -> None:
        """会话开始事件 — 记录环境快照便于复现。"""
        payload = {
            "mode": mode,
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
        self._closed = True
        try:
            self.event(EVT_SESSION_END, total_events=self._n)
            logger.info("[Trace] Session trace → %s (%d events)", self.path, self._n)
        except Exception:
            logger.exception("[Trace] Failed to finalize %s", self.path)

    # ── 事件记录方法 ─────────────────────────────────

    def user_message(self, message: str, project_file: str = "") -> None:
        self.event(EVT_USER_MESSAGE, message=message, project_file=project_file)

    def intent(self, intent: str, raw: str = "") -> None:
        self.event(EVT_INTENT, intent=intent, raw=raw)

    def kg_inject(self, context: str, query: str = "") -> None:
        """记录注入给模型的知识图谱上下文（去隐私/去敏感后）。"""
        self.event(EVT_KG_INJECT, query=query, context_length=len(context))

    def llm_request(self, *, provider: str, model: str, messages: list,
                    temperature: float | None, max_tokens: int | None,
                    tools: list | None = None, tool_choice: str | None = None,
                    span_id: str = "") -> str:
        """记录 LLM 请求（原始 prompt）。返回 span_id 供 response 关联。"""
        sid = span_id or new_trace_id()
        self._write({
            **_event(self.session_id, EVT_LLM_REQUEST,
                     trace_id=self._trace_id, span_id=sid),
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
            "messages": messages,
        })
        return sid

    def llm_response(self, *, span_id: str, content: str,
                     tool_calls: list | None = None,
                     usage: dict | None = None, error: str = "",
                     duration_ms: float = 0.0) -> None:
        """记录 LLM 响应（完整 completion/tool_calls/usage）。"""
        self._write({
            **_event(self.session_id, EVT_LLM_RESPONSE,
                     trace_id=self._trace_id, span_id=span_id,
                     parent_span_id=span_id),
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "error": error,
            "duration_ms": round(duration_ms, 1),
        })

    def agent_step(self, *, step: int, thought: str = "", actions: list | None = None,
                   is_final: bool = False) -> None:
        """记录 ReAct 单步（不含工具详情，详情单独 tool_call/tool_result）。"""
        self.event(
            EVT_AGENT_STEP,
            step=step, thought=thought, actions=actions or [], is_final=is_final,
        )

    def tool_call(self, *, step: int, tool_name: str, arguments: dict,
                  parent_span_id: str = "") -> str:
        """记录工具调用。返回 span_id 供 tool_result 关联。"""
        sid = new_trace_id()
        self._write({
            **_event(self.session_id, EVT_TOOL_CALL,
                     trace_id=self._trace_id, span_id=sid,
                     parent_span_id=parent_span_id),
            "step": step,
            "tool_name": tool_name,
            "arguments": arguments,
        })
        return sid

    def tool_result(self, *, span_id: str, tool_name: str, observation: str,
                    duration_ms: float = 0.0, error: str = "") -> None:
        """记录工具返回（完整 observation，不截断）。"""
        self._write({
            **_event(self.session_id, EVT_TOOL_RESULT,
                     trace_id=self._trace_id, span_id=span_id,
                     parent_span_id=span_id),
            "tool_name": tool_name,
            "observation": observation,
            "duration_ms": round(duration_ms, 1),
            "error": error,
        })

    def review_request(self, *, review_id: int, review_type: str,
                       title: str, question: str, content: str = "") -> None:
        self.event(EVT_REVIEW_REQUEST, review_id=review_id,
                   review_type=review_type, title=title,
                   question=question, content=content)

    def review_response(self, *, review_id: int, response: str) -> None:
        self.event(EVT_REVIEW_RESPONSE, review_id=review_id, response=response)

    def done(self, *, mode: str, answer: str) -> None:
        self.event(EVT_DONE, mode=mode, answer=answer)

    def error(self, *, event_type: str, message: str) -> None:
        self.event(EVT_ERROR, source=event_type, message=message)


def _chat_log_dir() -> str:
    """计算 chat_log 目录（与 pipeline_log 同级）。"""
    from app.core.config import get_settings
    settings = get_settings()
    return os.path.normpath(os.path.abspath(
        os.path.join(os.path.dirname(settings.uml_dir), "chat_log"),
    ))


# ── 全局 LLM trace 钩子 ──────────────────────────────
# BaseAgentsLLM 不直接依赖本模块，通过注册的回调转发原始往返。
# agent_chat_ws 创建 session 时注册，会话结束取消注册，避免跨会话串扰。

_TRACE_HOOK: dict = {"handler": None}
_TRACE_HOOK_LOCK = threading.Lock()


def set_trace_hook(handler=None):
    """注册/注销全局 LLM trace 处理器（callable，见 ChatTraceHook 约定）。"""
    with _TRACE_HOOK_LOCK:
        _TRACE_HOOK["handler"] = handler


def get_trace_hook():
    with _TRACE_HOOK_LOCK:
        return _TRACE_HOOK["handler"]


def _safe_hook(kind: str, *args, **kwargs):
    """安全调用全局 hook，异常不影响主流程。"""
    handler = get_trace_hook()
    if handler is None:
        return None
    try:
        return handler(kind, *args, **kwargs)
    except Exception:
        logger.exception("[Trace] hook(%s) failed", kind)
        return None
