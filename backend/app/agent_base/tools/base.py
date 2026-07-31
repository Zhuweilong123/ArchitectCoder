"""工具基类 — 所有工具必须继承并实现 ``run`` 和 ``get_parameters``"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
from pydantic import BaseModel


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


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

            def run(self, parameters: dict) -> str:
                return f"结果: {parameters['q']}"
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    def run(self, parameters: Dict[str, Any]) -> str:
        """执行工具，返回字符串结果"""
        ...

    @abstractmethod
    def get_parameters(self) -> List[ToolParameter]:
        """获取工具参数定义"""
        ...

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI Function Calling schema"""
        parameters = self.get_parameters()
        properties = {}
        required = []

        for param in parameters:
            prop = {
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
                },
            },
        }
