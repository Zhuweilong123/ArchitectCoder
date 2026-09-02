"""UML common infrastructure: optimize_v2/V1 shared functions.

Extracted from code_generator.py (2026-08-03).

Provides:
  _build_reference_index   — cross-diagram entity index for prompt & validation
  _format_index_for_prompt — compact markdown render of the index
  _load_guide              — load design-guide markdown files
  _fetch_kg_hits           — fetch knowledge-graph hits (if available)
  _build_project_summary   — compact project summary for scope analysis
  _analyze_scope           — Phase 1: lightweight LLM call to scope the task
  _build_focused_index     — filter index to only relevant target diagrams
  _build_global_prompt     — assemble the Phase 2 optimization prompt
  _normalize_optimize_result — coerce LLM output into canonical form
  _fuzzy_match_class       — fuzzy-match class references to real IDs
  _apply_auto_fixes        — programmatic fixes for known cross-ref issues
  _validate_cross_references — post-validation: check class_ref, component_id, interface consistency
  _normalize_llm_output    — normalise enum values / field names to Pydantic model
"""

import json
import logging
import os
import sys
from pathlib import Path as _Path
from app.core.json_utils import clean_llm_json_response

def _build_reference_index(diagrams: list[dict]) -> dict:
    """Build a structured cross-diagram reference index for prompt injection and post-validation.

    Scans all diagrams and extracts classes, lifelines, components, and their
    cross-references. Also flags orphan lifelines (no class_ref) and unlinked
    diagrams (no component_id).

    Returns a dict with keys: classes, lifelines, components, diagram_links,
    orphan_lifelines, unlinked_diagrams.

    Each component entry includes linkage statistics (linked_class_count,
    linked_seq_count, linked_class_names, linked_seq_names) computed
    after scanning all diagrams.
    """
    index: dict = {
        "classes": {},       # class_id → {name, methods[], provided[], required[]}
        "lifelines": {},     # lifeline_id → {name, class_ref}
        "components": {},    # component_id → {name, provided[], required[], ...linkage stats}
        "diagram_links": [], # [{type, name, component_id}]
        "orphan_lifelines": [],    # lifeline_ids with empty class_ref
        "unlinked_diagrams": [],   # diagram names with empty component_id
    }

    for d in (diagrams or []):
        dtype = d.get("diagram_type") or d.get("type") or "class"
        dname = d.get("name", "Untitled")
        # Read from both data-wrapped and top-level formats
        data = d.get("data") or d
        cid = d.get("component_id") or data.get("component_id", "")
        # Merge top-level fields into data for unified access
        if "classes" not in data and "classes" in d:
            data = {**data, "classes": d["classes"], "relations": d.get("relations", [])}
        if "lifelines" not in data and "lifelines" in d:
            data = {**data, "lifelines": d["lifelines"], "messages": d.get("messages", [])}
        if "components" not in data and "components" in d:
            data = {**data, "components": d["components"], "comp_relations": d.get("comp_relations", [])}

        # Track diagram-level links
        link_entry = {"type": dtype, "name": dname, "component_id": cid}
        index["diagram_links"].append(link_entry)
        if not cid:
            index["unlinked_diagrams"].append(dname)

        # ── Class diagram data ──
        for cls in data.get("classes") or []:
            cid_key = cls.get("id", "")
            if cid_key:
                methods = []
                for m in cls.get("methods") or []:
                    params = m.get("params", "")
                    method_sig = f"{m.get('name', '')}({params})"
                    methods.append(method_sig)
                index["classes"][cid_key] = {
                    "name": cls.get("name", ""),
                    "methods": methods,
                    "provided": list(cls.get("provided_interfaces") or []),
                    "required": list(cls.get("required_interfaces") or []),
                }

        # ── Sequence diagram data ──
        for ll in data.get("lifelines") or []:
            ll_id = ll.get("id", "")
            if ll_id:
                cref = ll.get("class_ref", "")
                index["lifelines"][ll_id] = {
                    "name": ll.get("name", ""),
                    "class_ref": cref,
                }
                if not cref:
                    index["orphan_lifelines"].append(ll_id)

        # ── Component diagram data ──
        for comp in data.get("components") or []:
            comp_id = comp.get("id", "")
            if comp_id:
                index["components"][comp_id] = {
                    "name": comp.get("name", ""),
                    "provided": list(comp.get("provided_interfaces") or []),
                    "required": list(comp.get("required_interfaces") or []),
                    "parent_id": comp.get("parent_id", ""),
                }

    # ── Enrich components with linkage statistics ──
    for cid, cinfo in index["components"].items():
        linked_class = [dl for dl in index["diagram_links"]
                        if dl["component_id"] == cid and dl["type"] == "class"]
        linked_seq = [dl for dl in index["diagram_links"]
                      if dl["component_id"] == cid and dl["type"] == "sequence"]
        cinfo["linked_class_count"] = len(linked_class)
        cinfo["linked_seq_count"] = len(linked_seq)
        cinfo["linked_class_names"] = [dl["name"] for dl in linked_class]
        cinfo["linked_seq_names"] = [dl["name"] for dl in linked_seq]
        # Derive coverage status
        has_class = len(linked_class) > 0
        has_seq = len(linked_seq) > 0
        if has_class and has_seq:
            cinfo["coverage"] = "complete"       # ✅
        elif has_class or has_seq:
            cinfo["coverage"] = "partial"         # ⚠️
        else:
            cinfo["coverage"] = "missing"         # ❌

    return index


