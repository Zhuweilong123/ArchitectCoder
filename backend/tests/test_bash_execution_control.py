import asyncio

from app.agent_base.core.hooks import AgentRuntime, reset_runtime, set_runtime
from app.agent_base.tools.my_tools.file_system_tools import BashTool


def _write_script(workspace, name, body):
    script = workspace / name
    script.write_text(body, encoding="utf-8")
    return script


def test_bash_timeout_terminates_command(tmp_path):
    _write_script(tmp_path, "sleep_script.py", "import time; time.sleep(5)\n")
    bash = BashTool(str(tmp_path), timeout=0.2)

    result = asyncio.run(bash._execute({"command": "python sleep_script.py"}))

    assert "timed out after 0.2s" in result


def test_bash_stop_check_terminates_command(tmp_path):
    _write_script(tmp_path, "sleep_script.py", "import time; time.sleep(5)\n")
    bash = BashTool(str(tmp_path), timeout=30)
    stopped = False

    async def scenario():
        nonlocal stopped
        token = set_runtime(AgentRuntime(stop_check=lambda: stopped))
        try:
            task = asyncio.create_task(bash._execute({"command": "python sleep_script.py"}))
            await asyncio.sleep(0.2)
            stopped = True
            return await task
        finally:
            reset_runtime(token)

    result = asyncio.run(scenario())

    assert result == "Error: command canceled"


def test_bash_output_cap_is_per_tool(tmp_path):
    _write_script(tmp_path, "output_script.py", "print('x' * 5000)\n")
    bash = BashTool(str(tmp_path), output_cap=1024)

    result = asyncio.run(bash._execute({"command": "python output_script.py"}))

    assert len(result) == 1024
