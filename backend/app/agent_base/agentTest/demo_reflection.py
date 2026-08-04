"""
精简示例：ReflectionAgent 基础用法

运行方式:
    cd backend && python app/agent_base/agentTest/demo_reflection.py
"""

import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from app.agent_base import BaseAgentsLLM, ReflectionAgent, Config


def main():
    llm = BaseAgentsLLM.from_settings()
    print(f"Provider:  {llm.provider}")
    print(f"Model:     {llm.model}")
    print(f"Base URL:  {llm.base_url}")
    print()

    prompt = """你是一个专业的UML设计助手，擅长类图、序列图、组件图设计。
请为一个在线书店设计UML类图，包含用户、订单、书籍、购物车等实体。
输出完整的JSON格式UML设计。"""

    agent = ReflectionAgent(
        name="UML设计助手",
        llm=llm,
        system_prompt="你是一个专业的UML设计助手，擅长类图、序列图、组件图设计。",
        config=Config(temperature=0.5, max_tokens=8192),
        max_iterations=3,
    )

    result = agent.run(prompt)
    print("=" * 60)
    print("最终结果:")
    print("=" * 60)
    print(result)
    print()
    print(f"对话历史: {len(agent.get_history())} 条消息")


if __name__ == "__main__":
    main()