def _format_index_for_prompt(index: dict) -> str:
    """Format the reference index as a compact markdown block for LLM prompt injection."""
    lines = ["## Cross-Diagram Reference Index", ""]

    # Class Directory
    if index["classes"]:
        lines.append("### Class Directory")
        for cid, cinfo in sorted(index["classes"].items(), key=lambda x: x[1]["name"]):
            methods_str = ", ".join(cinfo["methods"][:8]) or "(none)"
            if len(cinfo["methods"]) > 8:
                methods_str += f" ... (+{len(cinfo['methods']) - 8} more)"
            ifaces = []
            if cinfo["provided"]:
                ifaces.append(f"◉ provides: [{', '.join(cinfo['provided'])}]")
            if cinfo["required"]:
                ifaces.append(f"◡ requires: [{', '.join(cinfo['required'])}]")
            iface_str = "  |  " + "  ".join(ifaces) if ifaces else ""
            lines.append(f"  `{cid}` **{cinfo['name']}** — methods: {methods_str}{iface_str}")
        lines.append(f"  ({len(index['classes'])} classes total)")
        lines.append("")

    # Lifeline → Class Mapping
    if index["lifelines"]:
        lines.append("### Lifeline → Class Mapping")
        for lid, linfo in sorted(index["lifelines"].items(), key=lambda x: x[1]["name"]):
            if linfo["class_ref"]:
                cls_name = index["classes"].get(linfo["class_ref"], {}).get("name", "?")
                lines.append(f"  `{lid}` **{linfo['name']}** → `{linfo['class_ref']}` ({cls_name})")
            else:
                lines.append(f"  `{lid}` **{linfo['name']}** → ⚠ NO CLASS_REF")
        lines.append(f"  ({len(index['lifelines'])} lifelines total)")
        lines.append("")

    # ── Component Manifest (with coverage status) ──
    if index["components"]:
        lines.append("### Component Manifest — Diagram Coverage Status")
        lines.append("")
        lines.append("Each component below is a node in the component diagram. Use its `id` as")
        lines.append("the `component_id` field when creating or updating class/sequence diagrams.")
        lines.append("Aim for each component to have at least one class diagram (internal structure)")
        lines.append("and at least one sequence diagram (key interactions).")
        lines.append("")
        # Sort: ❌ missing first, then ⚠️ partial, then ✅ complete
        cov_order = {"missing": 0, "partial": 1, "complete": 2}
        sorted_comps = sorted(
            index["components"].items(),
            key=lambda x: (cov_order.get(x[1].get("coverage", "missing"), 0), x[1]["name"])
        )
        emoji = {"missing": "❌", "partial": "⚠️", "complete": "✅"}
        for cid, cinfo in sorted_comps:
            status = emoji.get(cinfo.get("coverage", "missing"), "❌")
            class_str = ", ".join(f'"{n}"' for n in cinfo.get("linked_class_names", [])) or "(none)"
            seq_str = ", ".join(f'"{n}"' for n in cinfo.get("linked_seq_names", [])) or "(none)"
            ifaces = []
            if cinfo.get("provided"):
                ifaces.append(f"◉ provides: [{', '.join(cinfo['provided'])}]")
            if cinfo.get("required"):
                ifaces.append(f"◡ requires: [{', '.join(cinfo['required'])}]")
            iface_str = "  |  " + "  ".join(ifaces) if ifaces else ""
            parent_info = f"  |  child of: `{cinfo['parent_id']}`" if cinfo.get("parent_id") else ""
            lines.append(f"  {status} `{cid}` **{cinfo['name']}**{iface_str}{parent_info}")
            lines.append(f"       Class diagrams ({cinfo.get('linked_class_count', 0)}): {class_str}")
            lines.append(f"       Sequence diagrams ({cinfo.get('linked_seq_count', 0)}): {seq_str}")
        lines.append("")
        lines.append(f"  **Legend:** ✅ = has both diagram types  |  ⚠️ = needs one type  |  ❌ = needs both")
        lines.append("")
        # Coverage summary
        cov_counts = {"missing": 0, "partial": 0, "complete": 0}
        for _, cinfo in index["components"].items():
            cov_counts[cinfo.get("coverage", "missing")] += 1
        lines.append(f"  ({len(index['components'])} components: "
                     f"{cov_counts['complete']} complete, "
                     f"{cov_counts['partial']} partial, "
                     f"{cov_counts['missing']} need diagrams)")
        lines.append("")

    # Issues
    issues = []
    if index["orphan_lifelines"]:
        issues.append(f"⚠ {len(index['orphan_lifelines'])} lifelines without class_ref: {', '.join(index['orphan_lifelines'])}")
    if index["unlinked_diagrams"]:
        issues.append(f"⚠ {len(index['unlinked_diagrams'])} diagrams without component_id: {', '.join(index['unlinked_diagrams'])}")
    # Coverage gaps
    for cid, cinfo in sorted(index["components"].items(), key=lambda x: x[1]["name"]):
        cov = cinfo.get("coverage", "missing")
        if cov == "missing":
            issues.append(f"❌ Component `{cid}` ({cinfo['name']}) has NO linked class or sequence diagrams")
        elif cov == "partial":
            missing_type = "sequence" if cinfo.get("linked_seq_count", 0) == 0 else "class"
            issues.append(f"⚠️ Component `{cid}` ({cinfo['name']}) needs a {missing_type} diagram")
    if issues:
        lines.append("### Issues Detected in Existing Diagrams")
        lines.append("(These should be addressed in your output to improve diagram coverage)")
        for issue in issues:
            lines.append(f"  {issue}")
        lines.append("")

    return "\n".join(lines)


