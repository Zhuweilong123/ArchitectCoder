"""Code generation service – supported languages + UML optimization entry points.

代码生成能力已于 2026-08-27 下线（主画布「生成代码」按钮 + 相关端点移除）。
本模块保留：
- ``SUPPORTED_LANGUAGES`` — 语言选择能力（供 /api/llm/languages 与后续复用）
- ``optimize_uml`` — 单图优化（/api/llm/optimize-uml，前端「单图设计」依赖）
- ``optimize_project`` / ``optimize_project_stream`` — V2 全局优化兼容 wrapper
"""

import json
import logging

from app.models.uml import UmlDiagram
from app.services.llm_service import chat
from app.services.tools import clean_llm_json_response
from app.services.uml_common import _normalize_llm_output  # moved from this module

SUPPORTED_LANGUAGES = [
    "python", "java", "typescript", "javascript", "csharp", "cpp",
    "go", "rust", "ruby", "swift", "kotlin", "php",
]


async def optimize_project(
    diagrams: list[dict] | None = None,
    instructions: str = "",
    project_file: str = "",
) -> dict:
    """Cross-validate and optimize all project diagrams together.

    统一委托到 V2 optimize_v2（scope 分析 + 单次 LLM + 程序化验证），
    替代 V1 的 ReflectionAgent 多轮迭代。

    Accepts a list of existing diagram dicts as optional reference.
    Returns a dict with ``diagrams`` array (new format).
    """
    from app.services.uml_optimizer_v2 import run_optimize_v2

    return await run_optimize_v2(
        project_file=project_file,
        instructions=instructions,
    )


async def optimize_project_stream(
    diagrams: list[dict] | None = None,
    instructions: str = "",
):
    """Streaming version: extracts complete JSON elements from the LLM stream
    and yields them for real-time rendering.

    统一委托到 V2 optimize_v2_stream（流式生成 + 程序化验证）。
    当前无调用方，保留接口兼容性。

    Accepts a list of existing diagram dicts as optional reference.
    """
    from app.services.uml_optimizer_v2 import optimize_v2_stream

    async for line in optimize_v2_stream(
        project_file="",
        instructions=instructions,
    ):
        yield line
    yield "DONE"


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
