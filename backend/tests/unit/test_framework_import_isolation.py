"""A fresh interpreter can use host protocols without any extension modules."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_public_host_api_and_framework_do_not_load_optional_plugins():
    root = Path(__file__).resolve().parents[3]
    code = '''
import importlib.abc, sys
class AbsentPlugins(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions" or fullname.startswith("extensions."):
            raise ModuleNotFoundError("Optional plugin directory absent: " + fullname)
sys.meta_path.insert(0, AbsentPlugins())
from app.agent_base.host_api.lifecycle import HookEvent
from app.agent_base.host_api.execution import ExecutionRequest
from app.agent_base.host_api.services import get_provider_services
from app.agent_base import PlanAndSolveAgent, ReActAgent
assert "app.agent_base.core.llm" not in sys.modules
from app.agent_base import ToolRegistry, BaseAgentsLLM
assert get_provider_services() is not None
assert not any(name.startswith("extensions") for name in sys.modules)
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root)))}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("first", [
    "app.agent_base.execution.tool_executor",
    "app.agent_base.tools.base",
    "app.agent_base.tools.registry",
])
def test_tool_execution_imports_do_not_depend_on_import_order(first):
    root = Path(__file__).resolve().parents[3]
    code = f'''
import importlib
importlib.import_module({first!r})
from app.agent_base.tools import Tool, AsyncTool, ToolRegistry
from app.agent_base.execution import ToolExecutor
assert isinstance(ToolRegistry().executor, ToolExecutor)
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root)))}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
