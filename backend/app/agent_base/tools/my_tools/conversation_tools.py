"""对话工具 — 将子 Agent 封装为对话 Agent 可调用的工具

每个工具包装一个子 Agent，使其被对话 Agent 像调用函数一样使用：
- optimize_uml: OptimizeUmlTool → V2 直连优化引擎（scope 分析 + 单次 LLM + 程序化验证）
- generate_code: GenerateCodeTool → 代码生成
- validate_code: ValidateCodeTool → 代码语法/导入/运行验证
- generate_tests: GenerateTestsTool → 测试生成
- fix_code: FixCodeTool → pytest 驱动的源码修复
- run_tests: RunTestsTool → 运行测试
- write_files: WriteFilesTool → 写文件落盘

Usage::

    from app.agent_base.tools.my_tools.conversation_tools import (
        create_conversation_tools,
        make_async_tool,
    )

    registry = ToolRegistry()
    for tool in create_conversation_tools(llm, source_dir="src/"):
        registry.register_tool(tool)

    agent = ReActAgent("全栈开发", llm, registry, ...)
    result = await agent.arun("给用户模块加 OAuth 登录")
"""

from __future__ import annotations

import json
import os
import asyncio
import logging
from typing import Any, Callable

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ── 异步工具基类 — returan coroutine，由 aexecute_tool_with_params  await ──

class AsyncTool(Tool):
    """异步工具基类。

    ``run()`` 返回 coroutine，由 ``ToolRegistry.aexecute_tool_with_params()``
    在 ReActAgent FC 循环中正确地 await 它。
    """

    def get_parameters(self) -> list:
        return []  # 子类通过 to_openai_schema() 直接提供 schema

    def run(self, parameters: dict) -> str:
        """返回 coroutine，由 aexecute_tool_with_params await。"""
        return self._execute(parameters)  # type: ignore[return-value]

    async def _execute(self, parameters: dict) -> str:
        raise NotImplementedError

    def to_openai_schema(self) -> dict:
        raise NotImplementedError


# ── 原始生成代码和生成测试的简化版 ──

async def _generate_code_from_uml(
    diagram_json: str, language: str, llm
) -> dict[str, str]:
    """从 UML 图 JSON 生成代码 — 直接调用 LLM。"""
    from app.services.tools import clean_llm_json_response
    from app.services.code_generator import _build_class_prompt
    from app.models.uml import UmlDiagram

    try:
        diagram_dict = json.loads(diagram_json)
    except json.JSONDecodeError:
        diagram_dict = {}

    # 构建 UmlDiagram 对象
    data = diagram_dict.get("data", diagram_dict)
    try:
        diagram = UmlDiagram(**data) if isinstance(data, dict) and data else UmlDiagram(name="Generated")
    except Exception:
        diagram = UmlDiagram(name="Generated")

    prompt = _build_class_prompt(diagram, language)
    response = await llm.ainvoke([{"role": "user", "content": prompt}])

    # 解析代码文件
    import re
    cleaned = clean_llm_json_response(response)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "files" in parsed:
            return parsed["files"]
    except json.JSONDecodeError:
        pass

    # 回退：尝试从 markdown 代码块解析
    result = {}
    blocks = re.findall(
        r'###\s*(\S+)\s*\n```(?:\w+)?\n(.*?)```',
        response, re.DOTALL,
    )
    for fname, code in blocks:
        result[fname.strip()] = code.strip()
    return result or {"main.py": response[:5000]}


async def _generate_tests_from_code(
    source_files: dict[str, str], language: str, test_cases: str, llm
) -> dict[str, str]:
    """从源码生成测试代码。"""
    from app.services.tools import clean_llm_json_response

    src_text = "\n\n".join(
        f"### {f}\n```{language}\n{c}\n```"
        for f, c in source_files.items()
    )

    prompt = f"""You are a test generation expert. Generate pytest tests for the following source code.

## Source Code:
{src_text[:8000]}

## Test Cases:
{test_cases or 'Generate comprehensive tests covering all public APIs, edge cases, and error handling.'}

## Requirements:
1. Use pytest framework
2. Write complete, runnable tests (with imports, fixtures, assertions)
3. Each test file should be named test_<module>.py
4. Cover happy path, edge cases, and error handling
5. Include necessary imports

Output your response as a valid JSON object exactly like:
{{"files": {{"test_app.py": "import pytest\\n...", "test_utils.py": "..."}}}}

Return ONLY the JSON — no markdown fences, no extra text.
"""

    response = await llm.ainvoke([{"role": "user", "content": prompt}])
    cleaned = clean_llm_json_response(response)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "files" in parsed:
            return parsed["files"]
    except json.JSONDecodeError:
        pass
    return {}


