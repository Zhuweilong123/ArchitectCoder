from app.runtime.sandbox_worker import ContainerCommandExecutor, ContainerWorker, WslWorker
from app.runtime.task_contracts import ExecutionPolicy, NetworkPolicy, ResourceLimits


class _Executor:
    def __init__(self, error=None):
        self.error = error

    def preflight(self):
        if self.error:
            raise self.error


def test_wsl_worker_reports_launcher_but_not_isolation_by_default():
    worker = WslWorker(_Executor())
    status = worker.preflight()

    assert status.ready
    assert status.capabilities.worker_id == "wsl"
    assert not status.capabilities.network_isolated
    assert not worker.supports(ExecutionPolicy(sandbox="wsl", network=NetworkPolicy.DENY))


def test_wsl_worker_can_be_enabled_only_with_explicit_capabilities():
    worker = WslWorker(
        _Executor(), filesystem_isolated=True, network_isolated=True,
    )

    policy = ExecutionPolicy(
        sandbox="wsl", network=NetworkPolicy.DENY, writable_roots=("build",),
    )
    assert worker.supports(policy)


def test_wsl_worker_preflight_reports_unavailable():
    worker = WslWorker(_Executor(RuntimeError("not ready")))
    status = worker.preflight()

    assert not status.ready
    assert status.status == "unavailable"
    assert "not ready" in status.reason


def test_container_executor_preflight_fails_closed_when_docker_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.runtime.sandbox_worker.shutil.which", lambda _: None)
    worker = ContainerWorker(str(tmp_path), executable="docker")

    status = worker.preflight()

    assert not status.ready
    assert status.capabilities.filesystem_isolated
    assert status.capabilities.network_isolated
    assert "unavailable" in status.reason


def test_container_executor_can_require_digest_pinned_images(tmp_path, monkeypatch):
    monkeypatch.setattr("app.runtime.sandbox_worker.shutil.which", lambda _: "docker")
    worker = ContainerWorker(
        str(tmp_path), image="toolchain:latest", require_digest=True,
    )

    status = worker.preflight()

    assert not status.ready
    assert "sha256 digest" in status.reason


def test_container_worker_reports_missing_local_image(tmp_path, monkeypatch):
    monkeypatch.setattr("app.runtime.sandbox_worker.shutil.which", lambda _: "docker")

    def run(argv, **kwargs):
        if argv[1:3] == ["image", "inspect"]:
            return type("Result", (), {"returncode": 1, "stdout": b"", "stderr": b"No such image"})()
        return type("Result", (), {"returncode": 0, "stdout": b"Docker info", "stderr": b""})()

    monkeypatch.setattr("app.runtime.sandbox_worker.subprocess.run", run)
    status = ContainerWorker(str(tmp_path), image="toolchain:test").preflight()

    assert not status.ready
    assert "not available locally" in status.reason


def test_container_executor_builds_network_disabled_workspace_command(tmp_path, monkeypatch):
    executor = ContainerCommandExecutor(str(tmp_path), image="toolchain:test")
    calls = {}

    monkeypatch.setattr("app.runtime.sandbox_worker.shutil.which", lambda _: "docker")
    monkeypatch.setattr(
        "app.runtime.sandbox_worker.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": b"sha256:image", "stderr": b""})(),
    )
    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls["argv"] = argv
            calls["kwargs"] = kwargs

    monkeypatch.setattr("app.runtime.sandbox_worker.subprocess.Popen", FakePopen)
    executor.start_program("cmake", ["--build", "."], str(tmp_path / "build"))

    assert calls["argv"][:4] == ["docker", "run", "--rm", "--init"]
    assert calls["argv"][4:8] == ["--pull", "never", "--network", "none"]
    assert "--volume" in calls["argv"]
    assert "/workspace/build" in calls["argv"]
    assert calls["argv"][-4:] == ["toolchain:test", "cmake", "--build", "."]


def test_container_executor_applies_supported_resource_limits(tmp_path, monkeypatch):
    executor = ContainerCommandExecutor(str(tmp_path), image="toolchain:test")
    captured = {}
    monkeypatch.setattr("app.runtime.sandbox_worker.shutil.which", lambda _: "docker")
    monkeypatch.setattr(
        "app.runtime.sandbox_worker.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": b"sha256:image", "stderr": b""})(),
    )

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv

    monkeypatch.setattr("app.runtime.sandbox_worker.subprocess.Popen", FakePopen)
    executor.start_program_with_limits(
        "ctest", [], str(tmp_path), ResourceLimits(cpu_seconds=4, memory_mb=512, max_processes=32),
    )

    assert "--ulimit" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--ulimit") + 1] == "cpu=4"
    assert "--memory" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--memory") + 1] == "512m"
    assert "--pids-limit" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--pids-limit") + 1] == "32"
