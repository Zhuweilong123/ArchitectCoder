"""Exercise native process launching on the Windows CI worker."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.runtime.command import NativePowerShellExecutor
from app.runtime.encoding import decode_process_output


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows processes")


def _collect(executor: NativePowerShellExecutor, process: subprocess.Popen) -> str:
    try:
        stdout, stderr = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            executor.terminate(process)
            process.communicate(timeout=5)
    assert process.returncode == 0, decode_process_output(stderr)
    return decode_process_output(stdout).strip()


def test_native_powershell_uses_working_directory_with_spaces(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    executor = NativePowerShellExecutor()

    output = _collect(executor, executor.start("(Get-Location).Path", str(workspace)))

    assert Path(output).resolve() == workspace.resolve()


def test_native_program_preserves_literal_arguments_and_working_directory(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    script = workspace / "echo arguments.py"
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n",
        encoding="utf-8",
    )
    arguments = ["with spaces", "semi;colon", "$literal", 'quote"inside', "中文"]
    executor = NativePowerShellExecutor()

    output = _collect(executor, executor.start_program(
        sys.executable, [str(script), *arguments], str(workspace),
    ))

    result = json.loads(output)
    assert result["argv"] == arguments
    assert Path(result["cwd"]).resolve() == workspace.resolve()
