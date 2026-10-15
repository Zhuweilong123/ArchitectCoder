"""Restricted tool assembly and prompt selection for child agents."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Unpack
from app.agent_base.host_api.agents import ReviewManagerPort, ProgressPort
from app.agent_base.host_api.data import JsonObject, ToolSchema
from app.agent_base.host_api.contexts import SkillContext
from app.agent_base.adapters.skills import SkillCatalog, capture_skill_catalog
from app.agent_base.tools.base import Tool
from app.agent_base.tools.command_options import CommandToolOptions
from app.agent_base.tools.result import ToolResult
from app.agent_base.tools.registry import ToolRegistry
from app.core.capabilities import CapabilityPolicy
from app.runtime.command import CommandExecutor
from app.runtime import TaskKind
from .foundation_tools import RunTaskTool, create_foundation_tools
from .skill_loader import SkillTool, build_skills_section

SUBAGENT_SYSTEM = (
    "You are a coding subagent. Complete the given task, then return a concise "
    "final summary of what you did and found. Do not spawn more agents."
)

STRATEGY_SUBAGENT_SYSTEM = (
    "You are a read-only strategy advisor for a development task. Do not modify "
    "files and do not propose unverified implementation details. Inspect only the "
    "minimum evidence needed, then return a concise plan with: canonical source, "
    "target scope, ordered steps, acceptance criteria, and risks. Do not spawn more agents."
)

VERIFICATION_SUBAGENT_SYSTEM = (
    "You are a source-safe verification subagent. Inspect the relevant files, then "
    "run only the minimum fixed project verification task needed: test, build, "
    "lint, typecheck, or validate. Do not modify source files, run arbitrary programs, "
    "use shell commands, or spawn more agents. Return concise evidence and any "
    "failure diagnosis."
)

# ── 子代理工具包（toolkit）──────────────────────────────────────
# 主 agent 按任务类型选工具包，框架展开成受限工具集。安全不变量由本表强制，
# 不依赖主 agent 自觉：
#   * 任何工具包都不含 spawn_subagent / submit_uml_review（防递归 / 防审核绕过）
#   * 子代理工具集是主 agent 允许集的子集（无提权）
TOOLKIT_NAMES = ("standard", "read_only", "kg_analysis", "strategy", "verification")


class VerificationRunTaskTool(RunTaskTool):
    """Run project-declared, non-formatting checks inside a subagent.

    The resolver and broker remain the source of truth for supported project
    tasks.  Verification only applies its semantic safety policy and does not
    maintain a second language/task allowlist.
    """

    # Compatibility fallback/schema hint only. Resolved project tasks do not
    # need to be added here; the resolver handles those dynamically.
    TASKS = {
        name: RunTaskTool.TASKS[name]
        for name in ("test", "build", "lint", "typecheck", "validate")
    }

    def __init__(self, source_dir: str = "", test_dir: str = "", design_dir: str = "",
                 **kwargs: Unpack[CommandToolOptions]) -> None:
        super().__init__(source_dir, test_dir, design_dir, **kwargs)
        self.description = (
            "Run one project-declared verification task without source editing. "
            "The task must resolve from the project's build metadata; formatting, "
            "arbitrary programs, and shell commands are not available in this "
            "toolkit. Build/test caches or generated artifacts may be produced."
        )

    async def _execute_result(self, params: JsonObject) -> str | ToolResult:
        task = str(params.get("task", "")).lower().strip()
        if not task:
            return "Error: task must be a non-empty string"
        if task == "format":
            return (
                "Error: formatting tasks are not available in the verification toolkit."
            )
        # Classify resolved tasks by their contract rather than by a language
        # or task-name list.  A custom task declared by any supported project
        # adapter is valid unless it resolves to the formatting kind.
        resolved_cwd, cwd_error = self._resolve_cwd(params.get("cwd"))
        target = params.get("target")
        profile = params.get("profile")
        if target is not None and not isinstance(target, str):
            return "Error: target must be a string"
        if profile is not None and not isinstance(profile, str):
            return "Error: profile must be a string"
        if not cwd_error and resolved_cwd:
            resolution = self._task_resolver.resolve(
                task, resolved_cwd, target=target, profile=profile,
            )
            if resolution.resolved and resolution.task is not None and resolution.task.kind is TaskKind.FORMAT:
                return (
                    "Error: formatting tasks are not available in the verification toolkit."
                )
        return ToolResult.from_value(await super()._execute_result(params))

    def to_openai_schema(self) -> ToolSchema:
        # Keep the legacy built-in enum as a compatibility hint for clients
        # that cache this schema.  Runtime validation above still accepts any
        # project-declared task resolved by TaskResolver; the enum is not the
        # execution policy.
        schema: ToolSchema = super().to_openai_schema()
        properties = schema["function"]["parameters"].get("properties")
        if isinstance(properties, dict):
            task = properties.get("task")
            if isinstance(task, dict):
                task["enum"] = [name for name in self.TASKS]
        return schema


def _build_toolkit_tools(
    kind: str,
    source_dir: str, test_dir: str, design_dir: str,
    review_manager: ReviewManagerPort | None, progress: ProgressPort | None,
    command_executor: CommandExecutor, workspace_root: str,
    skill_catalog: SkillCatalog | None = None,
) -> list[Tool]:
    """按工具包名构建工具列表（不含 spawn_subagent / submit_uml_review）。"""
    foundation = create_foundation_tools(
        source_dir, test_dir, design_dir,
        review_manager=review_manager, progress=progress,
        command_executor=command_executor,
        workspace_root=workspace_root,
    )
    by_name = {tool.name: tool for tool in foundation}
    if skill_catalog is None:
        skill_catalog = capture_skill_catalog(context=SkillContext(workspace_root=workspace_root))
    skill_tools = [SkillTool(catalog=skill_catalog)] if skill_catalog.entries else []
    if kind == "standard":
        return [*foundation, *skill_tools]

    # Read-only toolkits intentionally expose only the foundation inspection
    # contract.  Directory listing and text search are needed to make a
    # bounded analysis useful without granting execution or write access.
    # KG toolkit names remain for compatibility, but graph tools are disabled
    # for DevAgent to avoid broad exploration and repeated reads.
    inspection_tools = [
        by_name["list_files"],
        by_name["read_file"],
        by_name["search_text"],
        by_name["read_tool_output"],
    ]
    if kind == "read_only":
        return inspection_tools
    if kind in {"kg_analysis", "strategy"}:
        return [*inspection_tools, *skill_tools]
    if kind == "verification":
        return [
            *inspection_tools,
            *skill_tools,
            VerificationRunTaskTool(
                source_dir=source_dir,
                test_dir=test_dir,
                design_dir=design_dir,
                review_manager=review_manager,
                progress=progress,
                command_executor=command_executor,
                workspace_root=workspace_root,
            ),
        ]
    raise ValueError(f"unknown toolkit: {kind}")


@dataclass(frozen=True)
class SubagentToolkits:
    registries: dict[str, ToolRegistry]
    prompts: dict[str, str]


def build_subagent_toolkits(
    toolkits: tuple[str, ...], *, source_dir: str, test_dir: str, design_dir: str,
    review_manager: ReviewManagerPort | None, progress: ProgressPort | None,
    command_executor: CommandExecutor, workspace_root: str,
    skill_catalog: SkillCatalog | None = None,
) -> SubagentToolkits:
    unknown = set(toolkits) - set(TOOLKIT_NAMES)
    if not toolkits or unknown:
        raise ValueError(f"unknown or empty subagent toolkits: {sorted(unknown)}")
    if skill_catalog is None:
        skill_catalog = capture_skill_catalog(context=SkillContext(workspace_root=workspace_root))
    skills = build_skills_section(catalog=skill_catalog)
    registries: dict[str, ToolRegistry] = {}
    prompts: dict[str, str] = {}
    for kind in toolkits:
        registry = ToolRegistry(policy=CapabilityPolicy(
            workspace_roots=[workspace_root, source_dir, test_dir, design_dir],
        ))
        for t in _build_toolkit_tools(
            kind, source_dir, test_dir, design_dir,
            review_manager, progress,
            command_executor, workspace_root, skill_catalog,
        ):
            registry.register_tool(t)
        registries[kind] = registry
        if kind == "strategy":
            prompt = STRATEGY_SUBAGENT_SYSTEM
        elif kind == "verification":
            prompt = VERIFICATION_SUBAGENT_SYSTEM
        else:
            prompt = SUBAGENT_SYSTEM
        if kind != "read_only" and skills:
            prompt = f"{prompt}\n\n{skills}"
        prompt += (
            "\n.architectcoder is an internal project state directory managed "
            "by the host, not source code, tests, or design content. Exclude "
            "it from exploration and do not read, edit, or delete its contents. "
            "Task configuration is resolved by the host through run_task."
        )
        prompts[kind] = prompt

    return SubagentToolkits(registries, prompts)
