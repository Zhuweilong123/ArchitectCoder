"""
ChatTrace — 会话级结构化 trace 层 (JSONL).

专为智能体 trace 追踪与问题复现设计。

核心特性:
  - JSONL 每行一个事件，程序化可解析、可重放
  - trace_id / span_id / parent_span_id 组成因果链
  - ts_ms（墙上毫秒）+ monotonic_ns（单调纳秒）支撑时序重建与延迟测量
  - 记录 LLM 原始往返（prompt/completion/model/tokens）、工具调用参数与完整返回
  - 记录环境快照（agent/prompt 版本、KG 状态），便于复现现场

文件命名: trace_{session_id}.jsonl，位于 temp/chat_log/ 目录。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
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


def _split_system_prompt(messages: list) -> tuple[str, list]:
    """拆分 system prompt 与对话流。

    system 消息作为独立字段记录，messages 只保留 user/assistant/tool 对话流，
    避免同一份 system 内容在 trace 中重复出现。

    复现约定：`[{"role": "system", "content": system_prompt}] + messages`
    即重建当时的完整请求。无 system 消息时返回空串与原始列表。
    """
    if not messages:
        return "", []
    stripped = [m for m in messages
                if not (isinstance(m, dict) and m.get("role") == "system")]
    system_prompt = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content")
            system_prompt = content if isinstance(content, str) else ""
            break
    return system_prompt, stripped


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
EVT_KG_INJECT = "kg_inject"
EVT_CONTEXT_COMPACTED = "context_compacted"


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

    def __init__(self, session_id: str = ""):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = ""
        self._trace_id = new_trace_id()
        self._lock = threading.Lock()
        self._path: str | None = None
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
    ) -> None:
        """Persist the checkpoint used to restore a compacted session."""
        self.event(
            EVT_CONTEXT_COMPACTED,
            summary=summary,
            dropped_messages=dropped_messages,
            dropped_tokens=dropped_tokens,
        )

    def llm_request(self, *, provider: str, model: str, messages: list,
                    temperature: float | None, max_tokens: int | None,
                    tools: list | None = None, tool_choice: str | None = None,
                    response_format: dict | None = None, timeout: int | None = None,
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
                  parent_span_id: str = "") -> str:
        """记录工具调用。返回 span_id 供 tool_result 关联。"""
        sid = new_trace_id()
        self._write({
            **_event(self.session_id, EVT_TOOL_CALL,
                     trace_id=self._trace_id, span_id=sid,
                     parent_span_id=parent_span_id, run_id=self.run_id),
            "step": step,
            "tool_name": tool_name,
            "arguments": arguments,
        })
        return sid

    def tool_result(self, *, span_id: str, tool_name: str, observation: str,
                    duration_ms: float = 0.0, error: str = "",
                    fed_truncated: bool = False, fed_length: int = 0) -> None:
        """记录工具返回（完整 observation，不截断）。

        fed_truncated / fed_length 标记该返回喂回模型前是否被截断，
        用于区分「模型实际看到的口径」与「工具完整返回的口径」。
        """
        self._write({
            **_event(self.session_id, EVT_TOOL_RESULT,
                     trace_id=self._trace_id, span_id=span_id,
                     parent_span_id=span_id, run_id=self.run_id),
            "tool_name": tool_name,
            "observation": observation,
            "fed_truncated": fed_truncated,
            "fed_length": fed_length,
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

    def done(self, *, answer: str) -> None:
        self.event(EVT_DONE, answer=answer)

    def error(self, *, event_type: str, message: str) -> None:
        self.event(EVT_ERROR, source=event_type, message=message)


def _chat_log_dir() -> str:
    """计算 chat_log 目录（与 pipeline_log 同级）。"""
    from app.core.config import get_settings
    settings = get_settings()
    return os.path.normpath(os.path.abspath(
        os.path.join(os.path.dirname(settings.uml_dir), "chat_log"),
    ))


# ── trace_span — 调用栈标记（contextvars，线程+异步安全）────
# 子 Agent 入口处用 `with trace_span("UmlOptimizer"):` 包裹，
# 全局 LLM hook 自动将 span_path 注入每个 llm_request/llm_response 事件。

import contextvars

_trace_spans: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "trace_spans", default=[]
)


class trace_span:
    """上下文管理器 — 在 LLM trace 中标记当前调用栈层级。

    用法::

        with trace_span("UmlOptimizer"):
            with trace_span("reflect"):
                feedback = llm.invoke(...)  # span_path = "UmlOptimizer/reflect"

    span_path 自动写入全局 hook 的 llm_request / llm_response 事件中。
    """

    def __init__(self, name: str):
        self._name = name
        self._token = None

    def __enter__(self):
        spans = list(_trace_spans.get())
        spans.append(self._name)
        self._token = _trace_spans.set(spans)
        return self

    def __exit__(self, *args):
        if self._token is not None:
            _trace_spans.reset(self._token)
            self._token = None

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        self.__exit__(*args)


def current_trace_spans() -> list[str]:
    """返回当前线程/协程的 span_path 列表（最内层在末尾）。"""
    return _trace_spans.get()


# ── 全局 LLM trace 钩子 ──────────────────────────────
# BaseAgentsLLM 不直接依赖本模块，通过注册的回调转发原始往返。
#
# 钩子栈（stack）语义 —— 嵌套场景（如 pipeline 调用 optimize_v2）下，
# 内层 TraceSession push 自己的 bridge，外层不受影响。
# LLM 调用总是路由到栈顶 handler。

# 钩子必须按协程隔离。进程级 list 会让并发 WebSocket 互相覆盖/清理 hook，
# 也会把一个会话的 LLM trace 写入另一个会话。
_TRACE_HOOK_STACK: ContextVar[tuple] = ContextVar(
    "trace_hook_stack", default=()
)


def push_trace_hook(handler) -> None:
    """注册 LLM trace 处理器到栈顶。"""
    _TRACE_HOOK_STACK.set((*_TRACE_HOOK_STACK.get(), handler))


def pop_trace_hook(handler) -> None:
    """注销栈顶 LLM trace 处理器（调用者应传入与 push 相同的 handler 对象）。"""
    stack = _TRACE_HOOK_STACK.get()
    if stack and stack[-1] is handler:
        _TRACE_HOOK_STACK.set(stack[:-1])


# 保留旧 API 兼容性（agent_chat_ws 等旧调用方仍在用，逐步迁移）
def set_trace_hook(handler=None):
    """已废弃 — 请使用 push_trace_hook / pop_trace_hook 或 TraceSession。

    传入 None 时仅清空当前协程上下文的栈，不影响其他会话。
    """
    if handler is None:
        _TRACE_HOOK_STACK.set(())
    else:
        push_trace_hook(handler)


def get_trace_hook():
    """返回栈顶 handler（栈空则 None）。"""
    stack = _TRACE_HOOK_STACK.get()
    return stack[-1] if stack else None


def _safe_hook(kind: str, *args, **kwargs):
    """安全调用栈顶 hook，异常不影响主流程。"""
    handler = get_trace_hook()
    if handler is None:
        return None
    try:
        return handler(kind, *args, **kwargs)
    except Exception:
        logger.exception("[Trace] hook(%s) failed", kind)
        return None


# ── TraceSession — 任务函数内部自动管理 trace 生命周期 ────

class TraceSession:
    """trace 生命周期上下文管理器 — 在任务函数内部使用。

    自动完成: 创建 ChatTraceLogger → start → push hook → run → pop hook → close

    用法::

        with TraceSession(session_id="my_proj_20240804_120000",
                          user_message="优化类图",
                          project_file="proj.umlproj") as tracer:
            # LLM 调用自动记录 trace
            result = await llm.ainvoke(...)
            tracer.done(answer="优化完成")

    嵌套安全：内层 TraceSession push 到栈顶，LLM 调用路由到最内层；
    退出后自动恢复外层 handler。
    """

    def __init__(self, *, session_id: str, user_message: str = "",
                 project_file: str = "", source_dir: str = "",
                 test_dir: str = "", env_snapshot: dict | None = None):
        self._sid = session_id
        self._user_message = user_message
        self._project_file = project_file
        self._source_dir = source_dir
        self._test_dir = test_dir
        self._env_snapshot = env_snapshot
        self._tracer: ChatTraceLogger | None = None
        self._bridge = None

    @property
    def tracer(self) -> ChatTraceLogger:
        if self._tracer is None:
            raise RuntimeError("TraceSession not entered")
        return self._tracer

    # ── 内部 bridge ─────────────────────────────────

    def _make_bridge(self):
        """构造 bridge 闭包 — 捕获 self._tracer，直接桥接 LLM 事件。"""
        tracer = self._tracer

        def bridge(kind: str, *args, **kwargs):
            spans = current_trace_spans()
            span_path = "/".join(spans) if spans else ""
            if kind == "llm_request":
                return tracer.llm_request(
                    provider=kwargs.get("provider", "unknown"),
                    model=kwargs.get("model", ""),
                    messages=kwargs.get("messages", []),
                    temperature=kwargs.get("temperature"),
                    max_tokens=kwargs.get("max_tokens"),
                    tools=kwargs.get("tools"),
                    tool_choice=kwargs.get("tool_choice"),
                    response_format=kwargs.get("response_format"),
                    timeout=kwargs.get("timeout"),
                    span_path=span_path,
                )
            elif kind == "llm_response":
                tracer.llm_response(
                    span_id=kwargs.get("span_id", ""),
                    content=kwargs.get("content", ""),
                    tool_calls=kwargs.get("tool_calls"),
                    usage=kwargs.get("usage"),
                    error=kwargs.get("error", ""),
                    duration_ms=kwargs.get("duration_ms", 0.0),
                    span_path=span_path,
                )
                return None
            return None

        return bridge

    # ── 上下文管理 ─────────────────────────────────

    def __enter__(self):
        self._tracer = ChatTraceLogger(session_id=self._sid)
        self._tracer.start(
            user_message=self._user_message,
            project_file=self._project_file,
            source_dir=self._source_dir,
            test_dir=self._test_dir,
            env_snapshot=self._env_snapshot,
        )
        self._bridge = self._make_bridge()
        push_trace_hook(self._bridge)
        return self._tracer

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._bridge is not None:
                pop_trace_hook(self._bridge)
        finally:
            if self._tracer is not None and not self._tracer._closed:
                if exc_type is not None:
                    self._tracer.error(
                        event_type="exception",
                        message=f"{exc_type.__name__}: {exc_val}",
                    )
                self._tracer.close()
        return False  # 不吞异常

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)