# ── Scope Analysis (Phase 1) ─────────────────────────────
# 轻量级 LLM 调用，分析任务范围，决定哪些图/指南/规则需要加载。

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

def _fetch_kg_hits(project_file: str, instructions: str,
                   out: dict[str, set[str]]) -> None:
    """同步封装：提取 KG BM25 命中实体 → 图映射，存入 out dict。

    对 instructions 进行多粒度搜索（全指令 + 短关键词），
    确保不同表达方式都能命中实体。"""
    try:
        project_id = os.path.splitext(os.path.basename(project_file))[0]
        _settings = get_settings()
        kg_db = os.path.normpath(os.path.abspath(
            os.path.join(os.path.dirname(_settings.uml_dir), "data", "knowledge_graph.db"),
        ))
        if not os.path.isfile(kg_db):
            return
        from knowledge_graph.retriever import GraphRetriever
        _retriever = GraphRetriever(db_path=kg_db)
        try:
            # ── 多粒度搜索：全指令 + 2-3 词短词组 ──
            queries = [instructions]
            words = instructions.split()
            if len(words) >= 2:
                queries.extend([" ".join(words[i:i+3]) for i in range(0, len(words), 2)])
            # 去重但保序
            seen = set()
            queries = [q for q in queries if q and not (q in seen or seen.add(q))]
            queries = queries[:5]  # 最多 5 条

            for query in queries:
                _results = _retriever.db.search_bm25(
                    project_id=project_id,
                    query=query,
                    top_k=6,
                )
                for _r in _results:
                    # 如果实体本身是 diagram，直接加入
                    if _r.node.node_type.value == "diagram":
                        out.setdefault(_r.node.name, set()).add(
                            f"{_r.node.name}({_r.node.node_type.value})"
                        )
                    # 沿所有 incoming edges 反向查找，直到找到 diagram
                    # （fragment 用 'fragments' 边，不是 'contains'，所以不限 edge_type）
                    _to_find = {(_r.node.id, _r.node.name, _r.node.node_type.value)}
                    _seen_ids = set()
                    while _to_find:
                        _cur_id, _cur_name, _cur_type = _to_find.pop()
                        if _cur_id in _seen_ids:
                            continue
                        _seen_ids.add(_cur_id)
                        # 反向查找，不限制 edge_type（fragment 用 'fragments' 边，不是 'contains'）
                        _incoming = _retriever.db.conn.execute(
                            "SELECT e.source_id, s.name, s.node_type FROM kg_edges e "
                            "JOIN kg_nodes s ON e.source_id = s.id "
                            "WHERE e.target_id = ?",
                            (_cur_id,),
                        ).fetchall()
                        for _src_id, _src_name, _src_type in _incoming:
                            if _src_type == "diagram":
                                out.setdefault(_src_name, set()).add(
                                    f"{_cur_name}({_cur_type})"
                                )
                            elif _src_type == "project":
                                pass  # 继续往上走找到 diagram
                            else:
                                _to_find.add((_src_id, _src_name, _src_type))
        finally:
            _retriever.close()
    except Exception:
        pass  # KG 不可用时静默退化


