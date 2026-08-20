"""task_system 单元测试（TaskStore + WorktreeStore）。"""
import subprocess

import pytest

from app.agent_base.tools.task_system import TaskStore, WorktreeStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / ".tasks")


def test_create_and_list(store):
    t = store.create_task("step 1", "do something")
    assert t.status == "pending"
    assert t.owner is None
    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == t.id


def test_update_adds_dependencies(store):
    a = store.create_task("a")
    b = store.create_task("b")
    store.update_task(b.id, [a.id])
    assert store.load_task(b.id).blockedBy == [a.id]


def test_update_rejects_cycle(store):
    a = store.create_task("a")
    b = store.create_task("b")
    store.update_task(b.id, [a.id])
    with pytest.raises(ValueError):
        store.update_task(a.id, [b.id])


def test_claim_blocked_and_complete_unblocks(store):
    a = store.create_task("a")
    b = store.create_task("b")
    store.update_task(b.id, [a.id])

    # 依赖未完成，b 不能 claim
    assert "blocked" in store.claim_task(b.id)

    assert "Claimed" in store.claim_task(a.id)
    a = store.load_task(a.id)
    assert a.status == "in_progress"
    assert a.owner == "agent"

    result = store.complete_task(a.id)
    assert "Completed" in result
    assert "Unblocked" in result

    assert "Claimed" in store.claim_task(b.id)


def test_claim_rejects_owned(store):
    a = store.create_task("a")
    store.claim_task(a.id)
    # 已 in_progress 的任务不能再 claim
    assert "cannot claim" in store.claim_task(a.id, owner="other")


def test_complete_requires_owner_match(store):
    a = store.create_task("a")
    store.claim_task(a.id, owner="agent")
    # owner 不匹配不能 complete
    assert "not other" in store.complete_task(a.id, owner="other")
    assert "Completed" in store.complete_task(a.id, owner="agent")


def _init_git_repo(repo):
    """初始化一个最小 git 仓库，失败返回 False（用于 skip）。"""
    try:
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True, check=True)
        (repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def test_worktree_create_and_remove(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    if not _init_git_repo(repo):
        pytest.skip("git unavailable or repo init failed")

    store = TaskStore(tmp_path / ".tasks")
    worktrees = WorktreeStore(str(repo), str(tmp_path / ".worktrees"), str(tmp_path / ".tasks"))
    task = store.create_task("t")

    result = worktrees.create_worktree("wt1", task.id)
    assert "created" in result
    assert store.load_task(task.id).worktree == "wt1"

    # 完成 task 后可移除
    store.claim_task(task.id)
    store.complete_task(task.id)
    assert "removed" in worktrees.remove_worktree("wt1")
    assert store.load_task(task.id).worktree is None
