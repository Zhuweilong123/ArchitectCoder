"""Code generation service – generates code in 12 languages from UML diagrams."""

import json
import logging
from app.models.uml import UmlDiagram
from app.services.llm_service import chat, chat_stream
from app.services.tools import clean_llm_json_response

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


def _build_global_prompt(
    diagrams: list[dict] | None = None,
    instructions: str = "",
) -> tuple[str, str, bool]:
    """Build the shared prompt + system_prompt for global optimization.

    Accepts a list of existing diagrams as optional reference context.
    Returns (prompt, full_system, is_empty).
    Used by both optimize_project and optimize_project_stream.
    """
    _logger = logging.getLogger(__name__)

    # Load design guides
    guide_parts = []
    try:
        from pathlib import Path as _P
        _gd = _P(__file__).resolve().parent.parent.parent.parent / "uml_guide"
        for _t in ["class_diagram", "sequence_diagram", "component_diagram"]:
            _gf = _gd / f"{_t}_guide.md"
            if _gf.exists():
                guide_parts.append(_gf.read_text(encoding="utf-8"))
    except Exception:
        pass
    global_guide = "\n\n".join(guide_parts) if guide_parts else ""

    # Collect existing diagram data from list
    parts = []
    if diagrams:
        for d in diagrams:
            dtype = d.get("diagram_type") or d.get("type") or "class"
            dname = d.get("name", "Untitled")
            # Determine if non-empty by checking type-specific fields
            has_content = False
            if dtype in ("class",):
                has_content = bool(d.get("classes") or d.get("relations"))
            elif dtype in ("sequence",):
                has_content = bool(d.get("lifelines") or d.get("messages"))
            elif dtype in ("component",):
                has_content = bool(d.get("components") or d.get("comp_relations"))
            else:
                has_content = bool(d.get("classes") or d.get("lifelines") or d.get("components"))
            if has_content:
                _type_labels = {"class": "Class Diagram", "sequence": "Sequence Diagram",
                                "component": "Component Diagram"}
                label = _type_labels.get(dtype, f"{dtype} Diagram")
                parts.append(f"""## {label} "{dname}":
```json
{json.dumps(d, indent=2, ensure_ascii=False)}
```""")

    is_empty = len(parts) == 0

    # ── New-format JSON example: diagrams array instead of fixed 3-type object ──
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

    _cross_validation_rules = """1. Sequence diagram lifelines MUST reference classes that exist in class diagrams (via class_ref)
2. Sequence diagram method calls MUST match method signatures in class diagrams
3. Component diagram interfaces MUST be consistent with class diagram provided/required interfaces
4. Flag any inconsistencies found between diagrams in the consistency_report
5. If any diagram type is missing from the existing set, generate it based on the others
6. Optimize each diagram while maintaining consistency across all types
7. PRESERVE all coordinate fields (position/size/x/y/width/height) — NEVER zero them out
8. If the user requests repositioning, adjust coordinates thoughtfully. Otherwise, keep existing positions
9. COMPONENT LINKING: Class and sequence diagrams have a "component_id" field that links them to a
   component diagram node (CompNode.id). Set component_id to the matching component's id when a diagram
   describes the internals or interactions of a specific component
10. MULTIPLE DIAGRAMS PER TYPE: You may generate MULTIPLE diagrams of the same type (e.g. two sequence
    diagrams for different scenarios, two class diagrams for different layers). Each must have a distinct
    "name" field. Use the "type" field ("class"/"sequence"/"component") to declare the diagram type
11. GENERATION ORDER: Generate component diagrams first (overall architecture), then class diagrams
    (structure), then sequence diagrams (behavior). Reference component IDs created in earlier entries"""

    if is_empty:
        prompt = f"""Generate a complete UML system design from the following requirements.
Determine which diagram types are needed — you may generate zero or more of each type
(class, sequence, component). Typically you should generate at least one of each, but
you may create multiple diagrams of the same type for different aspects or scenarios.

## Design Requirements:
{instructions or "Design a well-structured software system with clear class hierarchy, interaction flows, and component architecture."}

## Output Format — return a JSON object with a "diagrams" array:
{_json_example}
Only output the JSON object, no other text.
"""
    else:
        prompt = f"""Cross-validate and optimize the following UML diagrams as a complete system design.

{chr(10).join(parts)}

## Cross-Validation Rules:
{_cross_validation_rules}

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


async def optimize_project(
    diagrams: list[dict] | None = None,
    instructions: str = "",
) -> dict:
    """Cross-validate and optimize all project diagrams together.

    Accepts a list of existing diagram dicts as optional reference.
    Returns a dict with ``diagrams`` array (new format), normalized via
    ``_normalize_optimize_result()`` for backward compatibility.
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[OptimizeProject] Global optimization request")

    prompt, full_system, is_empty = _build_global_prompt(
        diagrams=diagrams, instructions=instructions,
    )

    # Save prompt + response for diagnostics
    try:
        from pathlib import Path as _P3
        _log_d = _P3(__file__).resolve().parent.parent.parent.parent / "temp" / "pipeline_log"
        _log_d.mkdir(exist_ok=True)
        _ts = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        _f = _log_d / f"llm_global_optimize_{_ts}.md"
        _f.write_text(f"# Global Optimize ({'GENERATE' if is_empty else 'OPTIMIZE'})\n\n## System Prompt\n```\n{full_system}\n```\n\n## User Prompt\n```\n{prompt}\n```", encoding="utf-8")
        _logger.info(f"[OptimizeProject] Prompt saved: {_f}")
    except Exception:
        _f = None

    response = await chat(
        prompt=prompt,
        system_prompt=full_system,
        temperature=0.5,
        max_tokens=8192,
    )

    # Append response to log
    if _f:
        try:
            _current = _f.read_text(encoding="utf-8")
            _f.write_text(_current + f"\n\n## Response\n```\n{response}\n```", encoding="utf-8")
        except Exception:
            pass

    try:
        cleaned = clean_llm_json_response(response)
        result = json.loads(cleaned)
        _logger.info(f"[OptimizeProject] Result keys: {list(result.keys())}")
        return _normalize_optimize_result(result)
    except json.JSONDecodeError:
        _logger.warning("[OptimizeProject] JSON parse failed")
        return {
            "diagrams": [],
            "consistency_report": [],
            "changes_summary": "Failed to parse LLM response",
            "design_constraints": {},
            "diff": response,
        }


