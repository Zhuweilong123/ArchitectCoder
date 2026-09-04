"""UML optimization prompt and scope-analysis services."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path as _Path

from app.agent_base.core.knowledge_graph import get_knowledge_graph
from app.core.json_utils import clean_llm_json_response

from .uml_index import (
    _build_focused_index,
    _build_reference_index,
    _format_index_for_prompt,
)

_GUIDE_DIR = _Path(__file__).resolve().parent.parent.parent.parent / "skills" / "uml-design-guide"

def _load_guide(name: str) -> str:
    """加载单份设计指南的 Markdown 内容。name 不含后缀，如 'sequence_diagram'。"""
    gf = _GUIDE_DIR / f"{name}_guide.md"
    return gf.read_text(encoding="utf-8") if gf.exists() else ""


def _detect_existing_types(diagrams: list[dict]) -> set[str]:
    """检测项目中已有的非空图表类型。

    返回 {"class", "sequence", "component"} 中已有内容的类型集合。
    只检查数据层实际有元素的图，空图不算已有。
    """
    existing: set[str] = set()
    for d in (diagrams or []):
        dtype = d.get("diagram_type") or d.get("type") or "class"
        data = d.get("data") or {}
        if dtype == "class" and (data.get("classes") or d.get("classes")):
            existing.add("class")
        elif dtype == "sequence" and (data.get("lifelines") or d.get("lifelines")):
            existing.add("sequence")
        elif dtype == "component" and (data.get("components") or d.get("components")):
            existing.add("component")
    return existing


def _load_example(name: str) -> str:
    """加载单份示例文件的 Markdown 内容。name 为简名如 'class_diagram'。"""
    ef = _GUIDE_DIR / f"{name}_example.md"
    return ef.read_text(encoding="utf-8") if ef.exists() else ""


# ── KG 辅助：同步 BM25 查询 ─────────────────────────────────

def _fetch_kg_hits(
    project_file: str,
    instructions: str,
    out: dict[str, set[str]],
    graph_loader=None,
) -> None:
    """同步封装：提取 KG BM25 命中实体 → 图映射，存入 out dict。

    对 instructions 进行多粒度搜索（全指令 + 短关键词），
    确保不同表达方式都能命中实体。"""
    try:
        project_id = os.path.splitext(os.path.basename(project_file))[0]
        # 多粒度搜索：全指令 + 2-3 词短词组；查询细节由 provider 实现。
        queries = [instructions]
        words = instructions.split()
        if len(words) >= 2:
            queries.extend([" ".join(words[i:i+3]) for i in range(0, len(words), 2)])
        seen = set()
        queries = [q for q in queries if q and not (q in seen or seen.add(q))][:5]
        graph = graph_loader() if graph_loader is not None else get_knowledge_graph()
        matches = graph.search_diagrams(project_id, queries, top_k=6)
        for diagram_name, reasons in matches.items():
            out.setdefault(diagram_name, set()).update(reasons)
    except Exception:
        pass  # KG 不可用时静默退化


# ── 项目摘要 ────────────────────────────────────────────

def _build_project_summary(
    diagrams: list[dict], index: dict,
    project_file: str = "",
    instructions: str = "",
) -> str:
    """构建轻量项目摘要（<800 chars），供 Phase 1 scope 分析用。

    如果 project_file 存在且知识图谱 provider 可用，会搜索
    instructions 关键词，把匹配到的实体名附加到对应图条目后面，
    帮助 Phase 1 LLM 在不明确的指令下精确定位目标图。
    """
    lines = []
    nc = len(index.get("classes", {}))
    nl = len(index.get("lifelines", {}))
    ncomp = len(index.get("components", {}))
    issues_count = len(index.get("orphan_lifelines", [])) + len(index.get("unlinked_diagrams", []))
    lines.append(f"Project: {nc} classes, {nl} lifelines, {ncomp} components, {issues_count} issues")
    lines.append("Diagrams:")

    # ── KG augmentation: BM25 搜索匹配的实体 → 在图条目中内联显示 ──
    kg_hits_by_diag: dict[str, set[str]] = {}
    if project_file and instructions:
        _fetch_kg_hits(project_file, instructions, kg_hits_by_diag)

    for d in diagrams:
        dtype = d.get("diagram_type") or d.get("type") or "class"
        dname = d.get("name", "Untitled")
        data = d.get("data") or d
        # 统计元素数量
        counts = ""
        if dtype == "class":
            nc2 = len(data.get("classes") or d.get("classes", []))
            nr = len(data.get("relations") or d.get("relations", []))
            counts = f" — {nc2} classes, {nr} relations"
        elif dtype == "sequence":
            nll = len(data.get("lifelines") or d.get("lifelines", []))
            nmsg = len(data.get("messages") or d.get("messages", []))
            nfrag = len(data.get("fragments") or d.get("fragments", []))
            counts = f" — {nll} lifelines, {nmsg} messages, {nfrag} fragments"
        elif dtype == "component":
            nc2 = len(data.get("components") or d.get("components", []))
            ncr = len(data.get("comp_relations") or d.get("comp_relations", []))
            counts = f" — {nc2} components, {ncr} relations"

        line = f"  {dtype} \"{dname}\"{counts}"
        # 把 KG 命中的实体附加到对应的图行
        hits = kg_hits_by_diag.get(dname, set())
        if hits:
            line += f"  [matched: {', '.join(sorted(hits)[:5])}]"
        lines.append(line)
    return "\n".join(lines)


async def _analyze_scope(
    instructions: str,
    diagrams: list[dict],
    index: dict,
    llm,  # BaseAgentsLLM or compatible, must have .ainvoke()
    project_file: str = "",
) -> dict:
    """Phase 1: 轻量 LLM 调用，分析优化任务的精确范围。

    返回 dict:
        target_keys: list[str]  — 需要修改的图，格式 "type:name"，空=全部
        change_type: str        — "modify" | "add" | "restructure" | "full"
        guides_needed: list[str] — ["class", "sequence", "component", "cross"]
        include_index: bool     — 是否需要完整跨图索引
        include_all_rules: bool — 是否需要全部 14 条验证规则
        output_scope: str       — "changed_only" | "all"
        reasoning: str          — 一句话解释

    失败时返回 None（调用方应回退到完整 prompt）。
    """
    _logger = logging.getLogger(__name__)
    summary = _build_project_summary(diagrams, index, project_file, instructions)

    scope_prompt = f"""Analyze this UML design task and return ONLY a JSON object (no other text).

