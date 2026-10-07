"""A fresh interpreter can use host protocols without any extension modules."""
import os
from pathlib import Path
import subprocess
import sys


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
from app.agent_base.host_api.services import get_host_services
from app.agent_base import ToolRegistry, ReActAgent, BaseAgentsLLM
assert get_host_services() is not None
assert not any(name.startswith("extensions") for name in sys.modules)
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root)))}
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
