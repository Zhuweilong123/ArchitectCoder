"""
UML 全局优化 v2 — 简洁版

对比 v1 (uml_optimizer.py + ReflectionAgent + ReActAgent 工具链):
- 不再经过 ReActAgent 工具调用链
- 不再使用 ReflectionAgent 多轮迭代
- 直接: 加载项目 → 构建 prompt → 调用 LLM → 一次验证 → 规范化 → 布局

Usage::

    from app.services.uml_optimizer_v2 import optimize_v2, optimize_v2_stream

    # 非流式
    result = await optimize_v2(project_file="project.umlproj", instructions="增加支付模块")

    # 流式
    async for elem_type, elem_json in optimize_v2_stream(
        project_file="project.umlproj",
        instructions="增加支付模块",
    ):
        ...
"""

import json
import logging
from typing import Optional, Callable, AsyncIterator

from app.agent_base.core.llm import BaseAgentsLLM
from app.services.uml_common import (
    _build_reference_index,
    _build_global_prompt,
    _analyze_scope,
    _normalize_optimize_result,
    _normalize_llm_output,
    _validate_cross_references,
    _apply_auto_fixes,
)
from app.services.layout_engine import auto_layout
from app.services.tools import clean_llm_json_response
from app.services.file_service import load_project
from app.agent_base.tools.my_tools.uml_optimizer import _JsonElementExtractor
from app.services.chat_trace import trace_span

logger = logging.getLogger(__name__)

# 流式模式下存储最终结果（供 API 层发送 design_updated 使用）
_stream_last_result: dict | None = None


def _get_stream_last_result() -> dict | None:
    return _stream_last_result


def _try_parse_json(content: str) -> dict | None:
    """安全解析 JSON，支持 markdown 包装清理"""
    try:
        cleaned = clean_llm_json_response(content)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None


def _process_result(raw_answer: str, original_index: dict) -> dict:
    """处理 LLM 输出：解析 → 规范化 → 验证 → 自动修复 → 布局"""
    result = _try_parse_json(raw_answer)
    if result is None:
        logger.warning("[optimize_v2] LLM 输出无法解析为 JSON")
        return {
            "diagrams": [],
            "consistency_report": [{"severity": "error", "msg": "LLM 输出无法解析为 JSON"}],
            "changes_summary": "解析失败",
        }

    # 规范化
    result = _normalize_optimize_result(result)
    for dspec in result.get("diagrams", []):
        if isinstance(dspec.get("data"), dict):
            dspec["data"] = _normalize_llm_output(dspec["data"])

    # 验证 + 自动修复
    issues = _validate_cross_references(result, original_index)
    if issues:
        existing = result.get("consistency_report", [])
        result["consistency_report"] = existing + issues
        _apply_auto_fixes(result, issues)
        logger.info(
            "[optimize_v2] 跨图验证: %d issues (%d auto-fixed, %d errors)",
            len(issues),
            sum(1 for i in issues if i.get("auto_fixed")),
            sum(1 for i in issues if i.get("severity") == "error"),
        )

    # 自动布局
    result = auto_layout(result)

    return result


async def _get_llm(llm: BaseAgentsLLM | None = None) -> BaseAgentsLLM:
    """获取或创建 LLM 实例"""
    if llm is not None:
        return llm
    return BaseAgentsLLM.from_settings()