Project Summary:
{summary}

User Instruction: "{instructions}"

Return JSON with these fields:
{{
  "target_keys": ["type:name", ...],
  "change_type": "modify" | "add" | "restructure" | "full",
  "guides_needed": ["class", "sequence", "component", "cross"],
  "include_index": true | false,
  "include_all_rules": true | false,
  "output_scope": "changed_only" | "all",
  "reasoning": "one brief line"
}}

Rules:
- target_keys: list "type:name" for each diagram the user wants to modify. Empty array or omitted = ALL diagrams.
- change_type: "modify" for small edits inside existing diagrams, "add" for creating new diagrams,
  "restructure" for major refactoring (moving items between diagrams), "full" for comprehensive optimization.
- guides_needed: only include the design guides actually relevant. "cross" guide is only needed when
  cross-diagram references (component_id, class_ref, interfaces) change.
- include_index: true only when the task involves cross-diagram relationships. false for single-diagram edits.
- include_all_rules: true for multi-diagram or architectural tasks. false for single-diagram minor edits.
- output_scope: "changed_only" means only output the diagrams that actually change.
  "all" means output all diagrams of the affected types.

Only output the JSON object."""

    try:
        # Phase 1 scope analysis uses the same configured model as the main
        # optimization call so model selection remains stable and cacheable.
        raw_scope = await llm.ainvoke(
            [{"role": "user", "content": scope_prompt}],
            temperature=0.1, max_tokens=1000,
        )
        raw = raw_scope
        _logger.info("[scope_analysis] raw (%d chars): %.200s", len(raw), raw)
        if not raw or not raw.strip():
            _logger.warning("[scope_analysis] empty LLM response, falling back to full prompt")
            return None
        # 清理 + 解析
        cleaned = clean_llm_json_response(raw)
        if not cleaned or not cleaned.strip():
            _logger.warning("[scope_analysis] empty after cleaning, raw=%.200s", raw)
            return None
        scope = json.loads(cleaned)
        _logger.info(
            "[scope_analysis] %s — targets=%s, guides=%s, output=%s, reasoning=%s",
            instructions[:60],
            scope.get("target_keys", []),
            scope.get("guides_needed", []),
            scope.get("output_scope", "all"),
            scope.get("reasoning", ""),
        )
        return scope
    except json.JSONDecodeError:
        _logger.warning("[scope_analysis] JSON parse failed, raw=%.300s", raw)
        return None
    except Exception:
        _logger.exception("[scope_analysis] Failed, will fall back to full prompt")
        return None


# ── 精简的图索引：只包含指定图涉及的实体 ──



def _build_global_prompt(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    index: dict | None = None,
    scope: dict | None = None,
) -> tuple[str, str, bool]:
    """Build the shared prompt + system_prompt for global optimization.

    Accepts a list of existing diagrams as optional reference context and an
    optional pre-built cross-diagram reference index.

    If ``scope`` is provided (from _analyze_scope), the prompt is tailored:
    only relevant design guides + diagrams + index + rules are included.

    Returns (prompt, full_system, is_empty).
    Used by both optimize_project and optimize_project_stream.
    """
    _logger = logging.getLogger(__name__)

    # ── Parse scope ──
    target_keys: set[str] = set()
    guides_needed: set[str] = {"class", "sequence", "component", "cross"}
    include_index = True
    include_all_rules = True
    output_scope = "all"

    if scope:
        # Phase 1 告诉我们的精确范围
        target_keys = set(scope.get("target_keys") or [])
        guides_needed = set(scope.get("guides_needed", ["class", "sequence", "component", "cross"]))
        include_index = scope.get("include_index", True)
        include_all_rules = scope.get("include_all_rules", True)
        output_scope = scope.get("output_scope", "all")
        _logger.info(
            "[prompt] Scope: targets=%s, guides=%s, index=%s, all_rules=%s, output=%s",
            target_keys or "(all)",
            guides_needed,
            include_index,
            include_all_rules,
            output_scope,
        )

    # ── Load design guides (only those needed) ──
    guide_map = {
        "class": "class_diagram",
        "sequence": "sequence_diagram",
        "component": "component_diagram",
        "cross": "cross_diagram",
    }
    # Detect which diagram types the project already has
    existing_types = _detect_existing_types(diagrams or [])
    _logger.info("[prompt] Existing diagram types: %s", existing_types)

    guide_parts = []
    for gkey, gfile in guide_map.items():
        if gkey in guides_needed:
            content = _load_guide(gfile)
            if content:
                # 按需加载示例：项目已有该类型图 → 跳过示例，省 token
                if gkey in ("class", "sequence", "component") and gkey not in existing_types:
                    example = _load_example(gfile)
                    if example:
                        content += "\n\n" + example
                        _logger.info("[prompt] Appended %s example — project has no existing %s diagram", gkey, gkey)
                guide_parts.append(content)
    global_guide = "\n\n".join(guide_parts) if guide_parts else ""

    # ── Select diagram data ──
    _type_labels = {"class": "Class Diagram", "sequence": "Sequence Diagram",
                    "component": "Component Diagram"}
    parts = []
    if diagrams:
        for d in diagrams:
            dtype = d.get("diagram_type") or d.get("type") or "class"
            dname = d.get("name", "Untitled")
            _inner = d.get("data") or {}
            has_content = False
            if dtype in ("class",):
                has_content = bool(d.get("classes") or d.get("relations")
                                   or _inner.get("classes") or _inner.get("relations"))
            elif dtype in ("sequence",):
                has_content = bool(d.get("lifelines") or d.get("messages")
                                   or _inner.get("lifelines") or _inner.get("messages"))
            elif dtype in ("component",):
                has_content = bool(d.get("components") or d.get("comp_relations")
                                   or _inner.get("components") or _inner.get("comp_relations"))
            else:
                has_content = bool(d.get("classes") or d.get("lifelines") or d.get("components")
                                   or _inner.get("classes") or _inner.get("lifelines") or _inner.get("components"))
            if not has_content:
                continue

            key = f"{dtype}:{dname}"
            is_target = (not target_keys) or (key in target_keys)
            label = _type_labels.get(dtype, f"{dtype} Diagram")

            if is_target:
                # 完整 JSON
                parts.append(f"""## {label} \"{dname}\":
