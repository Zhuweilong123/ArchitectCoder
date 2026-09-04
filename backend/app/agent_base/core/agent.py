"""Agent 抽象基类

所有 Agent 实现必须继承此类并实现 ``run`` 方法。

Usage::

    class MyAgent(Agent):
        def run(self, input_text: str, **kwargs) -> str:
            ...
"""

from abc import ABC, abstractmethod
from typing import Optional, Any

from .message import Message
from .llm import BaseAgentsLLM
from backend.config import AgentConfig


class Agent(ABC):
    """BaseAgents 框架的 Agent 抽象基类

    定义统一的接口规范：
    - ``run(input_text)`` — 核心执行入口，子类必须实现
    - 内建历史记录管理
    """

    def __init__(
        self,
        name: str,
        llm: BaseAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
    ):
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.config = config or AgentConfig()
        self._history: list[Message] = []

    @abstractmethod
    def run(self, input_text: str, **kwargs) -> str:
        """运行 Agent，子类必须实现"""
        ...

    def add_message(self, message: Message):
        """添加消息到历史记录"""
        self._history.append(message)

    def clear_history(self):
        """清空历史记录"""
        self._history.clear()

    def get_history(self) -> list[Message]:
        """获取历史记录副本"""
        return self._history.copy()

    def __str__(self) -> str:
        return f"Agent(name={self.name}, provider={self.llm.provider})"
