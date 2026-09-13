import asyncio
import threading
from pathlib import Path

from app.runtime.execution_broker import LocalExecutionBroker
from app.runtime.task_contracts import (
    ApprovalClass,
    ExecutionPolicy,
    NetworkPolicy,
    TaskKind,
    TaskSpec,
)


class _Process:
    def __init__(self, stdout=b"ok", stderr=b"", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.terminated = False

    def communicate(self):
        return self.stdout, self.stderr


class _BlockingProcess(_Process):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def communicate(self):
        self.release.wait(2)
        return self.stdout, self.stderr


class _Executor:
    def __init__(self, process=None, validation=None):
        self.process = process or _Process()
        self.validation = validation
        self.started = None

    def preflight(self):
        return None

    def validate_program(self, program, args):
        return self.validation

    def start_program(self, program, args, cwd):
        self.started = (program, args, cwd)
        return self.process

    def terminate(self, process):
        process.terminated = True
        if hasattr(process, "release"):
            process.release.set()


class _ResolverExecutor(_Executor):
    def validate_resolved_program(self, program, args):
        return "toolchain executable 'cmake' is unavailable on the worker"


def _task(**kwargs):
    return TaskSpec(
        task_id="cpp.build", kind=TaskKind.BUILD,
        argv=("cmake", "--build", "build"), **kwargs,
    )


def test_local_broker_runs_literal_task_and_returns_evidence(tmp_path):
    executor = _Executor()
    broker = LocalExecutionBroker(executor, [str(tmp_path)])

    evidence = asyncio.run(broker.execute(_task(), str(tmp_path)))

    assert evidence.status == "success"
    assert evidence.exit_code == 0
    assert evidence.command == ("cmake", "--build", "build")
    assert executor.started == ("cmake", ["--build", "build"], str(tmp_path))


def test_local_broker_blocks_network_and_user_approval(tmp_path):
    executor = _Executor()
    broker = LocalExecutionBroker(executor, [str(tmp_path)])

    network = asyncio.run(broker.execute(
        _task(network=NetworkPolicy.ALLOW), str(tmp_path),
    ))
    approval = asyncio.run(broker.execute(
        _task(approval=ApprovalClass.USER_APPROVAL), str(tmp_path),
    ))

    assert network.status == "blocked"
    assert network.diagnostics["category"] == "network_policy"
    assert approval.status == "blocked"
    assert approval.diagnostics["category"] == "approval_required"
    assert executor.started is None


def test_local_broker_enforces_workspace_root_and_executor_policy(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    executor = _Executor(validation="executable denied by host policy")
    broker = LocalExecutionBroker(executor, [str(tmp_path / "allowed")])
    (tmp_path / "allowed").mkdir()

    path_result = asyncio.run(broker.execute(_task(), str(outside)))
    policy_result = asyncio.run(broker.execute(_task(), str(tmp_path / "allowed")))

    assert path_result.diagnostics["category"] == "path_policy"
    assert policy_result.diagnostics["category"] == "executor_policy"
    assert executor.started is None


def test_local_broker_classifies_missing_resolved_toolchain(tmp_path):
    executor = _ResolverExecutor()
    broker = LocalExecutionBroker(executor, [str(tmp_path)])

    evidence = asyncio.run(broker.execute(_task(), str(tmp_path)))

    assert evidence.status == "blocked"
    assert evidence.diagnostics["category"] == "toolchain_unavailable"
    assert executor.started is None


def test_broker_evidence_is_structured_and_round_trippable(tmp_path):
    executor = _Executor()
    broker = LocalExecutionBroker(executor, [str(tmp_path)])

    evidence = asyncio.run(broker.execute(_task(), str(tmp_path)))
    payload = evidence.to_dict()

    assert payload["task_id"] == "cpp.build"
    assert payload["toolchain_id"] == "host"
    assert payload["command"] == ["cmake", "--build", "build"]
    assert payload["diagnostics"]["category"] == "process_exit"


def test_local_broker_honors_runtime_stop_check(tmp_path):
    process = _BlockingProcess()
    executor = _Executor(process=process)
    broker = LocalExecutionBroker(executor, [str(tmp_path)], stop_check=lambda: True)

    evidence = asyncio.run(broker.execute(_task(), str(tmp_path)))

    assert evidence.status == "canceled"
    assert process.terminated