# ── 项目摘要 ────────────────────────────────────────────

def _build_project_summary(
    diagrams: list[dict], index: dict,
    project_file: str = "",
    instructions: str = "",
) -> str:
    """构建轻量项目摘要（<800 chars），供 Phase 1 scope 分析用。

    如果 project_file 存在且 knowledge_graph.db 可用，会用 BM25 搜索
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

def _build_focused_index(diagrams: list[dict], target_keys: set[str]) -> dict:
    """构建只含目标图中实体的精简索引。target_keys 为 {"type:name", ...} 集合。"""
    index = _build_reference_index(diagrams)

    if not target_keys:
        return index  # 空 = 全部

    # 收集目标图中出现的 class_id / lifeline_id / component_id
    relevant_classes: set[str] = set()
    relevant_lifelines: set[str] = set()
    relevant_components: set[str] = set()

    for d in diagrams:
        dtype = d.get("diagram_type") or d.get("type") or "class"
        dname = d.get("name", "Untitled")
        key = f"{dtype}:{dname}"
        if key not in target_keys:
            continue
        data = d.get("data") or d
        for cls in (data.get("classes") or d.get("classes", [])):
            relevant_classes.add(cls.get("id", ""))
        for ll in (data.get("lifelines") or d.get("lifelines", [])):
            relevant_lifelines.add(ll.get("id", ""))
        for comp in (data.get("components") or d.get("components", [])):
            relevant_components.add(comp.get("id", ""))

    # 过滤索引
    return {
        **index,
        "classes": {k: v for k, v in index["classes"].items() if k in relevant_classes},
        "lifelines": {k: v for k, v in index["lifelines"].items() if k in relevant_lifelines},
        "components": {k: v for k, v in index["components"].items() if k in relevant_components},
    }


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


def _normalize_optimize_result(raw: dict) -> dict:
    """Convert LLM response to canonical format with a ``diagrams`` array.

    Handles both:
    - New format: ``{"diagrams": [...], "consistency_report": [...], ...}``
    - Old format: ``{"optimized": {"class": {...}, "sequence": {...}, "component": {...}}, ...}``
    """
    if "diagrams" in raw and isinstance(raw["diagrams"], list):
        return raw  # already new format

    if "optimized" in raw:
        optimized = raw["optimized"]
        diagrams = []
        for dtype in ("class", "sequence", "component"):
            opt_data = optimized.get(dtype)
            if opt_data and isinstance(opt_data, dict):
                # A non-empty type-specific diagram — include it
                diagrams.append({
                    "type": dtype,
                    "name": opt_data.get("name", dtype.capitalize()),
                    "component_id": opt_data.get("component_id", ""),
                    "data": opt_data,
                })
        return {
            "diagrams": diagrams,
            "consistency_report": raw.get("consistency_report", []),
            "changes_summary": raw.get("changes_summary", ""),
            "design_constraints": raw.get("design_constraints", {}),
            "diff": raw.get("diff", ""),
        }

    # Fallback: empty result
    return {
        "diagrams": [],
        "consistency_report": [],
        "changes_summary": "No diagrams generated",
        "design_constraints": {},
        "diff": "",
    }


def _fuzzy_match_class(ref: str, classes: dict) -> str | None:
    """Try to match an invalid class_ref to an actual class ID by name similarity."""
    if not ref or not classes:
        return None
    # Normalize: strip prefix, lowercase, remove underscores
    ref_clean = ref.lower().replace("class_", "").replace("_", "").replace("-", "")
    for cid, cinfo in classes.items():
        name_clean = cinfo["name"].lower().replace("_", "").replace("-", "")
        if name_clean == ref_clean:
            return cid
    # Also try: ref might be the class name itself (not ID)
    for cid, cinfo in classes.items():
        if cinfo["name"].lower() == ref.lower():
            return cid
    return None


def _apply_auto_fixes(result: dict, issues: list[dict]) -> None:
    """Apply auto-fixable issues directly to the result's diagrams data."""
    for issue in issues:
        if not issue.get("auto_fixed"):
            continue
        diag_idx = issue.get("_diagram_index")
        ll_id = issue.get("_lifeline_id")
        fix_to = issue.get("_fix_to")
        if diag_idx is not None and ll_id and fix_to:
            diags = result.get("diagrams", [])
            if diag_idx < len(diags):
                data = diags[diag_idx].get("data", {})
                for ll in data.get("lifelines", []):
                    if ll.get("id") == ll_id:
                        ll["class_ref"] = fix_to
                        break


