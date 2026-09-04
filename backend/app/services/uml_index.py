"""Cross-diagram UML indexing and focused context selection."""

from __future__ import annotations

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




__all__ = [
    "_build_reference_index",
    "_format_index_for_prompt",
    "_build_focused_index",
]
