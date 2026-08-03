"""Code generation service – generates code in 12 languages from UML diagrams."""

import json
import logging
import os
import sys
from pathlib import Path as _Path
from app.models.uml import UmlDiagram
from app.services.llm_service import chat, chat_stream
from app.services.tools import clean_llm_json_response
from app.services.layout_engine import auto_layout
from app.core.config import get_settings

SUPPORTED_LANGUAGES = [
    "python", "java", "typescript", "javascript", "csharp", "cpp",
    "go", "rust", "ruby", "swift", "kotlin", "php",
]

LANGUAGE_TEMPLATES: dict[str, str] = {
    "python": """
# Python code generated from UML diagram: {diagram_name}
# Stereotype mapping:
#   class     -> regular class
#   interface -> ABC (abstract base class)
#   abstract  -> ABC with @abstractmethod
#   enum      -> Enum subclass
""",
    "java": """
// Java code generated from UML diagram: {diagram_name}
""",
    "typescript": """
// TypeScript code generated from UML diagram: {diagram_name}
""",
    "cpp": """
// C++ code generated from UML diagram: {diagram_name}
""",
    "go": """
// Go code generated from UML diagram: {diagram_name}
""",
}


def _build_class_prompt(diagram: UmlDiagram, language: str) -> str:
    """Build a structured prompt for the LLM to generate code."""
    classes_desc = []
    for c in diagram.classes:
        attrs = []
        for a in c.attributes:
            static = "static " if a.is_static else ""
            attrs.append(f"    {a.visibility} {static}{a.name}: {a.type}")
        methods = []
        for m in c.methods:
            static = "static " if m.is_static else ""
            abstract = "abstract " if m.is_abstract else ""
            methods.append(f"    {m.visibility} {abstract}{static}{m.name}({m.params}): {m.return_type}")
        # Include class notes + interfaces
        note_block = ""
        if c.note.strip():
            note_block = f"\n  Business Rules: {c.note}"
        ifaces = []
        if c.provided_interfaces:
            ifaces.append(f"  ◉ Provides: {', '.join(c.provided_interfaces)}")
        if c.required_interfaces:
            ifaces.append(f"  ◡ Requires: {', '.join(c.required_interfaces)}")
        iface_block = "\n" + "\n".join(ifaces) if ifaces else ""
        classes_desc.append(
            f"Class: {c.name} (stereotype={c.stereotype}){note_block}{iface_block}\n"
            + "Attributes:\n" + "\n".join(attrs or ["    (none)"]) + "\n"
            + "Methods:\n" + "\n".join(methods or ["    (none)"])
        )

    relations_desc = []
    for r in diagram.relations:
        src_name = next((c.name for c in diagram.classes if c.id == r.source), r.source)
        tgt_name = next((c.name for c in diagram.classes if c.id == r.target), r.target)
        # Include relation metadata
        extras = []
        if r.role_name:
            extras.append(f"role={r.role_name}")
        if r.multiplicity_source or r.multiplicity_target:
            extras.append(f"mult={r.multiplicity_source}..{r.multiplicity_target}")
        if r.note.strip():
            extras.append(f"note={r.note}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        relations_desc.append(f"  {src_name} --({r.type})--> {tgt_name}{extra_str}")

    prompt = f"""Generate complete, compilable {language} code from the following UML class diagram.

## Classes:
{chr(10).join(classes_desc)}

## Relations:
{chr(10).join(relations_desc) if relations_desc else "  (none)"}

## Requirements:
- CRITICAL: Implement ALL business rules described in each class's "Business Rules" section.
  These are the core logic requirements — do NOT skip them.
- Generate a separate file for each class where appropriate.
- Follow {language} best practices and conventions.
- Implement all specified attributes, methods, and relations.
- For inheritance/realization, use proper {language} syntax.
- For composition/aggregation, use member variables.
- Add proper imports/includes.
- Return the result as a JSON object mapping filenames to file content:
```json
{{"filename1.ext": "content...", "filename2.ext": "content..."}}
```
Only output the JSON object, no other text.
"""
    return prompt


def _build_test_prompt(code_files: dict[str, str], language: str, test_cases: str = "") -> str:
    """Build a prompt to generate tests for the given code, using Excel test case requirements."""
    code_block = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in code_files.items()
    )

    # ── Extract actual API signatures from source code ──
    api_sigs = _extract_api_signatures(code_files, language)

    # ── Truncation detection ──
    MAX_TEST_CASES = 10000
    MAX_CODE_LEN = 8000
    tc_truncated = len(test_cases) > MAX_TEST_CASES if test_cases else False
    code_truncated = len(code_block) > MAX_CODE_LEN

    if test_cases and test_cases.strip():
        test_cases_section = test_cases[:MAX_TEST_CASES]
        code_section = code_block[:MAX_CODE_LEN]

        trunc_warning = ""
        if tc_truncated or code_truncated:
            trunc_warning = "⚠️ WARNING: Some data was truncated due to length limits. "
            if tc_truncated:
                trunc_warning += f"Test cases truncated from {len(test_cases)} to {MAX_TEST_CASES} chars. "
            if code_truncated:
                trunc_warning += f"Source code truncated from {len(code_block)} to {MAX_CODE_LEN} chars. "
            trunc_warning += "Use the API signatures below as the authoritative reference.\n\n"

        prompt = f"""You MUST generate unit tests for {language}. Follow these rules EXACTLY.

## YOUR PRIMARY TASK: ONE test function per case ID below

For EACH case ID in the test case list below, you MUST create exactly one test function.
The function name MUST be: `test_<CASE_ID>_<short_description>`

Example mapping:
  TC-OTA-001 "OtaTask.execute normal execution" → `def test_TC_OTA_001_ota_execute():`
  TC-CROW-002 "crow time window 2:00-4:00" → `def test_TC_CROW_002_crow_time_window():`
  TC-BASE-001 "subclass instantiation" → `def test_TC_BASE_001_subclass_instantiation():`

## ACTUAL SOURCE API (tests MUST match these exact signatures):
{api_sigs}

{trunc_warning}## TEST CASES (MUST cover ALL of these):
{test_cases_section}

## Complete Source Code (for understanding business logic):
{code_section}

## OUTPUT REQUIREMENTS:
1. Return ONLY a JSON object mapping filenames to file content.
2. Each test function's docstring MUST include: Case ID, test steps, and expected result.
3. Use the standard testing framework for {language}.
4. Group test functions logically — one test file per source module.
5. CRITICAL: Import paths and class/method signatures MUST match the ACTUAL SOURCE API above exactly.
   DO NOT invent parameter names — use the exact signatures shown.

Output format:
```json
{{"test_module1.ext": "code...", "test_module2.ext": "code..."}}
```
"""
    else:
        code_section = code_block[:MAX_CODE_LEN]
        prompt = f"""Generate comprehensive unit tests for the following {language} code.

## Source Code:
{code_section}

## Requirements:
- Write tests using the standard testing framework for {language}.
- Cover all public methods and edge cases.
- Each test function name MUST describe the scenario (e.g., `test_classname_method_scenario`).
- Return the result as a JSON object mapping test filenames to content:
```json
{{"test_filename.ext": "content..."}}
```
Only output the JSON object, no other text.
"""

    return prompt


