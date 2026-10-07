"""Bounded session-local tool results, independent of trace recording."""
from __future__ import annotations

from collections import OrderedDict
from contextvars import ContextVar, Token
import uuid


class ToolOutputStore:
    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_records: int = 128):
        if max_bytes < 1 or max_records < 1:
            raise ValueError("Tool output storage limits must be positive")
        self.max_bytes, self.max_records = max_bytes, max_records
        self._outputs: OrderedDict[str, tuple[str, str, int]] = OrderedDict()
        self._bytes = 0

    def put(self, tool_name: str, text: str) -> str:
        """Return an independent output reference, or empty when too large to store."""
        size = len(text.encode("utf-8"))
        if size > self.max_bytes:
            return ""
        output_id = uuid.uuid4().hex[:16]
        self._outputs[output_id] = (tool_name, text, size)
        self._bytes += size
        while self._bytes > self.max_bytes or len(self._outputs) > self.max_records:
            self._bytes -= self._outputs.popitem(last=False)[1][2]
        return output_id

    def read(self, output_id: str, *, excluded_tool_names: frozenset[str] = frozenset()) -> str | None:
        item = self._outputs.get(output_id)
        return item[1] if item and item[0] not in excluded_tool_names else None


_store: ContextVar[ToolOutputStore | None] = ContextVar("tool_output_store", default=None)


def current_tool_output_store() -> ToolOutputStore | None:
    return _store.get()


def bind_tool_output_store(store: ToolOutputStore) -> Token:
    return _store.set(store)


def reset_tool_output_store(token: Token) -> None:
    _store.reset(token)
