"""Application-owned assembly for the production development Agent.

This module is the composition boundary between the Agent framework and the
optional extensions.  Transport adapters (WebSocket, evaluation, and future
HTTP/CLI entry points) can build the same Agent without importing one another.
"""

from __future__ import annotations

import os
from datetime import datetime

from backend.config import get_settings

from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.llm import BaseAgentsLLM
from app.agent_base.core.policy import ExecutionBudget
from app.agent_base.adapters.memory import (NoOpMemory, load_memory)
from app.agent_base.core.extension_context import ExtensionContext, extension_scope
from app.agent_base.tools.my_tools.conversation_tools import (
    ProgressRelay,
    create_conversation_tools,
)
from app.agent_base.tools.my_tools.skill_loader import build_skills_section
from app.agent_base.adapters.skills import (SkillCatalog, capture_skill_catalog, load_skills)
from app.agent_base.host_api.contexts import SkillContext
from app.agent_base.tools.registry import ToolRegistry
from app.runtime import (
    WorkspaceManifest,
    build_command_executor,
    build_environment_context,
    workspace_root_for,
)
from app.core.capabilities import CapabilityPolicy
from app.services.change_set import ChangeSet
from app.services.context_manager import ContextBudget, ContextBudgetManager, estimate_tokens
from app.agent_base.adapters.contract_gate import (load_contract_gate)
from app.agent_base.adapters.contracts import load_contracts
from app.agent_base.adapters.contract_analysis import (load_contract_failure_analyzer)
from app.runtime.language_runner import (broker_command_runner)
from app.agent_base.core.plugin_runtime import pin_plugins


def enabled_tools_context() -> str:
    """Describe the stable core tool surface and its routing order."""
    return (
        "## Tool policy\n"
        "Use only the supplied tool schemas; do not invent tools.\n"
        "Core workspace tools are: list_files, read_file, search_text, apply_changes, "
        "run_task, run_program, and shell. Route execution in this order: run_task "
        "for test/build/lint/format/typecheck/validate; run_program for a direct "
        "allowlisted executable with literal argv (never powershell/cmd/bash or shell "
        "syntax); shell only for one simple allowlisted native command with no pipes, "
        "chaining, redirection, substitution, or nested shell. Use apply_changes for "
        "all file creation, editing, deletion, moving, and copying. Use read_file "
        "for current workspace file content and its line-based continuation. "
        "Use read_tool_output only when another tool provides an output_id and "
        "next_offset for a truncated result."
    )


class DevPromptBuilder:
    """Build the stable and per-turn prompt sections for the DevAgent."""

    def __init__(
        self,
        *,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        environment_context=None,
        skill_catalog: SkillCatalog | None = None,
        extension_context: ExtensionContext | None = None,
    ):
        self.prompt_version = "3.1-r4"
        if environment_context is None:
            design_dir = design_dir or ""
            workspace_root = workspace_root_for(source_dir, test_dir, design_dir)
            environment_context = build_environment_context(
                cwd=workspace_root or source_dir or design_dir or None,
                workspace_roots=(workspace_root,) if workspace_root else (),
                workspace_layout=(
                    ("design", design_dir),
                    ("src", source_dir),
                    ("test", test_dir),
                ),
            )
        self.system_prompt = self._build_static_prompt(
            environment_context=environment_context,
            skill_catalog=skill_catalog,
        )
        self.extension_context = extension_context or ExtensionContext()
        self.static_prompt_report = {
            "chars": len(self.system_prompt),
            "estimated_tokens": estimate_tokens(self.system_prompt),
        }
        self._ctx_value = ""
        self.last_context_report: dict = {
            "sections": {},
            "total_chars": 0,
            "estimated_tokens": 0,
        }

    @staticmethod
    def _build_static_prompt(
        *, environment_context=None, skill_catalog: SkillCatalog | None = None,
    ) -> str:
        runtime_block = (
            environment_context.to_prompt()
            if environment_context is not None
            else "## Runtime environment\n- Runtime environment is selected by the host runtime."
        )
        prompt_parts = [
            "You are DevAgent, a coding and UML engineering agent operating only inside the configured workspace.",
            "Complete the user's request end to end: inspect relevant state, evolve existing artifacts instead of redesigning them unless requested, make scoped changes, verify results, and report what was done and what remains.",
            "",
            runtime_block,
            "",
            "## Execution rules",
            "- Do only what was asked. For a greeting or pure chat, reply briefly without tools.",
            "- Read the smallest useful context before editing. Preserve unrelated user changes and do not invent files, tool results, tests, or completion.",
            "- .architectcoder is an internal project state directory managed by the host, not source code, tests, or design content. Exclude it from project exploration and do not read, edit, or delete its contents. Task configuration is resolved by the host through run_task.",
            "- Make the minimal correct change and verify each completed phase before moving to the next phase. For repairs, run the focused existing test early and rerun it after the fix.",
            "- For a multi-step task with two or more meaningful phases, call todo_write before other tools and create 3-5 concise todos. Include one verification item, keep one item in_progress, update statuses as phases finish, and complete all items before the final response. Do not use it for greetings, simple single-step edits, pure review, or status questions.",
            "- Treat a human-review pause as a normal phase boundary. Preserve the latest accepted state and resume from the review result.",
            "- When a command or tool fails, use the error as evidence, change approach, and report anything that remains unverified.",
            "- Do not reopen completed work unless the user explicitly changes the request. Do not duplicate discovery or create helper scripts only to inspect existing files.",
            "- Treat the supplied Source directory as the working root for relative paths and execution tools.",
            "- Use only tools exposed for the current task and their supplied schemas. Prefer the supplied file and task tools over shell workarounds.",
            "- Use a subagent only when the task genuinely benefits from separate read-only fact finding; do not use it for simple edits, review, or final verification.",
            "- Do not report the task as complete until the required design review, implementation, and verification phases have finished.",
            "",
            "## Software requirement workflow",
            "- Classify the request as design-impacting or implementation-only. Design-impacting means changing system behavior, data models, classes, interfaces, components, APIs, module boundaries, or cross-component interactions.",
            "- For a design-impacting task, use this TODO order: inspect context → update UML → validate and submit human review → implement only after acceptance → verify. If rejected, revise the UML and resubmit; do not modify business code before acceptance.",
            "- For an implementation-only single-step task, work directly. For implementation-only multi-step work, use the same 3-5 item TODO and verification rule without creating or reviewing UML.",
            "- If the user asks for design only, stop after the design review.",
            "- Verify that the project loads with a non-empty diagram set; JSON parsing alone is insufficient. Use the current task's design and test policy, and report modified, verified, partial, blocked, and failed states distinctly.",
            "",
            "If a safety rule, missing authority, or hard budget prevents completion, stop safely and report completed work, remaining work, and the exact reason.",
        ]
        skills_section = build_skills_section(catalog=skill_catalog)
        if skills_section:
            prompt_parts.extend(["", skills_section])
        return "\n".join(prompt_parts)

    async def build_context(self, *args, **kwargs):
        from app.agent_base.core.hooks import HookEvent, get_runtime
        from app.agent_base.core.operations import operation_scope, current_operation
        parent = current_operation()
        with extension_scope(self.extension_context.fork()), operation_scope("prepare", run_id=get_runtime().run_id or (parent.run_id if parent else ""), stage=HookEvent.PREPARE.value):
            return await self._build_context_impl(*args, **kwargs)

    async def _build_context_impl(
        self, project_file: str, source_dir: str, test_dir: str, user_message: str
    ) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        from app.agent_base.core.hooks import HookEvent, HookContext, get_hooks, get_runtime
        from backend.config.project_storage import project_id_for
        project_id = project_id_for(project_file) if project_file else ""
        sections: dict[str, str] = {}
        await get_hooks().aemit(HookEvent.PREPARE, HookContext(
            HookEvent.PREPARE, "DevAgent", run_id=get_runtime().run_id,
            payload={"project_id": project_id, "project_file": project_file,
                     "user_message": user_message, "sections": sections},
        ))
        sections["date"] = f"Current date: {today}"
        self._ctx_value = "\n\n".join(value for value in sections.values() if value)
        self.last_context_report = {
            "sections": {
                name: {"chars": len(value), "estimated_tokens": estimate_tokens(value)}
                for name, value in sections.items() if value
            },
            "total_chars": len(self._ctx_value),
            "estimated_tokens": estimate_tokens(self._ctx_value),
        }
        return self._ctx_value

@pin_plugins
async def create_dev_agent(*args, **kwargs):
    from app.agent_base.core.hooks import HookContext, HookEvent, get_hooks
    from app.agent_base.core.operations import operation_scope
    with operation_scope("initialize", stage=HookEvent.INITIALIZE.value, scope="agent"):
        await get_hooks().aemit(HookEvent.INITIALIZE, HookContext(HookEvent.INITIALIZE, "DevAgent"))
        return await _create_dev_agent_impl(*args, **kwargs)


