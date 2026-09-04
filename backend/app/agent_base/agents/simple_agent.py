"""SimpleAgent — 基础对话 Agent

支持可选的工具调用（基于文本格式的工具调用解析）。
适合：简单对话、不需要复杂推理的场景。

Usage::

    from app.agent_base.agents.simple_agent import SimpleAgent

    agent = SimpleAgent(name="助手", llm=llm, system_prompt="你是一个有用的AI助手")
    response = agent.run("你好！")
"""

import re
import logging
from typing import Optional, Iterator

from ..core.agent import Agent
from ..core.llm import BaseAgentsLLM
from ..core.message import Message
from backend.config import AgentConfig
from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── 默认系统提示词 ─────────────────────────────────────
DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant. Answer questions in a concise and clear manner."


class SimpleAgent(Agent):
    """简单对话 Agent

    最基础的 Agent 实现，直接封装 LLM 调用。
    支持可选的工具调用（文本格式）。
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
        tool_registry: Optional[ToolRegistry] = None,
        enable_tool_calling: bool = True,
    ):
        super().__init__(name, llm, system_prompt or DEFAULT_SYSTEM_PROMPT, config)
        self.tool_registry = tool_registry
        self.enable_tool_calling = enable_tool_calling and tool_registry is not None
        logger.info(
            "✅ %s 初始化完成，工具调用: %s",
            name, "启用" if self.enable_tool_calling else "禁用",
        )

    # ── 核心运行逻辑 ──────────────────────────────────

    def run(self, input_text: str, max_tool_iterations: int = 3, **kwargs) -> str:
        """执行对话

        Args:
            input_text: 用户输入
            max_tool_iterations: 工具调用最大轮数
        """
        logger.info("🤖 %s 正在处理: %s", self.name, input_text)

        messages = []

        # 1. 构建系统消息（含工具信息）
        enhanced_prompt = self._get_enhanced_system_prompt()
        messages.append({"role": "system", "content": enhanced_prompt})

        # 2. 添加历史消息
        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})

        # 3. 添加用户消息
        messages.append({"role": "user", "content": input_text})

        # 4. 无工具 → 直接调用
        if not self.enable_tool_calling:
            response = self.llm.invoke(messages, **kwargs)
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(response, "assistant"))
            logger.info("✅ %s 响应完成", self.name)
            return response

        # 5. 有工具 → 多轮工具调用循环
        return self._run_with_tools(messages, input_text, max_tool_iterations, **kwargs)

    # ── 流式运行 ──────────────────────────────────────

    def stream_run(self, input_text: str, **kwargs) -> Iterator[str]:
        """流式运行，实时产出响应文本"""
        logger.info("🌊 %s 开始流式处理: %s", self.name, input_text)

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": input_text})

        full_response = ""
        print("📝 实时响应: ", end="")
        for chunk in self.llm.stream_invoke(messages, **kwargs):
            full_response += chunk
            print(chunk, end="", flush=True)
            yield chunk
        print()

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(full_response, "assistant"))
        logger.info("✅ %s 流式响应完成", self.name)

    # ── 工具调用内部逻辑 ──────────────────────────────────

    def _get_enhanced_system_prompt(self) -> str:
        """构建增强系统提示词，注入工具信息"""
        base_prompt = self.system_prompt or DEFAULT_SYSTEM_PROMPT

        if not self.enable_tool_calling or not self.tool_registry:
            return base_prompt

        tools_desc = self.tool_registry.get_tools_description()
        if not tools_desc or tools_desc == "no tools available":
            return base_prompt

        tools_section = "\n\n## Available tools\n"
        tools_section += "You can use the following tools to help answer questions:\n"
        tools_section += tools_desc + "\n"
        tools_section += "\n## Tool call format\n"
        tools_section += "When you need to use a tool, use this format:\n"
        tools_section += "`[TOOL_CALL:{tool_name}:{parameters}]`\n"
        tools_section += "For example: `[TOOL_CALL:search:Python programming]` or `[TOOL_CALL:memory:recall=user info]`\n\n"
        tools_section += "Tool call results are automatically inserted into the conversation, and you can continue answering based on them.\n"

        return base_prompt + tools_section

    def _run_with_tools(
        self, messages: list, input_text: str, max_iterations: int, **kwargs
    ) -> str:
        """工具调用循环"""
        current_iteration = 0
        final_response = ""

        while current_iteration < max_iterations:
            response = self.llm.invoke(messages, **kwargs)
            tool_calls = self._parse_tool_calls(response)

            if tool_calls:
                logger.info("🔧 检测到 %d 个工具调用", len(tool_calls))
                tool_results = []
                clean_response = response

                for call in tool_calls:
                    result = self._execute_tool_call(call["tool_name"], call["parameters"])
                    tool_results.append(result)
                    clean_response = clean_response.replace(call["original"], "")

                messages.append({"role": "assistant", "content": clean_response})
                tool_results_text = "\n\n".join(tool_results)
                messages.append({
                    "role": "user",
                    "content": f"工具执行结果:\n{tool_results_text}\n\n请基于这些结果给出完整的回答。",
                })
                current_iteration += 1
                continue

            final_response = response
            break

        if current_iteration >= max_iterations and not final_response:
            final_response = self.llm.invoke(messages, **kwargs)

        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_response, "assistant"))
        logger.info("✅ %s 响应完成", self.name)
        return final_response

    def _parse_tool_calls(self, text: str) -> list:
        """解析 [TOOL_CALL:name:params] 格式"""
        pattern = r'\[TOOL_CALL:([^:]+):([^\]]+)\]'
        matches = re.findall(pattern, text)
        return [
            {
                "tool_name": m[0].strip(),
                "parameters": m[1].strip(),
                "original": f"[TOOL_CALL:{m[0]}:{m[1]}]",
            }
            for m in matches
        ]

    def _execute_tool_call(self, tool_name: str, parameters: str) -> str:
        """执行单个工具调用"""
        if not self.tool_registry:
            return "❌ 错误: 未配置工具注册表"

        try:
            param_dict = self._parse_tool_parameters(tool_name, parameters)
            tool = self.tool_registry.get_tool(tool_name)
            if tool:
                result = tool.run(param_dict)
            else:
                result = self.tool_registry.execute_tool(tool_name, parameters)
            return f"🔧 工具 {tool_name} 执行结果:\n{result}"
        except Exception as e:
            return f"❌ 工具调用失败: {e}"

    def _parse_tool_parameters(self, tool_name: str, parameters: str) -> dict:
        """智能解析工具参数字符串"""
        if "=" in parameters:
            pairs = parameters.split(",")
            return {
                kv.split("=", 1)[0].strip(): kv.split("=", 1)[1].strip()
                for kv in pairs
                if "=" in kv
            }
        # 直接参数 → 按工具类型推断
        param_map = {
            "search": {"query": parameters},
            "memory": {"action": "search", "query": parameters},
        }
        return param_map.get(tool_name, {"input": parameters})

    # ── 便利方法 ──────────────────────────────────────

    def add_tool(self, tool) -> None:
        """添加工具到 Agent"""
        if not self.tool_registry:
            self.tool_registry = ToolRegistry()
            self.enable_tool_calling = True
        self.tool_registry.register_tool(tool)
        logger.info("🔧 工具 '%s' 已添加", tool.name)

    def remove_tool(self, tool_name: str) -> bool:
        """移除工具"""
        if self.tool_registry:
            return self.tool_registry.unregister(tool_name)
        return False

    def has_tools(self) -> bool:
        """检查是否有可用工具"""
        return self.enable_tool_calling and bool(self.tool_registry)

    def list_tools(self) -> list:
        """列出所有可用工具"""
        if self.tool_registry:
            return self.tool_registry.list_tools()
        return []
