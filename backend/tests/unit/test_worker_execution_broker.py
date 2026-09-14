import asyncio

from types import SimpleNamespace

from app.runtime.execution_broker import WorkerExecutionBroker, build_execution_broker
from app.runtime.sandbox_worker import WorkerCapabilities, WorkerStatus
from app.runtime.task_contracts import (
    ExecutionPolicy,
    NetworkPolicy,
    ResourceLimits,
    TaskKind,
    TaskSpec,
)


class _Executor:
    def preflight(self):
        return None

    def start_program(self, program, args, cwd):
        raise AssertionError("worker policy should block before process start")


class _Worker:
    capabilities = WorkerCapabilities("fake", True, True, True, True)

    def __init__(self, ready=True, supports=False):
        self.ready = ready
        self._supports = supports

    def preflight(self):
        return WorkerStatus(
            "fake", "ready" if self.ready else "unavailable",
            self.capabilities, "worker offline" if not self.ready else "",
        )

    def supports(self, policy):
        return self._supports


def _task():
    return TaskSpec(task_id="cpp.test", kind=TaskKind.TEST, argv=("ctest",))


def test_worker_broker_reports_unavailable_worker(tmp_path):
    broker = WorkerExecutionBroker(
        _Executor(), [str(tmp_path)], worker=_Worker(ready=False),
    )
    evidence = asyncio.run(broker.execute(_task(), str(tmp_path)))

    assert evidence.status == "blocked"
    assert evidence.diagnostics["category"] == "sandbox_unavailable"


def test_worker_broker_reports_missing_capability(tmp_path):
    broker = WorkerExecutionBroker(
        _Executor(), [str(tmp_path)], worker=_Worker(ready=True, supports=False),
    )
    evidence = asyncio.run(broker.execute(
        _task(), str(tmp_path), policy=ExecutionPolicy(sandbox="wsl", network=NetworkPolicy.DENY),
    ))

    assert evidence.status == "blocked"
    assert evidence.diagnostics["category"] == "sandbox_capability_missing"


def test_worker_broker_fails_closed_for_unenforced_resource_limits(tmp_path):
    class Worker(_Worker):
        capabilities = WorkerCapabilities("fake", True, True, False, True)

    broker = WorkerExecutionBroker(
        _Executor(), [str(tmp_path)], worker=Worker(ready=True, supports=True),
    )
    evidence = asyncio.run(broker.execute(
        TaskSpec(
            task_id="cpp.test", kind=TaskKind.TEST, argv=("ctest",),
            resources=ResourceLimits(memory_mb=256),
        ),
        str(tmp_path),
    ))

    assert evidence.status == "blocked"
    assert evidence.diagnostics["category"] == "resource_policy"


def test_worker_broker_uses_worker_owned_executor_and_sandbox_name(tmp_path):
    executor = _Executor()

    class Worker(_Worker):
        capabilities = WorkerCapabilities("container", True, True, False, True)

        def __init__(self):
            super().__init__(ready=True, supports=True)
            self.executor = executor

        def supports(self, policy):
            return policy.sandbox == "container"

    worker = Worker()
    broker = WorkerExecutionBroker(_Executor(), [str(tmp_path)], worker=worker)

    assert broker.executor is executor
    assert broker.sandbox_name == "container"


def test_worker_broker_attaches_worker_attestation_to_execution_evidence(tmp_path):
    class Process:
        returncode = 0

        def communicate(self):
            return b"ok", b""

    class Executor(_Executor):
        def start_program(self, program, args, cwd):
            return Process()

    class Worker(_Worker):
        capabilities = WorkerCapabilities("container", True, True, True, True)

        def supports(self, policy):
            return policy.sandbox == "container"

    broker = WorkerExecutionBroker(
        Executor(), [str(tmp_path)], worker=Worker(ready=True, supports=True),
    )
    evidence = asyncio.run(broker.execute(
        _task(), str(tmp_path), policy=ExecutionPolicy(sandbox="container"),
    ))

    assert evidence.status == "success"
    assert evidence.diagnostics["worker"]["capabilities"]["worker_id"] == "container"


def test_build_execution_broker_selects_container_worker_without_host_fallback(tmp_path):
    settings = SimpleNamespace(
        agent_execution_worker="container",
        agent_container_image="toolchain:test",
        agent_container_executable="docker",
        agent_container_preflight_timeout_seconds=2,
    )
    host_executor = _Executor()

    broker = build_execution_broker(settings, host_executor, [str(tmp_path)])

    assert isinstance(broker, WorkerExecutionBroker)
    assert broker.sandbox_name == "container"
    assert broker.executor is broker.worker.executor
    assert broker.executor is not host_executor


def test_build_execution_broker_wsl_mode_uses_worker_executor(tmp_path):
    settings = SimpleNamespace(
        agent_execution_worker="wsl",
        agent_wsl_distribution="Ubuntu",
        agent_wsl_executable="wsl.exe",
        agent_wsl_preflight_timeout_seconds=2,
    )
    host_executor = _Executor()

    broker = build_execution_broker(settings, host_executor, [str(tmp_path)])

    assert isinstance(broker, WorkerExecutionBroker)
    assert broker.sandbox_name == "wsl"
    assert broker.executor is broker.worker.executor
    assert broker.executor is not host_executor