```json
{json.dumps(d, indent=2, ensure_ascii=False)}
```""")
            else:
                # 摘要行
                parts.append(f"- {label} \"{dname}\" ({dtype}) — see index for structure")

    is_empty = len(parts) == 0

    # ── New-format JSON example ──
    _json_example = """```json
{{
  "diagrams": [
    {{
      "type": "component",
      "name": "System Architecture",
      "component_id": "",
      "data": {{
        "components": [{{"id": "...", "name": "...", "x": 100, "y": 100, "width": 200, "height": 160, "parent_id": "", "provided_interfaces": [], "required_interfaces": []}}],
        "comp_relations": [{{"id": "...", "source": "...", "target": "...", "type": "dependency"}}]
      }}
    }},
    {{
      "type": "class",
      "name": "Domain Model",
      "component_id": "comp_xxx",
      "data": {{
        "name": "Domain Model",
        "classes": [{{"id": "...", "name": "...", "stereotype": "class", "attributes": [...], "methods": [...], "position": {{"x": 100, "y": 100}}, "size": {{"width": 200, "height": 150}}, "note": "", "provided_interfaces": [], "required_interfaces": []}}],
        "relations": [{{"id": "...", "source": "...", "target": "...", "type": "association", "multiplicity_source": "", "multiplicity_target": "", "role_name": "", "note": ""}}]
      }}
    }},
    {{
      "type": "sequence",
      "name": "Main Flow",
      "component_id": "comp_xxx",
      "data": {{
        "name": "Main Flow",
        "lifelines": [{{"id": "...", "name": "...", "x": 100, "class_ref": "", "activations": []}}],
        "messages": [{{"id": "...", "from_lifeline": "...", "to_lifeline": "...", "label": "method()", "type": "sync", "order": 1, "y": 190, "note": ""}}],
        "fragments": []
      }}
    }}
  ],
  "consistency_report": [
    {{"severity": "error|warning", "msg": "描述信息"}}
  ],
  "changes_summary": "Created new design from requirements",
  "diff": "All diagrams generated from scratch",
  "design_constraints": {{
    "must_preserve": ["User 和 Order 的 1..* association 关系不可删除"],
    "immutable_entities": ["User", "Order"],
    "design_rationale": "采用策略模式支持多种支付方式扩展"
  }}
}}
```"""

    # ── Index block (focused or full) ──
    if include_index and index:
        if target_keys:
            focused_index = _build_focused_index(diagrams or [], target_keys)
            index_block = _format_index_for_prompt(focused_index)
        else:
            index_block = _format_index_for_prompt(index)
    else:
        index_block = ""

    # ── Output scope hint ──
    output_hint = ""
    if output_scope == "changed_only" and target_keys:
        names = [k.split(":", 1)[1] for k in target_keys]
        output_hint = (
            f"\n\n## IMPORTANT: Only output diagrams that actually changed.\n"
            f"The user only asked you to modify: {', '.join(names)}.\n"
            f"Your \"diagrams\" array should contain ONLY those diagrams with changes.\n"
            f"Do NOT re-output unchanged diagrams — this saves time and avoids truncation.\n"
        )

    if is_empty:
        prompt = f"""You are designing a complete UML system from scratch based on requirements below.