def _validate_cross_references(result: dict, original_index: dict) -> list[dict]:
    """Post-validate LLM output against the reference index.

    Checks:
    1. Lifeline class_ref → valid class ID (auto-fix via fuzzy match)
    2. Message method names → class method signatures
    3. Diagram component_id → valid component ID
    4. Component provided/required interface consistency
    5. Component diagram coverage — every component should have diagrams

    Validates against the MERGED index (original + new) so cross-references
    to entities that exist in the project but weren't in the LLM's current
    output batch are not falsely flagged.

    Returns a list of {severity, type, msg, auto_fixed, ...} issue dicts.
    """
    issues = []
    opt_diagrams = result.get("diagrams", [])
    if not opt_diagrams:
        return issues

    opt_index = _build_reference_index(opt_diagrams)

    # ── Build merged index: original + new (new overrides on same key) ──
    # This ensures cross-references to entities in the full project scope
    # are validated, not just the LLM's current output subset.
    merged = {
        "classes": {**original_index.get("classes", {}), **opt_index["classes"]},
        "components": {**original_index.get("components", {}), **opt_index["components"]},
        "lifelines": {**original_index.get("lifelines", {}), **opt_index["lifelines"]},
        "diagram_links": original_index.get("diagram_links", []) + opt_index.get("diagram_links", []),
    }

    # ── Check 0: class relation source/target validity ──
    for di, diag in enumerate(opt_diagrams):
        if diag.get("type") != "class":
            continue
        data = diag.get("data", {})
        diag_classes = {c["id"]: c for c in data.get("classes", [])}
        # Collect class IDs from this diagram's output, falling back to merged
        all_class_ids = set(diag_classes.keys()) | set(merged["classes"].keys())
        for rel in data.get("relations", []):
            rel_id = rel.get("id", "?")
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            if src and src not in all_class_ids:
                # Try fuzzy match against merged classes
                match = _fuzzy_match_class(src, merged["classes"])
                if match:
                    rel["source"] = match
                    issues.append({
                        "severity": "warning", "type": "bad_relation_source",
                        "msg": f"Relation '{rel_id}' source='{src}' → auto-fixed to '{merged['classes'][match]['name']}'",
                        "auto_fixed": True,
                    })
                else:
                    issues.append({
                        "severity": "error", "type": "bad_relation_source",
                        "msg": f"Relation '{rel_id}' source='{src}' references non-existent class",
                        "auto_fixed": False,
                    })
            if tgt and tgt not in all_class_ids:
                match = _fuzzy_match_class(tgt, merged["classes"])
                if match:
                    rel["target"] = match
                    issues.append({
                        "severity": "warning", "type": "bad_relation_target",
                        "msg": f"Relation '{rel_id}' target='{tgt}' → auto-fixed to '{merged['classes'][match]['name']}'",
                        "auto_fixed": True,
                    })
                else:
                    issues.append({
                        "severity": "error", "type": "bad_relation_target",
                        "msg": f"Relation '{rel_id}' target='{tgt}' references non-existent class",
                        "auto_fixed": False,
                    })

    # ── Check 0.5: component comp_rel source/target validity ──
    for di, diag in enumerate(opt_diagrams):
        if diag.get("type") != "component":
            continue
        data = diag.get("data", {})
        diag_comps = {c["id"] for c in data.get("components", [])}
        # Also collect from merged index
        all_comp_ids = diag_comps | set(merged.get("components", {}).keys())
        for rel in data.get("comp_relations", []):
            rel_id = rel.get("id", "?")
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            if src and src not in all_comp_ids:
                issues.append({
                    "severity": "error", "type": "bad_comp_rel_source",
                    "msg": f"CompRelation '{rel_id}' source='{src}' references non-existent component (diagram #{di})",
                    "auto_fixed": False,
                })
            if tgt and tgt not in all_comp_ids:
                issues.append({
                    "severity": "error", "type": "bad_comp_rel_target",
                    "msg": f"CompRelation '{rel_id}' target='{tgt}' references non-existent component (diagram #{di})",
                    "auto_fixed": False,
                })

    # ── Check 1: class_ref validity ──
    for di, diag in enumerate(opt_diagrams):
        if diag.get("type") != "sequence":
            continue
        data = diag.get("data", {})
        for ll in data.get("lifelines", []):
            ll_id = ll.get("id", "")
            cref = ll.get("class_ref", "")
            if not cref:
                # Missing class_ref — check if a class with matching name exists
                ll_name = ll.get("name", "")
                if ll_name:
                    match = _fuzzy_match_class(ll_name, merged["classes"])
                    if match:
                        ll["class_ref"] = match
                        issues.append({
                            "severity": "info", "type": "missing_class_ref",
                            "msg": f"Lifeline '{ll_name}' had no class_ref, auto-assigned to '{merged['classes'][match]['name']}'",
                            "auto_fixed": True,
                            "_diagram_index": di, "_lifeline_id": ll_id, "_fix_to": match,
                        })
                continue
            if cref not in merged["classes"]:
                match = _fuzzy_match_class(cref, merged["classes"])
                if match:
                    ll["class_ref"] = match
                    issues.append({
                        "severity": "warning", "type": "bad_class_ref",
                        "msg": f"Lifeline '{ll.get('name','')}' class_ref='{cref}' → auto-fixed to '{merged['classes'][match]['name']}'",
                        "auto_fixed": True,
                        "_diagram_index": di, "_lifeline_id": ll_id, "_fix_to": match,
                    })
                else:
                    issues.append({
                        "severity": "error", "type": "bad_class_ref",
                        "msg": f"Lifeline '{ll.get('name','')}' class_ref='{cref}' references non-existent class",
                        "auto_fixed": False,
                    })

    # ── Check 2: message method → class method ──
    for diag in opt_diagrams:
        if diag.get("type") != "sequence":
            continue
        data = diag.get("data", {})
        lifelines = {l["id"]: l for l in data.get("lifelines", [])}
        for msg in data.get("messages", []):
            target_ll = lifelines.get(msg.get("to_lifeline", ""))
            if not target_ll:
                continue
            cref = target_ll.get("class_ref", "")
            if not cref or cref not in merged["classes"]:
                continue
            cls = merged["classes"][cref]
            label = msg.get("label", "")
            if not label:
                continue
            method_name = label.split("(")[0].strip()
            if method_name and method_name not in ["return", ""]:
                cls_methods = [m.split("(")[0] for m in cls["methods"]]
                if cls_methods and method_name not in cls_methods:
                    # Only flag if the class actually has methods (not empty skeleton)
                    issues.append({
                        "severity": "warning", "type": "method_mismatch",
                        "msg": f"Message '{label}' → class '{cls['name']}' (has methods: {', '.join(cls_methods[:5])})",
                        "auto_fixed": False,
                    })

    # ── Check 3: component_id validity ──
    for diag in opt_diagrams:
        cid = diag.get("component_id", "")
        if cid and cid not in merged["components"]:
            issues.append({
                "severity": "warning", "type": "bad_component_id",
                "msg": f"Diagram '{diag.get('name','')}' component_id='{cid}' not found in component diagram",
                "auto_fixed": False,
            })

    # ── Check 4: component interface consistency ──
    # Check ALL components (merged, not just new) against linked class diagrams
    # across the full project scope (original diagram_links + new).
    for cid, comp in merged["components"].items():
        # Find linked class diagrams: both new (opt_diagrams) and original (diagram_links)
        linked_class_names = [
            dl["name"] for dl in merged["diagram_links"]
            if dl["component_id"] == cid and dl["type"] == "class"
        ]
        # Collect class data from linked diagrams in the new output
        all_class_provided = set()
        all_class_required = set()
        for lcd in opt_diagrams:
            if lcd.get("type") == "class" and lcd.get("component_id") == cid:
                lcd_classes = (lcd.get("data") or {}).get("classes", [])
                for cls in lcd_classes:
                    all_class_provided.update(cls.get("provided_interfaces", []))
                    all_class_required.update(cls.get("required_interfaces", []))
        # If no linked class diagrams exist at all, skip interface checks
        # (coverage check in Check 5 handles that case)
        if not linked_class_names and not all_class_provided and not all_class_required:
            # No class diagrams linked to this component anywhere — skip,
            # Check 5 will flag the coverage gap.
            continue
        # Component provided should be covered by class provided
        for iface in comp["provided"]:
            if iface not in all_class_provided:
                issues.append({
                    "severity": "warning", "type": "interface_mismatch",
                    "msg": f"Component '{comp['name']}' provides '{iface}' but no linked class implements it",
                    "auto_fixed": False,
                })
        # Component required should appear in class required
        for iface in comp["required"]:
            if iface not in all_class_required:
                issues.append({
                    "severity": "warning", "type": "interface_mismatch",
                    "msg": f"Component '{comp['name']}' requires '{iface}' but no linked class declares it",
                    "auto_fixed": False,
                })

    # ── Check 5: component diagram coverage ──
    # Every component (merged across original + new) should ideally have
    # at least one class diagram and one sequence diagram linked to it.
    for cid, comp in merged["components"].items():
        linked_class = [
            dl["name"] for dl in merged["diagram_links"]
            if dl["component_id"] == cid and dl["type"] == "class"
        ]
        linked_seq = [
            dl["name"] for dl in merged["diagram_links"]
            if dl["component_id"] == cid and dl["type"] == "sequence"
        ]
        if not linked_class and not linked_seq:
            issues.append({
                "severity": "warning", "type": "component_coverage",
                "msg": f"Component '{comp['name']}' ({cid}) has NO class or sequence diagrams. "
                       f"Consider adding at least a class diagram describing its internal structure.",
                "auto_fixed": False,
            })
        elif not linked_class:
            issues.append({
                "severity": "info", "type": "component_coverage",
                "msg": f"Component '{comp['name']}' ({cid}) has {len(linked_seq)} sequence diagram(s) but NO class diagram.",
                "auto_fixed": False,
            })
        elif not linked_seq:
            issues.append({
                "severity": "info", "type": "component_coverage",
                "msg": f"Component '{comp['name']}' ({cid}) has {len(linked_class)} class diagram(s) but NO sequence diagram.",
                "auto_fixed": False,
            })

    return issues