async def optimize_project_stream(
    diagrams: list[dict] | None = None,
    instructions: str = "",
):
    """Streaming version: extracts complete JSON elements from the LLM stream
    and yields them for real-time rendering.

    Accepts a list of existing diagram dicts as optional reference.
    """
    import logging as _log2
    _l = _log2.getLogger(__name__)
    _l.info("[OptimizeStream] Starting streaming optimization (JSON mode)")
    _lf = None

    prompt, full_system, is_empty = _build_global_prompt(
        diagrams=diagrams, instructions=instructions,
    )

    # Save prompt for diagnostics
    try:
        from pathlib import Path as _PP2
        _ld = _PP2(__file__).resolve().parent.parent.parent.parent / "temp" / "pipeline_log"
        _ld.mkdir(exist_ok=True)
        _ts = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        _lf = _ld / f"llm_stream_optimize_{_ts}.md"
        _lf.write_text(
            f"# Stream Optimize ({'GENERATE' if is_empty else 'OPTIMIZE'})\n\n"
            f"## System Prompt\n```\n{full_system}\n```\n\n"
            f"## User Prompt\n```\n{prompt}\n```",
            encoding="utf-8"
        )
        _l.info(f"[OptimizeStream] Prompt saved: {_lf}")
    except Exception:
        _lf = None

    _l.info("[OptimizeStream] Starting LLM stream (JSON mode)")
    full_response = ""

    # Use element extractor to yield complete JSON objects as they arrive
    extractor = _JsonElementExtractor()
    elem_count = 0
    async for chunk in chat_stream(
        prompt=prompt,
        system_prompt=full_system,
        temperature=0.5,
        max_tokens=8192,
    ):
        full_response += chunk
        for elem_type, elem_json in extractor.feed(chunk):
            elem_count += 1
            _l.info(f"[OptimizeStream] Element #{elem_count}: {elem_type} ({len(elem_json)} chars)")
            yield f"{elem_type}:{elem_json}"

    yield "DONE"
    _l.info(f"[OptimizeStream] Stream ended. Total: {elem_count} elements, {len(full_response)} chars")

    # Append full response to log
    if _lf:
        try:
            _current = _lf.read_text(encoding="utf-8")
            _lf.write_text(_current + f"\n\n## Response\n```\n{full_response}\n```", encoding="utf-8")
        except Exception:
            pass


class _JsonElementExtractor:
    """Extracts complete JSON objects from a streaming JSON text via brace-depth tracking.

    Elements at depth 4 inside arrays (classes, relations, lifelines, etc.) are
    extracted and classified. Nested sub-objects at depth 5+ (attributes, methods)
    are correctly ignored.
    """

    # Keys whose appearance at depth 2 signals a section change.
    # Also used to refine relation vs comp_rel classification.
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
        # Detect "type": "class"/"sequence"/"component" patterns in the diagrams array
        # and extract "name" to emit diagram_create so the frontend can auto-create tabs
        _new_for_dc = self._buf[self._diagram_scan_pos:]
        _dc_idx = 0
        while True:
            _dc_idx = _new_for_dc.find('"type"', _dc_idx)
            if _dc_idx < 0:
                break
            # Find colon and value
            _colon = _new_for_dc.find(':', _dc_idx)
            if _colon < 0: break
            _vstart = _new_for_dc.find('"', _colon + 1)
            if _vstart < 0: break
            _vend = _new_for_dc.find('"', _vstart + 1)
            if _vend < 0: break
            _dtype = _new_for_dc[_vstart + 1:_vend]
            if _dtype in ('class', 'sequence', 'component'):
                # Find the "name" key nearby (within 300 chars)
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
                                    # Look for component_id
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
            # Find the colon and value string
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

        # Trim consumed prefix to bound memory.
        # Keep a 512-char window before _pos so backward scans (for section keys,
        # opening braces) still work after strings cross chunk boundaries.
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

    import uuid as _uuid

    def walk(obj, parent_key=""):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                # Remap known alias fields
                mapped_key = FIELD_ALIASES.get(k, k)
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
