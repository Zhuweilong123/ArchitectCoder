from app.runtime.command import NativeLinuxBashExecutor, resolve_command_environment
from app.agent_base.tools.my_tools.foundation_tools import ShellTool


def test_auto_environment_uses_native_bash_on_linux():
    assert resolve_command_environment("auto", host_os="posix", platform_name="linux") == "native_posix"


def test_bash_schema_advertises_native_linux_contract(tmp_path):
    bash = ShellTool(str(tmp_path), command_executor=NativeLinuxBashExecutor())
    assert "Linux/POSIX bash" in bash.to_openai_schema()["function"]["description"]
    result = __import__("asyncio").run(bash._execute({"command": "dir"}))
    assert "executable 'dir' is not allowed" in result


def test_bash_allows_linux_environment_diagnostics():
    for command in ("printf ready", "uname -s", "pwd"):
        assert ShellTool._validate_shell_command(command) is None
