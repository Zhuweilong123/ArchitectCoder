"""M6.2 runtime toolchain attestation matrix.

The matrix is environment-aware: a deployment only exercises tools it has
installed, while the broker contract remains identical for every toolchain.
"""

import asyncio
import os
import shutil

import pytest

from app.runtime.command import NativeLinuxBashExecutor, NativePowerShellExecutor
from app.runtime.execution_broker import LocalExecutionBroker
from app.runtime.task_contracts import TaskKind, TaskSpec


_TOOLCHAINS = (
    ("python", "python"),
    ("node", "node"),
    ("cmake", "cmake"),
    ("cargo", "rust"),
    ("go", "go"),
    ("java", "java"),
    ("dotnet", "dotnet"),
    ("mvn", "java"),
    ("gradle", "java"),
)


@pytest.mark.parametrize(("program", "family"), _TOOLCHAINS)
def test_available_toolchain_reports_runtime_attestation(tmp_path, program, family):
    if shutil.which(program) is None:
        pytest.skip(f"{program} is not installed in this test environment")

    executor = (
        NativePowerShellExecutor()
        if os.name == "nt" else NativeLinuxBashExecutor()
    )
    broker = LocalExecutionBroker(
        executor,
        [str(tmp_path)],
        toolchain_version_policy="observe",
    )
    task = TaskSpec(
        task_id=f"{family}.runtime-version",
        kind=TaskKind.CUSTOM,
        argv=(program, "--version"),
        toolchain_id=f"{family}-runtime",
        toolchain_version="0",
    )

    evidence = asyncio.run(broker.execute(task, str(tmp_path)))

    assert evidence.status in {"success", "failed"}
    assert evidence.toolchain_probe["status"] in {
        "available", "unavailable", "timeout", "error",
    }
    assert evidence.toolchain_probe["declared_version"] == "0"
    assert evidence.toolchain_id == f"{family}-runtime"