def _normalize_llm_output(data: dict) -> dict:
    """Normalize LLM output to ensure all enum values and field names match Pydantic model."""

    VIS_MAP = {
        "public": "+", "private": "-", "protected": "#",
        "+": "+", "-": "-", "#": "#",
    }

    VALID_STEREOTYPES = {"class", "interface", "abstract", "enum"}

    RELATION_TYPE_MAP = {
        "inheritance": "inheritance", "generalization": "inheritance",
        "extends": "inheritance", "composition": "composition",
        "aggregation": "aggregation", "association": "association",
        "realization": "realization", "implements": "realization",
        "dependency": "dependency", "depends": "dependency",
        "composite": "composition", "aggregate": "aggregation",
    }

    # Map alternate field names LLM might use → correct field name
    FIELD_ALIASES = {
        # Relation fields
        "source_id": "source", "target_id": "target",
        "from": "source", "to": "target",
        "from_id": "source", "to_id": "target",
        "source_mult": "multiplicity_source", "target_mult": "multiplicity_target",
        "source_multiplicity": "multiplicity_source", "target_multiplicity": "multiplicity_target",
        "mult_src": "multiplicity_source", "mult_tgt": "multiplicity_target",
        "label": "role_name",
        # Class fields
        "is_abstract_class": "stereotype",
        "class_name": "name",
    }

    # Message-field aliases (only applied when parent_key == "messages",
    # not when inside relations which also have source/target fields).
    MESSAGE_FIELD_ALIASES = {
        "source": "from_lifeline",
        "target": "to_lifeline",
        "name": "label",
        "arguments": "label",
    }

    import uuid as _uuid

    def walk(obj, parent_key=""):
        if isinstance(obj, dict):
            result = {}
            # Determine context for alias resolution
            _is_message = "from_lifeline" in obj or "to_lifeline" in obj
            _is_msg_list = parent_key == "messages"
            for k, v in obj.items():
                # Remap known alias fields (message fields use a different mapping
                # only when inside a messages array to avoid clashing with relation source/target)
                if parent_key == "messages":
                    mapped_key = MESSAGE_FIELD_ALIASES.get(k, FIELD_ALIASES.get(k, k))
                else:
                    mapped_key = FIELD_ALIASES.get(k, k)
                # For sequence messages, "label" is the correct field name (method name).
                # Only remap label→role_name in relations, not messages.
                if k == "label" and (_is_message or _is_msg_list):
                    mapped_key = "label"  # keep as-is for messages
                if k == "visibility" and isinstance(v, str):
                    v = VIS_MAP.get(v.lower(), "+")
                elif mapped_key == "stereotype":
                    if isinstance(v, str) and v.lower() in VALID_STEREOTYPES:
                        v = v.lower()
                    else:
                        v = "class"
                elif k == "is_abstract_class" and v is True:
                    mapped_key = "stereotype"
                    v = "abstract"
                elif mapped_key == "type" and isinstance(v, str):
                    v = RELATION_TYPE_MAP.get(v.lower(), "association")
                elif k in ("is_static", "is_abstract") and v is None:
                    v = False
                elif k == "default_value" and v == "":
                    v = None
                result[mapped_key] = walk(v, mapped_key)
            # Auto-generate missing IDs for relations
            if parent_key == "relations" and "id" not in result:
                result["id"] = f"rel_{_uuid.uuid4().hex[:8]}"
            if parent_key == "classes" and "id" not in result:
                result["id"] = f"class_{_uuid.uuid4().hex[:8]}"
            return result
        elif isinstance(obj, list):
            return [walk(item, parent_key) for item in obj]
        return obj

    result = walk(data)

    # Detect if LLM zeroed out all positions (common failure mode)
    classes = result.get("classes", [])
    if classes and all(
        isinstance(c, dict) and c.get("position", {}).get("x", 0) == 0
        and c.get("position", {}).get("y", 0) == 0
        for c in classes
    ):
        logging.getLogger(__name__).warning(
            "[Optimize] LLM returned all-zero positions for classes — positions may have been lost"
        )

    return result


# ── JSON 元素流式提取器 ──────────────────────────────────

class JsonElementExtractor:
    """从流式 JSON 文本中通过 brace 深度追踪提取完整 JSON 对象。

    Elements at depth 4 inside arrays (classes, relations, lifelines, etc.) are
    extracted and classified. Nested sub-objects at depth 5+ (attributes, methods)
    are correctly ignored.

    Extracted from uml_optimizer.py (2026-08-04) — shared by v2 SSE and Agent paths.
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
        self._current_diagram_name = None  # current diagram name for element routing
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
                                self._current_diagram_name = _dname
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
                                # Inject diagram_name for frontend routing
                                if self._current_diagram_name:
                                    obj["diagram_name"] = self._current_diagram_name
                                elements.append((tp, json.dumps(obj, ensure_ascii=False)))
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