async def optimize_v2(
    project_file: str = "",
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
) -> dict:
    """非流式全局 UML 优化。

    一次 LLM 调用 + 一次程序化跨图验证。

    Args:
        project_file: .umlproj 文件路径
        instructions: 用户优化指令（自然语言）
        llm: 可选 LLM 实例，不传则自动创建

    Returns:
        {"diagrams": [...], "consistency_report": [...]}
    """
    _llm = await _get_llm(llm)

    if not project_file:
        return {
            "diagrams": [],
            "consistency_report": [{"severity": "error", "msg": "未提供 project_file"}],
            "changes_summary": "无项目文件",
        }

    # 1. 加载项目（支持空项目：few-shot 生成新设计）
    project = load_project(project_file)
    diagrams = [d.model_dump() for d in project.diagrams]

    logger.info("[optimize_v2] 加载 %d 张图, 指令: %s", len(diagrams), instructions[:80])

    # 2. 构建跨图索引
    index = _build_reference_index(diagrams)

    # 2.5. Phase 1: 智能范围分析（失败时回退到完整 prompt）
    with trace_span("scope_analysis"):
        scope = await _analyze_scope(instructions, diagrams, index, _llm, project_file)

    # 3. 构建 LLM prompt（按 scope 精简；空项目时生成 from-scratch prompt）
    user_prompt, system_prompt, is_empty = _build_global_prompt(
        diagrams=diagrams, instructions=instructions, index=index,
        scope=scope,
    )

    # 4. 调用 LLM
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    with trace_span("optimize_v2"):
        raw = await _llm.ainvoke(messages, temperature=0.5, max_tokens=32768,
                                model="deepseek-v4-pro")
    logger.info("[optimize_v2] LLM 返回 %d 字符", len(raw))

    # 5. 处理结果
    return _process_result(raw, index)


async def optimize_v2_stream(
    project_file: str = "",
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    progress: Callable[[dict], None] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """流式全局 UML 优化。

    逐元素实时推送设计元素到前端画布。

    Yields:
        (element_type, json_string) 元组，其中 element_type 为
        "diagram_create", "class", "relation", "lifeline", "message",
        "fragment", "component", "comp_rel", "diagram_update"

    完成后可通过 ``_get_stream_last_result()`` 获取最终处理结果。
    """
    global _stream_last_result
    _stream_last_result = None

    _llm = await _get_llm(llm)

    if not project_file:
        _stream_last_result = {
            "diagrams": [],
            "consistency_report": [{"severity": "error", "msg": "未提供 project_file"}],
        }
        return

    # 1. 加载项目（支持空项目：few-shot 生成新设计）
    project = load_project(project_file)
    diagrams = [d.model_dump() for d in project.diagrams]

    logger.info("[optimize_v2_stream] 加载 %d 张图, 指令: %s", len(diagrams), instructions[:80])

    # 2. 构建索引和 prompt
    index = _build_reference_index(diagrams)

    # 2.5. Phase 1: 智能范围分析（失败时回退到完整 prompt）
    with trace_span("scope_analysis"):
        scope = await _analyze_scope(instructions, diagrams, index, _llm, project_file)

    user_prompt, system_prompt, is_empty = _build_global_prompt(
        diagrams=diagrams, instructions=instructions, index=index,
        scope=scope,
    )

    # 3. 流式生成 + 实时元素提取（空项目时 from-scratch prompt 有效）
    extractor = _JsonElementExtractor()
    full_response = ""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    with trace_span("optimize_v2_stream"):
        async for chunk in _llm.athink(messages, temperature=0.5, max_tokens=32768,
                                       model="deepseek-v4-pro"):
            full_response += chunk
            for elem_type, elem_json in extractor.feed(chunk):
                if progress:
                    progress({
                        "event": "design_element",
                        "type": elem_type,
                        "data": elem_json,
                    })
                yield (elem_type, elem_json)

    logger.info("[optimize_v2_stream] 流生成完成: %d 字符", len(full_response))

    # 4. 处理结果
    result = _process_result(full_response, index)
    _stream_last_result = result

    # 5. 发送变更后的完整图数据（布局后位置可能已调整）
    for d in result.get("diagrams", []):
        data = d.get("data", {})
        dtype = d.get("type", "class")
        update_json = json.dumps({
            "type": dtype,
            "name": d.get("name", ""),
            "component_id": d.get("component_id", ""),
            "data": data,
        }, ensure_ascii=False)
        if progress:
            progress({
                "event": "design_element",
                "type": "diagram_update",
                "data": update_json,
            })
        yield ("diagram_update", update_json)
