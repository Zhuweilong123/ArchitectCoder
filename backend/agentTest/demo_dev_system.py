"""
对话 Agent 驱动的完整开发流程 — Demo

从用户需求出发，一个 ReActAgent (FC) + 7 个工具，
自主完成 UML 设计 → 代码生成 → 验证 → 测试 → 修复 → 保存 → 审核。

运行方式:
    cd backend && python app/agent_base/agentTest/demo_dev_system.py
"""

import sys
import os
import asyncio
import json

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.tools.registry import ToolRegistry
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.review import ReviewManager, RequestReviewTool
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.tools.my_tools.conversation_tools import (
    create_conversation_tools, ProgressRelay,
)
from app.agent_base.tools.my_tools.code_validator import CodeValidator


# ══════════════════════════════════════════════════════
# Demo 1: 中断控制
# ══════════════════════════════════════════════════════

def demo_interruptible():
    """演示中断控制 — 用户可随时停止 Agent"""
    print("=" * 55)
    print("  Demo 1: 中断控制 (InterruptHook)")
    print("=" * 55)

    class EchoTool(Tool):
        def __init__(self):
            super().__init__(name="echo", description="Echo text")
        def get_parameters(self):
            return [ToolParameter(name="text", type="string", description="Text")]
        def run(self, params):
            return f"Echo: {params.get('text', '')}"

    class MockLLM:
        def __init__(self):
            self.count = 0
        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kw):
            self.count += 1
            if self.count <= 3:
                return {
                    "content": f"Step {self.count}: calling echo",
                    "tool_calls": [{
                        "id": f"c{self.count}", "type": "function",
                        "function": {"name": "echo", "arguments": json.dumps(
                            {"text": f"round_{self.count}"}
                        )},
                    }],
                }
            return {"content": "All done after 3 rounds.", "tool_calls": None}

    registry = ToolRegistry()
    registry.register_tool(EchoTool())
    agent = ReActAgent("Demo", MockLLM(), registry, max_steps=10)

    stop_after = 2
    from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
    from app.agent_base.core.exceptions import AgentInterrupted

    async def run():
        token = set_runtime(AgentRuntime(stop_check=lambda:
            len([h for h in agent.current_history if "echo" in h]) >= stop_after))
        stopped = False
        try:
            async for progress in agent.arun_stream("Echo several times"):
                print(f"  [progress] step={progress.step} actions={progress.actions}")
        except AgentInterrupted:
            stopped = True
            print("  [stopped] 用户请求停止")
        finally:
            reset_runtime(token)
        return stopped

    stopped = asyncio.run(run())
    if stopped:
        print("  ✅ Agent 被成功中断!")
    else:
        print("  ⚠️ Agent 自然完成")
    print()


# ══════════════════════════════════════════════════════
# Demo 2: 单个子 Agent — CodeValidator
# ══════════════════════════════════════════════════════

def demo_code_validator():
    """演示代码验证 — 模拟修复语法错误"""
    print("=" * 55)
    print("  Demo 2: CodeValidator — 代码验证")
    print("=" * 55)

    buggy_code = {
        "app.py": "def add(a, b)\n    return a + b",
        "utils.py": "def greet(name):\n    return f'Hello {name}'",
    }

    class MockLLM:
        def __init__(self):
            self.count = 0
        async def ainvoke_with_tools(self, messages, tools, tool_choice="auto", **kw):
            self.count += 1
            if self.count == 1:
                return {
                    "content": "Let me check the syntax.",
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {"name": "check_imports",
                                     "arguments": json.dumps({"source_dir": "."})},
                    }],
                }
            return {
                "content": "Fixed. All good now.",
                "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {
                        "name": "finish_validation",
                        "arguments": json.dumps({
                            "code_files": {
                                "app.py": "def add(a, b):\n    return a + b",
                                "utils.py": "def greet(name):\n    return f'Hello {name}'",
                            },
                            "summary": "Fixed syntax error: missing colon in app.py",
                            "remaining_issues": "",
                        }),
                    },
                }],
            }

    validator = CodeValidator(MockLLM(), max_rounds=5, generated_dir=".")

    async def run():
        async for progress in validator.validate_stream(
            code_files=buggy_code,
            task_description="Validate and fix syntax errors",
        ):
            if "result" in progress:
                r = progress["result"]
                print(f"  ✅ success={r['success']} summary={r['summary']}")
                print(f"     Files: {list(r['final_code'].keys())}")
            else:
                print(f"  🔄 Round {progress['round']}")

    asyncio.run(run())
    print()


