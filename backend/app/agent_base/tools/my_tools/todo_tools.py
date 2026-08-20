"""TodoWriteTool — 会话级任务列表工具。"""

from __future__ import annotations

from typing import Any, List

from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.core.hooks import get_runtime

VALID_STATUS = ("pending", "in_progress", "completed")


class TodoWriteTool(Tool):
    """维护当前会话的 todo 列表，跟踪长任务子步骤进度。

    todo 状态存于 ``AgentRuntime``（contextvar），供 reminder hook 与
    本工具共享；更新时同时清零 ``rounds_since_todo`` 计数。
    """

    def __init__(self):
        super().__init__(
            name="todo_write",
            description=(
                "Create and manage a task list for the current session. "
                "Use for multi-step tasks to track progress. todos is a list "
                "of {content, status} where status is pending/in_progress/completed."
            ),
        )

    def get_parameters(self) -> List[ToolParameter]:
        return []  # 用 to_openai_schema 提供精确的「对象数组」schema

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"],
                                    },
                                },
                                "required": ["content", "status"],
                            },
                        },
                    },
                    "required": ["todos"],
                },
            },
        }

    def run(self, parameters: dict) -> str:
        todos = parameters.get("todos", [])
        if not isinstance(todos, list):
            return "Error: todos must be a list"

        normalized: list[dict] = []
        for i, t in enumerate(todos):
            if not isinstance(t, dict) or "content" not in t or "status" not in t:
                return f"Error: todos[{i}] must have 'content' and 'status'"
            if t["status"] not in VALID_STATUS:
                return f"Error: todos[{i}] has invalid status '{t['status']}'"
            normalized.append({"content": t["content"], "status": t["status"]})

        runtime = get_runtime()
        runtime.todos = normalized
        runtime.rounds_since_todo = 0
        return f"Updated {len(normalized)} todos"
