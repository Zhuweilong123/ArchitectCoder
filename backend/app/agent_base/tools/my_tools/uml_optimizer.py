"""
UML 全局优化 — 基于 ReflectionAgent 的增强版替代 optimize_project()

将原有的"单次 chat() 调用"替换为 ReflectionAgent 的 "生成 → 验证 → 修复" 循环。
反射阶段的反馈由 UmlValidationTool（程序化验证）提供，而非纯 LLM 自省。

Usage::

    from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer

    optimizer = UmlOptimizer(llm)
    result = await optimizer.optimize(diagrams=[...], instructions="增加支付模块")
"""

import json
import logging
from typing import Optional, Callable

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.core.config import Config
from app.agent_base.agents.reflection_agent import ReflectionAgent
from app.services.code_generator import (
    _build_reference_index,
    _build_global_prompt,
    _normalize_optimize_result,
    _normalize_llm_output,
    _validate_cross_references,
    _apply_auto_fixes,
)
from app.services.layout_engine import auto_layout
from app.services.tools import clean_llm_json_response

logger = logging.getLogger(__name__)


class UmlOptimizer:
    """基于 ReflectionAgent 的 UML 全局优化器

    替代原有的 ``optimize_project()`` 单次调用模式，使用反射循环提升设计质量：

    - initial: LLM 根据 prompt + 设计指南 + 交叉索引生成全部 diagrams
    - reflect:  UmlValidationTool 程序化验证 + LLM 语义审查 → 反馈
    - refine:  LLM 根据反馈修正设计
    - 循环直到验证通过或达到 max_iterations

    Usage::

        from app.agent_base import BaseAgentsLLM
        from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer

        llm = BaseAgentsLLM.from_settings()
        optimizer = UmlOptimizer(llm, max_iterations=3)

        result = await optimizer.optimize(
            diagrams=[...],
            instructions="增加支付模块，完善异常处理流程",
        )
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        max_iterations: int = 3,
        temperature: float = 0.5,
        max_tokens: int = 8192,
    ):
        self.llm = llm
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def optimize(
        self,
        diagrams: list[dict] | None = None,
        instructions: str = "",
    ) -> dict:
        """执行 UML 全局优化

        Args:
            diagrams: 现有图列表，None 表示从零生成
            instructions: 用户的设计指令

        Returns:
            dict with ``diagrams`` array (new format)，兼容原 ``optimize_project()`` 返回值
        """
        logger.info(
            "[UmlOptimizer] 开始优化: %d 张现有图, instructions=%s",
            len(diagrams) if diagrams else 0,
            instructions[:80],
        )

        # ── Step 1: 构建上下文（与原 optimize_project 完全一致）──
        original_index = _build_reference_index(diagrams) if diagrams else {}
        prompt, full_system, is_empty = _build_global_prompt(
            diagrams=diagrams,
            instructions=instructions,
            index=original_index,
        )

        # ── Step 2: 准备 UML 专用提示词 ──
        uml_prompts = {
            "initial": prompt,  # 复用原有的完整 prompt
            "reflect": """你是UML审查专家。请结合自动验证结果和语义审查，分析以下UML设计的质量。

## 设计上下文:
{context}

## 原始需求:
{task}

## 当前设计:
{content}

## 自动验证结果:
{auto_feedback}

请从以下维度补充语义审查（自动验证已覆盖结构性问题）:
1. 设计是否完整覆盖了用户需求？
2. 类的职责划分是否合理？
3. 交互流程是否完整？
4. 是否有遗漏的实体或关系？
5. 设计模式选择是否恰当？

如果有改进空间，请给出具体的修改建议。
如果设计已满足需求且自动验证通过，请回答"无需改进"。
""",
            "refine": """你是UML设计专家。请根据审查反馈修正UML设计。

## 设计上下文:
{context}

## 原始需求:
{task}

## 上一版设计:
{last_attempt}

## 审查反馈:
{feedback}

请输出修正后的完整JSON（保持原有格式，包含 diagrams 数组）。
只输出JSON对象，不要其他文字。
""",
        }

        # ── Step 3: 创建 ReflectionAgent ──
        agent = ReflectionAgent(
            name="UML设计助手",
            llm=self.llm,
            system_prompt=full_system,
            config=Config(temperature=self.temperature, max_tokens=self.max_tokens),
            max_iterations=self.max_iterations,
            custom_prompts=uml_prompts,
            context=json.dumps({
                "has_existing": not is_empty,
                "diagram_count": len(diagrams) if diagrams else 0,
                "class_count": len(original_index.get("classes", {})),
                "component_count": len(original_index.get("components", {})),
            }, ensure_ascii=False),
        )

        # ── Step 4: 运行反射循环 ──
        raw_answer = agent.run(
            input_text=instructions or "设计完整的UML图系统",
            reflect_hook=self._validate_hook,
            post_process=self._post_process_hook,
        )

        # ── Step 5: 返回结果（与原有格式兼容）──
        return self._finalize_result(raw_answer, original_index)

    # ══════════════════════════════════════════════════════
    #  Hook 实现
    # ══════════════════════════════════════════════════════

    def _validate_hook(self, task: str, content: str, context: str) -> str:
        """反射阶段的验证 Hook — 调用 _validate_cross_references 做程序化检查"""
        result = self._try_parse_json(content)
        if result is None:
            return (
                "❌ 无法解析当前设计 JSON。请确保输出是纯 JSON 对象（不要包裹在 markdown 代码块中），"
                "格式为 {\"diagrams\": [...], ...}"
            )

        if not result.get("diagrams"):
            return "❌ 设计缺少 'diagrams' 数组，请添加至少一张图。"

        # ── 规范化后验证 ──
        result = _normalize_optimize_result(result)
        issues = _validate_cross_references(result, {})

        if not issues:
            return ""  # 空字符串 = 验证通过，停止迭代

        # 格式化问题为反馈
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        lines = [f"发现 {len(issues)} 个问题 ({len(errors)} 错误, {len(warnings)} 警告):"]
        for item in issues:
            severity = item.get("severity", "info")
            emoji = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(severity, "•")
            auto = " [已自动修复]" if item.get("auto_fixed") else ""
            lines.append(f"  {emoji} [{item.get('type', '')}] {item['msg']}{auto}")

        return "\n".join(lines)

    def _post_process_hook(self, content: str) -> str:
        """精炼后的后处理 — JSON 解析 + 规范化"""
        result = self._try_parse_json(content)
        if result is None:
            return content  # 保持原始字符串，让下一轮 reflect hook 报告解析失败

        # 规范化格式
        result = _normalize_optimize_result(result)
        for dspec in result.get("diagrams", []):
            if isinstance(dspec.get("data"), dict):
                dspec["data"] = _normalize_llm_output(dspec["data"])

        return json.dumps(result, indent=2, ensure_ascii=False)

    # ══════════════════════════════════════════════════════
    #  结果最终化
    # ══════════════════════════════════════════════════════

    def _finalize_result(self, raw_answer: str, original_index: dict) -> dict:
        """最终处理：JSON 解析 → 规范化 → 验证 → 自动修复 → 布局"""
        result = self._try_parse_json(raw_answer)

        if result is None:
            logger.warning("[UmlOptimizer] 最终解析失败，返回空结果")
            return {
                "diagrams": [],
                "consistency_report": [{"severity": "error", "msg": "Agent 输出无法解析为 JSON"}],
                "changes_summary": "解析失败",
                "design_constraints": {},
                "diff": raw_answer,
            }

        # 规范化
        result = _normalize_optimize_result(result)
        for dspec in result.get("diagrams", []):
            if isinstance(dspec.get("data"), dict):
                dspec["data"] = _normalize_llm_output(dspec["data"])

        # 最终验证 + 自动修复
        post_issues = _validate_cross_references(result, original_index)
        if post_issues:
            existing = result.get("consistency_report", [])
            result["consistency_report"] = existing + post_issues
            _apply_auto_fixes(result, post_issues)
            logger.info(
                "[UmlOptimizer] 最终验证: %d issues (%d auto-fixed, %d errors)",
                len(post_issues),
                sum(1 for i in post_issues if i.get("auto_fixed")),
                sum(1 for i in post_issues if i.get("severity") == "error"),
            )

        # 自动布局
        result = auto_layout(result)

        return result

    @staticmethod
    def _try_parse_json(content: str) -> dict | None:
        """安全解析 JSON，支持清理 markdown 包装"""
        try:
            cleaned = clean_llm_json_response(content)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试直接解析
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None


# ── 便捷函数 ─────────────────────────────────────────────


async def optimize_project_v2(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    max_iterations: int = 3,
) -> dict:
    """``optimize_project()`` 的 ReflectionAgent 增强版替代

    与原有函数签名兼容，额外的参数可通过 kwargs 传入。

    Usage::

        from app.agent_base.tools.my_tools.uml_optimizer import optimize_project_v2

        # 替换原来的 optimize_project()
        result = await optimize_project_v2(
            diagrams=existing_diagrams,
            instructions="增加支付模块",
        )
    """
    if llm is None:
        llm = BaseAgentsLLM.from_settings()

    optimizer = UmlOptimizer(llm, max_iterations=max_iterations)
    return await optimizer.optimize(diagrams=diagrams, instructions=instructions)
