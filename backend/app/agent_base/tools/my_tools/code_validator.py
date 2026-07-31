"""CodeValidator — Agent 驱动的代码验证器

替代 ``ReActEngine``，基于 ReActAgent (Function Calling) 实现
生成代码的自动化验证与修复。

核心能力:
- 语法检查 (ast.parse)
- 导入检查 (python -c "import module")
- 模块运行 (python -c "import module")
- 安全 Bash 执行 (沙箱化的环境查询)
- 错误分析 + 差异对比
- 变更率控制 (server-side guard, 与 ReActEngine 一致)
- 设计约束注入

Usage::

    from app.agent_base.tools.my_tools.code_validator import CodeValidator

    llm = BaseAgentsLLM.from_settings()
    validator = CodeValidator(llm, language="python", max_rounds=5,
                              change_ratio=30, design_constraints={...})

    # 流式验证 — 兼容 ReActEngine 的接口
    async for progress in validator.validate_stream(
        code_files={"app.py": "print('hello')"},
        task_description="Validate generated code",
    ):
        if "result" in progress:
            print(f"Success: {progress['result']['success']}")
        else:
            print(f"Round {progress['round']}: {progress['react_steps']}")
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.agents.react_agent import ReActAgent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 验证工具 — 直接复用 services/tools.py 的实现
# ═══════════════════════════════════════════════════════════

def _create_validation_tools(
    language: str = "python",
    generated_dir: str = "",
    original_code: dict[str, str] | None = None,
    change_ratio: int = 0,
) -> list[Tool]:
    """构建验证工具集。

    直接使用 ``services/tools.py`` 的核心函数，
    封装为 BaseAgents 的 Tool 对象。
    """
    # 默认生成目录
    if not generated_dir:
        import os
        generated_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "generated"))
    from app.services.tools import (
        _check_imports,
        _run_module,
        _run_bash,
        _analyze_error,
        _diff_code,
    )

    tools: list[Tool] = []

    # ── 1. check_imports — 语法 + 导入 ──
    tools.append(
        _make_async_tool(
            name="check_imports",
            description=(
                "Validate syntax AND imports for Python files. "
                "Pass source_dir to check files on disk (preferred — no code transfer needed). "
                "Pass code_files dict only when files are not on disk yet. "
                "Reports per-file pass/fail with specific error messages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_dir": {
                        "type": "string",
                        "description": "Directory containing .py files (preferred — reads from disk)",
                    },
                    "code_files": {
                        "type": "object",
                        "description": "Map of filename→content (fallback when files not on disk)",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            fn=lambda source_dir="", code_files=None: _check_imports(
                language, code_files, source_dir,
            ),
        )
    )

    # ── 2. run_module — 执行 import ──
    tools.append(
        _make_async_tool(
            name="run_module",
            description=(
                "Run 'python -c \"import <module_name>\"' to catch ImportError/SyntaxError. "
                "Pass source_dir to run from disk (preferred). Pass code_files only as fallback."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "module_name": {
                        "type": "string",
                        "description": "Module name to import (without .py extension)",
                    },
                    "source_dir": {
                        "type": "string",
                        "description": "Directory to add to sys.path (preferred)",
                    },
                    "code_files": {
                        "type": "object",
                        "description": "Map of filename→content (fallback)",
                    },
                },
                "required": ["module_name"],
                "additionalProperties": False,
            },
            fn=lambda module_name, source_dir="", code_files=None: _run_module(
                language, module_name, code_files, source_dir,
            ),
        )
    )

    # ── 3. run_bash — 安全沙箱命令 ──
    tools.append(
        _make_async_tool(
            name="run_bash",
            description=(
                "Execute a READ-ONLY bash command to inspect environment, "
                "list files, check installed packages, or run tests (e.g. pytest). "
                "Pipes (|) allowed between allowed commands. "
                "PROHIBITED: rm, sudo, curl, kill, file writes (>, >>), "
                "command chaining (; && ||), command substitution ($(), ``). "
                "Each call capped at 30s timeout and 50 KiB output."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            fn=lambda command: _run_bash(command),
        )
    )

    # ── 4. analyze_error — 错误分析 ──
    tools.append(
        _make_async_tool(
            name="analyze_error",
            description="Analyze a compilation or runtime error and extract key information.",
            parameters={
                "type": "object",
                "properties": {
                    "error_message": {
                        "type": "string",
                        "description": "Full error output from compiler or runtime",
                    },
                    "code_files": {
                        "type": "object",
                        "description": "Map of filename→content for context",
                    },
                },
                "required": ["error_message", "code_files"],
                "additionalProperties": False,
            },
            fn=lambda error_message, code_files: _analyze_error(
                language, error_message, code_files,
            ),
        )
    )

    # ── 5. diff_code — 差异对比 ──
    tools.append(
        _make_async_tool(
            name="diff_code",
            description="Compare original and modified code to see what changed.",
            parameters={
                "type": "object",
                "properties": {
                    "original": {
                        "type": "object",
                        "description": "Original filename→content map",
                    },
                    "modified": {
                        "type": "object",
                        "description": "Modified filename→content map",
                    },
                },
                "required": ["original", "modified"],
                "additionalProperties": False,
            },
            fn=lambda original, modified: _diff_code(original, modified),
        )
    )

    # ── 6. finish_validation — 完成信号 ──
    tools.append(
        _make_async_tool(
            name="finish_validation",
            description=(
                "Signal that validation is complete and return the final (fixed) code files. "
                "Call this ONLY when all checks have passed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code_files": {
                        "type": "object",
                        "description": "Final validated filename→content map",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Summary of changes made and validation results",
                    },
                    "remaining_issues": {
                        "type": "string",
                        "description": "Any known remaining issues (empty string if none)",
                    },
                },
                "required": ["code_files", "summary", "remaining_issues"],
                "additionalProperties": False,
            },
            fn=lambda code_files, summary, remaining_issues="": json.dumps({
                "status": "complete",
                "files": list(code_files.keys()) if isinstance(code_files, dict) else [],
                "summary": summary,
                "remaining_issues": remaining_issues,
            }, ensure_ascii=False),
        )
    )

    return tools


# ═══════════════════════════════════════════════════════════
# Helper: 将 async 函数包装为 BaseAgents Tool
# ═══════════════════════════════════════════════════════════

class _AsyncTool(Tool):
    """内部工具类 — 将 JSON Schema + 异步函数包装为 Tool"""

    def __init__(self, name: str, description: str,
                 parameters: dict, fn):
        super().__init__(name=name, description=description)
        self._parameters = parameters
        self._fn = fn

    def get_parameters(self):
        return []

    def to_openai_schema(self) -> dict:
        """直接返回 JSON Schema — 绕过 TemplateParameter 限制。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._parameters,
            },
        }

    def run(self, parameters: dict) -> str:
        """返回 coroutine — 由 aexecute_tool_with_params await。"""
        return self._fn(**parameters)


