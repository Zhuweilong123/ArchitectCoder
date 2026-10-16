"""Shared Agent state; concrete agents define their supported execution API."""

from copy import deepcopy
from typing import Generic, Optional, TypeVar

from .message import Message
from ..host_api.agents import ModelIdentityPort
from backend.config import AgentConfig


ModelT = TypeVar("ModelT", bound=ModelIdentityPort)


class Agent(Generic[ModelT]):
    """Own common configuration and history without imposing sync or async execution."""

    def __init__(
        self,
        name: str,
        llm: ModelT,
        system_prompt: Optional[str] = None,
        config: Optional[AgentConfig] = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.config = config or AgentConfig()
        self._history: list[Message] = []

    def add_message(self, message: Message) -> None:
        """添加消息到历史记录"""
        self._history.append(deepcopy(message))

    def clear_history(self) -> None:
        """清空历史记录"""
        self._history.clear()

    def get_history(self) -> list[Message]:
        """获取历史记录副本"""
        return deepcopy(self._history)

    def __str__(self) -> str:
        return f"Agent(name={self.name}, provider={self.llm.provider})"
