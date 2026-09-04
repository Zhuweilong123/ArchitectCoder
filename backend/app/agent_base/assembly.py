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
from app.agent_base.core.memory import (
    MemoryPort,
    MemoryRecallRequest,
    NoOpMemory,
    load_memory,
)
from app.agent_base.tools.my_tools.conversation_tools import (
    ProgressRelay,
    create_conversation_tools,
)
from app.agent_base.tools.registry import ToolRegistry
from app.runtime import build_command_executor, build_environment_context
from app.core.capabilities import CapabilityPolicy
from app.services.change_set import ChangeSet
from app.services.context_manager import ContextBudget, ContextBudgetManager, estimate_tokens


def enabled_tools_context() -> str:
    """Describe the stable core tool surface for an Agent prompt."""
    return (
        "## Tool policy\n"
        "Use only the supplied tool schemas; do not invent tools.\n"
        "Core workspace tools are: list_files, read_file, search_text, apply_changes, "
        "run_program, run_task, and shell. Use apply_changes for all file creation, "
        "editing, deletion, moving, and copying; use shell only when the operation "
        "cannot be expressed otherwise."
    )


def _workspace_root(source_dir: str, test_dir: str, design_dir: str) -> str:
    paths = [path for path in (source_dir, test_dir, design_dir) if path]
    if not paths:
        return ""
    try:
        return os.path.commonpath([os.path.abspath(path) for path in paths])
    except ValueError:
        return os.path.abspath(paths[0])


class DevPromptBuilder:
    """Build the stable and per-turn prompt sections for the DevAgent."""

    def __init__(
        self,
        memory: MemoryPort | None = None,
        *,
        source_dir: str = "",
        test_dir: str = "",
        design_dir: str = "",
        environment_context=None,
        memory_recall_top_k: int = 3,
        memory_recall_max_tokens: int = 500,
    ):
        self.prompt_version = "3.1-r4"
        if environment_context is None:
            design_dir = design_dir or ""
            workspace_root = _workspace_root(source_dir, test_dir, design_dir)
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
        )
        self.memory = memory if memory is not None else NoOpMemory()
        self.memory_recall_top_k = max(1, int(memory_recall_top_k))
        self.memory_recall_max_tokens = max(1, int(memory_recall_max_tokens))
        self.static_prompt_report = {
            "chars": len(self.system_prompt),
            "estimated_tokens": estimate_tokens(self.system_prompt),
        }
        self._ctx_key: tuple | None = None
        self._ctx_value = ""
        self.last_context_report: dict = {
            "sections": {},
            "total_chars": 0,
            "estimated_tokens": 0,
        }

    @staticmethod
    def _build_static_prompt(
        *, environment_context=None,
    ) -> str:
        runtime_block = (
            environment_context.to_prompt()
            if environment_context is not None
            else "## Runtime environment\n- Runtime environment is selected by the host runtime."
        )
        return "\n".join([
            "You are DevAgent, a coding and UML engineering agent operating only inside the configured workspace.",
            "Complete the user's request end to end: inspect relevant state, evolve existing artifacts instead of redesigning them unless requested, make scoped changes, verify results, and report what was done and what remains.",
            "",
            runtime_block,
            "",
            "## Execution rules",
            "- Do only what was asked. For a greeting or pure chat, reply briefly without tools.",
            "- Read the smallest useful context before editing. Preserve unrelated user changes; do not add comments or emojis to code unless requested; do not invent files, tool results, tests, or completion.",
            "- Make the minimal correct edit. For a repair, run the focused existing test early, fix its exact failure before broadening scope, then rerun it. Do not claim success until required verification passes.",
            "- For a concrete multi-step request, keep one short execution thread: inspect only the named artifact, make the requested file change with apply_changes, verify it immediately with run_task or run_program, then move to the next user-requested step.",
            "- When a command or tool fails, treat its error as evidence and change approach. Do not spend the remaining budget probing interpreters, package managers, shells, or unrelated directories; use the supplied file/domain tools and report any unverified step.",
            "- In a continuing conversation, preserve the latest accepted state and never reopen a completed step unless a later request explicitly changes it. A status question should summarize the checkpoint, not start a new investigation.",
            "- Do not duplicate discovery, inspect a directory merely to find the supplied workspace, or create a helper script solely to inspect or summarize an existing file.",
            "- Treat the supplied Source directory as the working root for relative paths and execution tools.",
            "- Use only tools exposed for the current task and their supplied schemas. Prefer list_files/read_file/search_text/apply_changes/run_task/run_program over shell workarounds; treat tool errors as capability facts and change approach instead of repeating them.",
            "- After any successful UML/design change, call submit_uml_review before reporting completion, wait for its accept/reject decision, and revise if rejected. This remains required when review is auto-approved.",
            "- For a task spanning UML/design and source or multiple unrelated files, call spawn_subagent once before broad direct exploration. Give it a read-only fact-finding task, use its summary, and do not repeat the same discovery yourself. Do not call it for greetings, simple single-file work, edits, review, or final verification.",
            "",
            "## UML and verification",
            "- Do not modify UML unless requested or required by an externally visible code-contract change. For UML work, prefer graph and targeted file tools; load a skill only when its guidance is necessary.",
            "- For a full migration from a known canonical design artifact to a stale peer, reuse the canonical artifact instead of reconstructing diagrams. Use one workspace-local copy operation, then validate the target.",
            "- JSON parsing alone does not verify a UML repair: verify the project loads with a non-empty diagram set, using a valid project as structural reference rather than creating an empty placeholder.",
            "- Follow the current task policy for planning and verification; do not create plans or worktrees when they are not required. Report modified, verified, partial, blocked, and failed states distinctly.",
            "",
            "If a safety rule, missing authority, or hard budget prevents completion, stop safely and report completed work, remaining work, and the exact reason.",
        ])

    async def build_context(
        self, project_file: str, source_dir: str, test_dir: str, user_message: str
    ) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        key = (project_file, today, user_message)
        if key == self._ctx_key:
            return self._ctx_value

        sections: list[tuple[str, str]] = []

        def add_section(name: str, value: str) -> None:
            if value:
                sections.append((name, value))

        project_id = (
            os.path.splitext(os.path.basename(project_file))[0]
            if project_file else ""
        )
        memory_block = await self._recall_memory_block(project_id, user_message)
        if memory_block:
            add_section("memory", memory_block)
        add_section("date", f"Current date: {today}")

        self._ctx_key = key
        self._ctx_value = "\n\n".join(value for _, value in sections)
        self.last_context_report = {
            "sections": {
                name: {"chars": len(value), "estimated_tokens": estimate_tokens(value)}
                for name, value in sections
            },
            "total_chars": len(self._ctx_value),
            "estimated_tokens": estimate_tokens(self._ctx_value),
        }
        return self._ctx_value

    async def _recall_memory_block(self, project_id: str, user_message: str) -> str:
        if not project_id:
            return ""
        try:
            result = await self.memory.recall(MemoryRecallRequest(
                project_id=project_id,
                query=user_message,
                top_k=self.memory_recall_top_k,
                max_tokens=self.memory_recall_max_tokens,
            ))
            if result.context_block and result.memory_ids:
                await self.memory.reinforce(result.memory_ids, project_id=project_id)
            return result.context_block
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "[Memory] Recall failed (non-fatal)", exc_info=True,
            )
            return ""