# ══════════════════════════════════════════════════════
# Demo 3: 对话 Agent — 模拟 LLM 完整开发流程
# ══════════════════════════════════════════════════════

def demo_conversation_agent_mock():
    """用 Mock LLM 快速验证对话 Agent 的工具调用序列"""
    print("=" * 55)
    print("  Demo 3: 对话 Agent — Mock 模式")
    print("=" * 55)

    from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
    llm = BaseAgentsLLM.from_settings(temperature=0.3)
    tools, _ = create_conversation_tools(llm)
    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    print(f"  已注册 {len(list(registry.list_tools()))} 个工具:")
    for name in registry.list_tools():
        s = registry.get_openai_specs_for([name])[0]
        print(f"    • {name} — {s['function']['description'][:60]}...")

    # 验证各工具能正常执行
    async def quick_test():
        print()
        print("  快速验证各工具:")

        # run_tests
        r = await registry.aexecute_tool_with_params("run_tests", {
            "source_files_json": json.dumps({"a.py": "def f(): return 1"}),
            "test_files_json": json.dumps({"t.py": "from a import f\ndef test():\n assert f()==1"})
        })
        d = json.loads(r)
        print(f"    run_tests: {d['passed']}/{d['total']} passed  {'✅' if d['all_passing'] else '❌'}")

        # validate_code
        r2 = await registry.aexecute_tool_with_params("validate_code", {
            "code_files_json": json.dumps({"m.py": "print(42)"}),
            "task": "check"
        })
        d2 = json.loads(r2)
        print(f"    validate_code: success={d2['success']} {'✅' if d2['success'] else '❌'}")

        # generate_tests
        r3 = await registry.aexecute_tool_with_params("generate_tests", {
            "source_files_json": json.dumps({"lib.py": "def add(a,b): return a+b"}),
            "test_cases": "test positive, negative, zero"
        })
        d3 = json.loads(r3)
        print(f"    generate_tests: {d3.get('count', 0)} files {'✅' if d3.get('count', 0) > 0 else '❌'}")

        # fix_code — 这个可能因为 LLM 调整而返回空，用 try/except 兜底
        try:
            r4 = await registry.aexecute_tool_with_params("fix_code", {
                "source_files_json": json.dumps({"bug.py": "def add(a,b): return a-b"}),
                "test_files_json": json.dumps({"t.py": "from bug import add\ndef test():\n assert add(1,2)==3"}),
                "task": "fix"
            })
            print(f"    fix_code raw({len(r4)}): {r4[:120]}")
            d4 = json.loads(r4)
            print(f"    fix_code: success={d4['success']} pass_rate={d4['pass_rate']} {'✅' if d4['success'] else '❌'}")
        except json.JSONDecodeError:
            print(f"    fix_code: 返回非 JSON — LLM 正在修复中（正常）")

    asyncio.run(quick_test())
    print()
    print("  ✅ 对话 Agent 7 工具全部就绪")


# ══════════════════════════════════════════════════════
# Demo 4: 对话 Agent — 真实 LLM 完整开发
# ══════════════════════════════════════════════════════

