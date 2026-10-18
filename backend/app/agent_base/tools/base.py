"""工具基类 — 所有工具必须继承并实现 ``run`` 和 ``get_parameters``"""

from app.agent_base.host_api.data import JsonObject, JsonValue, ToolSchema
from .contracts import ToolExecution

from abc import ABC, abstractmethod
from pydantic import BaseModel


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str
    description: str
    required: bool = True
    default: JsonValue = None


class Tool(ABC):
    """工具基类

    统一接口：``name`` + ``description`` 用于自描述，
    ``run`` 执行工具逻辑，``get_parameters`` 定义参数 schema。

    Usage::

        class MyTool(Tool):
            def __init__(self):
                super().__init__(name="my_tool", description="...")

            def get_parameters(self) -> list[ToolParameter]:
                return [ToolParameter(name="q", type="string", description="查询")]

            def run(self, parameters: JsonObject) -> str:
                return f"结果: {parameters['q']}"
    """

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        # Optional non-schema names used by compatibility callers.  Aliases
        # resolve to the same tool but are never exposed as duplicate model
        # functions by the registry.
        self.aliases: tuple[str, ...] = ()
        # 默认保守：只有明确声明无副作用的工具才允许同轮并发。
        self.read_only = False
        self.can_parallel = False

    @abstractmethod
    def run(self, parameters: JsonObject) -> ToolExecution:
        """Return a JSON value/result, directly or through an awaitable."""
        ...

    def run_result(self, parameters: JsonObject) -> ToolExecution:
        """Structured execution hook; legacy tools delegate to their run method."""
        return self.run(parameters)

    @abstractmethod
    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        ...

    def to_openai_schema(self) -> ToolSchema:
        """转换为 OpenAI Function Calling schema"""
        parameters = self.get_parameters()
        properties: JsonObject = {}
        required: list[JsonValue] = []

        for param in parameters:
            prop: JsonObject = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                prop["description"] = f"{param.description} (默认: {param.default})"
            if param.type == "array":
                prop["items"] = {"type": "string"}
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }
