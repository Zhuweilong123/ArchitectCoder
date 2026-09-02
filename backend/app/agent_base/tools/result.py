"""统一工具执行结果协议。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    status: str = "success"  # success | error | blocked
    data: Any = ""
    error_code: str = ""
    retryable: bool = False

    @property
    def text(self) -> str:
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, ensure_ascii=False, default=str)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "data": self.data,
            "error_code": self.error_code,
            "retryable": self.retryable,
        }

    @classmethod
    def success(cls, data: Any = "") -> "ToolResult":
        return cls(status="success", data=data)

    @classmethod
    def error(cls, message: Any, code: str = "TOOL_ERROR", retryable: bool = False) -> "ToolResult":
        return cls(status="error", data=message, error_code=code, retryable=retryable)

