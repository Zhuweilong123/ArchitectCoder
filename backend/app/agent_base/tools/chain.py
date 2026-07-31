"""工具链管理系统 — 支持多个工具的顺序执行，输出传递"""

from typing import List, Dict, Any, Optional
from .registry import ToolRegistry


class ToolChain:
    """工具链 — 按顺序执行多个工具，前一步输出可被后一步引用

    Usage::

        chain = ToolChain(name="search_and_calc", description="搜索并计算")
        chain.add_step("search", "{input}", output_key="search_result")
        chain.add_step("calculator", "{search_result}", output_key="final")
        result = chain.execute(registry, "Python最新版本")
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.steps: List[Dict[str, Any]] = []

    def add_step(self, tool_name: str, input_template: str, output_key: Optional[str] = None):
        """添加工具执行步骤

        Args:
            tool_name: 工具名称
            input_template: 输入模板，支持 ``{变量名}`` 变量替换
            output_key: 输出结果的键名，用于后续步骤引用
        """
        self.steps.append({
            "tool_name": tool_name,
            "input_template": input_template,
            "output_key": output_key or f"step_{len(self.steps)}_result",
        })
        return self  # 链式调用

    def execute(self, registry: ToolRegistry, initial_input: str, context: Dict[str, Any] = None) -> str:
        """执行工具链，返回最后一步的结果"""
        context = context or {}
        context["input"] = initial_input

        print(f"🔗 开始执行工具链: {self.name}")

        for i, step in enumerate(self.steps, 1):
            tool_name = step["tool_name"]
            input_template = step["input_template"]
            output_key = step["output_key"]

            # 模板变量替换
            try:
                tool_input = input_template.format(**context)
            except KeyError as e:
                return f"❌ 工具链执行失败: 模板变量 {e} 未找到"

            print(f"  步骤 {i}: 使用 '{tool_name}' 处理 '{tool_input[:50]}...'")

            result = registry.execute_tool(tool_name, tool_input)
            context[output_key] = result

            print(f"  ✅ 步骤 {i} 完成，结果长度: {len(result)} 字符")

        final_result = context[self.steps[-1]["output_key"]]
        print(f"🎉 工具链 '{self.name}' 执行完成")
        return final_result


class ToolChainManager:
    """工具链管理器 — 管理多条命名工具链

    Usage::

        manager = ToolChainManager(registry)
        manager.register_chain(chain)
        result = manager.execute_chain("research", "query")
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.chains: Dict[str, ToolChain] = {}

    def register_chain(self, chain: ToolChain):
        """注册工具链"""
        self.chains[chain.name] = chain
        print(f"✅ 工具链 '{chain.name}' 已注册 ({len(chain.steps)} 步)")

    def execute_chain(self, chain_name: str, input_data: str, context: Dict[str, Any] = None) -> str:
        """执行指定的工具链"""
        if chain_name not in self.chains:
            return f"❌ 工具链 '{chain_name}' 不存在"
        return self.chains[chain_name].execute(self.registry, input_data, context)

    def list_chains(self) -> List[str]:
        """列出所有工具链名称"""
        return list(self.chains.keys())

    def remove_chain(self, name: str) -> bool:
        """移除工具链"""
        if name in self.chains:
            del self.chains[name]
            return True
        return False
