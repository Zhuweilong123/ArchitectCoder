"""
UML 全局优化 — 统一的优化入口（ReflectionAgent + Streaming）

提供两种模式：
- optimize():       ReflectionAgent "生成 → 验证 → 修复" 循环（完整模式）
- optimize_stream(): chat_stream 实时元素流 + 后验证修复（流式模式）

所有调用路径（Toolbar、Pipeline、对话 Agent）统一使用此模块。

Usage::

    from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer

    optimizer = UmlOptimizer(llm)
    result = await optimizer.optimize(diagrams=[...], instructions="增加支付模块")

    async for elem_type, elem_json in optimizer.optimize_stream(diagrams=[...], instructions="..."):
        ...
"""

import json
import logging
from typing import Optional, Callable, AsyncIterator

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
from app.services.llm_service import chat_stream as _chat_stream, chat as _chat

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
        """执行 UML 全局优化"""
        logger.info("[UmlOptimizer] 优化: %d 图, %s",
                    len(diagrams) if diagrams else 0, instructions[:80])

        try:
            return await self._optimize_internal(diagrams, instructions)
        except Exception as e:
            logger.exception("[UmlOptimizer] Internal error")
            return {
                "diagrams": diagrams or [],
                "consistency_report": [{"severity": "error", "msg": str(e)}],
                "changes_summary": f"Optimization failed: {e}",
                "design_constraints": {},
                "diff": "",
            }

    async def _optimize_internal(
        self, diagrams: list[dict] | None, instructions: str
    ) -> dict:
        """内部优化逻辑"""
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

        # ── 修复 prompt 中的裸 {} 以避免 ReflectionAgent .format() 解析错误 ──
        # _build_global_prompt 的 JSON 示例包含 {{}} （Python 字符串字面量转义后的 {}），
        # 当 ReflectionAgent 再次调用 .format() 时会误解析它们。
        # 方案：预填充 instructions 占位符 + 对 prompt 中所有非标准 {} 做二次转义。
        import re
        prompt = prompt.replace("{instructions}", instructions or "Design a complete UML system")
        # 对任何不在已知 format key 列表中的 {} 做转义
        _valid_keys = {'task', 'context', 'content', 'auto_feedback',
                       'last_attempt', 'feedback'}
        prompt = re.sub(r'(?<!\{)\{(?![\{])', '{{', prompt)
        prompt = re.sub(r'(?<!\})(\})(?!\})', '}}', prompt)

        # ── Step 2: 准备 UML 专用提示词 ──
        uml_prompts = {
            "initial": prompt,  # 复用原有的完整 prompt（已预填充 instructions）
            "reflect": """You are a UML review expert. Combine the automated validation result and semantic review to analyze the quality of the following UML design.

## Design context:
{context}

## Original requirements:
{task}

## Current design:
{content}

## Automated validation result:
{auto_feedback}

Add semantic review from the following dimensions (automated validation already covers structural issues):
1. Does the design fully cover the user's requirements?
2. Are the class responsibilities reasonably divided?
3. Is the interaction flow complete?
4. Are there any missing entities or relationships?
5. Is the design pattern choice appropriate?

If there is room for improvement, give specific modification suggestions.
If the design satisfies the requirements and automated validation passed, reply "no improvement needed".
""",
            "refine": """You are a UML design expert. Fix the UML design based on the review feedback.

## Design context:
{context}

## Original requirements:
{task}

## Previous design:
{last_attempt}

## Review feedback:
{feedback}

Output the corrected complete JSON (keep the original format, including the diagrams array).
Output only the JSON object, no other text.
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
            input_text=instructions or "Design a complete UML diagram system",
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
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None

    # ══════════════════════════════════════════════════════
    #  流式模式
    # ══════════════════════════════════════════════════════

    async def optimize_stream(
        self,
        diagrams: list[dict] | None = None,
        instructions: str = "",
        progress=None,
    ) -> AsyncIterator[tuple[str, str]]:
        """流式全局优化：实时元素流 + 后验证 + 可选 refine。

        Phase 1: chat_stream + _JsonElementExtractor → yield 元素 (实时绘制)
        Phase 2: 组装完整 JSON → 程序化验证
        Phase 3: 若有问题 → 单轮 refine → diff → yield 变更元素
        Phase 4: 终止（调用者在最后 yield \"DONE\" 或前端自行处理）

        通过 progress (ProgressRelay) 推送 design_element 事件到 WebSocket。
        """
        _l = logging.getLogger(__name__)
        _l.info("[UmlOptimizer.optimize_stream] 流式优化: %d 图, %s",
                len(diagrams) if diagrams else 0, instructions[:80])

        original_index = _build_reference_index(diagrams) if diagrams else {}
        prompt, full_system, is_empty = _build_global_prompt(
            diagrams=diagrams, instructions=instructions, index=original_index,
        )

        # ── Phase 1: 流式生成 ──
        extractor = _JsonElementExtractor()
        full_response = ""
        async for chunk in _chat_stream(
            prompt=prompt,
            system_prompt=full_system,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        ):
            full_response += chunk
            for elem_type, elem_json in extractor.feed(chunk):
                if progress:
                    progress.emit({
                        "event": "design_element",
                        "type": elem_type,
                        "data": elem_json,
                    })
                yield (elem_type, elem_json)

        _l.info("[UmlOptimizer.optimize_stream] 流生成完成: %d 字符", len(full_response))

        # ── Phase 2: 解析 + 程序化验证 ──
        result = self._try_parse_json(full_response)
        if result is None:
            _l.warning("[UmlOptimizer.optimize_stream] 最终解析失败")
            return

        result = _normalize_optimize_result(result)
        for dspec in result.get("diagrams", []):
            if isinstance(dspec.get("data"), dict):
                dspec["data"] = _normalize_llm_output(dspec["data"])

        issues = _validate_cross_references(result, original_index)

        if not issues:
            _l.info("[UmlOptimizer.optimize_stream] 验证通过，无需 refine")
            return

        # ── Phase 3: 单轮 refine + diff ──
        _l.info("[UmlOptimizer.optimize_stream] 验证发现 %d 问题，执行单轮 refine", len(issues))
        feedback = self._validate_hook("", full_response, "")
        if not feedback.strip():
            return

        # 精炼 prompt（直接拼接，不经过 ReflectionAgent 的 .format()）
        refine_prompt = f"""You are a UML design expert. Fix the UML design based on the review feedback.

## Design context:
{json.dumps({"has_existing": not is_empty, "diagram_count": len(diagrams) if diagrams else 0}, ensure_ascii=False)}

## Original requirements:
{instructions or "Design a complete UML diagram system"}

## Current design:
{full_response[:12000]}

## Review feedback:
{feedback}

Output the corrected complete JSON (keep the original format, including the diagrams array).
Output only the JSON object, no other text."""

        try:
            refined_raw = await _chat(
                prompt=refine_prompt,
                system_prompt=full_system,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            _l.warning("[UmlOptimizer.optimize_stream] refine 调用失败: %s", e)
            return

        refined_result = self._try_parse_json(refined_raw)
        if refined_result is None:
            _l.warning("[UmlOptimizer.optimize_stream] refine 结果解析失败")
            return

        refined_result = _normalize_optimize_result(refined_result)
        for dspec in refined_result.get("diagrams", []):
            if isinstance(dspec.get("data"), dict):
                dspec["data"] = _normalize_llm_output(dspec["data"])

        # Diff: 只 yield 变更的元素
        _apply_auto_fixes(refined_result, _validate_cross_references(refined_result, original_index))
        for change in self._diff_elements(result.get("diagrams", []),
                                           refined_result.get("diagrams", [])):
            if progress:
                progress.emit({
                    "event": "design_element",
                    "type": change[0],
                    "data": change[1],
                })
            yield change
        _l.info("[UmlOptimizer.optimize_stream] refine 完成")

    def _diff_elements(
        self, before: list[dict], after: list[dict]
    ) -> list[tuple[str, str]]:
        """比较 refine 前后的 diagrams，yield 所有变更（新增/修改/删除）。

        策略：为了简单可靠，refine 后整个 after 集合都作为变更发送。
        流式模式中 Phase 1 已发送过初始版本，现在发送修正版本覆盖。
        """
        results: list[tuple[str, str]] = []
        # 按图名索引 after
        after_by_key = {}
        for d in after:
            key = f"{d.get('type','')}:{d.get('name','')}"
            after_by_key[key] = d

        for d in after:
            data = d.get("data", {})
            dtype = d.get("type", "class")
            # 发送整个图的 data 作为 diagram_update 事件
            results.append(("diagram_update", json.dumps({
                "type": dtype,
                "name": d.get("name", ""),
                "component_id": d.get("component_id", ""),
                "data": data,
            }, ensure_ascii=False)))

        return results


# ── JSON 元素流式提取器（从 code_generator.py 移入）──────────

class _JsonElementExtractor:
    """从流式 JSON 文本中通过 brace 深度追踪提取完整 JSON 对象。

    Elements at depth 4 inside arrays (classes, relations, lifelines, etc.) are
    extracted and classified. Nested sub-objects at depth 5+ (attributes, methods)
    are correctly ignored.
    """

    # Keys whose appearance at depth 2 signals a section change.
    _SECTION_KEYS = ('class', 'sequence', 'component')

    def __init__(self):
        self._buf = ""
        self._pos = 0
        self._depth = 0        # brace depth ({ only, [ ] are ignored)
        self._in_str = False
        self._esc = False
        self._elem_start = -1   # buffer offset where current depth-4 element begins
        self._section = None    # 'class', 'sequence', or 'component'
        self._scan_pos = 0      # last position scanned for component_id
        self._seen_cids = set() # avoid duplicate diagram_meta emission
        self._seen_diagrams = set()  # avoid duplicate diagram_create emission
        self._diagram_scan_pos = 0   # last position scanned for diagram_create

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Feed a new text chunk. Returns (type, json_string) tuples for completed elements."""
        self._buf += chunk
        elements: list[tuple[str, str]] = []

        # ── Scan new content for diagram_create events ─────
        _new_for_dc = self._buf[self._diagram_scan_pos:]
        _dc_idx = 0
        while True:
            _dc_idx = _new_for_dc.find('"type"', _dc_idx)
            if _dc_idx < 0:
                break
            _colon = _new_for_dc.find(':', _dc_idx)
            if _colon < 0: break
            _vstart = _new_for_dc.find('"', _colon + 1)
            if _vstart < 0: break
            _vend = _new_for_dc.find('"', _vstart + 1)
            if _vend < 0: break
            _dtype = _new_for_dc[_vstart + 1:_vend]
            if _dtype in ('class', 'sequence', 'component'):
                _search_end = min(len(_new_for_dc), _vend + 300)
                _name_idx = _new_for_dc.find('"name"', _vend, _search_end)
                if _name_idx >= 0:
                    _ncolon = _new_for_dc.find(':', _name_idx)
                    if _ncolon >= 0:
                        _nvstart = _new_for_dc.find('"', _ncolon + 1)
                        if _nvstart >= 0 and _nvstart < _search_end:
                            _nvend = _new_for_dc.find('"', _nvstart + 1)
                            if _nvend >= 0 and _nvend < _search_end:
                                _dname = _new_for_dc[_nvstart + 1:_nvend]
                                _dkey = f"{_dtype}:{_dname}"
                                if _dkey not in self._seen_diagrams:
                                    self._seen_diagrams.add(_dkey)
                                    _cid = ""
                                    _cid_idx = _new_for_dc.find('"component_id"', _vend, _search_end)
                                    if _cid_idx >= 0:
                                        _ccolon = _new_for_dc.find(':', _cid_idx)
                                        if _ccolon >= 0:
                                            _cvstart = _new_for_dc.find('"', _ccolon + 1)
                                            if _cvstart >= 0 and _cvstart < _search_end:
                                                _cvend = _new_for_dc.find('"', _cvstart + 1)
                                                if _cvend >= 0:
                                                    _cid = _new_for_dc[_cvstart + 1:_cvend]
                                    elements.append(('diagram_create', json.dumps({
                                        'type': _dtype,
                                        'name': _dname,
                                        'component_id': _cid,
                                    })))
            _dc_idx = _vend + 1
        self._diagram_scan_pos = max(0, len(self._buf) - 1024)

        # ── Scan new content for component_id values ─────
        new_text = self._buf[self._scan_pos:]
        idx = 0
        while True:
            idx = new_text.find('"component_id"', idx)
            if idx < 0:
                break
            colon_idx = new_text.find(':', idx)
            if colon_idx < 0:
                break
            val_start = new_text.find('"', colon_idx + 1)
            if val_start < 0:
                break
            val_end = new_text.find('"', val_start + 1)
            if val_end < 0:
                break
            cid = new_text[val_start + 1:val_end]
            if cid and cid not in self._seen_cids:
                self._seen_cids.add(cid)
                elements.append(('diagram_meta', json.dumps({
                    'component_id': cid,
                    'diagram_type': self._section or 'class',
                })))
            idx = val_end + 1
        self._scan_pos = max(0, len(self._buf) - 512)

        while self._pos < len(self._buf):
            c = self._buf[self._pos]

            if self._esc:
                self._esc = False
            elif c == '\\' and self._in_str:
                self._esc = True
            elif c == '"':
                self._in_str = not self._in_str
                if not self._in_str and self._depth == 2:
                    self._update_section()
            elif not self._in_str:
                if c == '{':
                    if self._depth == 3:
                        self._elem_start = self._pos
                    self._depth += 1
                elif c == '}':
                    self._depth -= 1
                    if self._depth == 3 and self._elem_start >= 0:
                        txt = self._buf[self._elem_start:self._pos + 1]
                        try:
                            obj = json.loads(txt)
                            tp = self._classify(obj)
                            if tp:
                                elements.append((tp, txt))
                        except json.JSONDecodeError:
                            pass  # incomplete object — wait for more data
                        self._elem_start = -1

            self._pos += 1

        # Trim consumed prefix to bound memory (512-char window)
        _window = 512
        if self._elem_start >= 0:
            _keep = max(0, self._elem_start - _window)
            self._buf = self._buf[_keep:]
            self._pos -= _keep
            self._elem_start -= _keep
        else:
            _keep = max(0, self._pos - _window)
            self._buf = self._buf[_keep:]
            self._pos -= _keep

        return elements

    def _update_section(self):
        """Called when a string key closes at depth 2 — update section context."""
        j = self._pos - 1
        while j >= 0 and self._buf[j] != '"':
            j -= 1
        if j >= 0:
            key = self._buf[j + 1:self._pos]
            if key in self._SECTION_KEYS:
                self._section = key

    def _classify(self, obj: dict) -> str | None:
        """Determine element type from JSON keys with section-context-aware relation vs comp_rel."""
        if 'stereotype' in obj:
            return 'class'
        if 'from_lifeline' in obj:
            return 'message'
        if 'y_start' in obj or 'y_end' in obj:
            return 'fragment'
        if 'class_ref' in obj:
            return 'lifeline'
        if 'source' in obj and 'target' in obj:
            return 'comp_rel' if self._section == 'component' else 'relation'
        if 'parent_id' in obj or 'provided_interfaces' in obj:
            return 'component'
        return None


# ── 便捷函数 ─────────────────────────────────────────────


async def optimize_project_v2(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    max_iterations: int = 3,
) -> dict:
    """``optimize_project()`` 的 ReflectionAgent 增强版替代。

    与原有函数签名兼容，是所有调用路径的统一入口。

    Usage::

        from app.agent_base.tools.my_tools.uml_optimizer import optimize_project_v2

        result = await optimize_project_v2(
            diagrams=existing_diagrams,
            instructions="增加支付模块",
        )
    """
    if llm is None:
        llm = BaseAgentsLLM.from_settings()

    optimizer = UmlOptimizer(llm, max_iterations=max_iterations)
    return await optimizer.optimize(diagrams=diagrams, instructions=instructions)


async def optimize_project_stream_v2(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    llm: BaseAgentsLLM | None = None,
    max_iterations: int = 3,
):
    """``optimize_project_stream()`` 的统一替代 — 流式元素 + 后验证。

    Usage::

        from app.agent_base.tools.my_tools.uml_optimizer import optimize_project_stream_v2

        async for elem_type, elem_json in optimize_project_stream_v2(
            diagrams=existing_diagrams,
            instructions="设计 OTA 升级系统",
        ):
            ...
    """
    if llm is None:
        llm = BaseAgentsLLM.from_settings()

    optimizer = UmlOptimizer(llm, max_iterations=max_iterations)
    async for payload in optimizer.optimize_stream(
        diagrams=diagrams, instructions=instructions,
    ):
        yield payload
