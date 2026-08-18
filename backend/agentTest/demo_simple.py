"""
SimpleAgent 对话示例

运行方式:
    cd backend && python app/agent_base/agentTest/demo_simple.py
"""

import sys
import os
import asyncio

# Windows 下强制 UTF-8 输出，避免 GBK 编码报错
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent_base import BaseAgentsLLM, SimpleAgent, ReActAgent, Config, ToolRegistry
from app.agent_base.tools.base import Tool, ToolParameter


def demo_basic_chat(llm):
    """基础对话"""
    print("=" * 50)
    print("  基础对话")
    print("=" * 50)

    agent = SimpleAgent(
        name="基础助手",
        llm=llm,
        system_prompt="你是一个友好的AI助手，请用简洁的方式回答问题。",
    )

    questions = [
        "用一句话解释什么是多态？",
        "Python 中的 with 语句有什么作用？",
    ]
    for q in questions:
        print(f"\n👤 用户: {q}")
        answer = agent.run(q)
        print(f"🤖 助手: {answer}")


def demo_tool_chat(llm):
    """带工具的对话"""
    print("\n" + "=" * 50)
    print("  工具增强对话")
    print("=" * 50)

    def my_calculator(expression: str) -> str:
        """安全数学表达式求值（AST 白名单）"""
        import ast, operator, math

        _OPS = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Pow: operator.pow, ast.USub: operator.neg,
        }
        _FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round}

        def _eval(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.BinOp):
                return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp):
                return _OPS[type(node.op)](_eval(node.operand))
            if isinstance(node, ast.Call):
                args = [_eval(a) for a in node.args]
                return _FUNCS[node.func.id](*args)
            raise ValueError(f"不支持: {ast.dump(node)}")

        try:
            tree = ast.parse(expression.strip(), mode="eval")
            return str(_eval(tree.body))
        except Exception as e:
            return f"计算失败: {e}"

    registry = ToolRegistry()
    registry.register_function(
        name="calculator",
        description="数学计算工具，支持加减乘除",
        func=my_calculator,
    )

    agent = SimpleAgent(
        name="计算助手",
        llm=llm,
        system_prompt="你是一个会计算的助手，遇到数学问题请使用计算器工具。",
        tool_registry=registry,
        enable_tool_calling=True,
    )

    questions = [
        "25 * 4 + 18 等于多少？",
        "帮我算一下 365 / 7 的结果",
    ]
    for q in questions:
        print(f"\n👤 用户: {q}")
        answer = agent.run(q)
        print(f"🤖 助手: {answer}")


def demo_streaming(llm):
    """流式输出"""
    print("\n" + "=" * 50)
    print("  流式输出")
    print("=" * 50)

    agent = SimpleAgent(
        name="流式助手",
        llm=llm,
        system_prompt="你是一个友好的助手。",
    )

    print("\n👤 用户: 请用三句话介绍 Python\n")
    print("🤖 助手: ", end="", flush=True)
    for chunk in agent.stream_run("请用三句话介绍 Python"):
        pass  # stream_run 内部已实时打印，这里只消费迭代器
    print()


def demo_history(llm):
    """多轮对话与历史"""
    print("=" * 50)
    print("  多轮对话")
    print("=" * 50)

    agent = SimpleAgent(
        name="记忆助手",
        llm=llm,
        system_prompt="你是一个记得对话上下文的助手。",
    )

    turns = [
        "我今天买了3本书。",
        "我刚才告诉你我买了几本书？",
    ]
    for q in turns:
        print(f"\n👤 用户: {q}")
        answer = agent.run(q)
        print(f"🤖 助手: {answer}")

    history = agent.get_history()
    print(f"\n📋 对话历史: {len(history)} 条消息")
    for msg in history:
        print(f"  [{msg.role}] {msg.content[:50]}...")


def demo_react_fc(llm):
    """ReActAgent — 原生 Function Calling 模式"""
    print("=" * 50)
    print("  ReActAgent FC 模式")
    print("=" * 50)

    # ── 构建 Echo + Calculator 工具 ──
    class EchoTool(Tool):
        def __init__(self):
            super().__init__(
                name="echo",
                description="回显输入文本，用于验证工具调用是否正常",
            )

        def get_parameters(self):
            return [
                ToolParameter(name="text", type="string", description="要回显的文本"),
            ]

        def run(self, parameters: dict) -> str:
            return f"回显: {parameters.get('text', '')}"

    class CalculatorTool(Tool):
        def __init__(self):
            super().__init__(
                name="calculator",
                description="安全地计算数学表达式，支持 + - * / ** sqrt abs round",
            )

        def get_parameters(self):
            return [
                ToolParameter(
                    name="expression", type="string", description="数学表达式，如 '2+3*4'"
                ),
            ]

        def run(self, parameters: dict) -> str:
            import ast
            import operator
            import math

            _OPS = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.Pow: operator.pow,
                ast.USub: operator.neg,
            }
            _FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round}

            def _eval(node):
                if isinstance(node, ast.Constant):
                    return node.value
                if isinstance(node, ast.BinOp):
                    return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
                if isinstance(node, ast.UnaryOp):
                    return _OPS[type(node.op)](_eval(node.operand))
                if isinstance(node, ast.Call):
                    args = [_eval(a) for a in node.args]
                    return _FUNCS[node.func.id](*args)
                raise ValueError(f"不支持: {ast.dump(node)}")

            try:
                tree = ast.parse(parameters["expression"].strip(), mode="eval")
                return f"计算结果: {_eval(tree.body)}"
            except Exception as e:
                return f"计算失败: {e}"

    registry = ToolRegistry()
    registry.register_tool(EchoTool())
    registry.register_tool(CalculatorTool())

    agent = ReActAgent(
        name="ReAct_FC助手",
        llm=llm,
        tool_registry=registry,
        max_steps=5,
        use_native_fc=True,
        system_prompt="你是会使用工具的AI助手。数学问题请调用 calculator，"
                      "验证工具可用性请调用 echo。"
                      "得到足够信息后直接给出答案。",
    )

    questions = [
        "帮我算一下 (25 + 15) * 3 等于多少？",
        "请用 echo 工具回显'Hello ReAct!'，然后告诉我结果",
    ]
    for q in questions:
        print(f"\n👤 用户: {q}")
        answer = asyncio.run(agent.arun(q))
        print(f"🤖 助手: {answer}")
        print(f"📋 执行记录: {len(agent.current_history)} 步")
        for h in agent.current_history:
            print(f"    {h[:120]}")

    print()


def demo_react_text_fallback(llm):
    """ReActAgent — 文本解析降级模式"""
    print("=" * 50)
    print("  ReActAgent 文本降级模式")
    print("=" * 50)

    def my_echo(text: str) -> str:
        return f"Echo: {text}"

    registry = ToolRegistry()
    registry.register_function("echo", "回显工具", my_echo)

    agent = ReActAgent(
        name="ReAct_Text助手",
        llm=llm,
        tool_registry=registry,
        max_steps=3,
        use_native_fc=False,
    )

    print("\n👤 用户: 请回显'降级测试'")
    answer = agent.run("请回显'降级测试'")
    print(f"🤖 助手: {answer}")
    print()


def main():
    # 一行对接现有配置
    llm = BaseAgentsLLM.from_settings()
    print(f"Provider: {llm.provider}  Model: {llm.model}\n")

    demo_basic_chat(llm)
    demo_tool_chat(llm)
    demo_streaming(llm)
    demo_history(llm)
    demo_react_fc(llm)
    demo_react_text_fallback(llm)


if __name__ == "__main__":
    main()
