"""CodeFixer — 测试驱动的代码修复 (基于 ReflectionAgent)

用 pytest 验证代码，用 ReflectionAgent 迭代修复。
替代 Pipeline Stage 6 的固定 3 轮循环，Agent 自主判断何时完成。

Usage::

    from app.agent_base.tools.my_tools.code_fixer import CodeFixer

    fixer = CodeFixer(llm, max_iterations=5, source_dir="src/", test_dir="tests/")
    result = fixer.fix(
        source_code={"app.py": "def add(a,b): return a-b"},
        test_code={"test_app.py": "from app import add; assert add(1,2)==3"},
    )
    # result = {success, final_source, test_output, iterations, pass_rate}
"""

from __future__ import annotations

import json
import logging
import asyncio
import subprocess
import os
import sys
from typing import Any, Optional

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.agents.reflection_agent import ReflectionAgent

logger = logging.getLogger(__name__)

# ── 代码修复专用提示词模板 ───────────────────────────────

FIXER_PROMPTS = {
    "initial": """You are a professional Python code fixing expert.

## Task:
{task}

## Project context:
{context}

Analyze the source and test code, fix the bugs in the source so that all tests pass.
Output only the fixed complete source files (mark each file with ### filename), no other explanation.
""",
    "reflect": """You are a code review expert. Review the fixed code below.

## Code:
{content}

## Automated test result:
{auto_feedback}

Analyze:
1. Which tests still fail? What are the failure reasons?
2. Is the fix correct, without introducing new bugs?
3. Are there any unhandled edge cases?

If all tests pass and the fix is correct, reply "no improvement needed".
Otherwise, give specific modification suggestions.
""",
    "refine": """You are a code fixing expert. Fix the code based on the feedback.

## Previous code:
{last_attempt}

## Review feedback:
{feedback}

Output only the fixed complete code files, marking each file's start with ### filename.
""",
}

# ── pytest 执行函数 ────────────────────────────────────

async def _run_pytest(
    source_files: dict[str, str],
    test_files: dict[str, str],
    source_dir: str = "",
    test_dir: str = "",
    timeout: int = 60,
) -> str:
    """执行 pytest 并返回格式化结果。

    将源文件和测试文件写入临时目录，运行 pytest，收集输出。
    """
    import tempfile
    import shutil

    # 总是使用临时目录确保执行隔离
    work_dir = tempfile.mkdtemp(prefix="codefixer_")
    for fname, content in {**source_files, **test_files}.items():
        fpath = os.path.join(work_dir, fname)
        os.makedirs(os.path.dirname(fpath) or work_dir, exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

    try:
        test_path = work_dir
        cmd = [
            sys.executable, "-m", "pytest", test_path,
            "-v", "--tb=short",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=work_dir,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 10,
        )
        output = stdout.decode("utf-8", errors="replace")[:10000]

        # ── 汇总 ──
        lines = output.split("\n")
        summary_lines = [l for l in lines if "passed" in l.lower()
                        or "failed" in l.lower() or "error" in l.lower()
                        or "===" in l]
        summary = "\n".join(summary_lines[-10:]) if summary_lines else output[-2000:]

        return f"exit_code={proc.returncode}\n{summary}"
    except asyncio.TimeoutError:
        return "TIMEOUT: pytest exceeded time limit"
    except Exception as e:
        return f"ERROR running pytest: {e}"
    finally:
        if not source_dir:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)


# ── CodeFixer ──────────────────────────────────────────