def _make_async_tool(name: str, description: str,
                     parameters: dict, fn) -> Tool:
    """创建一个验证工具（支持 async 函数）。"""
    return _AsyncTool(name, description, parameters, fn)


# ═══════════════════════════════════════════════════════════
# CodeValidator
# ═══════════════════════════════════════════════════════════

class CodeValidator:
    """Agent 驱动的代码验证器。

    组合 ReActAgent (FC) + 验证工具 + 约束守卫，
    提供与 ReActEngine 完全兼容的流式接口。

    Usage::

        llm = BaseAgentsLLM.from_settings()
        validator = CodeValidator(llm, max_rounds=5, change_ratio=30)

        async for progress in validator.validate_stream(
            code_files={"app.py": "..."},
            task_description="Validate and fix errors",
        ):
            if "result" in progress:
                # progress["result"] = {"success": bool, "final_code": {...}, ...}
                pass
            else:
                # progress = {"react_steps": [...], "round": N}
                pass
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        language: str = "python",
        max_rounds: int = 5,
        change_ratio: int = 0,
        design_constraints: dict | None = None,
        generated_dir: str = "",
    ):
        self.llm = llm
        self.language = language
        self.max_rounds = max_rounds
        self.change_ratio = change_ratio
        self.design_constraints = design_constraints
        self.generated_dir = generated_dir

    # ── Public API ────────────────────────────────────

    async def validate_stream(
        self,
        code_files: dict[str, str],
        task_description: str = "",
        original_code: dict[str, str] | None = None,
    ) -> AsyncIterator[dict]:
        """流式验证代码，yield 与 ReActEngine 兼容的 progress dict。

        Yields:
            ``{"react_steps": [...], "round": N}`` after each round,
            ``{"result": {success, final_code, summary, steps, ...}}`` on completion.
        """
        if not code_files:
            yield {"result": {"success": True, "final_code": {}, "summary": "No files to validate", "steps": []}}
            return

        # ── 1. 构建工具注册表 ──
        registry = ToolRegistry()
        orig = original_code or {}
        tools = _create_validation_tools(
            language=self.language,
            generated_dir=self.generated_dir,
            original_code=orig,
            change_ratio=self.change_ratio,
        )
        for t in tools:
            registry.register_tool(t)

        # ── 2. 构建 system prompt ──
        system_prompt = self._build_system_prompt()

        # ── 3. 构建 user prompt ──
        files_text = "\n\n".join(
            f"### {fname}\n```{self.language}\n{content}\n```"
            for fname, content in code_files.items()
        )

        # 检测主模块
        main_module = self._detect_main_module(code_files)

        user_prompt = f"""## Task: {task_description or 'Validate generated code — fix syntax, import, and runtime errors'}