def _extract_api_signatures(code_files: dict[str, str], language: str) -> str:
    """Extract importable class/func signatures from source code so LLM can match exact API."""
    import re
    lines_out = []
    for fname, content in code_files.items():
        lines_out.append(f"### {fname}")
        module_name = fname.rsplit(".", 1)[0] if "." in fname else fname
        lines_out.append(f"# Import: from {module_name} import ...")
        # Extract class definitions with constructor params
        for line in content.split("\n"):
            stripped = line.strip()
            # Class definition
            if stripped.startswith("class "):
                lines_out.append(stripped)
            # Method/function definition (top-level or class method)
            elif stripped.startswith("def ") and not stripped.startswith("def test_"):
                lines_out.append(f"  {stripped}")
        lines_out.append("")
    return "\n".join(lines_out)


def _dump_raw_llm_response(response: str, context: str, label: str = "fallback"):
    """Save raw LLM response to pipeline_log for post-mortem debugging."""
    try:
        from pathlib import Path as _P
        from datetime import datetime as _dt
        _log_d = _P(__file__).resolve().parent.parent.parent.parent / "temp" / "pipeline_log"
        _log_d.mkdir(exist_ok=True)
        _ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        _fname = f"llm_raw_{label}_{context}_{_ts}.txt"
        _fp = _log_d / _fname
        _fp.write_text(response, encoding="utf-8")
        _log = logging.getLogger(__name__)
        _log.warning(
            f"[RawLLM] {label} for '{context}': "
            f"saved {len(response)} chars → {_fp}"
        )
        return str(_fp)
    except Exception:
        return ""


def _parse_code_response(response: str, context: str = "code") -> dict[str, str]:
    """Parse LLM response into ``{filename: content}`` dict.

    1. Try direct JSON (via ``clean_llm_json_response``).
    2. Fallback: split markdown-fenced ``### filename`` blocks.
    3. Last resort: wrap entire response as ``{context}.py``.

    When the direct JSON path fails, the raw response is saved to
    ``pipeline_log/`` for post-mortem diagnostics.
    """
    import re as _re

    # ── 1. Direct JSON ──
    try:
        cleaned = clean_llm_json_response(response)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        _dump_raw_llm_response(response, context, label="json_parse_failed")

    # ── 2. Markdown-fenced blocks ──
    blocks = _re.findall(
        r'(?:###\s*|#\s*File:\s*)(\S+\.\w+)\s*\n\s*```\w*\n(.*?)```',
        response, _re.DOTALL,
    )
    if blocks:
        return {name: content.strip() for name, content in blocks}

    # Also try blocks without the ### header
    fence_blocks = _re.findall(
        r'```(\w+)\n(.*?)```', response, _re.DOTALL,
    )
    if fence_blocks:
        lang_ext = {"python": ".py", "javascript": ".js", "typescript": ".ts",
                    "java": ".java", "cpp": ".cpp", "csharp": ".cs",
                    "go": ".go", "rust": ".rs", "ruby": ".rb",
                    "swift": ".swift", "kotlin": ".kt", "php": ".php"}
        result = {}
        for i, (lang, content) in enumerate(fence_blocks):
            ext = lang_ext.get(lang, f".{lang}")
            name_match = _re.search(r'(?:class|def|func|function|struct|interface)\s+(\w+)', content)
            fname = f"{name_match.group(1).lower()}{ext}" if name_match else f"module_{i+1}{ext}"
            result[fname] = content.strip()
        return result

    # ── 3. Wrap entire response ──
    _dump_raw_llm_response(response, context, label="wrapped_as_raw")
    return {f"{context}.py": response.strip()}


