"""Semantic compression for multi-turn session context.

This service runs between task turns.  It never participates in the active
ReAct/tool loop, and the original messages remain available in the session
trace.  Only the compact semantic view is injected into later turns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.context_manager import estimate_tokens

logger = logging.getLogger(__name__)


def _message_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
            "metadata": message.get("metadata") or {},
        }
    return {
        "role": str(getattr(message, "role", "user") or "user"),
        "content": str(getattr(message, "content", "") or ""),
        "metadata": getattr(message, "metadata", {}) or {},
    }


def _strip_fenced_json(content: str) -> str:
    value = str(content or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


@dataclass
class SessionCompressionResult:
    triggered: bool = False
    compressed: bool = False
    estimated_tokens: int = 0
    dropped_messages: int = 0
    summary: str = ""
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


class SessionContextCompressor:
    """Create a bounded semantic view of older session turns."""

    def __init__(
        self,
        llm: Any,
        *,
        model: str = "deepseek-flash",
        hard_limit_tokens: int = 256000,
        trigger_ratio: float = 0.7,
        max_output_tokens: int = 4000,
        keep_recent_turns: int = 3,
    ) -> None:
        self.llm = llm
        self.model = str(model or "deepseek-flash")
        self.hard_limit_tokens = max(1, int(hard_limit_tokens))
        self.trigger_ratio = float(trigger_ratio)
        if not 0 < self.trigger_ratio <= 1:
            raise ValueError("trigger_ratio must be in (0, 1]")
        self.max_output_tokens = max(256, int(max_output_tokens))
        self.keep_recent_turns = max(1, int(keep_recent_turns))

    async def maybe_compress(
        self,
        agent: Any,
        *,
        session_id: str = "",
        trace_log: Any | None = None,
    ) -> SessionCompressionResult:
        history = [_message_dict(message) for message in getattr(agent, "_history", [])]
        prior_summary = str(getattr(agent, "_history_summary", "") or "")
        estimated = estimate_tokens(json.dumps(
            {"summary": prior_summary, "history": history},
            ensure_ascii=False,
            sort_keys=True,
        ))
        result = SessionCompressionResult(
            estimated_tokens=estimated,
            triggered=estimated >= self.hard_limit_tokens * self.trigger_ratio,
        )
        turn_starts = [
            index for index, message in enumerate(history)
            if message["role"] == "user"
        ]
        if not result.triggered or len(turn_starts) <= self.keep_recent_turns:
            return result

        recent_start = turn_starts[-self.keep_recent_turns]
        older = history[:recent_start]
        recent = history[recent_start:]
        source_refs = self._source_refs(
            trace_log=trace_log,
            session_id=session_id,
            history=history,
        )
        result.source_refs = source_refs
        records = []
        for index, message in enumerate(older):
            records.append({
                "history_index": index,
                "role": message["role"],
                "content": message["content"],
                "source": source_refs[index] if index < len(source_refs) else {
                    "session_id": session_id,
                    "history_index": index,
                },
            })

        system_prompt = (
            "You are a session-context compactor. Extract only durable, useful "
            "information from the supplied conversation. Omit casual chat and "
            "social filler. Preserve user goals, constraints, decisions, "
            "preferences, completed work, unresolved work, and important facts. "
            "Every retained item must include one or more exact source objects "
            "copied from the input. Return JSON only with keys: facts, decisions, "
            "constraints, preferences, completed, unresolved. Each value is a "
            "list of objects with `content` and `sources`."
        )
        user_payload = {
            "previous_summary": prior_summary,
            "turns": records,
            "source_rule": "Do not invent or modify source fields.",
        }
        try:
            response = await self.llm.ainvoke_with_metadata(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(
                        user_payload, ensure_ascii=False,
                    )},
                ],
                model=self.model,
                temperature=0.1,
                max_tokens=self.max_output_tokens,
                json_mode=True,
                trace_context={
                    "scope": "session_compression",
                    "session_id": session_id,
                    "estimated_session_tokens": estimated,
                    "hard_limit_tokens": self.hard_limit_tokens,
                    "trigger_ratio": self.trigger_ratio,
                },
            )
            parsed = json.loads(_strip_fenced_json(response.get("content", "")))
            summary = self._render_summary(parsed, source_refs)
            if not summary:
                raise ValueError("semantic compressor returned no durable information")
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("[SessionCompression] semantic compression failed", exc_info=True)
            return result

        from app.agent_base.core.message import Message

        agent._history_summary = summary
        agent._history = [
            Message(message["content"], message["role"], metadata=message.get("metadata") or {})
            for message in recent
        ]
        result.compressed = True
        result.dropped_messages = len(older)
        result.summary = summary
        if trace_log is not None:
            try:
                trace_log.event(
                    "session_context_compressed",
                    session_id=session_id,
                    model=self.model,
                    trigger_ratio=self.trigger_ratio,
                    hard_limit_tokens=self.hard_limit_tokens,
                    estimated_session_tokens=estimated,
                    dropped_messages=len(older),
                    summary=summary,
                    source_refs=source_refs,
                )
            except Exception:
                logger.warning("[SessionCompression] failed to write trace event", exc_info=True)
        return result

    @staticmethod
    def _render_summary(value: Any, source_refs: list[dict[str, Any]]) -> str:
        if not isinstance(value, dict):
            return ""
        allowed_sources = {
            json.dumps(source, ensure_ascii=False, sort_keys=True)
            for source in source_refs
        }
        sections = (
            ("Facts", "facts"),
            ("Decisions", "decisions"),
            ("Constraints", "constraints"),
            ("Preferences", "preferences"),
            ("Completed", "completed"),
            ("Unresolved", "unresolved"),
        )
        lines = ["## Session semantic summary"]
        for title, key in sections:
            items = value.get(key) or []
            retained = []
            for item in items:
                if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                    continue
                sources = [
                    source for source in item.get("sources") or []
                    if json.dumps(source, ensure_ascii=False, sort_keys=True) in allowed_sources
                ]
                if not sources:
                    continue
                refs = ", ".join(
                    f"{source.get('event_type', 'event')}:{source.get('span_id', 'unknown')}"
                    for source in sources
                )
                retained.append(f"- {str(item['content']).strip()} [source: {refs}]")
            if retained:
                lines.append(f"### {title}")
                lines.extend(retained)
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _source_refs(*, trace_log: Any | None, session_id: str, history: list[dict]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        trace_path = str(getattr(trace_log, "path", "") or "")
        if trace_path:
            try:
                with Path(trace_path).open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("event_type") in {"user_message", "done"}:
                            events.append(event)
            except OSError:
                pass
        refs: list[dict[str, Any]] = []
        turn = -1
        for index, message in enumerate(history):
            if message["role"] == "user":
                turn += 1
            event = events[turn * 2 + (1 if message["role"] == "assistant" else 0)] if (
                turn >= 0 and turn * 2 + (1 if message["role"] == "assistant" else 0) < len(events)
            ) else None
            refs.append({
                "session_id": session_id,
                "trace_id": (event or {}).get("trace_id", ""),
                "trace_path": trace_path,
                "event_type": (event or {}).get("event_type", message["role"]),
                "span_id": (event or {}).get("span_id", ""),
                "turn": max(0, turn),
                "history_index": index,
            })
        return refs


__all__ = ["SessionCompressionResult", "SessionContextCompressor"]