## Generated Code to Validate:
{files_text[:8000]}

## Entry Point for run_module
Use **{main_module or 'N/A'}** — the main application module.
Call run_module ONCE with this module name.

Start by validating syntax, then check imports, then try running the module.
Fix any problems found, then call finish_validation.
"""

        # ── 4. 创建 ReActAgent ──
        agent = ReActAgent(
            name="CodeValidator",
            llm=self.llm,
            tool_registry=registry,
            system_prompt=system_prompt,
            max_steps=self.max_rounds,
            use_native_fc=True,
        )

        # ── 5. 运行流式循环 ──
        steps_for_frontend: list[dict] = []
        final_code = dict(code_files)
        final_summary = ""
        final_remaining = ""

        async for progress in agent.arun_stream(user_prompt):
            d = progress.to_dict()

            # 序列化 steps（兼容 ReActEngine 的 _serialize_steps 格式）
            serialized = {
                "round": d["step"],
                "thought": d["thought"][:300],
                "action": ", ".join(d["actions"]),
                "action_input": (
                    d["tool_calls_detail"][0].get("arguments", {})
                    if d["tool_calls_detail"] else {}
                ),
                "observation": (
                    "\n".join(td.get("observation", "")[:300]
                              for td in d["tool_calls_detail"])
                ),
                "is_final": d["is_final"],
            }
            steps_for_frontend.append(serialized)

            # 检测 finish_validation 调用 — 从 arguments 提取 code_files
            for td in d["tool_calls_detail"]:
                if td.get("name") == "finish_validation":
                    args = td.get("arguments", {})
                    if isinstance(args, dict) and args.get("code_files"):
                        code_files_from_args = args["code_files"]
                        if isinstance(code_files_from_args, dict) and code_files_from_args:
                            final_code = code_files_from_args
                    # summary / remaining_issues 从 observation（返回值）提取
                    obs_str = td.get("observation", "")
                    try:
                        obs_data = json.loads(obs_str)
                        if obs_data.get("status") == "complete":
                            final_summary = obs_data.get("summary", args.get("summary", ""))
                            final_remaining = obs_data.get("remaining_issues", args.get("remaining_issues", ""))
                    except json.JSONDecodeError:
                        final_summary = args.get("summary", "")
                        final_remaining = args.get("remaining_issues", "")

            # 检查变更率（server-side guard）
            if d["is_final"] and self.change_ratio > 0 and original_code:
                change_ok = await self._check_change_ratio_guard(
                    original_code, final_code,
                )
                if not change_ok:
                    # 不终止 — 继续让 Agent 修复
                    yield {
                        "react_steps": steps_for_frontend,
                        "round": d["step"],
                        "change_ratio_blocked": True,
                    }
                    continue

            yield {
                "react_steps": steps_for_frontend,
                "round": d["step"],
            }

            if d["is_final"]:
                break

        # ── 6. 最终结果 ──
        # 尝试从 messages 中提取 final_code（finish_validation 参数）
        yield {
            "result": {
                "success": True,
                "final_code": final_code,
                "summary": final_summary or "Validation complete",
                "steps": steps_for_frontend,
                "remaining_issues": final_remaining,
            },
        }

    # ── Internal ──────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """构建验证专用的 system prompt。"""
        parts = [
            f"You are an expert {self.language} code validator. Your job is simple: "
            "validate code files and call finish_validation.",
            "",
            "## CRITICAL: Exact Workflow",
            "1. Call check_imports(source_dir=...) AND run_module(module_name=..., source_dir=...) TOGETHER",
            "2. If BOTH pass: IMMEDIATELY call finish_validation. STOP. Do NOT call any more tools.",
            "3. If ANY fail: fix the file, re-save it, go back to step 1",
            "",
            "## Rules (READ CAREFULLY)",
            "- Do NOT use run_bash for checking files — check_imports already verifies everything",
            "- Do NOT use cat, type, ls, or any file inspection after validation passes",
            "- Once check_imports returns passed:[] and failed:{}, and run_module returns exit_code=0, "
            "  you are DONE — call finish_validation NOW",
            "- You have VERY limited rounds — do not waste them on redundant checks",
            "- Call finish_validation ONLY when every validation passes",
        ]

        # 变更率约束
        if self.change_ratio > 0:
            parts.extend([
                "",
                f"## Change Limit ({self.change_ratio}%)",
                f"This is an EXISTING project — code modifications MUST stay within {self.change_ratio}% per file.",
                "- Only modify what is NECESSARY to fix errors — do not rewrite or restructure working code",
                "- New files (not in the original) are exempt from the limit",
                "- If a validation error genuinely requires a large change, note it in finish_validation remaining_issues",
            ])

        # 设计约束注入
        if self.design_constraints:
            constraints_block = self._build_constraints_block()
            if constraints_block:
                parts.append("")
                parts.append(constraints_block)

        return "\n".join(parts)

    def _build_constraints_block(self) -> str:
        """从设计约束构建 prompt 块。"""
        if not self.design_constraints:
            return ""

        lines = [
            "## Design Constraints (MUST preserve across validation)",
            "These were extracted from the UML optimisation stage. Do NOT violate them.",
            "",
        ]
        must_preserve = self.design_constraints.get("must_preserve", [])
        if must_preserve:
            lines.append("**Must preserve:**")
            for item in must_preserve:
                lines.append(f"  - {item}")
            lines.append("")

        immutable = self.design_constraints.get("immutable_entities", [])
        if immutable:
            lines.append("**Immutable entities** (do NOT rename or delete):")
            lines.append("  " + ", ".join(immutable))
            lines.append("")

        rationale = self.design_constraints.get("design_rationale", "")
        if rationale:
            lines.append(f"**Design rationale:** {rationale}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _detect_main_module(code_files: dict[str, str]) -> str:
        """检测主模块名。"""
        py_files = [f for f in code_files if f.endswith(".py") and not f.startswith("test_")]
        for keyword in ("app", "main"):
            for f in py_files:
                if keyword in f.lower():
                    return f.replace(".py", "")
        for f in py_files:
            content = code_files.get(f, "")
            if "class" in content and "ABC" not in content and "abstract" not in content.lower():
                return f.replace(".py", "")
        if py_files:
            return py_files[0].replace(".py", "")
        return ""

    async def _check_change_ratio_guard(
        self, original: dict, modified: dict,
    ) -> bool:
        """服务端变更率检查。返回 True 表示通过。"""
        from app.services.tools import _check_change_ratio
        cr_result = await _check_change_ratio(original, modified, self.change_ratio)
        cr_data = json.loads(cr_result)
        if cr_data.get("exceeds_threshold"):
            exceeded = cr_data.get("exceeded_files", [])
            total_pct = cr_data.get("total_pct", 0)
            logger.warning(
                f"[CodeValidator] Change ratio BLOCKED: {len(exceeded)} file(s) "
                f"exceed {self.change_ratio}% (total: {total_pct}%)"
            )
            return False
        logger.info(
            f"[CodeValidator] Change ratio OK: {cr_data.get('total_pct', 0)}% "
            f"within {self.change_ratio}% limit"
        )
        return True
