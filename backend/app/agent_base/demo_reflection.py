"""
集成示例：使用 BaseAgents ReflectionAgent 接入 DeepSeek 模型

运行方式:
    cd backend && python app/agent_base/demo_reflection.py
"""

import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.agent_base import BaseAgentsLLM, ReflectionAgent, Config


def main():
    # ── 一行对接现有 Settings 配置 ─────────────────────
    llm = BaseAgentsLLM.from_settings()

    print(f"Provider:  {llm.provider}")
    print(f"Model:     {llm.model}")
    print(f"Base URL:  {llm.base_url}")
    print()

    # ── 也可手动指定（用 flash 模型更快/更便宜）───────
    # llm = BaseAgentsLLM.from_settings(
    #     model=os.getenv("SUB_AGENT_MODEL"),   # deepseek-v4-flash
    # )

    # ── 创建 ReflectionAgent ─────────────────────────
    # custom_prompts 可按场景定制三阶段提示词
    uml_prompts = {
        "initial": """你是UML设计专家。请根据用户需求生成完整的UML图JSON。

任务: {task}

请提供准确、符合UML规范的JSON输出。""",
        "reflect": """请以UML审查专家的身份，检查以下UML设计的质量:

# 原始需求:
{task}

# 当前UML设计:
{content}

请从以下维度审查:
1. 类之间的关系是否正确
2. 方法签名是否与序列图一致
3. 组件接口是否完整
4. 是否有遗漏的实体

如果设计已满足需求，请回答"无需改进"。""",
        "refine": """请根据审查反馈修正UML设计:

# 原始需求:
{task}

# 上一版设计:
{last_attempt}

# 审查反馈:
{feedback}

请输出修正后的完整JSON。""",
    }

    agent = ReflectionAgent(
        name="UML设计助手",
        llm=llm,
        system_prompt="你是一个专业的UML设计助手，擅长类图、序列图、组件图设计。",
        config=Config(temperature=0.5, max_tokens=8192),
        max_iterations=3,
        custom_prompts=uml_prompts,
    )

    # ── 运行 ─────────────────────────────────────────
    result = agent.run("为一个在线书店设计UML类图，包含用户、订单、书籍、购物车等实体")
    print("=" * 60)
    print("最终结果:")
    print("=" * 60)
    print(result)
    print()
    print(f"对话历史: {len(agent.get_history())} 条消息")


if __name__ == "__main__":
    main()