# ── 进度事件转发 ──

class ProgressRelay:
    """子 Agent 进度 → 外层编排层的事件转发器。

    对话 Agent 调用工具时，子 Agent 内部的流式进度通过此转发器
    推送到前端。编排层在对话 Agent 运行前注入转发器实例。
    """

    def __init__(self):
        self._events: list[dict] = []
        self._on_progress: Callable[[dict], None] | None = None

    def on_progress(self, callback: Callable[[dict], None]):
        """注册进度回调。"""
        self._on_progress = callback

    def emit(self, event: dict):
        """发送进度事件。"""
        self._events.append(event)
        if self._on_progress:
            result = self._on_progress(event)
            # 如果回调是协程，需要调度到事件循环执行
            import asyncio
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    pass  # 无事件循环时忽略

    def clear(self):
        self._events.clear()

    @property
    def events(self) -> list[dict]:
        return self._events


# ═══════════════════════════════════════════════════════════
# Tool 1: optimize_uml
# ═══════════════════════════════════════════════════════════

class OptimizeUmlTool(AsyncTool):
    """UML 设计优化工具 — 使用 V2 直连优化引擎。

    对话 Agent 调用此工具来优化/修改 UML 设计，返回优化后的图 JSON
    和设计约束。内部使用 V2 直连优化引擎（optimize_v2），含 scope 分析 +
    单次 LLM 生成 + 程序化跨图验证。

    优先从 project_file 加载现有图（无需 Agent 手拼 JSON）；若未提供
    project_file，则返回错误。
    """

    def __init__(self, llm: BaseAgentsLLM, project_file: str = "",
                 progress: ProgressRelay | None = None):
        super().__init__(
            name="optimize_uml",
            description=(
                "Optimize or modify the project's UML design (class, sequence, "
                "component diagrams). Based on the user's instructions, add, remove, "
                "or update any element in the diagrams — all element types are "
                "operable (classes, components, interfaces, lifelines, messages, "
                "relationships, fragments, etc.). The tool rewrites the full diagram "
                "set from the instructions, which is expected behavior. It also "
                "cross-validates and fixes cross-diagram reference consistency. "
                "Best usage: pass project_file (the .umlproj path from project_info) "
                "and instructions; the tool loads the existing diagrams itself and "
                "returns the updated diagrams JSON. Alternatively pass diagrams_json "
                "with the existing diagram list."
            ),
        )
        self.llm = llm
        self.project_file = project_file
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        from app.services.uml_optimizer_v2 import run_optimize_v2

        instructions = params.get("instructions", "")

        # ── 1) 优先从 project_file 加载现有图 ──
        # self.project_file (构造时注入的实际路径) 优先级高于 LLM 传入的猜测值。
        # LLM 可能猜错文件名（如 "radar_uml.umlproj" vs 实际 "radar_design_0730.umlproj"），
        # 所以只用 LLM 的 project_file 当 self.project_file 未设置时作为回退。
        llm_project_file = params.get("project_file", "")
        project_file = self.project_file or llm_project_file

        # ── 安全阀：无 project_file → 不触发优化 ──
        if not project_file or not os.path.isfile(project_file):
            msg = (
                "No valid project_file found. Provide the correct .umlproj path "
                "as project_file."
            )
            if llm_project_file and not os.path.isfile(llm_project_file):
                msg = (
                    f"Project file '{llm_project_file}' not found. "
                    "Ask explore_project or the user for the correct .umlproj path, "
                    "then retry with the correct project_file."
                )
            return json.dumps({
                "error": msg,
                "diagrams": [],
                "design_constraints": {},
                "changes_summary": "No project file available",
                "consistency_report": [{"severity": "error", "msg": msg}],
            }, ensure_ascii=False)

        logger.info("[OptimizeUmlTool] project_file=%s, instructions=%s",
                    project_file, instructions[:80])

        try:
            self.progress and self.progress.emit({
                "event": "sub_agent",
                "agent": "UmlOptimizerV2",
                "status": "started",
                "message": f"Optimizing project: {project_file}",
            })

            result = await run_optimize_v2(
                project_file=project_file,
                instructions=instructions,
            )

            self.progress and self.progress.emit({
                "event": "sub_agent",
                "agent": "UmlOptimizerV2",
                "status": "done",
            })

            # ── 3) 可选落盘：只有优化成功时才写回 .umlproj ──
            save_to_project = bool(params.get("save_to_project", False))
            saved_path = ""
            changes_summary = result.get("changes_summary", "")
            optimization_failed = changes_summary.startswith("Optimization failed:")
            if save_to_project and result.get("diagrams") and not optimization_failed:
                saved_path = self._save_to_project(project_file, result["diagrams"])

            out = {
                "diagrams": result.get("diagrams", []),
                "design_constraints": result.get("design_constraints", {}),
                "changes_summary": changes_summary,
                "consistency_report": result.get("consistency_report", []),
            }
            if saved_path:
                out["saved_to"] = saved_path
            elif save_to_project:
                out["saved_to"] = ""
            return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            logger.exception("[OptimizeUmlTool] Failed")
            return json.dumps({
                "error": f"UML optimization failed: {e}",
                "diagrams": [],
            }, ensure_ascii=False)

    def _save_to_project(self, project_file: str, diagrams: list[dict]) -> str:
        """Normalize LLM output and write diagrams back to .umlproj. Returns path on success, '' on failure."""
        try:
            from app.services.file_service import load_project, save_project
            from app.models.uml import UmlDiagram
            from app.services.uml_common import _normalize_llm_output

            project = load_project(project_file)
            converted: list[UmlDiagram] = []
            for d in diagrams:
                data = d.get("data") if isinstance(d, dict) else None
                if isinstance(data, dict):
                    # Normalize LLM output to match Pydantic field names
                    data = _normalize_llm_output(data)
                    if "diagram_type" not in data and "type" in d:
                        data = {**data, "diagram_type": d["type"]}
                    converted.append(UmlDiagram(**data))
                elif isinstance(d, dict):
                    data = _normalize_llm_output(d)
                    converted.append(UmlDiagram(**data))
            if not converted:
                logger.warning("[OptimizeUmlTool] No valid diagrams to save")
                return ""
            project.diagrams = converted
            saved = save_project(project, project_file)
            logger.info("[OptimizeUmlTool] Saved %d diagrams → %s", len(converted), saved)
            return saved
        except Exception as e:
            logger.exception("[OptimizeUmlTool] Save to project failed: %s", e)
            return ""

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_file": {
                            "type": "string",
                            "description": "Path to the .umlproj project file. The tool loads existing diagrams from it.",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "User instructions for optimizing the UML design, e.g. 'add a payment module', 'remove the association from the sequence diagram', 'rename class X to Y'.",
                        },
                        "save_to_project": {
                            "type": "boolean",
                            "description": "Whether to write optimized diagrams back to the .umlproj file. Default false.",
                        },
                    },
                    "required": ["instructions"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 2: validate_code
# ═══════════════════════════════════════════════════════════

class ValidateCodeTool(AsyncTool):
    """代码验证工具 — 包装 CodeValidator (ReActAgent FC)。

    对话 Agent 生成代码后调用此工具验证正确性。
    内部自动完成 check_imports → run_module → fix → finish 循环。
    """

    def __init__(self, llm: BaseAgentsLLM, source_dir: str = "",
                 progress: ProgressRelay | None = None):
        super().__init__(
            name="validate_code",
            description=(
                "Validate the correctness of Python code files. "
                "Checks syntax errors, import errors, and runtime errors. "
                "Automatically fixes errors found and re-validates. "
                "Returns a validation report and (possibly fixed) code files. "
                "code_files_json must be a JSON string like {\"filename\": \"content\", ...}."
            ),
        )
        self.llm = llm
        self.source_dir = source_dir
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        from app.agent_base.tools.my_tools.code_validator import CodeValidator

        code_files_json = params.get("code_files_json", "{}")
        task = params.get("task", "Validate and fix errors")

        try:
            code_files = json.loads(code_files_json) if isinstance(code_files_json, str) else code_files_json
        except json.JSONDecodeError:
            return json.dumps({"success": False, "error": f"Invalid code_files_json: {code_files_json[:200]}"})

        if not isinstance(code_files, dict):
            return json.dumps({"success": False, "error": "code_files_json must be a JSON object"})

        validator = CodeValidator(
            self.llm, max_rounds=5, generated_dir=self.source_dir,
        )

        self.progress and self.progress.emit({
            "event": "sub_agent",
            "agent": "CodeValidator",
            "status": "started",
            "files": list(code_files.keys()),
        })

        result_data = {}
        async for progress in validator.validate_stream(
            code_files=code_files, task_description=task,
        ):
            if "result" in progress:
                result_data = progress["result"]
                self.progress and self.progress.emit({
                    "event": "sub_agent",
                    "agent": "CodeValidator",
                    "status": "done",
                    "success": result_data.get("success"),
                })
            else:
                self.progress and self.progress.emit({
                    "event": "sub_agent",
                    "agent": "CodeValidator",
                    "status": "running",
                    "round": progress.get("round"),
                })

        return json.dumps({
            "success": result_data.get("success", False),
            "final_code": result_data.get("final_code", code_files),
            "summary": result_data.get("summary", ""),
            "remaining_issues": result_data.get("remaining_issues", ""),
            "steps_count": len(result_data.get("steps", [])),
        }, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_files_json": {
                            "type": "string",
                            "description": "JSON string of code files: {\"filename.py\": \"source code\", ...}",
                        },
                        "task": {
                            "type": "string",
                            "description": "Short description of the validation task",
                        },
                    },
                    "required": ["code_files_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 3: fix_code
# ═══════════════════════════════════════════════════════════

class FixCodeTool(AsyncTool):
    """代码修复工具 — 包装 CodeFixer (ReflectionAgent)。

    对话 Agent 在测试失败后调用此工具修复源码。
    内部自动完成 pytest → reflect → refine 循环。
    """

    def __init__(self, llm: BaseAgentsLLM, source_dir: str = "",
                 test_dir: str = "", progress: ProgressRelay | None = None):
        super().__init__(
            name="fix_code",
            description=(
                "Fix source code to pass tests. Run pytest, analyze failures, "
                "modify the source, then re-run tests, looping until all tests pass. "
                "source_files_json and test_files_json are both JSON strings like "
                "{\"filename\": \"content\", ...}."
            ),
        )
        self.llm = llm
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        from app.agent_base.tools.my_tools.code_fixer import CodeFixer

        source_json = params.get("source_files_json", "{}")
        test_json = params.get("test_files_json", "{}")
        task = params.get("task", "Fix bugs to make all tests pass")

        try:
            source = json.loads(source_json) if isinstance(source_json, str) else source_json
        except json.JSONDecodeError:
            return json.dumps({"success": False, "error": "Invalid source_files_json"})

        try:
            tests = json.loads(test_json) if isinstance(test_json, str) else test_json
        except json.JSONDecodeError:
            return json.dumps({"success": False, "error": "Invalid test_files_json"})

        fixer = CodeFixer(
            self.llm, max_iterations=5,
            source_dir=self.source_dir, test_dir=self.test_dir,
        )

        self.progress and self.progress.emit({
            "event": "sub_agent",
            "agent": "CodeFixer",
            "status": "started",
            "message": f"Fixing {len(source)} source files with {len(tests)} test files",
        })

        result = await fixer.fix(source_code=source, test_code=tests, task=task)

        self.progress and self.progress.emit({
            "event": "sub_agent",
            "agent": "CodeFixer",
            "status": "done",
            "pass_rate": result.get("pass_rate"),
        })

        return json.dumps({
            "success": result.get("success", False),
            "final_source": result.get("final_source", {}),
            "test_output": result.get("test_output", "")[:2000],
            "pass_rate": result.get("pass_rate", "N/A"),
            "iterations": result.get("iterations", 0),
        }, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_files_json": {
                            "type": "string",
                            "description": "JSON string of source files: {\"app.py\": \"code...\", ...}",
                        },
                        "test_files_json": {
                            "type": "string",
                            "description": "JSON string of test files: {\"test_app.py\": \"code...\", ...}",
                        },
                        "task": {
                            "type": "string",
                            "description": "Description of the fix task",
                        },
                    },
                    "required": ["source_files_json", "test_files_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 4: generate_code
# ═══════════════════════════════════════════════════════════

class GenerateCodeTool(AsyncTool):
    """代码生成工具 — 从 UML 图 JSON 生成源码文件。"""

    def __init__(self, llm: BaseAgentsLLM, progress: ProgressRelay | None = None):
        super().__init__(
            name="generate_code",
            description=(
                "Generate Python source code from a UML class diagram (JSON). "
                "Input diagram_json — the JSON representation of a single class diagram. "
                "Returns a dict of generated code files."
            ),
        )
        self.llm = llm
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        diagram_json = params.get("diagram_json", "{}")
        language = params.get("language", "python")

        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "CodeGenerator", "status": "started",
        })

        files = await _generate_code_from_uml(diagram_json, language, self.llm)

        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "CodeGenerator", "status": "done",
            "file_count": len(files),
        })

        return json.dumps({"files": files, "count": len(files)}, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "diagram_json": {
                            "type": "string",
                            "description": "JSON representation of the UML class diagram (take the class-type diagram from the diagrams array returned by optimize_uml)",
                        },
                        "language": {
                            "type": "string",
                            "description": "Target language, default python",
                        },
                    },
                    "required": ["diagram_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 5: generate_tests
# ═══════════════════════════════════════════════════════════

class GenerateTestsTool(AsyncTool):
    """测试生成工具 — 从源码生成 pytest 测试。"""

    def __init__(self, llm: BaseAgentsLLM, progress: ProgressRelay | None = None):
        super().__init__(
            name="generate_tests",
            description=(
                "Generate pytest test files from Python source code. "
                "Automatically creates a test_<module>.py file for each module. "
                "Covers normal paths, edge cases, and error handling."
            ),
        )
        self.llm = llm
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        source_json = params.get("source_files_json", "{}")
        test_cases = params.get("test_cases", "")
        language = params.get("language", "python")

        try:
            source = json.loads(source_json) if isinstance(source_json, str) else source_json
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid source_files_json"})

        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "TestGenerator", "status": "started",
        })

        files = await _generate_tests_from_code(source, language, test_cases, self.llm)

        self.progress and self.progress.emit({
            "event": "sub_agent", "agent": "TestGenerator", "status": "done",
            "file_count": len(files),
        })

        return json.dumps({"files": files, "count": len(files)}, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_files_json": {
                            "type": "string",
                            "description": "JSON string of source files: {\"app.py\": \"code...\", ...}",
                        },
                        "test_cases": {
                            "type": "string",
                            "description": "Optional test case description, e.g. 'verify login, register, password reset'",
                        },
                        "language": {
                            "type": "string",
                            "description": "Target language, default python",
                        },
                    },
                    "required": ["source_files_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 6: run_tests
# ═══════════════════════════════════════════════════════════

class RunTestsTool(AsyncTool):
    """运行测试工具 — 执行 pytest 并返回结果。"""

    def __init__(self, source_dir: str = "", test_dir: str = "",
                 progress: ProgressRelay | None = None):
        super().__init__(
            name="run_tests",
            description=(
                "Run pytest tests. Use to quickly check whether tests pass. "
                "Returns test output, pass rate, and failure details."
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        from app.agent_base.tools.my_tools.code_fixer import _run_pytest

        source_json = params.get("source_files_json", "{}")
        test_json = params.get("test_files_json", "{}")

        try:
            source = json.loads(source_json) if isinstance(source_json, str) else source_json
        except json.JSONDecodeError:
            source = {}

        try:
            tests = json.loads(test_json) if isinstance(test_json, str) else test_json
        except json.JSONDecodeError:
            tests = {}

        output = await _run_pytest(
            source, tests,
            source_dir=self.source_dir, test_dir=self.test_dir,
        )

        from app.agent_base.tools.my_tools.code_fixer import CodeFixer
        passed = CodeFixer._count_passed(output)
        total = CodeFixer._count_total(output)

        return json.dumps({
            "output": output[:3000],
            "passed": passed,
            "total": total,
            "all_passing": passed == total and total > 0,
        }, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_files_json": {
                            "type": "string",
                            "description": "JSON string of source files",
                        },
                        "test_files_json": {
                            "type": "string",
                            "description": "JSON string of test files",
                        },
                    },
                    "required": ["source_files_json", "test_files_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# Tool 7: write_files
# ═══════════════════════════════════════════════════════════

class WriteFilesTool(AsyncTool):
    """写入文件到磁盘。对话 Agent 在验证通过后持久化文件。"""

    def __init__(self, source_dir: str = "", test_dir: str = "",
                 progress: ProgressRelay | None = None):
        super().__init__(
            name="write_files",
            description=(
                "Write code files to disk. Use to save the final versions of source "
                "and test files. files_json: a JSON string like "
                "{\"filename\": \"content\", ...}. file_type: 'source' or 'test'."
            ),
        )
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        files_json = params.get("files_json", "{}")
        file_type = params.get("file_type", "source")

        try:
            files = json.loads(files_json) if isinstance(files_json, str) else files_json
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid files_json"})

        target_dir = self.test_dir if file_type == "test" else self.source_dir
        if not target_dir:
            target_dir = os.path.join(os.getcwd(), "..", "generated", file_type)

        os.makedirs(target_dir, exist_ok=True)
        written = []
        for fname, content in files.items():
            fpath = os.path.join(target_dir, fname)
            os.makedirs(os.path.dirname(fpath) or target_dir, exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(fname)

        return json.dumps({
            "written": written,
            "count": len(written),
            "directory": target_dir,
        }, ensure_ascii=False)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files_json": {
                            "type": "string",
                            "description": "JSON string of files: {\"filename.py\": \"code...\", ...}",
                        },
                        "file_type": {
                            "type": "string",
                            "description": "'source' or 'test'",
                        },
                    },
                    "required": ["files_json"],
                },
            },
        }


