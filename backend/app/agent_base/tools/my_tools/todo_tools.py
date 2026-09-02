"""TodoWriteTool — 会话级任务列表工具。"""

from __future__ import annotations

from typing import Any, List

from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.core.hooks import get_runtime

VALID_STATUS = ("pending", "in_progress", "completed")
VALID_KIND = ("analysis", "execution", "verification")


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
                "of {content, status}; complex planning tasks additionally require "
                "{kind, acceptance} for every item, including one verification item."
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
                                    "kind": {
                                        "type": "string",
                                        "enum": ["analysis", "execution", "verification"],
                                        "description": "Required for an acceptance-driven plan.",
                                    },
                                    "acceptance": {
                                        "type": "string",
                                        "description": "Observable completion criterion for this item.",
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

        runtime = get_runtime()
        requires_contract = runtime.requires_acceptance_todos
        requires_plan = runtime.requires_todo_plan or requires_contract
        if requires_contract and not 3 <= len(todos) <= 5:
            return "Error: acceptance-driven plans require 3 to 5 todos"
        if requires_plan and not requires_contract and not 1 <= len(todos) <= 3:
            return "Error: task plans require 1 to 3 concise todos"

        normalized: list[dict] = []
        for i, t in enumerate(todos):
            if not isinstance(t, dict) or "content" not in t or "status" not in t:
                return f"Error: todos[{i}] must have 'content' and 'status'"
            if not isinstance(t["content"], str) or not t["content"].strip():
                return f"Error: todos[{i}].content must be a non-empty string"
            if t["status"] not in VALID_STATUS:
                return f"Error: todos[{i}] has invalid status '{t['status']}'"
            item = {"content": t["content"].strip(), "status": t["status"]}
            if requires_contract:
                if t.get("kind") not in VALID_KIND:
                    return f"Error: todos[{i}].kind must be one of {', '.join(VALID_KIND)}"
                acceptance = t.get("acceptance")
                if not isinstance(acceptance, str) or not acceptance.strip():
                    return f"Error: todos[{i}].acceptance must be a non-empty string"
                item.update({"kind": t["kind"], "acceptance": acceptance.strip()})
            else:
                # Preserve optional metadata outside planning mode, so a caller can
                # begin with a rich list without changing the lightweight semantics.
                if t.get("kind") in VALID_KIND:
                    item["kind"] = t["kind"]
                if isinstance(t.get("acceptance"), str) and t["acceptance"].strip():
                    item["acceptance"] = t["acceptance"].strip()
            normalized.append(item)

        if requires_contract and not any(item.get("kind") == "verification" for item in normalized):
            return "Error: acceptance-driven plans require a verification todo"

        runtime.todos = normalized
        runtime.rounds_since_todo = 0
        return f"Updated {len(normalized)} todos"