async def _create_dev_agent_impl(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    user_message: str = "",
    progress: ProgressRelay | None = None,
    restore_history: list[dict] | None = None,
    task_scope: str = "",
    auto_approve_reviews: bool = False,
    max_tool_calls: int | None = None,
    max_run_seconds: float | None = None,
    max_total_tokens: int | None = None,
    design_dir: str = "",
    workspace_root: str = "",
):
    """Assemble the production DevAgent independently of any transport."""
    settings = get_settings()
    # The manifest discovers only existing workspace directories. Do not fill
    # an unconfigured design scope with an unrelated global project directory.
    manifest = WorkspaceManifest.from_paths(
        project_file=project_file,
        source_dir=source_dir,
        test_dir=test_dir,
        design_dir=design_dir,
        workspace_root=workspace_root,
    )
    source_dir = manifest.source_root
    test_dir = manifest.test_root
    project_file = manifest.project_file
    design_dir = manifest.design_root
    workspace_root = manifest.workspace_root
    change_set = ChangeSet(project_file=project_file)
    command_executor = build_command_executor(settings)
    skill_catalog = capture_skill_catalog(
        load_skills(settings=settings), context=SkillContext(workspace_root=workspace_root),
    )
    tools, review_mgr = create_conversation_tools(
        llm,
        source_dir=source_dir,
        test_dir=test_dir,
        project_file=project_file,
        include_review=True,
        progress=progress,
        task_scope=task_scope or project_file,
        change_set=change_set,
        review_session_id=task_scope or "",
        review_project_id=(
            os.path.splitext(os.path.basename(project_file))[0]
            if project_file else ""
        ),
        auto_approve_reviews=auto_approve_reviews,
        command_executor=command_executor,
        include_subagent=settings.agent_main_subagent_enabled,
        workspace_root=workspace_root,
        design_dir=design_dir,
        skill_catalog=skill_catalog,
    )

    workspace_roots = list(manifest.workspace_roots)
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=workspace_roots))
    for tool in tools:
        registry.register_tool(tool)

    environment_context = build_environment_context(
        executor=command_executor,
        cwd=workspace_root or source_dir or design_dir or None,
        workspace_roots=workspace_roots,
        workspace_layout=(
            ("design", design_dir),
            ("src", source_dir),
            ("test", test_dir),
        ),
    )
    memory_provider = load_memory(
        llm=llm, settings=settings, project_file=project_file, workspace_root=workspace_root,
        source_dir=source_dir, test_dir=test_dir, design_dir=design_dir,
        environment_context=environment_context,
    )
    from backend.config.project_storage import project_id_for
    extension_context = ExtensionContext(metadata={
        "project_id": project_id_for(project_file) if project_file else "", "workspace": manifest.to_dict(),
    })
    if not isinstance(memory_provider, NoOpMemory):
        extension_context.bind("memory", memory_provider, settings=settings, options={
            "recall_top_k": settings.agent_memory_recall_top_k,
            "recall_max_tokens": settings.agent_memory_recall_max_tokens,
        })
    prompt_builder = DevPromptBuilder(
        extension_context=extension_context,
        source_dir=source_dir,
        test_dir=test_dir,
        design_dir=design_dir,
        environment_context=environment_context,
        skill_catalog=skill_catalog,
    )
    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=prompt_builder.system_prompt,
        execution_budget=ExecutionBudget.from_settings(
            settings,
            max_tool_calls=(
                max_tool_calls if max_tool_calls is not None else settings.agent_max_tool_calls
            ),
            max_run_seconds=(
                max_run_seconds if max_run_seconds is not None else settings.agent_max_run_seconds
            ),
            max_total_tokens=(
                max_total_tokens if max_total_tokens is not None
                else settings.agent_context_soft_limit_tokens
            ),
        ),
        token_finalization_reserve_tokens=settings.agent_token_finalization_reserve_tokens,
        convergence_max_stalled_rounds=settings.agent_convergence_max_stalled_rounds,
        convergence_max_recovery_rounds=settings.agent_convergence_max_recovery_rounds,
        convergence_repeat_action_threshold=settings.agent_convergence_repeat_action_threshold,
        evidence_max_records=settings.agent_evidence_max_records,
        final_summary_max_tokens=settings.agent_final_summary_max_tokens,
        llm_timeout_seconds=settings.agent_llm_timeout_seconds,
        context_budget=ContextBudgetManager(budget=ContextBudget(
            max_context_tokens=settings.agent_context_hard_limit_tokens,
            max_history_turns=settings.agent_context_max_history_turns,
            max_summary_tokens=settings.agent_context_max_summary_tokens,
            soft_threshold_ratio=settings.agent_context_soft_threshold_ratio,
            compaction_trigger_ratio=settings.agent_context_compaction_threshold_ratio,
        )),
    )
    agent.change_set = change_set
    # The composition root owns the wiring between execution and contract
    # analysis.  ContractGate receives a capability, not a reference to the
    # Agent's tool registry or a tool's private implementation fields.
    run_task_tool = next(
        (tool for tool in tools if getattr(tool, "name", "") == "run_task"),
        None,
    )
    execution_broker = getattr(run_task_tool, "execution_broker", None)
    language_runner = (
        broker_command_runner(execution_broker)
        if execution_broker is not None else None
    )
    contract_provider = load_contracts(settings=settings, language_runner=language_runner)
    agent.contract_gate = load_contract_gate(settings=settings, provider=contract_provider)
    agent.contract_failure_analyzer = load_contract_failure_analyzer(settings=settings, provider=contract_provider)
    agent.extension_context = prompt_builder.extension_context
    agent.workspace_manifest = manifest.to_dict()
    if restore_history:
        agent.restore_history(restore_history)
    return agent, review_mgr, prompt_builder


__all__ = ["DevPromptBuilder", "ProgressRelay", "create_dev_agent", "enabled_tools_context"]