class CodeFixer:
    """测试驱动的代码修复器。

    使用 ReflectionAgent 的"生成 → 验证(pytest) → 修复"循环，
    自动迭代直到所有测试通过或达到最大轮数。

    Usage::

        llm = BaseAgentsLLM.from_settings()
        fixer = CodeFixer(llm, max_iterations=5)
        result = await fixer.fix(source_code, test_code)
    """

    def __init__(
        self,
        llm: BaseAgentsLLM,
        max_iterations: int = 5,
        source_dir: str = "",
        test_dir: str = "",
        pytest_timeout: int = 60,
    ):
        self.llm = llm
        self.max_iterations = max_iterations
        self.source_dir = source_dir
        self.test_dir = test_dir
        self.pytest_timeout = pytest_timeout

    async def fix(
        self,
        source_code: dict[str, str],
        test_code: dict[str, str],
        task: str = "Fix bugs to make all tests pass",
    ) -> dict[str, Any]:
        """执行测试驱动的代码修复。

        Returns:
            {
                "success": bool,
                "final_source": dict[str, str],
                "test_output": str,
                "iterations": int,
                "pass_rate": str,
            }
        """
        # 格式化源码和测试为 prompt
        src_text = "\n\n".join(
            f"### {fname}\n```python\n{content}\n```"
            for fname, content in source_code.items()
        )
        test_text = "\n\n".join(
            f"### {fname}\n```python\n{content}\n```"
            for fname, content in test_code.items()
        )

        # ── 预填充 context — 避免 .format() 中代码被解析 ──
        safe_context = json.dumps({
            "source_files": list(source_code.keys()),
            "test_files": list(test_code.keys()),
            "source_code": src_text,
            "test_code": test_text,
        }, ensure_ascii=False)

        # ── 预填充 initial prompt — 绕过 ReflectionAgent 的 .format() ──
        # ReflectionAgent.run() 会调用 prompts["initial"].format(task=..., context=...)
        # 我们的模板用 {task} 和 {context} 占位符（匹配 ReflectionAgent 的调用），
        # 源码和测试代码作为 JSON 放在 context 里，避免被二次解析
        FIXER_PROMPTS_SIMPLE = {
            "initial": (
                "You are a professional Python code fixing expert.\n\n"
                "## Task:\n{task}\n\n"
                "## Project context:\n{context}\n\n"
                "Analyze the source and test code, fix the bugs in the source so "
                "that all tests pass.\n"
                "Output only the fixed complete source files (mark each file with "
                "### filename), no other explanation."
            ),
            "reflect": (
                "You are a code review expert. Review the fixed code below.\n\n"
                "## Code:\n{content}\n\n"
                "## Automated test result:\n{auto_feedback}\n\n"
                "Analyze: 1. Which tests still fail? 2. Is the fix correct? "
                "3. Any unhandled edge cases?\n"
                "If all tests pass and the fix is correct, reply \"no improvement "
                "needed\". Otherwise give specific modification suggestions."
            ),
            "refine": (
                "You are a code fixing expert. Fix the code based on the "
                "feedback.\n\n"
                "## Previous code:\n{last_attempt}\n\n"
                "## Review feedback:\n{feedback}\n\n"
                "Output only the fixed complete code files, marking each file's "
                "start with ### filename."
            ),
        }

        # 创建 ReflectionAgent
        agent = ReflectionAgent(
            name="CodeFixer",
            llm=self.llm,
            max_iterations=self.max_iterations,
            custom_prompts=FIXER_PROMPTS_SIMPLE,
            context=safe_context,
        )

        logger.info("[CodeFixer] Starting fix loop: %d iterations max", self.max_iterations)

        # 运行 ReflectionAgent — input_text 映射到 initial 模板的 {task}
        final_answer = agent.run(
            input_text=task,
            reflect_hook=self._make_pytest_hook(source_code, test_code),
            post_process=self._extract_code_files,
        )

        # 最终测试运行
        final_source = self._parse_code_files(final_answer, source_code)
        final_test_output = await _run_pytest(
            final_source, test_code,
            source_dir=self.source_dir, test_dir=self.test_dir,
            timeout=self.pytest_timeout,
        )

        passed = self._count_passed(final_test_output)
        total = self._count_total(final_test_output)

        result = {
            "success": "failed" not in final_test_output.lower()
                       or "passed" in final_test_output.lower(),
            "final_source": final_source,
            "test_output": final_test_output,
            "iterations": agent.max_iterations,
            "pass_rate": f"{passed}/{total}" if total > 0 else "N/A",
        }
        logger.info("[CodeFixer] Done: pass_rate=%s", result["pass_rate"])
        return result

    # ── Hook 实现 ──────────────────────────────────────

    def _make_pytest_hook(
        self, source_code: dict[str, str], test_code: dict[str, str],
    ):
        """创建 pytest 验证 hook — 返回闭包给 ReflectionAgent 的 reflect_hook。"""

        def hook(task: str, content: str, context: str) -> str:
            """ReflectionAgent reflect_hook: 跑 pytest 验证当前代码。"""
            current_source = self._parse_code_files(content, source_code)
            return self._pytest_validate(current_source, test_code)

        return hook

    def _pytest_validate(
        self,
        source_code: dict[str, str],
        test_code: dict[str, str],
    ) -> str:
        """运行 pytest 并返回验证反馈。

        在 running event loop 中通过创建新线程的 event loop 来执行，
        避免 asyncio.run() 在已有 loop 中崩溃。
        """
        import concurrent.futures

        def _run_sync():
            return asyncio.run(_run_pytest(
                source_code, test_code,
                source_dir=self.source_dir, test_dir=self.test_dir,
                timeout=self.pytest_timeout,
            ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_sync)
            result = future.result(timeout=self.pytest_timeout + 30)

        passed = self._count_passed(result)
        total = self._count_total(result)
        if "failed" not in result.lower() and "error" not in result.lower():
            return ""  # 验证通过
        return f"测试结果 ({passed}/{total} 通过):\n{result}"

    @staticmethod
    def _count_passed(test_output: str) -> int:
        import re
        m = re.search(r"(\d+)\s+passed", test_output)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _count_total(test_output: str) -> int:
        import re
        passed = re.search(r"(\d+)\s+passed", test_output)
        failed = re.search(r"(\d+)\s+failed", test_output)
        err = re.search(r"(\d+)\s+error", test_output)
        total = 0
        for m in (passed, failed, err):
            if m:
                total += int(m.group(1))
        return total

    # ── 代码文件解析 ──────────────────────────────────

    @staticmethod
    def _parse_code_files(
        content: str, fallback: dict[str, str],
    ) -> dict[str, str]:
        """从 LLM 输出中提取代码文件。"""
        import re
        result = {}
        blocks = re.findall(
            r'###\s*(\S+)\s*\n```(?:\w+)?\n(.*?)```',
            content, re.DOTALL,
        )
        for fname, code in blocks:
            result[fname.strip()] = code.strip()
        return result if result else dict(fallback)

    def _extract_code_files(self, content: str) -> str:
        """后处理 hook — 保留代码文件格式。"""
        parsed = self._parse_code_files(content, {})
        if parsed:
            return "\n\n".join(
                f"### {fname}\n```python\n{code}\n```"
                for fname, code in parsed.items()
            )
        return content