async def generate_code(diagram: UmlDiagram, language: str) -> dict[str, str]:
    """Generate code files from a UML diagram."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}. Supported: {SUPPORTED_LANGUAGES}")

    prompt = _build_class_prompt(diagram, language)
    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} developer. Output only valid JSON mapping filenames to file content.",
        temperature=0.3,
        max_tokens=8192,
    )

    if not response.strip():
        _log = logging.getLogger(__name__)
        _log.warning("[CodeGen] Empty response from LLM")
        return {}

    result = _parse_code_response(response, context=diagram.name or "code")
    if not result:
        _log = logging.getLogger(__name__)
        _log.warning(f"[CodeGen] Could not parse response ({len(response)} chars): {response[:300]}")
    return result


async def generate_integrated_code(
    class_diagram: dict | None,
    sequence_diagram: dict | None,
    language: str,
    component_diagram: dict | None = None,
) -> tuple[dict, str]:
    """Generate code combining class diagram (structure) + sequence diagram (behavior)
    + component diagram (module architecture).

    Returns (files_dict, prompt_text).
    """
    _logger = logging.getLogger(__name__)

    if not class_diagram:
        return {}, ""

    has_seq = sequence_diagram and sequence_diagram.get("lifelines")
    has_comp = component_diagram and component_diagram.get("components")

    if not has_seq and not has_comp:
        from app.models.uml import UmlDiagram
        diagram = UmlDiagram(**class_diagram)
        _logger.info("[Integrated] No sequence/component diagrams — standard generation")
        return await generate_code(diagram, language), ""

    _logger.info(f"[Integrated] Generating: class{'+seq' if has_seq else ''}{'+comp' if has_comp else ''}")

    classes_text = json.dumps(class_diagram.get("classes", []), indent=2, ensure_ascii=False)

    # Sequence diagram → interaction summary
    msg_block = ""
    if has_seq:
        lifelines = sequence_diagram.get("lifelines", [])
        messages = sequence_diagram.get("messages", [])
        msg_lines = []
        for m in sorted(messages, key=lambda x: x.get("order", 0)):
            from_name = next((l["name"] for l in lifelines if l["id"] == m.get("from_lifeline")), "?")
            to_name = next((l["name"] for l in lifelines if l["id"] == m.get("to_lifeline")), "?")
            note = m.get("note", "")
            msg_lines.append(f"  {from_name} → {to_name}: {m.get('label', '')} [{m.get('type', 'sync')}]"
                             + (f"  ── {note}" if note else ""))
        msg_block = f"""## Sequence Diagram (method call chains — fill method bodies):
```
{chr(10).join(msg_lines)}
```
"""

    # Component diagram → module structure
    comp_block = ""
    if has_comp:
        comps = component_diagram.get("components", [])
        comp_lines = []
        for c in comps:
            ifaces = c.get("provided_interfaces", [])
            reqs = c.get("required_interfaces", [])
            detail = ""
            if ifaces: detail += f" provides: [{', '.join(ifaces)}]"
            if reqs: detail += f" requires: [{', '.join(reqs)}]"
            comp_lines.append(f"  {c.get('name', '?')}{' (sub-component)' if c.get('parent_id') else ''}{detail}")
        comp_block = f"""## Component Diagram (module architecture — imports and dependencies):
```
{chr(10).join(comp_lines) if comp_lines else '(none)'}
```
"""

    prompt = f"""Generate complete, compilable {language} code from the following multi-view design.

