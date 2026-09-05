"""UML result normalization and cross-diagram validation."""

from __future__ import annotations

import logging

from .uml_index import _build_reference_index

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
                    # ``type`` is shared by class relations, component
                    # relations, sequence messages and fragments.  Do not
                    # apply the class-relation alias map to every context:
                    # in particular, delegation is a valid component
                    # relation but not a UML class relation.
                    value = v.lower()
                    if parent_key == "comp_relations":
                        v = value if value in {"dependency", "delegation"} else "dependency"
                    elif parent_key == "messages":
                        v = value if value in {"sync", "async", "return", "simple", "self"} else "sync"
                    elif parent_key == "fragments":
                        v = value if value in {"loop", "alt", "opt", "break", "par", "critical", "neg"} else "loop"
                    else:
                        v = RELATION_TYPE_MAP.get(value, "association")
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



__all__ = [
    "_normalize_optimize_result",
    "_fuzzy_match_class",
    "_apply_auto_fixes",
    "_validate_cross_references",
    "_normalize_llm_output",
]