# ═══════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════

def create_conversation_tools(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    include_review: bool = True,
    progress: ProgressRelay | None = None,
) -> tuple[list[Tool], ReviewManager | None]:
    """创建对话 Agent 可用的完整工具集。

    Returns:
        (tools, review_manager) — tool 列表 + 审核管理器（若启用）
    """
    tools: list[Tool] = [
        # OptimizeUmlTool(llm, project_file=project_file, progress=progress),
        # GenerateCodeTool(llm, progress=progress),
        # ValidateCodeTool(llm, source_dir=source_dir, progress=progress),
        # GenerateTestsTool(llm, progress=progress),
        # FixCodeTool(llm, source_dir=source_dir, test_dir=test_dir, progress=progress),
        # RunTestsTool(source_dir=source_dir, test_dir=test_dir, progress=progress),
        # WriteFilesTool(source_dir=source_dir, test_dir=test_dir, progress=progress),
    ]

    # A 层文件系统原语工具（读/写/编辑/查找/跑命令）
    from .file_system_tools import create_file_system_tools
    from app.core.config import get_settings
    design_dir = os.path.abspath(get_settings().uml_dir)
    tools.extend(create_file_system_tools(source_dir, test_dir, design_dir))

    # # 项目探索子代理工具（总结/概览类任务委托，避免主 agent read_file 累加）
    # from .explore_project_tools import create_explore_project_tool
    # tools.append(create_explore_project_tool(
    #     llm=llm, project_file=project_file,
    #     source_dir=source_dir, test_dir=test_dir,
    # ))

    review_mgr: ReviewManager | None = None
    if include_review:
        from app.agent_base.tools.review import RequestReviewTool, ReviewManager
        review_mgr = ReviewManager()
        tools.append(RequestReviewTool(manager=review_mgr))

    return tools, review_mgr