## Class Diagram (structure):
```json
{classes_text}
```
{msg_block}{comp_block}
## Requirements:
- Generate a separate file for EACH class listed above — do NOT merge classes.
- CRITICAL: If sequence diagram is provided, use it to FILL method bodies with call logic.
- If component diagram is provided, use it to organize imports and module structure.
- Follow {language} best practices. Add proper imports. Keep the public API.
- Return the result as a JSON object mapping filenames to file content:
```json
{{"file1.py": "content...", "file2.py": "content..."}}
```
Only output the JSON object, no other text.
"""
    _logger.info(f"[Integrated] Prompt ({len(prompt)} chars):\n{prompt[:2000]}")

    _logger.info(f"[Integrated] Prompt ({len(prompt)} chars)")

    # Save prompt to pipeline_log for diagnostics
    try:
        from pathlib import Path as _Path
        _log_dir = _Path(__file__).resolve().parent.parent.parent.parent / "temp" / "pipeline_log"
        _log_dir.mkdir(exist_ok=True)
        _ts = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        _prompt_file = _log_dir / f"llm_prompt_{_ts}.md"
        _prompt_file.write_text(prompt, encoding="utf-8")
        _logger.info(f"[Integrated] Prompt saved to: {_prompt_file}")
    except Exception:
        pass

    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} developer. Generate code from UML+sequence designs. Output only valid JSON.",
        temperature=0.3,
        max_tokens=8192,
    )
    if not response.strip():
        _logger.warning("[Integrated] Empty response from LLM")
        return {}, prompt
    result = _parse_code_response(response, context="integrated")
    if result:
        _logger.info(f"[Integrated] Generated {len(result)} files from class+sequence diagrams")
    else:
        _logger.warning(f"[Integrated] Could not parse response ({len(response)} chars): {response[:200]}")
    return result, prompt


async def generate_tests_per_file(
    code_files: dict[str, str], language: str, test_cases: str = "",
    diagram: dict | None = None,
) -> dict[str, str]:
    """Generate tests one source file at a time to avoid token-limit truncation.

    Each LLM call receives:
    - UML diagram JSON (global architecture context)
    - Full source of the file under test
    - API signatures of all OTHER files (dependency interfaces only)
    - Test cases filtered to match the current module

    Returns the merged dict of all generated test files.
    """
    import re as _re
    _log = logging.getLogger(__name__)

    # ── Extract API signatures for all files (lightweight dependency context) ──
    all_signatures = _extract_api_signatures(code_files, language)

    # ── Build UML context block (global architecture, compact) ──
    uml_block = ""
    if diagram:
        uml_parts = []
        for cls in (diagram.get("classes") or []):
            methods = [m.get("name", "") + "(" + m.get("params", "") + ")"
                       for m in (cls.get("methods") or [])]
            uml_parts.append(
                f"  {cls.get('name','?')} ({cls.get('stereotype','')}): "
                f"{', '.join(methods) if methods else '(no methods)'}"
            )
        if diagram.get("relations"):
            uml_parts.append("Relations:")
            for r in diagram["relations"]:
                uml_parts.append(f"  {r.get('source','?')} → {r.get('target','?')} [{r.get('type','')}]")
        uml_block = "\n".join(uml_parts) if uml_parts else ""

    source_files = sorted(code_files.keys())
    all_tests: dict[str, str] = {}
    total_modules = len([f for f in source_files if f.endswith(".py") and not f.startswith("test_")])

    for fname in source_files:
        if not fname.endswith(".py") or fname.startswith("test_"):
            continue
        module_name = fname.rsplit(".", 1)[0]

        # ── Filter test cases relevant to this module ──
        module_cases = _filter_test_cases_for_module(test_cases, module_name, _re)
        if test_cases and not module_cases:
            _log.info(f"[TestGen] No test cases match module '{module_name}', skipping")
            continue

        # ── Build prompt: UML + target source + dependency signatures + cases ──
        target_source = code_files[fname]
        dep_signatures = "\n".join(
            sig for sig in all_signatures.split("\n")
            if fname.rsplit(".", 1)[0] not in sig.lower()
        ) if all_signatures else ""

        prompt = _build_per_file_test_prompt(
            module_name=module_name,
            target_source=target_source,
            dep_signatures=dep_signatures,
            module_cases=module_cases,
            language=language,
            uml_block=uml_block,
        )

        _log.info(
            f"[TestGen] Generating tests for '{module_name}' "
            f"({len(module_cases)} cases, {len(target_source)} chars source)"
        )

        response = await chat(
            prompt=prompt,
            system_prompt=f"You are an expert {language} test engineer. Output valid JSON with exactly one test file.",
            temperature=0.3,
            max_tokens=8192,
        )
        if not response.strip():
            _log.warning(f"[TestGen] Empty response for '{module_name}'")
            continue

        result = _parse_code_response(response, context=f"test_{module_name}")
        for tfname, tcontent in result.items():
            if tfname in all_tests:
                all_tests[tfname] += "\n\n" + tcontent
            else:
                all_tests[tfname] = tcontent

    _log.info(f"[TestGen] Per-file generation complete: {len(all_tests)} test files from {total_modules} modules")
    return all_tests


def _filter_test_cases_for_module(test_cases: str, module_name: str, _re=None) -> str:
    """Extract test case lines relevant to *module_name* from the test cases text."""
    if not test_cases or not test_cases.strip():
        return ""
    if _re is None:
        import re as _re
    # Test cases are in format: "- [TC-XXX-NNN] ClassName.methodName: description"
    lines = test_cases.split("\n")
    matched = []
    # Patterns to match: case ID prefix, or class name mentioned anywhere on the line
    mod_variants = [
        module_name,
        module_name.replace("_", ""),
        module_name.upper(),
    ]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(v.lower() in lower for v in mod_variants):
            matched.append(stripped)
    return "\n".join(matched) if matched else ""


def _build_per_file_test_prompt(
    module_name: str,
    target_source: str,
    dep_signatures: str,
    module_cases: str,
    language: str,
    uml_block: str,
) -> str:
    """Build a compact prompt for generating tests for a single module."""
    has_cases = bool(module_cases and module_cases.strip())

    parts = [
        f"Generate unit tests for the **{module_name}** module in {language}.",
        "",
    ]

    if uml_block:
        parts.extend([
            "## Global Architecture (class diagram context):",
            uml_block,
            "",
        ])

    parts.extend([
        f"## Source Code for {module_name}.py (the module under test):",
        f"```{language}",
        target_source,
        "```",
        "",
    ])

    if dep_signatures.strip():
        parts.extend([
            "## Dependencies (API signatures only — for correct imports):",
            dep_signatures[:3000],
            "",
        ])

    if has_cases:
        parts.extend([
            f"## Test Cases for {module_name}:",
            "ONE test function per case ID below.",
            module_cases,
            "",
            "Function naming: `test_<CASE_ID>_<short_desc>` (replace hyphens with underscores).",
            "",
        ])

    parts.extend([
        "## Output:",
        "Return a JSON object with exactly ONE test file:",
        f'{{{{"test_{module_name}.py": "import pytest\\n..."}}}}',
        "Only output the JSON object, no other text.",
        "IMPORTANT: output ONLY ONE test file for this module — do NOT generate tests for other modules.",
    ])

    return "\n".join(parts)


async def generate_tests(
    code_files: dict[str, str], language: str, test_cases: str = "",
    diagram: dict | None = None,
) -> dict[str, str]:
    """Generate test files. Uses per-file generation when *test_cases* are provided
    to avoid token-limit truncation with many test cases."""
    # Per-file mode: avoids truncation for large test case sets
    if test_cases and test_cases.strip():
        return await generate_tests_per_file(code_files, language, test_cases, diagram)

    # Single-call mode (no test cases — generic unit tests)
    prompt = _build_test_prompt(code_files, language, test_cases)
    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} test engineer. Output only valid JSON.",
        temperature=0.3,
        max_tokens=8192,
    )
    if not response.strip():
        _log_t = logging.getLogger(__name__)
        _log_t.warning("[TestGen] Empty response from LLM")
        return {}
    result = _parse_code_response(response, context="tests")
    if not result:
        _log_t = logging.getLogger(__name__)
        _log_t.warning(f"[TestGen] Could not parse response ({len(response)} chars): {response[:300]}")
    return result


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

_GUIDE_DIR = _Path(__file__).resolve().parent.parent.parent.parent / "uml_guide"


def _load_guide(name: str) -> str:
    """加载单份设计指南的 Markdown 内容。name 不含后缀，如 'sequence_diagram'。"""
    gf = _GUIDE_DIR / f"{name}_guide.md"
    return gf.read_text(encoding="utf-8") if gf.exists() else ""


# ── 核心规则：精简版（单图/简单变更时用）vs 完整版（跨图场景用）──

_CORE_RULES_SHORT = """## Core Rules
1. Lifeline class_ref MUST reference existing classes from class diagrams
2. Message labels MUST match method signatures in the referenced class
3. PRESERVE all coordinate fields (position/size/x/y/width/height) — NEVER zero them out
4. If the user requests repositioning, adjust coordinates thoughtfully
5. Validate your output against the Reference Index above — fix any broken cross-references"""

_VALIDATION_RULES_FULL = """## Core Validation Rules
1. Sequence diagram lifelines MUST reference classes that exist in class diagrams (via class_ref)
2. Sequence diagram method calls MUST match method signatures in class diagrams
3. Component diagram interfaces MUST be consistent with class diagram provided/required interfaces
4. Flag any inconsistencies found between diagrams in the consistency_report
5. If any diagram type is missing from the existing set, generate it based on the others
6. Optimize each diagram while maintaining consistency across all types
7. PRESERVE all coordinate fields (position/size/x/y/width/height) — NEVER zero them out
8. If the user requests repositioning, adjust coordinates thoughtfully. Otherwise, keep existing positions