Follow this structured generation workflow to ensure every component gets proper diagrams.

## Generation Workflow

### Step 1: Component Diagram (System Topology)
Create a component diagram FIRST. Each component node gets a meaningful "id"
(e.g., "comp_gateway", "comp_service", "comp_repo"). These IDs are the anchors
that ALL subsequent diagrams reference via their component_id field.

### Step 2: For EACH component, create its class diagram(s)
Pick a component's "id" from Step 1, set it as "component_id". Example:
```json
{{"type": "class", "name": "Gateway Layer", "component_id": "comp_gateway", "data": {{...}}}}
```
A single component may need MULTIPLE class diagrams for different layers
(e.g., "Domain Model" + "Service Layer" + "DTO Definitions").
ALL use the SAME component_id.

### Step 3: For EACH component, create its sequence diagram(s)
Same pattern — pick the "component_id", create diagrams for key interaction flows.
One component may need multiple sequence diagrams for different scenarios
(e.g., "Auth Flow" + "Error Recovery" + "Async Event Handling").

## Design Requirements:
{instructions or "Design a well-structured software system with clear class hierarchy, interaction flows, and component architecture."}

## Output Format
Return a JSON object with a "diagrams" array. Each entry has "type" (class/sequence/component),
"name", optional "component_id", and "data" (the full diagram content).
Generate component diagrams FIRST, then class, then sequence.
{_json_example}
Only output the JSON object, no other text.
"""
    else:
        prompt = f"""Cross-validate and optimize the following UML diagrams as a complete system design.

{index_block}

## Existing Diagram Data:
{chr(10).join(parts)}

{output_hint}
## User Instructions:
{instructions or "Overall system optimization: improve consistency, reduce duplication, ensure cross-diagram coherence"}

Return a JSON object with a "diagrams" array. Each entry has "type" (class/sequence/component),
"name", optional "component_id", and "data" (the full diagram content).
Match existing diagrams by "type" + "name", updating them; create new entries for new diagrams.
Include "consistency_report", "changes_summary", "diff", and "design_constraints" fields.
Only output the JSON object, no other text.
"""

    full_system = (global_guide + "\n\nYou are an expert software architect specializing in multi-view UML system design. Cross-validate diagrams for consistency.") if global_guide else "You are an expert software architect specializing in multi-view UML system design. Cross-validate diagrams for consistency."

    return prompt, full_system, is_empty




__all__ = [
    "_load_guide",
    "_load_example",
    "_fetch_kg_hits",
    "_build_project_summary",
    "_analyze_scope",
    "_build_global_prompt",
]