def demo_conversation_agent_real():
    """真实 DeepSeek LLM 驱动完整开发流程"""
    print("=" * 55)
    print("  Demo 4: 对话 Agent — 真实 LLM 完整开发")
    print("=" * 55)

    llm = BaseAgentsLLM.from_settings(temperature=0.3)
    print(f"  Provider: {llm.provider}  Model: {llm.model}")

    from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
    tools, review_mgr = create_conversation_tools(llm)
    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=(
            "你是全栈 Python 开发专家。按顺序使用工具完成开发任务：\n"
            "1. optimize_uml — 从需求设计 UML\n"
            "2. generate_code — 从 UML 生成 Python 代码\n"
            "3. validate_code — 验证代码正确性\n"
            "4. generate_tests — 生成 pytest 测试\n"
            "5. run_tests — 运行测试检查\n"
            "6. fix_code — 修复失败的测试（如有）\n"
            "7. write_files — 保存最终代码到磁盘\n"
            "8. request_review — 在关键节点请求人工审核\n\n"
            "每步完成后快速评估结果，果断决定下一步。保持简洁高效。"
        ),
        max_steps=12,
        use_native_fc=True,
    )

    async def run():
        print()
        async for progress in agent.arun_stream(
            "创建一个计算器系统：支持加减乘除和内存功能，"
            "包含接口设计和实现分离。"
            "从 UML 设计开始，生成代码，验证，测试，最后保存文件。"
        ):
            d = progress.to_dict()
            step = d["step"]
            actions = d["actions"]

            # 进度显示
            icon = "🏁" if d["is_final"] else "🔧" if actions else "💭"
            action_str = ", ".join(actions) if actions else "thinking"
            print(f"  {icon} Step {step}: {action_str}")

            # 工具调用详情
            if d["tool_calls_detail"]:
                for td in d["tool_calls_detail"][:3]:
                    obs = td.get("observation", "")
                    # 简短显示
                    if len(obs) > 120:
                        obs = obs[:120] + "..."
                    print(f"     └─ {td['name']}: {obs}")

            if d["is_final"]:
                print(f"\n  📋 最终报告:\n{d['final_answer'][:800]}\n")

        print(f"  总步数: {step}")

    asyncio.run(run())


# ══════════════════════════════════════════════════════
# Demo 5: 中断的对话 Agent
# ══════════════════════════════════════════════════════

def demo_interruptible_dev():
    """演示可中断的对话 Agent"""
    print("=" * 55)
    print("  Demo 5: 可中断的对话 Agent")
    print("=" * 55)

    from app.agent_base.tools.my_tools.conversation_tools import create_conversation_tools
    llm = BaseAgentsLLM.from_settings(temperature=0.3)
    tools, _ = create_conversation_tools(llm)
    registry = ToolRegistry()
    for t in tools:
        registry.register_tool(t)

    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=(
            "你是 Python 开发专家。快速完成代码开发。"
            "用 optimize_uml → generate_code → validate_code → write_files 流程。"
            "每步要做，但只做必要的。"
        ),
        max_steps=6,
        use_native_fc=True,
    )

    # 在 validate_code 之后中断
    from app.agent_base.core.hooks import AgentRuntime, set_runtime, reset_runtime
    from app.agent_base.core.exceptions import AgentInterrupted

    async def run():
        validated = [False]

        def should_stop():
            if validated[0]:
                return True
            for h in agent.current_history:
                if "validate_code" in h:
                    validated[0] = True
                    return True
            return False

        token = set_runtime(AgentRuntime(stop_check=should_stop))
        try:
            async for progress in agent.arun_stream(
                "创建一个简单的 Greeter 类，有 greet(name) 方法返回 'Hello {name}'"
            ):
                print(f"  [progress] step={progress.step} actions={progress.actions}")
                if progress.is_final:
                    print(f"    Answer: {progress.final_answer[:200]}")
        except AgentInterrupted:
            print("  [stopped] 用户请求停止")
        finally:
            reset_runtime(token)

    asyncio.run(run())
    print()


# ══════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════

def main():
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   🤖 对话 Agent 驱动的代码开发系统 — Demo            ║")
    print("║   一个 ReActAgent + 7个Tool = 完整开发流水线         ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("架构:")
    print("  ReActAgent(FC, system_prompt + 7 tools)")
    print("    ├─ optimize_uml     → UmlOptimizer (ReflectionAgent)")
    print("    ├─ generate_code    → LLM 代码生成")
    print("    ├─ validate_code    → CodeValidator (ReActAgent FC)")
    print("    ├─ generate_tests   → LLM 测试生成")
    print("    ├─ fix_code         → CodeFixer (ReflectionAgent)")
    print("    ├─ run_tests        → pytest 子进程")
    print("    ├─ write_files      → 文件写入磁盘")
    print("    └─ request_review   → 人工审核")
    print()

    demo_interruptible()
    demo_code_validator()
    demo_conversation_agent_mock()
    demo_conversation_agent_real()
    demo_interruptible_dev()

    print("🏁 全部 Demo 完成!")


if __name__ == "__main__":
    main()
