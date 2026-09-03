"""SpawnSubagentTool — 通用子代理工具。"""

from __future__ import annotations

import json
import logging

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.my_tools.conversation_tools import AsyncTool, _kg_db_path
from app.agent_base.tools.my_tools.file_system_tools import create_file_system_tools, ReadFileTool
from app.agent_base.tools.my_tools.knowledge_graph_v2_tools import create_kg_v2_tools
from app.agent_base.tools.my_tools.skill_loader import SkillTool, build_skills_section

logger = logging.getLogger(__name__)

SUBAGENT_SYSTEM = (
    "You are a coding subagent. Complete the given task, then return a concise "
    "final summary of what you did and found. Do not spawn more agents."
)

STRATEGY_SUBAGENT_SYSTEM = (
    "You are a read-only strategy advisor for a development task. Do not modify "
    "files and do not propose unverified implementation details. Inspect only the "
    "minimum evidence needed, then return a concise plan with: canonical source, "
    "target scope, ordered steps, acceptance criteria, and risks. Do not spawn more agents."
)

# ── 子代理工具包（toolkit）──────────────────────────────────────
# 主 agent 按任务类型选工具包，框架展开成受限工具集。安全不变量由本表强制，
# 不依赖主 agent 自觉：
#   * 任何工具包都不含 spawn_subagent / submit_uml_review（防递归 / 防审核绕过）
#   * 子代理工具集是主 agent 允许集的子集（无提权）
TOOLKIT_NAMES = ("standard", "read_only", "kg_analysis", "strategy")


def _build_toolkit_tools(
    kind: str,
    source_dir: str, test_dir: str, design_dir: str,
    db_path: str, project_file: str,
    review_manager, progress,
) -> list:
    """按工具包名构建工具列表（不含 spawn_subagent / submit_uml_review）。"""
    if kind == "standard":
        tools = list(create_file_system_tools(
            source_dir, test_dir, design_dir,
            review_manager=review_manager, progress=progress,
        ))
        tools.append(SkillTool())
        return tools

    # 只读类工具包共用 ReadFileTool（无 write/edit/bash）+ kg 工具子集
    read_tool = ReadFileTool(source_dir, test_dir, design_dir)
    kg = {t.name: t for t in create_kg_v2_tools(
        db_path=db_path, project_file=project_file, source_dir=source_dir,
    )}
    if kind == "read_only":
        return [kg["find_nodes"], kg["expand_neighbors"], read_tool]
    if kind == "kg_analysis":
        return [
            kg["find_nodes"],
            kg["expand_neighbors"],
            kg["get_project_map"],
            read_tool,
        ]
    if kind == "strategy":
        return [
            kg["find_nodes"],
            kg["get_project_map"],
            read_tool,
            SkillTool(),
        ]
    raise ValueError(f"unknown toolkit: {kind}")


class SpawnSubagentTool(AsyncTool):
    """通用子代理工具 — 委托一个子任务，返回 summary。

    子代理复用主代理的已选模型，并以受限工具集（文件系统原语）独立跑简化 FC
    循环，避免主上下文膨胀。工具集不含 submit_uml_review / spawn_subagent，
    防止递归子代理和 UML 审核嵌套；bash 敏感命令仍走人工审核（与主代理
    共用同一审核通道），防止委托绕过。
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        project_file: str = "",
        max_steps: int = 20,
        max_total_tokens: int = 500000,
        review_manager=None,
        progress=None,
        toolkits: tuple[str, ...] = TOOLKIT_NAMES,
        single_use: bool = False,
    ):
        super().__init__(
            name="spawn_subagent",
            description=(
                "Delegate one bounded, self-contained exploration to a read-only "
                "subagent and receive only a concise summary. Use for cross-file or "
                "UML/source impact analysis when many small reads would clutter the "
                "main context; do not use for greetings, simple single-file tasks, "
                "editing, review, or final verification. The subagent cannot spawn "
                "agents, write files, run bash, or submit UML review."
            ),
        )
        self.llm = llm
        self.max_steps = max_steps
        self.max_total_tokens = max(1, int(max_total_tokens))
        self.last_token_usage = 0
        self.toolkits = tuple(toolkits)
        self.single_use = single_use
        self._single_use_used = False
        unknown_toolkits = set(self.toolkits) - set(TOOLKIT_NAMES)
        if not self.toolkits or unknown_toolkits:
            raise ValueError(f"unknown or empty subagent toolkits: {sorted(unknown_toolkits)}")

        # 每个 toolkit 一个受限子 registry。审核通道透传给子代理的 bash ——
        # 敏感命令委托子代理也不能绕过人工审核（只有 standard 包含 bash）。
        db_path = _kg_db_path()
        skills = build_skills_section()
        self.sub_registries: dict[str, ToolRegistry] = {}
        self.system_prompts: dict[str, str] = {}
        for kind in self.toolkits:
            registry = ToolRegistry()
            for t in _build_toolkit_tools(
                kind, source_dir, test_dir, design_dir,
                db_path, project_file, review_manager, progress,
            ):
                registry.register_tool(t)
            self.sub_registries[kind] = registry
            prompt = STRATEGY_SUBAGENT_SYSTEM if kind == "strategy" else SUBAGENT_SYSTEM
            if kind == "standard" and skills:
                prompt = f"{SUBAGENT_SYSTEM}\n\n{skills}"
            self.system_prompts[kind] = prompt

    async def _execute(self, params: dict) -> str:
        description = params.get("description", "")
        if not isinstance(description, str) or not description.strip():
            return "Error: description is required"

        if self.single_use and self._single_use_used:
            return "Error: the strategy subagent may be used only once per task"

        toolkit = str(params.get("toolkit") or self.toolkits[0]).strip().lower()
        if toolkit not in self.sub_registries:
            toolkit = self.toolkits[0]
        if self.single_use:
            self._single_use_used = True
        registry = self.sub_registries[toolkit]
        sub_tools = registry.get_openai_specs()

        messages: list[dict] = [
            {"role": "system", "content": self.system_prompts[toolkit]},
            {"role": "user", "content": description},
        ]
        self.last_token_usage = 0

        for _ in range(self.max_steps):
            if self.last_token_usage >= self.max_total_tokens:
                return (
                    "Subagent budget exceeded: reached the configured limit of "
                    f"{self.max_total_tokens} tokens before completing the sub-task."
                )
            response = await self.llm.ainvoke_with_tools(
                messages=messages,
                tools=sub_tools,
                tool_choice="auto",
                temperature=0.3,
            )
            usage = response.get("usage") or {}
            if isinstance(usage, dict):
                self.last_token_usage += int(usage.get("total_tokens") or 0)
            content = response.get("content") or ""
            tool_calls = response.get("tool_calls")

            if not tool_calls:
                return content.strip() or "(subagent finished without a summary)"

            if self.last_token_usage >= self.max_total_tokens:
                return (
                    "Subagent budget exceeded: reached the configured limit of "
                    f"{self.max_total_tokens} tokens before producing a final summary."
                )

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
                result = await registry.aexecute_tool_with_params(
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
                        "toolkit": {
                            "type": "string",
                            "enum": list(self.toolkits),
                            "description": (
                                "Subagent tool scope. Available values: "
                                + ", ".join(self.toolkits) + "."
                            ),
                        },
                    },
                    "required": ["description"],
                },
            },
        }
