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

    # 流式 (SSE)
    async for line in optimize_v2_stream(
        project_file="project.umlproj",
        instructions="增加支付模块",
    ):
        ...
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional, AsyncIterator

from app.agent_base.core.llm import BaseAgentsLLM
from app.services.uml_common import (
    _build_reference_index,
    _build_global_prompt,
    _analyze_scope,
    _normalize_optimize_result,
    _normalize_llm_output,
    _validate_cross_references,
    _apply_auto_fixes,
    JsonElementExtractor,
)
from app.services.layout_engine import auto_layout
from app.services.tools import clean_llm_json_response
from app.services.file_service import load_project
from app.services.chat_trace import trace_span, TraceSession

logger = logging.getLogger(__name__)


def _sse_data(payload: str) -> str:
    """Format a single SSE ``data:`` line."""
    return f"data: {payload}\n\n"


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
    # 防御：LLM 可能将 design_constraints 返回为数组，归一化为 dict
    dc = result.get("design_constraints")
    if isinstance(dc, list):
        result["design_constraints"] = {"must_preserve": dc}
    elif not isinstance(dc, dict):
        result["design_constraints"] = {}
    for dspec in result.get("diagrams", []):
        if isinstance(dspec.get("data"), dict):
            dspec["data"] = _normalize_llm_output(dspec["data"])

    # 验证 + 自动修复
    issues = _validate_cross_references(result, original_index)
    if issues:
        existing = result.get("consistency_report", [])
        # 归一化：LLM 可能返回字符串而非列表
        if isinstance(existing, str):
            existing = [{"severity": "info", "msg": existing}]
        elif not isinstance(existing, list):
            existing = []
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


def _make_session_id(project_file: str) -> str:
    """根据项目文件名和时间生成 trace session_id。"""
    pid = os.path.splitext(os.path.basename(project_file))[0] if project_file else "no_project"
    return f"{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


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

    with TraceSession(
        session_id=_make_session_id(project_file),
        user_message=instructions,
        project_file=project_file,
        env_snapshot={"stream_mode": False, "version": "v2"},
    ) as trace:
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
        result = _process_result(raw, index)
        trace.done(answer="optimize_v2 completed")
        return result


async def optimize_v2_stream(
    project_file: str = "",
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
) -> AsyncIterator[str]:
    """流式全局 UML 优化（SSE lines）。

    返回符合 SSE 格式的字符串行，可直接用 StreamingResponse 流式输出。

    元素行格式:
        data: <type>:<json_string>\\n\\n

    最终结果行:
        event: design_updated\\ndata: <json>\\n\\n

    Yields:
        SSE-formatted strings.

    Trace 生命周期由本函数内部管理，调用方无需配置。
    """
    _llm = await _get_llm(llm)

    if not project_file:
        yield _sse_data(f"error:{json.dumps({'message': '未提供 project_file'})}")
        return

    with TraceSession(
        session_id=_make_session_id(project_file),
        user_message=instructions,
        project_file=project_file,
        env_snapshot={"stream_mode": True, "version": "v2"},
    ) as trace:
        # 1. 加载项目（支持空项目：few-shot 生成新设计）
        project = load_project(project_file)
        diagrams = [d.model_dump() for d in project.diagrams]

        logger.info("[optimize_v2_stream] 加载 %d 张图, 指令: %s", len(diagrams), instructions[:80])

        # 2. 构建索引和 prompt
        index = _build_reference_index(diagrams)

        # 2.5. Phase 1: 智能范围分析（失败时回退到完整 prompt）
        with trace_span("scope_analysis"):
            scope = await _analyze_scope(instructions, diagrams, index, _llm, project_file)

        # Phase 1 完成后通知前端影响范围摘要
        target_count = len(scope.get("target_keys", [])) if scope else 0
        change_type = scope.get("change_type", "optimize") if scope else "optimize"
        if target_count > 0:
            status_msg = f"影响范围已确定，正在优化 {target_count} 张图..."
        else:
            status_msg = "正在全局优化全部图表..."
        yield _sse_data(f"status:{json.dumps({'phase': 'scope_done', 'message': status_msg, 'target_count': target_count, 'change_type': change_type}, ensure_ascii=False)}")

        user_prompt, system_prompt, is_empty = _build_global_prompt(
            diagrams=diagrams, instructions=instructions, index=index,
            scope=scope,
        )

        # 3. 流式生成 + 实时元素提取
        extractor = JsonElementExtractor()
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
                    yield _sse_data(f"{elem_type}:{elem_json}")

        logger.info("[optimize_v2_stream] 流生成完成: %d 字符", len(full_response))

        # 4. 流结束标记
        yield _sse_data("DONE")

        # 5. 结果后处理 (验证+布局)
        result = _process_result(full_response, index)

        # 6. 发送最终 validated+layout 结果
        design_updated = json.dumps({
            "diagrams": result.get("diagrams", []),
            "consistency_report": result.get("consistency_report", []),
            "review": True,
        }, ensure_ascii=False)
        yield _sse_data(f"design_updated:{design_updated}")

        trace.done(answer="SSE stream completed")


# ── 统一入口：供 Agent Tool / Pipeline 等调用的 V2 非流式接口 ──

async def run_optimize_v2(
    project_file: str = "",
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
) -> dict:
    """V2 全局 UML 优化的统一入口（非流式）。

    替代 V1 的 ``UmlOptimizer.optimize()``，作为 Agent 工具和 Pipeline
    等处调用的唯一优化入口。

    Args:
        project_file: .umlproj 文件路径
        instructions: 用户优化指令（自然语言）
        llm: 可选 LLM 实例，不传则自动创建

    Returns:
        {"diagrams": [...], "consistency_report": [...], "changes_summary": "..."}
    """
    return await optimize_v2(
        project_file=project_file,
        instructions=instructions,
        llm=llm,
    )