## Component-Diagram Association Rules
9. COMPONENT LINKING: Class and sequence diagrams have a "component_id" field that links them to a
   component diagram node (CompNode.id). Set component_id to the matching component's id when a diagram
   describes the internals or interactions of a specific component.
10. COMPONENT MANIFEST USAGE: The Component Manifest above shows every component and its diagram
    coverage status. When generating or optimizing:
    - For each missing component, generate at least one class + one sequence diagram
    - For each partial component, fill the missing diagram type
    - Use the exact component "id" from the manifest as the diagram's "component_id"
11. MULTIPLE DIAGRAMS PER COMPONENT: A single component may need MULTIPLE diagrams of the same type:
    ALL diagrams belonging to the SAME component share the SAME component_id.
12. COMPONENT HIERARCHY: Parent-child relationships between components (parent_id field) imply
    architectural nesting. Generate diagrams at the appropriate level.
13. GENERATION ORDER: Always generate component diagrams FIRST (to establish IDs), then class
    diagrams (structure), then sequence diagrams (behavior).

## Reference Validation
14. REFERENCE INDEX: The Cross-Diagram Reference Index above lists all entities and their
    relationships. Use it to validate your output: every lifeline.class_ref must resolve to a
    class in the Class Directory, every message label should match a method in the target class,
    every component_id must reference a component in the Component Manifest. Fix any
    "Issues Detected" listed in the index — they are guidance for what to improve."""


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
        # knowledge_graph 在项目根，不在 backend 下，需要确保 path 可达
        _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        if _proj_root not in sys.path:
            sys.path.insert(0, _proj_root)
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
        messages = [
            {"role": "user", "content": scope_prompt},
        ]
        raw = await llm.ainvoke(messages, temperature=0.1, max_tokens=500)
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
    guide_parts = []
    for gkey, gfile in guide_map.items():
        if gkey in guides_needed:
            content = _load_guide(gfile)
            if content:
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

    # ── Validation rules ──
    rules = _VALIDATION_RULES_FULL if include_all_rules else _CORE_RULES_SHORT

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

{rules}
{output_hint}
## User Instructions:
{instructions or "Overall system optimization: improve consistency, reduce duplication, ensure cross-diagram coherence"}

## Output Format:
Return a JSON object with a "diagrams" array. Each entry has "type" (class/sequence/component),
"name", optional "component_id", and "data" (the full diagram content).
You may generate MULTIPLE diagrams of the same type for different aspects.
Match existing diagrams by "type" + "name", updating them; create new entries for new diagrams.
```json
{{
  "diagrams": [
    {{"type": "class", "name": "...", "component_id": "...", "data": {{ ... }}}},
    {{"type": "sequence", "name": "...", "component_id": "...", "data": {{ ... }}}}
  ],
  "consistency_report": [{{"severity": "error|warning", "msg": "..."}}],
  "changes_summary": "summary",
  "diff": "what changed",
  "design_constraints": {{
    "must_preserve": ["约束1: 关键关系和接口不可修改"],
    "immutable_entities": ["不可变实体名称"],
    "design_rationale": "核心设计理由简述"
  }}
}}
```
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


async def optimize_project(
    diagrams: list[dict] | None = None,
    instructions: str = "",
) -> dict:
    """Cross-validate and optimize all project diagrams together.

    统一委托到 UmlOptimizer.optimize()（ReflectionAgent 生成→验证→修复循环）。

    Accepts a list of existing diagram dicts as optional reference.
    Returns a dict with ``diagrams`` array (new format).
    """
    from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer
    from app.agent_base.core.llm import BaseAgentsLLM

    llm = BaseAgentsLLM.from_settings()
    optimizer = UmlOptimizer(llm, max_iterations=3)
    return await optimizer.optimize(diagrams=diagrams, instructions=instructions)


async def optimize_project_stream(
    diagrams: list[dict] | None = None,
    instructions: str = "",
):
    """Streaming version: extracts complete JSON elements from the LLM stream
    and yields them for real-time rendering.

    统一委托到 UmlOptimizer.optimize_stream()（流式生成 + 后验证修复）。

    Accepts a list of existing diagram dicts as optional reference.
    """
    from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer
    from app.agent_base.core.llm import BaseAgentsLLM

    llm = BaseAgentsLLM.from_settings()
    optimizer = UmlOptimizer(llm, max_iterations=3)
    async for elem_type, elem_json in optimizer.optimize_stream(
        diagrams=diagrams, instructions=instructions,
    ):
        yield f"{elem_type}:{elem_json}"
    yield "DONE"


# ── _JsonElementExtractor 已移至 uml_optimizer.py ──


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


async def optimize_uml(diagram: UmlDiagram, instructions: str = "") -> dict:
    """Ask LLM to optimize a UML diagram design. Handles both class and sequence diagrams."""
    dt = diagram.diagram_type or "class"

    if dt == "sequence":
        type_hint = "sequence diagram with lifelines and messages"
        rules = """CRITICAL RULES:
