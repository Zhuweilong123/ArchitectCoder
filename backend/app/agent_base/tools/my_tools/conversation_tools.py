"""对话工具 — 对话 Agent 的工具集工厂与基础设施。

提供对话 Agent 可调用的工具集（文件系统原语 / todo / skill / 子代理 /
任务系统 / 人工审核），以及异步工具基类 ``AsyncTool`` 与进度转发器
``ProgressRelay``。

Usage::

    from app.agent_base.tools.my_tools.conversation_tools import (
        create_conversation_tools,
    )

    registry = ToolRegistry()
    tools, review_mgr = create_conversation_tools(llm, source_dir="src/")
    for tool in tools:
        registry.register_tool(tool)

    agent = ReActAgent("全栈开发", llm, registry, ...)
    result = await agent.arun("给用户模块加 OAuth 登录")
"""

from __future__ import annotations

import os
from typing import Callable

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.base import Tool
from app.agent_base.tools.review import ReviewManager


# ── 异步工具基类 — run() 返回 coroutine，由 aexecute_tool_with_params await ──

class AsyncTool(Tool):
    """异步工具基类。

    ``run()`` 返回 coroutine，由 ``ToolRegistry.aexecute_tool_with_params()``
    在 ReActAgent FC 循环中正确地 await 它。
    """

    def get_parameters(self) -> list:
        return []  # 子类通过 to_openai_schema() 直接提供 schema

    def run(self, parameters: dict) -> str:
        """返回 coroutine，由 aexecute_tool_with_params await。"""
        return self._execute(parameters)  # type: ignore[return-value]

    async def _execute(self, parameters: dict) -> str:
        raise NotImplementedError

    def to_openai_schema(self) -> dict:
        raise NotImplementedError


# ── 进度事件转发 ──

class ProgressRelay:
    """子 Agent 进度 → 外层编排层的事件转发器。

    对话 Agent 调用工具时，子 Agent 内部的流式进度通过此转发器
    推送到前端。编排层在对话 Agent 运行前注入转发器实例。
    """

    def __init__(self):
        self._events: list[dict] = []
        self._on_progress: Callable[[dict], None] | None = None

    def on_progress(self, callback: Callable[[dict], None]):
        """注册进度回调。"""
        self._on_progress = callback

    def emit(self, event: dict):
        """发送进度事件。"""
        self._events.append(event)
        if self._on_progress:
            result = self._on_progress(event)
            # 如果回调是协程，需要调度到事件循环执行
            import asyncio
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    pass  # 无事件循环时忽略

    def clear(self):
        self._events.clear()

    @property
    def events(self) -> list[dict]:
        return self._events


# ═══════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════

def _kg_db_path() -> str:
    """知识图谱数据库路径（与 explore_project_tools._kg_db_path 同口径）。"""
    try:
        from app.core.config import get_settings
        base = os.path.dirname(get_settings().uml_dir)
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
    return os.path.normpath(os.path.abspath(
        os.path.join(base, "data", "knowledge_graph.db"),
    ))


def create_conversation_tools(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    include_review: bool = True,
    progress: ProgressRelay | None = None,
    task_scope: str = "",
    change_set=None,
    review_session_id: str = "",
    review_project_id: str = "",
    auto_approve_reviews: bool = False,
    command_executor=None,
    include_subagent: bool = False,
    include_task_system: bool = False,
) -> tuple[list[Tool], ReviewManager | None]:
    """创建对话 Agent 可用的完整工具集。

    Returns:
        (tools, review_manager) — tool 列表 + 审核管理器（若启用）
    """
    tools: list[Tool] = []
    if command_executor is None:
        # Conversation agents always use the configured production command
        # environment. Standalone low-level file-tool tests may inject the
        # compatibility adapter explicitly instead.
        from app.agent_base.execution import build_linux_command_executor
        from app.core.config import get_settings
        command_executor = build_linux_command_executor(get_settings())

    # 审核管理器提前创建：bash 敏感命令审核（文件系统工具）与
    # submit_uml_review 共用同一通道（ReviewManager + ProgressRelay）。
    review_mgr = None
    if include_review:
        review_mgr = ReviewManager(
            session_id=review_session_id,
            project_id=review_project_id,
            auto_approve_reviews=auto_approve_reviews,
        )

    # A 层文件系统原语工具（读/写/编辑/查找/跑命令）
    from .file_system_tools import create_file_system_tools
    from app.core.config import get_settings
    # 设计目录：优先 project_file 所在目录（当前项目的 design_dir），否则全局 uml_dir
    design_dir = (os.path.dirname(os.path.abspath(project_file))
                  if project_file else os.path.abspath(get_settings().uml_dir))
    tools.extend(create_file_system_tools(
        source_dir, test_dir, design_dir,
        review_manager=review_mgr, progress=progress,
        change_set=change_set,
        command_executor=command_executor,
    ))

    # todo_write：会话任务列表
    from .todo_tools import TodoWriteTool
    tools.append(TodoWriteTool())

    # skill：按需加载 skills/ 下的领域知识包（L1 目录由 prompt 注入）
    from .skill_loader import SkillTool
    tools.append(SkillTool())

    # 子代理和持久化任务 DAG 都会显著扩大工具 schema，并引入额外的
    # 推理/模型调用。默认 DevAgent 保持单 Agent 的直接执行路径；仅由
    # 专门编排场景显式开启，避免普通修复在编辑前耗尽预算。
    if include_subagent:
        from .subagent_tool import SpawnSubagentTool
        tools.append(SpawnSubagentTool(
            llm=llm,
            source_dir=source_dir, test_dir=test_dir, design_dir=design_dir,
            project_file=project_file,
            review_manager=review_mgr, progress=progress,
            toolkits=("strategy",),
            max_steps=6,
            max_total_tokens=get_settings().agent_subagent_max_total_tokens,
            single_use=True,
        ))

    if include_task_system:
        from app.agent_base.tools.task_system import create_task_system_tools
        tools.extend(create_task_system_tools(scope=task_scope))

    # KG 结构化理解工具（动词命名，与文件原语互补：回答「有没有/谁依赖谁/设计实现没」，
    # read_file/grep 回答具体内容与符号）
    # Knowledge-graph tools are intentionally disabled for DevAgent. The
    # implementation remains available for direct opt-in and its own tests,
    # but it is not exposed in the default conversation tool registry. This
    # avoids broad graph exploration and repeated reads during normal tasks.
    # from extensions.knowledge_graph.tools import create_kg_v2_tools
    # tools.extend(create_kg_v2_tools(
    #     db_path=_kg_db_path(),
    #     project_file=project_file, source_dir=source_dir,
    # ))

    if include_review:
        from app.agent_base.tools.review import SubmitUmlReviewTool
        tools.append(SubmitUmlReviewTool(
            manager=review_mgr, progress=progress, project_file=project_file,
        ))

    return tools, review_mgr
