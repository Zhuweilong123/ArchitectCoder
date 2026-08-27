"""SpawnSubagentTool — 通用子代理工具。"""

from __future__ import annotations

import json
import logging

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool
from app.agent_base.tools.my_tools.file_system_tools import create_file_system_tools
from app.agent_base.tools.my_tools.skill_loader import SkillTool, build_skills_section

logger = logging.getLogger(__name__)

SUBAGENT_SYSTEM = (
    "You are a coding subagent. Complete the given task, then return a concise "
    "final summary of what you did and found. Do not spawn more agents."
)


class SpawnSubagentTool(AsyncTool):
    """通用子代理工具 — 委托一个子任务，返回 summary。

    子代理用受限工具集（文件系统原语）+ ``sub_agent_model`` 独立跑简化 FC
    循环，避免主上下文膨胀。工具集不含 submit_uml_review / spawn_subagent，
    防止递归子代理和 UML 审核嵌套；bash 敏感命令仍走人工审核（与主代理
    共用同一审核通道），防止委托绕过。
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        sub_agent_model: str,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        max_steps: int = 20,
        review_manager=None,
        progress=None,
    ):
        super().__init__(
            name="spawn_subagent",
            description=(
                "Launch a focused subagent to complete a self-contained sub-task "
                "(e.g. explore, summarize, or implement an isolated change) and "
                "return only its final summary. Use to avoid cluttering the main "
                "context with many small reads."
            ),
        )
        self.llm = llm
        self.sub_agent_model = sub_agent_model
        self.max_steps = max_steps

        # 受限子 registry：文件系统原语 + skill。
        # 审核通道透传给子代理的 bash —— 敏感命令委托子代理也不能绕过人工审核。
        self.sub_registry = ToolRegistry()
        for t in create_file_system_tools(
            source_dir, test_dir, design_dir,
            review_manager=review_manager, progress=progress,
        ):
            self.sub_registry.register_tool(t)
        # 子代理常做「总结项目设计」，UML 建模规范对它同样有用
        self.sub_registry.register_tool(SkillTool())
        self.sub_tools = self.sub_registry.get_openai_specs()

        # skill 目录随 SKILL.md 静态不变，创建时拼一次即可
        skills = build_skills_section()
        self.system_prompt = f"{SUBAGENT_SYSTEM}\n\n{skills}" if skills else SUBAGENT_SYSTEM

    async def _execute(self, params: dict) -> str:
        description = params.get("description", "")
        if not isinstance(description, str) or not description.strip():
            return "Error: description is required"

        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": description},
        ]

        for _ in range(self.max_steps):
            response = await self.llm.ainvoke_with_tools(
                messages=messages,
                tools=self.sub_tools,
                tool_choice="auto",
                model=self.sub_agent_model,
                temperature=0.3,
            )
            content = response.get("content") or ""
            tool_calls = response.get("tool_calls")

            if not tool_calls:
                return content.strip() or "(subagent finished without a summary)"

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            for tc in tool_calls:
                fn = tc["function"]
                try:
                    args = json.loads(fn["arguments"])
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result = await self.sub_registry.aexecute_tool_with_params(
                    fn["name"], args,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        # 达 max_steps：返回最后一条 assistant content
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                return msg["content"]
        return "(subagent finished without a summary)"

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "The self-contained sub-task to delegate.",
                        },
                    },
                    "required": ["description"],
                },
            },
        }
