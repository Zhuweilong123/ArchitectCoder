"""
UML 全局优化 — 已迁移到 V2 直连引擎 (uml_optimizer_v2.py)

V1 的 UmlOptimizer (ReflectionAgent) 已下线，提供兼容委托函数。

Usage::

    from app.services.uml_optimizer_v2 import run_optimize_v2, optimize_v2

    result = await run_optimize_v2(
        project_file="project.umlproj",
        instructions="增加支付模块",
    )
"""

import logging

from app.agent_base.core.llm import BaseAgentsLLM
from app.services.uml_optimizer_v2 import run_optimize_v2

logger = logging.getLogger(__name__)


async def optimize_project_v2(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    max_iterations: int = 3,
    project_file: str = "",
) -> dict:
    """V1 ``optimize_project()`` 的兼容替换，委托到 V2 ``run_optimize_v2()``。

    兼容旧签名（接受 diagrams 参数但忽略，改为从 project_file 加载）。
    """
    return await run_optimize_v2(
        project_file=project_file,
        instructions=instructions,
        llm=llm,
    )


async def optimize_project_stream_v2(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    max_iterations: int = 3,
):
    """V2 流式兼容委托（当前仅用于保持接口兼容性）。

    注意：当前 AI 助手不使用流式优化模式，此函数仅为接口兼容保留。
    """
    from app.services.uml_optimizer_v2 import optimize_v2_stream

    async for line in optimize_v2_stream(
        project_file="",
        instructions=instructions,
        llm=llm,
    ):
        yield line
