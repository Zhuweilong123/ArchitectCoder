from dataclasses import FrozenInstanceError

import pytest

from app.runtime.task_contracts import (
    ApprovalClass,
    ExecutionEvidence,
    ExecutionPolicy,
    NetworkPolicy,
    ResourceLimits,
    TaskKind,
    TaskSpec,
    ToolchainProfile,
)


def test_task_spec_is_language_neutral_and_serializable():
    task = TaskSpec(
        task_id="cpp.build",
        kind=TaskKind.BUILD,
        argv=("cmake", "--build", "build", "--config", "Debug"),
        cwd="workspace",
        toolchain_id="cpp-clang-18",
        network=NetworkPolicy.DENY,
        expected_outputs=("build/bin/radar_tests.exe",),
    )

    assert task.to_dict() == {
        "task_id": "cpp.build",
        "kind": "build",
        "argv": ["cmake", "--build", "build", "--config", "Debug"],
        "cwd": "workspace",
        "toolchain_id": "cpp-clang-18",
        "network": "deny",
        "approval": "sandbox_auto",
        "resources": {
            "timeout_seconds": 600.0,
            "cpu_seconds": None,
            "memory_mb": None,
            "disk_mb": None,
            "max_processes": None,
        },
        "expected_outputs": ["build/bin/radar_tests.exe"],
        "source": "resolver",
    }


def test_task_spec_rejects_shell_control_characters():
    with pytest.raises(ValueError, match="literal argv"):
        TaskSpec(task_id="bad", kind=TaskKind.CUSTOM, argv=("sh", "-c", "echo ok; rm -rf /"))


def test_resource_limits_and_timeout_evidence_are_validated():
    with pytest.raises(ValueError, match="timeout_seconds"):
        ResourceLimits(timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout_reason"):
        ExecutionEvidence(
            task_id="test",
            status="timeout",
            toolchain_id="python",
            command=("python", "-m", "pytest"),
            cwd="test",
        )

    evidence = ExecutionEvidence(
        task_id="cpp.test",
        status="timeout",
        toolchain_id="cpp-clang-18",
        command=("ctest", "--test-dir", "build"),
        cwd="workspace",
        timeout_reason="process_timeout",
        network=NetworkPolicy.DENY,
    )
    assert evidence.to_dict()["timeout_reason"] == "process_timeout"


def test_toolchain_and_policy_keep_structured_capabilities():
    profile = ToolchainProfile(
        toolchain_id="rust-stable",
        family="rust",
        version="1.82",
        executor="container",
        capabilities=("build", "test", "lint"),
        image="architectcoder/rust:1.82",
    )
    policy = ExecutionPolicy(
        sandbox="container",
        network=NetworkPolicy.APPROVAL_REQUIRED,
        writable_roots=("build",),
        approval=ApprovalClass.USER_APPROVAL,
    )
    assert profile.to_dict()["capabilities"] == ["build", "test", "lint"]
    assert policy.network is NetworkPolicy.APPROVAL_REQUIRED


def test_contracts_are_immutable():
    task = TaskSpec(task_id="test", kind=TaskKind.TEST, argv=("pytest",))
    with pytest.raises(FrozenInstanceError):
        task.task_id = "other"