1. Use EXACTLY the same JSON field names and structure as the input.
2. lifelines: "id", "name", "class_ref", "x", "activations"
3. messages: "id", "from_lifeline", "to_lifeline", "label", "type" (sync|async|return|simple|self), "order", "note"
4. Every lifeline and message MUST have a unique "id"
5. Message "order" should be sequential from top to bottom
6. PRESERVE all "note" and "class_ref" fields
7. PRESERVE the "x" field on every lifeline — NEVER reset lifeline positions
8. PRESERVE the "y" and "order" fields on every message — NEVER reset message Y positions
9. PRESERVE the "component_id" field — it links this diagram to a component diagram node. If set, keep it; if designing for a specific component, reference its CompNode.id"""
        default_inst = "优化时序图交互流程：检查遗漏/多余消息、调用顺序合理性、消息命名准确性"
        system = "You are an expert software architect specializing in UML sequence diagrams and interaction design."
    elif dt == "component":
        type_hint = "component diagram with components and dependencies"
        rules = """CRITICAL RULES:
1. Use EXACTLY the same JSON field names and structure as the input.
2. components: "id", "name", "x", "y", "width", "height", "parent_id", "provided_interfaces", "required_interfaces"
4. comp_relations: "id", "source", "target", "type" (dependency|delegation)
5. Every component and relation MUST have a unique "id"
6. PRESERVE all "provided_interfaces" and "required_interfaces" lists
7. PRESERVE the "x", "y", "width", "height" fields on every component — NEVER reset their positions or sizes
8. PRESERVE the "parent_id" field on every component — it defines the component nesting hierarchy"""
        default_inst = "优化组件架构：检查组件职责划分、依赖关系合理性、接口设计完整性"
        system = "You are an expert software architect specializing in UML component diagrams and system architecture."
    else:
        type_hint = "UML class diagram"
        rules = """CRITICAL RULES:
