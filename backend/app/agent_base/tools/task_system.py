"""task_system — 持久化任务 DAG + claim/complete + git worktree。

为 dev_agent 提供任务分解能力，并为未来多 agent 铺路（owner/claim/worktree
完整保留）。参考 code.py 的 task 系统（127-700 行），做 Windows 适配：
``fcntl.flock``（POSIX）改为 ``threading.RLock``（uvicorn 单进程足够）。

任务存储于 ``temp/.tasks/task_<8hex>.json``，worktree 于 ``temp/.worktrees/``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from app.agent_base.tools.base import Tool, ToolParameter

logger = logging.getLogger(__name__)

TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")
VALID_STATUS = ("pending", "in_progress", "completed")

VALID_WORKTREE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ═══════════════════════════════════════════════════════
# Task 模型 + TaskStore
# ═══════════════════════════════════════════════════════

@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: Optional[str]
    blockedBy: list[str]
    worktree: Optional[str] = None


class TaskStore:
    """文件持久化的任务存储：依赖 DAG + claim/complete 状态机。"""

    def __init__(self, tasks_dir):
        self.tasks_dir = Path(tasks_dir)
        self._lock = threading.RLock()

    def _task_path(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"Invalid task ID: {task_id!r}")
        return self.tasks_dir / f"{task_id}.json"

    # ── 增删改查 ──────────────────────────────────────

    def create_task(self, subject: str, description: str = "") -> Task:
        subject = subject.strip()
        if not subject:
            raise ValueError("Task subject cannot be empty")
        with self._lock:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            for _ in range(100):
                task = Task(
                    id=f"task_{secrets.token_hex(4)}",
                    subject=subject,
                    description=description,
                    status="pending",
                    owner=None,
                    blockedBy=[],
                )
                try:
                    with self._task_path(task.id).open("x", encoding="utf-8") as f:
                        json.dump(asdict(task), f, indent=2)
                    return task
                except FileExistsError:
                    continue
        raise RuntimeError("Could not allocate a unique task ID")

    def save_task(self, task: Task):
        with self._lock:
            path = self._task_path(task.id)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                tmp.write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)

    def load_task(self, task_id: str) -> Task:
        with self._lock:
            data = json.loads(self._task_path(task_id).read_text(encoding="utf-8"))
            task = Task(**data)
            if task.id != task_id:
                raise ValueError(f"Task file ID does not match {task_id}")
            if task.status not in VALID_STATUS:
                raise ValueError(f"Invalid task status: {task.status}")
            return task

    def list_tasks(self) -> list[Task]:
        with self._lock:
            if not self.tasks_dir.exists():
                return []
            return [self.load_task(p.stem)
                    for p in sorted(self.tasks_dir.glob("task_*.json"))]

    def get_task_json(self, task_id: str) -> str:
        return json.dumps(asdict(self.load_task(task_id)), indent=2)

    # ── 依赖 DAG ──────────────────────────────────────

    def _task_depends_on(self, task_id: str, target_id: str) -> bool:
        """返回 task_id 是否（传递地）依赖 target_id。"""
        pending = [task_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self.load_task(current).blockedBy)
        return False

    def update_task(self, task_id: str, addBlockedBy: list[str]) -> Task:
        if not isinstance(addBlockedBy, list):
            raise ValueError("addBlockedBy must be a list of task IDs")
        with self._lock:
            task = self.load_task(task_id)
            if task.status != "pending" or task.owner is not None:
                raise ValueError(
                    "Task dependencies can only be updated while pending and unowned"
                )
            deps = list(dict.fromkeys(addBlockedBy))
            for dep in deps:
                if dep == task_id:
                    raise ValueError("Task cannot depend on itself")
                if not self._task_path(dep).is_file():
                    raise ValueError(f"Dependency not found: {dep}")
                if dep not in task.blockedBy and self._task_depends_on(dep, task_id):
                    raise ValueError(f"Dependency cycle detected: {task_id} -> {dep}")
            task.blockedBy = list(dict.fromkeys(task.blockedBy + deps))
            self.save_task(task)
            return task

    def can_start(self, task_id: str) -> bool:
        task = self.load_task(task_id)
        for dep_id in task.blockedBy:
            try:
                dep_path = self._task_path(dep_id)
            except ValueError:
                return False
            if not dep_path.exists() or self.load_task(dep_id).status != "completed":
                return False
        return True

    # ── claim / complete ─────────────────────────────

    def _owner_in_progress(self, owner: str) -> Optional[Task]:
        return next((t for t in self.list_tasks()
                     if t.status == "in_progress" and t.owner == owner), None)

    def claim_task(self, task_id: str, owner: str = "agent") -> str:
        with self._lock:
            task = self.load_task(task_id)
            if task.status != "pending":
                return f"Task {task_id} is {task.status}, cannot claim"
            if task.owner:
                return f"Task {task_id} is already owned by {task.owner}"
            if self._owner_in_progress(owner):
                return f"Owner {owner} must complete their current task first"
            if not self.can_start(task_id):
                return f"Task {task_id} is blocked by unfinished dependencies"
            task.owner = owner
            task.status = "in_progress"
            self.save_task(task)
        return f"Claimed {task.id} ({task.subject})"

    def complete_task(self, task_id: str, owner: str = "agent") -> str:
        with self._lock:
            task = self.load_task(task_id)
            if task.status != "in_progress":
                return f"Task {task_id} is {task.status}, cannot complete"
            if task.owner != owner:
                return f"Task {task_id} is owned by {task.owner}, not {owner}"
            task.status = "completed"
            self.save_task(task)
            unblocked = [t.subject for t in self.list_tasks()
                         if t.status == "pending" and t.blockedBy and self.can_start(t.id)]
        msg = f"Completed {task.id} ({task.subject})"
        if unblocked:
            msg += f"\nUnblocked: {', '.join(unblocked)}"
        return msg


# ═══════════════════════════════════════════════════════
# WorktreeStore（git worktree 完整管理）
# ═══════════════════════════════════════════════════════

class WorktreeStore:
    """task-bound git worktree 管理：创建/删除 + 路径守卫 + 失败回滚。"""

    def __init__(self, workdir: str, worktrees_dir: str, tasks_dir: str):
        self.workdir = Path(workdir)
        self.worktrees_dir = Path(worktrees_dir)
        self._tasks = TaskStore(tasks_dir)
        self._lock = threading.RLock()

    def _worktree_path(self, name: str) -> Path:
        path = (self.worktrees_dir / name).resolve()
        if (not path.is_relative_to(self.worktrees_dir.resolve())
                or path == self.worktrees_dir.resolve()):
            raise ValueError(f"Worktree path escapes directory: {name!r}")
        return path

    @staticmethod
    def _worktree_branch(name: str) -> str:
        return f"wt/{name}"

    @staticmethod
    def _run_git(args: list[str], cwd: Optional[Path] = None) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output[:5000] or "(no output)"

    def _registered_worktrees(self) -> tuple[dict, str | None]:
        ok, output = self._run_git(["worktree", "list", "--porcelain"], cwd=self.workdir)
        if not ok:
            return {}, f"cannot read Git worktree registry: {output}"
        entries: dict[str, dict] = {}
        current: dict = {}
        for line in output.splitlines() + [""]:
            if not line:
                raw_path = current.get("worktree")
                if raw_path:
                    entries[Path(raw_path).resolve()] = current
                current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return entries, None

    def _registered_worktree(self, name: str) -> tuple[Optional[Path], Optional[str]]:
        try:
            path = self._worktree_path(name)
        except ValueError as exc:
            return None, str(exc)
        entries, error = self._registered_worktrees()
        if error:
            return None, error
        if path not in entries:
            return None, f"worktree '{name}' is not registered with Git"
        if not path.is_dir():
            return None, f"worktree '{name}' is missing at {path}"
        expected_branch = f"refs/heads/{self._worktree_branch(name)}"
        if entries[path].get("branch") != expected_branch:
            return None, (f"worktree '{name}' is not registered on expected "
                          f"branch '{self._worktree_branch(name)}'")
        return path, None

    def task_worktree_cwd(self, task: Task) -> tuple[Path, Optional[str]]:
        if not task.worktree:
            return self.workdir, None
        path, error = self._registered_worktree(task.worktree)
        return (path or self.workdir), error

    def create_worktree(self, name: str, task_id: str) -> str:
        error = self._validate_name(name)
        if error:
            return f"Error: {error}"
        with self._lock:
            try:
                path = self._worktree_path(name)
                task = self._tasks.load_task(task_id)
            except (ValueError, FileNotFoundError) as exc:
                return f"Error: {exc}"
            if task.status != "pending" or task.owner is not None:
                return f"Error: Task {task_id} must be pending and unowned"
            if task.worktree:
                return f"Error: Task {task_id} already uses worktree '{task.worktree}'"
            if any(t.worktree == name for t in self._tasks.list_tasks() if t.id != task_id):
                return f"Error: Worktree '{name}' is already bound to another task"
            if path.exists():
                return f"Error: Worktree path already exists: {path}"

            branch = self._worktree_branch(name)
            ok, _ = self._run_git(["check-ref-format", "--branch", branch], cwd=self.workdir)
            if not ok:
                return f"Error: Invalid worktree branch '{branch}'"
            exists, _ = self._run_git(["show-ref", "--verify", "--quiet",
                                       f"refs/heads/{branch}"], cwd=self.workdir)
            if exists:
                return f"Error: Branch '{branch}' already exists"
            entries, registry_error = self._registered_worktrees()
            if registry_error:
                return f"Error: {registry_error}"
            if path in entries:
                return f"Error: Worktree path is already registered: {path}"

            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            ok, result = self._run_git(["worktree", "add", "-b", branch,
                                        str(path), "HEAD"], cwd=self.workdir)
            if not ok:
                return f"Error: Git worktree add failed: {result}"

            try:
                task.worktree = name
                self._tasks.save_task(task)
            except Exception as exc:
                return (f"Partial success: Worktree '{name}' created at {path}, "
                        f"but task binding failed: {exc}. Git data retained for manual recovery.")

        return f"Worktree '{name}' created at {path} for task {task_id}"

    def remove_worktree(self, name: str, discard_changes: bool = False) -> str:
        error = self._validate_name(name)
        if error:
            return f"Error: {error}"
        with self._lock:
            path, error = self._registered_worktree(name)
            if error:
                return f"Error: {error}"
            bound = [t for t in self._tasks.list_tasks() if t.worktree == name]
            if not bound:
                return f"Error: Worktree '{name}' is not bound to a task"
            active = [t for t in bound if t.status != "completed"]
            if active:
                return (f"Error: Worktree '{name}' is bound to active task "
                        f"{active[0].id}; complete it before removal")

            ok, status = self._run_git(["status", "--porcelain", "--ignored"], cwd=path)
            if not ok:
                return f"Error: Cannot verify worktree '{name}' status: {status}"
            if status != "(no output)" and not discard_changes:
                return f"Error: Worktree '{name}' has uncommitted changes; use discard_changes=true"

            args = ["worktree", "remove"]
            if discard_changes:
                args.append("--force")
            args.append(str(path))
            ok, result = self._run_git(args, cwd=self.workdir)
            if not ok:
                return f"Error: Git worktree remove failed: {result}"

            for task in bound:
                task.worktree = None
                self._tasks.save_task(task)
        return f"Worktree '{name}' removed; branch '{self._worktree_branch(name)}' retained"

    @staticmethod
    def _validate_name(name: str) -> Optional[str]:
        if not isinstance(name, str) or not VALID_WORKTREE_NAME.fullmatch(name):
            return ("worktree name must be 1-64 letters, digits, dots, "
                    "underscores, or dashes, and start with a letter or digit")
        if ".." in name:
            return "worktree name cannot contain '..'"
        return None


# ═══════════════════════════════════════════════════════
# 工具类
# ═══════════════════════════════════════════════════════

class CreateTaskTool(Tool):
    def __init__(self, store: TaskStore):
        super().__init__("create_task", "Create a task and return its runtime-generated ID.")
        self._store = store

    def get_parameters(self):
        return [
            ToolParameter(name="subject", type="string", description="Task subject.", required=True),
            ToolParameter(name="description", type="string", description="Task description.", required=False),
        ]

    def run(self, parameters):
        try:
            task = self._store.create_task(parameters.get("subject", ""), parameters.get("description", ""))
        except ValueError as e:
            return f"Error: {e}"
        return f"Created {task.id}: {task.subject}"


class UpdateTaskTool(Tool):
    def __init__(self, store: TaskStore):
        super().__init__("update_task", "Add dependencies (blockedBy) to a task using runtime IDs.")
        self._store = store

    def get_parameters(self):
        return [
            ToolParameter(name="task_id", type="string", description="Task ID.", required=True),
            ToolParameter(name="addBlockedBy", type="array", description="List of task IDs this task depends on.", required=True),
        ]

    def run(self, parameters):
        try:
            task = self._store.update_task(parameters.get("task_id", ""), parameters.get("addBlockedBy", []))
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"
        return f"Updated {task.id} blockedBy: {', '.join(task.blockedBy) or '(none)'}"


class ListTasksTool(Tool):
    def __init__(self, store: TaskStore):
        super().__init__("list_tasks", "List all tasks and their status.")
        self._store = store

    def get_parameters(self):
        return []

    def run(self, parameters):
        tasks = self._store.list_tasks()
        if not tasks:
            return "No tasks."
        return "\n".join(
            f"  {t.id}: {t.subject} [{t.status}]"
            + (f" (owner:{t.owner})" if t.owner else "")
            + (f" (wt:{t.worktree})" if t.worktree else "")
            for t in tasks
        )


class GetTaskTool(Tool):
    def __init__(self, store: TaskStore):
        super().__init__("get_task", "Get full details of a task.")
        self._store = store

    def get_parameters(self):
        return [ToolParameter(name="task_id", type="string", description="Task ID.", required=True)]

    def run(self, parameters):
        try:
            return self._store.get_task_json(parameters.get("task_id", ""))
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"


class ClaimTaskTool(Tool):
    def __init__(self, store: TaskStore, owner: str = "agent"):
        super().__init__("claim_task", "Claim a pending task to work on it.")
        self._store = store
        self._owner = owner

    def get_parameters(self):
        return [ToolParameter(name="task_id", type="string", description="Task ID.", required=True)]

    def run(self, parameters):
        try:
            return self._store.claim_task(parameters.get("task_id", ""), owner=self._owner)
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"


class CompleteTaskTool(Tool):
    def __init__(self, store: TaskStore, owner: str = "agent"):
        super().__init__("complete_task", "Mark an in-progress task as completed.")
        self._store = store
        self._owner = owner

    def get_parameters(self):
        return [ToolParameter(name="task_id", type="string", description="Task ID.", required=True)]

    def run(self, parameters):
        try:
            return self._store.complete_task(parameters.get("task_id", ""), owner=self._owner)
        except (ValueError, FileNotFoundError) as e:
            return f"Error: {e}"


class CreateWorktreeTool(Tool):
    def __init__(self, worktrees: WorktreeStore):
        super().__init__("create_worktree", "Create a task-bound git worktree for a pending task.")
        self._worktrees = worktrees

    def get_parameters(self):
        return [
            ToolParameter(name="name", type="string", description="Worktree name.", required=True),
            ToolParameter(name="task_id", type="string", description="Task ID.", required=True),
        ]

    def run(self, parameters):
        return self._worktrees.create_worktree(
            parameters.get("name", ""), parameters.get("task_id", ""),
        )


# ═══════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════

def _default_dirs(scope: str = ""):
    """从 settings.uml_dir 推导 temp/ 下的 .tasks 与 .worktrees。"""
    from app.core.config import get_settings
    base = Path(get_settings().uml_dir).resolve().parent  # temp/
    if scope:
        # 项目路径可能包含中文、空格或盘符；用可读前缀 + hash 组成稳定目录名。
        raw = str(Path(scope).resolve())
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(scope).stem or "session")[:32]
        scope_dir = f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
        return base / ".tasks" / scope_dir, base / ".worktrees" / scope_dir, base.parent
    return base / ".tasks", base / ".worktrees", base.parent  # workdir = 项目根


def create_task_system_tools(scope: str = "") -> list[Tool]:
    tasks_dir, worktrees_dir, workdir = _default_dirs(scope)
    store = TaskStore(tasks_dir)
    worktrees = WorktreeStore(str(workdir), str(worktrees_dir), str(tasks_dir))
    return [
        CreateTaskTool(store),
        UpdateTaskTool(store),
        ListTasksTool(store),
        GetTaskTool(store),
        ClaimTaskTool(store),
        CompleteTaskTool(store),
        CreateWorktreeTool(worktrees),
    ]
