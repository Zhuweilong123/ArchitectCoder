"""异步工具执行器 — 支持并行执行多个工具"""

import asyncio
import concurrent.futures
from typing import Dict, List
from .registry import ToolRegistry


class AsyncToolExecutor:
    """异步工具执行器

    使用线程池并行执行多个工具，适合 I/O 密集型工具（搜索、API 调用等）。

    Usage::

        executor = AsyncToolExecutor(registry, max_workers=4)
        results = await executor.execute_tools_parallel([
            {"tool_name": "search", "input_data": "Python"},
            {"tool_name": "search", "input_data": "机器学习"},
        ])
    """

    def __init__(self, registry: ToolRegistry, max_workers: int = 4):
        self.registry = registry
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    async def execute_tool_async(self, tool_name: str, input_data: str) -> str:
        """异步执行单个工具"""
        loop = asyncio.get_event_loop()

        def _execute():
            return self.registry.execute_tool(tool_name, input_data)

        result = await loop.run_in_executor(self._executor, _execute)
        return result

    async def execute_tools_parallel(self, tasks: List[Dict[str, str]]) -> List[str]:
        """并行执行多个工具任务

        Args:
            tasks: 任务列表，每项含 ``tool_name`` 和 ``input_data``

        Returns:
            结果列表，与 tasks 顺序一致
        """
        print(f"🚀 开始并行执行 {len(tasks)} 个工具任务")

        async_tasks = [
            self.execute_tool_async(task["tool_name"], task["input_data"])
            for task in tasks
        ]
        results = await asyncio.gather(*async_tasks)

        print("✅ 所有工具任务执行完成")
        return results

    def shutdown(self):
        """清理线程池资源"""
        self._executor.shutdown(wait=True)

    def __del__(self):
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass
