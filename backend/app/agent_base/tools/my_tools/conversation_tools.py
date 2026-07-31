"""对话工具 — 将子 Agent 封装为对话 Agent 可调用的工具

每个工具包装一个子 Agent，使其被对话 Agent 像调用函数一样使用：
- optimize_uml: UmlOptimizer (ReflectionAgent) → UML 设计优化
- validate_code: CodeValidator (ReActAgent FC) → 代码语法/导入/运行验证
- fix_code: CodeFixer (ReflectionAgent) → pytest 驱动的源码修复

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
            self._on_progress(event)

    def clear(self):
        self._events.clear()

    @property
    def events(self) -> list[dict]:
        return self._events


# ═══════════════════════════════════════════════════════════
# Tool 1: optimize_uml
# ═══════════════════════════════════════════════════════════

class OptimizeUmlTool(AsyncTool):
    """UML 设计优化工具 — 包装 UmlOptimizer (ReflectionAgent)。

    对话 Agent 调用此工具来优化 UML 设计，返回优化后的图 JSON
    和设计约束。内部自动完成验证-修复循环。
    """

    def __init__(self, llm: BaseAgentsLLM, progress: ProgressRelay | None = None):
        super().__init__(
            name="optimize_uml",
            description=(
                "优化 UML 设计（类图、时序图、组件图）。"
                "基于用户指令对现有图集进行交叉验证优化。"
                "自动检查跨图引用一致性并修复。"
                "返回优化后的 diagrams JSON 和设计约束。"
            ),
        )
        self.llm = llm
        self.progress = progress

    async def _execute(self, params: dict) -> str:
        from app.agent_base.tools.my_tools.uml_optimizer import UmlOptimizer

        diagrams_json = params.get("diagrams_json", "[]")
        instructions = params.get("instructions", "")

        try:
            if isinstance(diagrams_json, str):
                diagrams = json.loads(diagrams_json)
            elif isinstance(diagrams_json, list):
                diagrams = diagrams_json
            else:
                diagrams = []
        except json.JSONDecodeError:
            diagrams = []

        logger.info("[OptimizeUmlTool] %d diagrams, instructions=%s",
                    len(diagrams), instructions[:80])

        try:
            optimizer = UmlOptimizer(self.llm, max_iterations=3)
            self.progress and self.progress.emit({
                "event": "sub_agent",
                "agent": "UmlOptimizer",
                "status": "started",
                "message": f"Optimizing {len(diagrams)} diagrams...",
            })

            result = await optimizer.optimize(
                diagrams=diagrams if diagrams else None,
                instructions=instructions,
            )

            self.progress and self.progress.emit({
                "event": "sub_agent",
                "agent": "UmlOptimizer",
                "status": "done",
            })

            return json.dumps({
                "diagrams": result.get("diagrams", []),
                "design_constraints": result.get("design_constraints", {}),
                "changes_summary": result.get("changes_summary", ""),
                "consistency_report": result.get("consistency_report", []),
            }, ensure_ascii=False)
        except Exception as e:
            logger.exception("[OptimizeUmlTool] Failed")
            return json.dumps({
                "error": f"UML optimization failed: {e}",
                "diagrams": diagrams,
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
                        "diagrams_json": {
                            "type": "string",
                            "description": "现有图列表 JSON 字符串，格式为 [{\"type\":\"class\",\"data\":{...}}, ...]。空数组表示从零生成。",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "用户的优化指令，例如'增加支付模块，完善异常处理'",
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
                "验证 Python 代码文件的正确性。"
                "检查语法错误、导入错误、运行时错误。"
                "发现错误会自动修复并重新验证。"
                "返回验证报告和（可能修复后的）代码文件。"
                "code_files_json 传入 {\"filename\": \"content\", ...} 格式的 JSON 字符串。"
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
                            "description": "代码文件 JSON 字符串: {\"filename.py\": \"source code\", ...}",
                        },
                        "task": {
                            "type": "string",
                            "description": "验证任务的简短描述",
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
                "修复源码以通过测试。使用 pytest 运行测试，分析失败原因，"
                "修改源码，然后重新运行测试，循环直到所有测试通过。"
                "source_files_json 和 test_files_json 都是 {\"filename\": \"content\", ...} 的 JSON 字符串。"
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
                            "description": "源码文件 JSON: {\"app.py\": \"code...\", ...}",
                        },
                        "test_files_json": {
                            "type": "string",
                            "description": "测试文件 JSON: {\"test_app.py\": \"code...\", ...}",
                        },
                        "task": {
                            "type": "string",
                            "description": "修复任务描述",
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
                "从 UML 设计图（类图 JSON）生成 Python 源码。"
                "输入 diagram_json — 单张类图的 JSON 表示。"
                "返回生成的代码文件字典。"
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
                            "description": "UML 类图的 JSON 表示（从 optimize_uml 返回的 diagrams 数组中取 class 类型的图）",
                        },
                        "language": {
                            "type": "string",
                            "description": "目标语言，默认 python",
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
                "从 Python 源码生成 pytest 测试文件。"
                "自动为每个模块创建对应的 test_<module>.py 文件。"
                "覆盖正常路径、边界情况和错误处理。"
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
                            "description": "源码文件 JSON: {\"app.py\": \"code...\", ...}",
                        },
                        "test_cases": {
                            "type": "string",
                            "description": "测试用例描述（可选），如 '验证登录、注册、密码重置'",
                        },
                        "language": {
                            "type": "string",
                            "description": "目标语言，默认 python",
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
                "运行 pytest 测试。用于快速检查测试是否通过。"
                "返回测试输出、通过率、失败详情。"
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
                            "description": "源码文件 JSON",
                        },
                        "test_files_json": {
                            "type": "string",
                            "description": "测试文件 JSON",
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
                "将代码文件写入磁盘。用于保存最终版本的源码和测试文件。"
                "files_json: {\"filename\": \"content\", ...} 的 JSON 字符串。"
                "file_type: 'source' 或 'test'。"
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
                            "description": "文件 JSON: {\"filename.py\": \"code...\", ...}",
                        },
                        "file_type": {
                            "type": "string",
                            "description": "'source' 或 'test'",
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
    include_review: bool = True,
    progress: ProgressRelay | None = None,
) -> tuple[list[Tool], ReviewManager | None]:
    """创建对话 Agent 可用的完整工具集。

    Returns:
        (tools, review_manager) — tool 列表 + 审核管理器（若启用）
    """
    tools: list[Tool] = [
        OptimizeUmlTool(llm, progress=progress),
        GenerateCodeTool(llm, progress=progress),
        ValidateCodeTool(llm, source_dir=source_dir, progress=progress),
        GenerateTestsTool(llm, progress=progress),
        FixCodeTool(llm, source_dir=source_dir, test_dir=test_dir, progress=progress),
        RunTestsTool(source_dir=source_dir, test_dir=test_dir, progress=progress),
        WriteFilesTool(source_dir=source_dir, test_dir=test_dir, progress=progress),
    ]

    review_mgr: ReviewManager | None = None
    if include_review:
        from app.agent_base.tools.review import RequestReviewTool, ReviewManager
        review_mgr = ReviewManager()
        tools.append(RequestReviewTool(manager=review_mgr))

    return tools, review_mgr