1. Use EXACTLY the same JSON field names as the input. Relations use "source"/"target"
2. visibility values MUST be "+", "-", or "#"
3. stereotype values MUST be "class", "interface", "abstract", or "enum"
4. relation type values MUST be "inheritance", "composition", "aggregation", "association", "realization", or "dependency"
5. Every class and relation MUST have a unique "id"
6. PRESERVE all "note" fields on classes and relations
7. PRESERVE "role_name", "multiplicity_source", "multiplicity_target" on relations
8. PRESERVE all "position" and "size" fields on every class — NEVER reset them
9. PRESERVE the "component_id" field — it links this diagram to a component diagram node. If set, keep it; if designing for a specific component, reference its CompNode.id"""
        default_inst = "General design optimization: improve cohesion, reduce coupling, apply design patterns where appropriate."
        system = "You are an expert software architect specializing in UML design and design patterns. Always use +, -, # for visibility values."

    # Load design guide for this diagram type
    guide_text = ""
    try:
        from pathlib import Path as _Path
        guide_file = _Path(__file__).resolve().parent.parent.parent.parent / "uml_guide" / f"{dt}_diagram_guide.md"
        if guide_file.exists():
            guide_text = guide_file.read_text(encoding="utf-8")
            logging.getLogger(__name__).info(f"[Optimize] Loaded design guide: {guide_file.name} ({len(guide_text)} chars)")
    except Exception:
        pass

    # Detect empty diagram → generate from scratch instead of optimize
    has_content = bool(
        diagram.classes or diagram.lifelines or diagram.messages
        or diagram.components or diagram.comp_relations
    )

    if has_content:
        diagram_block = f"""## Current Diagram ({type_hint}):
```json
{diagram.model_dump_json(indent=2)}
```

## User Instructions:
{instructions or default_inst}"""
    else:
        diagram_block = f"""## Design Requirements:
{instructions or "Create a new " + type_hint + " based on best practices."}"""

    prompt = f"""{diagram_block}

## {rules}

## Output Format:
```json
{{
  "optimized": {{ COPY THE EXACT INPUT STRUCTURE }},
  "changes_summary": "brief summary",
  "diff": "what changed"
}}
```
Only output the JSON object, no other text.
"""
    full_system = (guide_text + "\n\n" + system) if guide_text else system

    # Save prompt for diagnostics
    try:
        from pathlib import Path as _P
        _log_d = _P(__file__).resolve().parent.parent.parent.parent / "temp" / "pipeline_log"
        _log_d.mkdir(exist_ok=True)
        _ts = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        _f = _log_d / f"llm_optimize_{_ts}.md"
        _f.write_text(f"# System Prompt\n```\n{full_system}\n```\n\n# User Prompt\n```\n{prompt}\n```", encoding="utf-8")
        logging.getLogger(__name__).info(f"[Optimize] Prompt saved: {_f}")
    except Exception:
        pass

    response = await chat(
        prompt=prompt,
        system_prompt=full_system,
        temperature=0.5,
        max_tokens=8192,
    )

    # Append LLM response to log
    try:
        _f.write_text(
            _f.read_text(encoding="utf-8") + f"\n\n# LLM Response\n```json\n{response}\n```",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        cleaned = clean_llm_json_response(response)
        result = json.loads(cleaned)
        # Normalize all enum values from LLM output
        if "optimized" in result and isinstance(result["optimized"], dict):
            result["optimized"] = _normalize_llm_output(result["optimized"])
        return result
    except json.JSONDecodeError:
        return {
            "optimized": diagram.model_dump(),
            "changes_summary": "Unable to parse optimization result",
            "diff": response,
        }


async def fix_code(
    code_files: dict[str, str],
    test_results: str,
    language: str,
) -> dict[str, str]:
    """Ask LLM to fix code based on test failure feedback."""
    code_block = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in code_files.items()
    )
    prompt = f"""Fix the following {language} code based on the test results.

## Current Code:
{code_block}

## Test Results (failures):
{test_results}