async def create_dev_agent(
    llm: BaseAgentsLLM,
    source_dir: str = "",
    test_dir: str = "",
    project_file: str = "",
    user_message: str = "",
    progress: ProgressRelay | None = None,
    restore_history: list[dict] | None = None,
    task_scope: str = "",
    auto_approve_reviews: bool = False,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    max_run_seconds: float | None = None,
    max_total_tokens: int | None = None,
    convergence_tool_steps: int | None = None,
):
    """Assemble the production DevAgent independently of any transport."""
    settings = get_settings()
    change_set = ChangeSet(project_file=project_file)
    command_executor = build_command_executor(settings)
    design_dir = (
        os.path.dirname(os.path.abspath(project_file))
        if project_file else os.path.abspath(settings.uml_dir)
    )
    workspace_root = _workspace_root(source_dir, test_dir, design_dir)
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
    )

    workspace_roots = [workspace_root] if workspace_root else []
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=workspace_roots))
    for tool in tools:
        registry.register_tool(tool)

    memory_provider = load_memory(llm=llm, settings=settings)
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
    prompt_builder = DevPromptBuilder(
        memory=memory_provider,
        source_dir=source_dir,
        test_dir=test_dir,
        design_dir=design_dir,
        environment_context=environment_context,
        memory_recall_top_k=settings.agent_memory_recall_top_k,
        memory_recall_max_tokens=settings.agent_memory_recall_max_tokens,
    )
    agent = ReActAgent(
        name="DevAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=prompt_builder.system_prompt,
        max_steps=max_steps or settings.agent_max_steps,
        max_tool_calls=max_tool_calls or settings.agent_max_tool_calls,
        max_repeated_tool_calls=settings.agent_max_repeated_tool_calls,
        max_run_seconds=max_run_seconds or settings.agent_max_run_seconds,
        max_total_tokens=max_total_tokens or settings.agent_max_total_tokens,
        token_finalization_reserve_tokens=settings.agent_token_finalization_reserve_tokens,
        convergence_tool_steps=(
            convergence_tool_steps
            if convergence_tool_steps is not None
            else settings.agent_convergence_tool_steps
        ),
        convergence_budget_ratio=settings.agent_convergence_budget_ratio,
        convergence_keep_recent_steps=settings.agent_convergence_keep_recent_steps,
        evidence_max_records=settings.agent_evidence_max_records,
        force_final_summary_on_step_limit=settings.agent_force_final_summary_on_step_limit,
        final_summary_max_tokens=settings.agent_final_summary_max_tokens,
        llm_timeout_seconds=settings.agent_llm_timeout_seconds,
        use_native_fc=True,
        context_budget=ContextBudgetManager(budget=ContextBudget(
            max_context_tokens=settings.agent_context_max_tokens,
            output_reserve_tokens=settings.agent_context_output_reserve_tokens,
            max_history_tokens=settings.agent_context_max_history_tokens,
            max_history_turns=settings.agent_context_max_history_turns,
            max_summary_tokens=settings.agent_context_max_summary_tokens,
            max_react_steps=settings.agent_context_max_react_steps,
        )),
    )
    agent.change_set = change_set
    agent.memory_provider = memory_provider
    if restore_history:
        agent.restore_history(restore_history)
    return agent, review_mgr, prompt_builder


__all__ = ["DevPromptBuilder", "ProgressRelay", "create_dev_agent", "enabled_tools_context"]
