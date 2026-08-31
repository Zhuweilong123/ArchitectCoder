"""工具注册机制 — 支持 Tool 对象注册和函数便捷注册"""

from typing import Dict, Any, Callable, Optional
from .base import Tool


class ToolRegistry:
    """工具注册表

    两种注册方式：
    1. ``register_tool(tool)`` — 完整的 Tool 对象
    2. ``register_function(name, desc, func)`` — 便捷函数注册

    Usage::

        registry = ToolRegistry()
        registry.register_tool(CalculatorTool())
        result = registry.execute_tool("calculator", "2+3")
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._functions: dict[str, dict[str, Any]] = {}

    # ── 注册 ──────────────────────────────────────────

    def register_tool(self, tool: Tool):
        """注册 Tool 对象"""
        if tool.name in self._tools:
            print(f"⚠️ 警告: 工具 '{tool.name}' 已存在，将被覆盖。")
        self._tools[tool.name] = tool
        print(f"✅ 工具 '{tool.name}' 已注册。")

    def register_function(self, name: str, description: str, func: Callable[[str], str]):
        """直接注册函数作为工具（简便方式）

        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数，接受字符串参数，返回字符串结果
        """
        if name in self._functions:
            print(f"⚠️ 警告: 工具 '{name}' 已存在，将被覆盖。")
        self._functions[name] = {
            "description": description,
            "func": func,
        }
        print(f"✅ 工具 '{name}' 已注册。")

    # ── 注销 ──────────────────────────────────────────

    def unregister(self, name: str) -> bool:
        """注销工具，返回是否成功"""
        if name in self._tools:
            del self._tools[name]
            return True
        if name in self._functions:
            del self._functions[name]
            return True
        return False

    # ── 查询 ──────────────────────────────────────────

    def get_tool(self, name: str) -> Optional[Tool]:
        """按名称获取 Tool 对象"""
        return self._tools.get(name)

    def get_tools_description(self) -> str:
        """获取所有工具的格式化描述（可直接注入 prompt）"""
        descriptions = []
        for tool in self._tools.values():
            descriptions.append(f"- {tool.name}: {tool.description}")
        for name, info in self._functions.items():
            descriptions.append(f"- {name}: {info['description']}")
        return "\n".join(descriptions) if descriptions else "暂无可用工具"

    def get_openai_specs(self) -> list[dict]:
        """收集所有工具的 OpenAI Function Calling schema。

        - 注册的 Tool 对象调用 ``to_openai_schema()``
        - :meth:`register_function` 注册的简装工具自动生成最简 schema
          （单一 ``input`` 字符串参数）
        """
        specs = []
        for tool in self._tools.values():
            schema = tool.to_openai_schema()
            params = schema.get("function", {}).get("parameters", {})
            if params.get("type") == "object":
                params.setdefault("additionalProperties", False)
            specs.append(schema)
        for name, info in self._functions.items():
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": info["description"],
                            },
                        },
                        "required": ["input"],
                    },
                },
            }
            schema["function"]["parameters"]["additionalProperties"] = False
            specs.append(schema)
        return specs

    def get_openai_specs_for(self, names: list[str]) -> list[dict]:
        """根据名称列表筛选 spec（用于限制本轮可用的工具）"""
        all_specs = {s["function"]["name"]: s for s in self.get_openai_specs()}
        return [all_specs[n] for n in names if n in all_specs]

    def can_parallel(self, name: str) -> bool:
        """返回工具是否明确声明可与同轮调用并行。"""
        tool = self._tools.get(name)
        return bool(tool and tool.read_only and tool.can_parallel)

    def list_tools(self) -> list[str]:
        """列出所有工具名称"""
        names = list(self._tools.keys()) + list(self._functions.keys())
        return names

    # ── 执行 ──────────────────────────────────────────

    def execute_tool(self, name: str, input_data: str) -> str:
        """执行工具

        - Tool 对象：input_data 传给 tool.run() 作为 name="input" 的参数
        - 函数工具：input_data 直接传给函数
        """
        # 先查 Tool 对象
        tool = self._tools.get(name)
        if tool:
            try:
                return tool.run({"input": input_data})
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        # 再查函数工具
        func_info = self._functions.get(name)
        if func_info:
            try:
                return func_info["func"](input_data)
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        return f"❌ 未找到工具: '{name}'"

    def execute_tool_with_params(self, name: str, parameters: Dict[str, Any]) -> str:
        """执行工具（带结构化参数）

        Tool 对象直接接收参数字典，函数工具忽略额外参数。
        """
        import asyncio

        tool = self._tools.get(name)
        if tool:
            try:
                result = tool.run(parameters)
                # Handle async tools — if we get a coroutine back, this is
                # a sync context error. Callers should use aexecute_tool_with_params.
                if asyncio.coroutines.iscoroutine(result):
                    return (
                        "ERROR: async tool returned coroutine in sync context. "
                        "Use aexecute_tool_with_params() instead."
                    )
                return str(result) if not isinstance(result, str) else result
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        func_info = self._functions.get(name)
        if func_info:
            try:
                result = func_info["func"](parameters.get("input", ""))
                if asyncio.coroutines.iscoroutine(result):
                    return (
                        "ERROR: async function returned coroutine in sync context. "
                        "Use aexecute_tool_with_params() instead."
                    )
                return str(result) if not isinstance(result, str) else result
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        return f"❌ 未找到工具: '{name}'"

    async def aexecute_tool_with_params(
        self, name: str, parameters: Dict[str, Any]
    ) -> str:
        """异步执行工具 — 正确处理 async 工具的 coroutine。

        当工具 run() 返回 coroutine 时，此方法会 await 它。
        ReActAgent FC 循环应优先使用此方法。
        """
        import asyncio

        tool = self._tools.get(name)
        if tool:
            try:
                result = tool.run(parameters)
                if asyncio.coroutines.iscoroutine(result):
                    result = await result
                return str(result) if not isinstance(result, str) else result
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        func_info = self._functions.get(name)
        if func_info:
            try:
                result = func_info["func"](parameters.get("input", ""))
                if asyncio.coroutines.iscoroutine(result):
                    result = await result
                return str(result) if not isinstance(result, str) else result
            except Exception as e:
                return f"❌ 工具 '{name}' 执行失败: {e}"

        return f"❌ 未找到工具: '{name}'"

    def __len__(self) -> int:
        return len(self._tools) + len(self._functions)

    def __contains__(self, name: str) -> bool:
        return name in self._tools or name in self._functions

    def __bool__(self) -> bool:
        return len(self) > 0