## Requirements:
- Fix all failing tests.
- Return the corrected code as a JSON object mapping filenames to content.
- Only output the JSON object, no other text.
"""
    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} developer fixing failing tests.",
        temperature=0.3,
        max_tokens=8192,
    )
    if not response.strip():
        return {f"fixed_{k}": v for k, v in code_files.items()}
    result = _parse_code_response(response, context="fixed")
    return result if result else {f"fixed_{k}": v for k, v in code_files.items()}


async def adapt_code_to_uml(
    existing_code: dict[str, str],
    diagram: UmlDiagram,
    language: str,
) -> dict[str, str]:
    """Adapt existing source code to match current UML diagram.

    Keeps existing business logic when UML is unchanged; adds/removes/changes
    classes, attributes, and methods based on UML diffs.
    """
    existing_text = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in existing_code.items()
    )

    prompt = f"""You are modifying existing {language} source code to match an updated UML class diagram.

## Current UML Design (authoritative — code must match this):
```json
{diagram.model_dump_json(indent=2)}
```

## Existing Source Code (adapt this to match the UML above):
{existing_text[:8000]}

## Rules for adaptation:
1. **UML ↔ Code consistency is the goal.** For each class in the UML, there must be a matching implementation.
2. **Preserve existing business logic** — if the UML class/attribute/method hasn't changed from the code, keep the existing implementation details (comments, algorithm choices, error handling).
3. **UML has a class that code doesn't** → create a new file with stub implementation, keeping the UML's stereotype and business rules from notes.
4. **Code has a class that UML doesn't** → remove that class/file.
5. **UML class has new attributes/methods** → add them to the existing class implementation.
6. **UML class removed attributes/methods** → remove them from the existing class.
7. Follow {language} best practices and conventions.
8. Return the COMPLETE modified source files as a JSON object mapping filenames to content.
9. Only output the JSON object, no other text.

```json
{{"file1.py": "full modified content...", "file2.py": "full modified content..."}}
```"""

    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} developer adapting code to a UML design. Output only valid JSON.",
        temperature=0.3,
        max_tokens=8192,
    )
    if not response.strip():
        import logging
        _logger = logging.getLogger(__name__)
        _logger.warning("[adapt_code] Empty response, keeping existing code")
        return existing_code
    result = _parse_code_response(response, context="adapted")
    if result:
        import logging
        _logger = logging.getLogger(__name__)
        _logger.info(f"[adapt_code] LLM returned {len(result)} modified source files: {list(result.keys())}")
        return result
    _logger = logging.getLogger(__name__)
    _logger.warning("[adapt_code] Could not parse response, keeping existing code")
    return existing_code


async def update_tests_incremental(
    existing_tests: dict[str, str],
    source_code: dict[str, str],
    language: str,
    changed_cases: str,
) -> dict[str, str]:
    """Incrementally update existing test files based on changed test cases.

    Preserves unchanged test functions; adds/updates/removes based on
    the changed cases summary from Stage 4 (case review).
    """
    test_text = "\n\n".join(
        f"### {fname}\n```{language}\n{content}\n```"
        for fname, content in existing_tests.items()
    )
    api_sigs = _extract_api_signatures(source_code, language)

    prompt = f"""You are incrementally updating existing {language} test code.

## Current Source API (tests MUST match these exact signatures):
{api_sigs}

## Changed Test Cases (from case review — only these need attention):
{changed_cases[:6000] if changed_cases else "(no specific changes — keep all existing tests as-is)"}

## Existing Test Code (incrementally update based on changed cases above):
{test_text[:8000]}

## Rules for incremental update:
1. **For test functions matching unchanged case IDs** → keep them exactly as-is.
2. **For test functions matching changed case IDs** → update the test body to match the new test steps and expected results.
3. **New case IDs that have no existing test function** → add a new test function following the naming pattern: `test_<CASE_ID>_<short_description>`.
4. **Test functions whose case IDs were deleted from the case sheet** → remove them.
5. Keep all imports, fixtures, and test utilities unchanged unless they contradict new requirements.
6. Return the COMPLETE modified test files as a JSON object mapping filenames to content.
7. Only output the JSON object, no other text.

```json
{{"test_module1.py": "full modified content...", "test_module2.py": "full modified content..."}}
```"""

    response = await chat(
        prompt=prompt,
        system_prompt=f"You are an expert {language} test engineer. Update test files incrementally. Output only valid JSON.",
        temperature=0.3,
        max_tokens=8192,
    )
    if not response.strip():
        import logging
        _logger = logging.getLogger(__name__)
        _logger.warning("[update_tests] Empty response, keeping existing tests")
        return existing_tests
    result = _parse_code_response(response, context="updated_tests")
    if result:
        import logging
        _logger = logging.getLogger(__name__)
        _logger.info(f"[update_tests] LLM returned {len(result)} modified test files: {list(result.keys())}")
        return result
    _logger = logging.getLogger(__name__)
    _logger.warning("[update_tests] Could not parse response, keeping existing tests")
    return existing_tests


def _get_extension(language: str) -> str:
    ext_map = {
        "python": "py", "java": "java", "typescript": "ts",
        "javascript": "js", "csharp": "cs", "cpp": "cpp",
        "go": "go", "rust": "rs", "ruby": "rb", "swift": "swift",
        "kotlin": "kt", "php": "php",
    }
    return ext_map.get(language, "txt")
