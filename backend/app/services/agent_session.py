"""Agent 会话注册表 — 跨 WebSocket 连接复用会话状态（内存版）。

背景
    原先每次 WebSocket 连接都会 new 一个 ChatTraceLogger，
    并懒创建全新的 DevAgent（_history 为空）。这导致刷新页面或重开面板后，
    agent 丢失完整对话历史，且同一会话被拆成多个 trace_*.jsonl 文件。

    本模块把「会话」从「连接」解耦：以稳定的 session_id 为键，在内存中
    跨连接复用 agent 实例（含 _history）、review_mgr、progress 与日志器。
    日志器在会话被 TTL 回收时才 close，从而同一会话持续追加到同一文件。

设计取舍
    - 内存版优先落地（覆盖刷新/重开面板这一主要场景）。
    - 服务重启仍会丢会话，若需扛重启，见方案 B：把 agent.get_history() 落盘。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# 无活动超过该时长后回收会话（并 finalize 日志文件）
SESSION_TTL_SECONDS = 2 * 3600  # 2 小时


@dataclass
class AgentSession:
    session_id: str
    project_file: str = ""
    agent: Any = None              # ReActAgent（含 _history）
    review_mgr: Any = None
    progress: Any = None
    prompt_builder: Any = None     # DevPromptBuilder（静态 system prompt + 易变上下文 memo）
    trace_log: Any = None          # ChatTraceLogger，跨连接复用
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()


_sessions: dict[str, AgentSession] = {}
_lock = threading.Lock()


def _finalize(s: AgentSession) -> None:
    """回收会话时关闭 trace 日志器（写入「会话结束」标记）。"""
    if s.trace_log is not None:
        try:
            s.trace_log.close()
        except Exception:
            pass


def get(session_id: str) -> Optional[AgentSession]:
    """按 session_id 取会话；超时则回收并返回 None。"""
    with _lock:
        s = _sessions.get(session_id)
        if s is None:
            return None
        if time.time() - s.last_active > SESSION_TTL_SECONDS:
            _finalize(s)
            del _sessions[session_id]
            return None
        return s


def get_or_create(session_id: str, project_file: str = "") -> AgentSession:
    """取会话，不存在则创建；存在则同步最新的 project_file。"""
    with _lock:
        s = _sessions.get(session_id)
        if s is None:
            s = AgentSession(session_id=session_id, project_file=project_file)
            _sessions[session_id] = s
        else:
            if project_file:
                s.project_file = project_file
        return s


def finalize(session_id: str) -> None:
    """显式回收某会话（主要用于测试 / 清理）。"""
    with _lock:
        s = _sessions.pop(session_id, None)
        if s is not None:
            _finalize(s)


def cleanup_expired(now: float | None = None) -> int:
    """回收所有超时会话，返回回收数量。供周期任务调用。"""
    now = now if now is not None else time.time()
    with _lock:
        expired = [
            sid for sid, s in _sessions.items()
            if now - s.last_active > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            _finalize(_sessions.pop(sid))
    return len(expired)


def active_count() -> int:
    with _lock:
        return len(_sessions)
