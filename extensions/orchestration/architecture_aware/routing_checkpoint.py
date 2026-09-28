"""One-time routing reminder after a broad main-Agent file listing."""

from __future__ import annotations

from app.agent_base.core.hooks import HookContext, HookEvent, get_hooks

_registered = False
_HEADING = "## Architecture routing checkpoint"


def _routing_checkpoint(ctx: HookContext):
    runtime = ctx.runtime
    if runtime is None or not runtime.policy_metadata.get("architecture_scheduling_enabled"):
        return None
    state = runtime.policy_metadata
    if ctx.event == HookEvent.TOOL_BATCH_AFTER:
        for detail in ctx.payload.get("details") or ():
            if not isinstance(detail, dict):
                continue
            name = detail.get("name")
            if name in {"route_architecture", "explore_architecture"}:
                state["architecture_route_decided"] = True
                state.pop("architecture_route_pending", None)
                return None
            if name != "list_files" or detail.get("status") != "success":
                continue
            arguments = detail.get("arguments") or {}
            if not isinstance(arguments, dict):
                continue
            pattern = str(arguments.get("pattern") or "**/*").strip()
            path = str(arguments.get("path") or "workspace").strip().lower()
            if (path in {"", ".", "workspace", "source", "src"}
                    and (pattern in {"*", "**/*"} or pattern.startswith("**/"))
                    and not state.get("architecture_route_decided")
                    and not state.get("architecture_route_checkpoint_sent")):
                state["architecture_route_pending"] = True
        return None
    if ctx.event == HookEvent.LLM_BEFORE and state.pop("architecture_route_pending", False):
        if state.get("architecture_route_decided") or state.get("architecture_route_checkpoint_sent"):
            return None
        if ctx.messages is None:
            return None
        ctx.messages.append({
            "role": "system",
            "content": (
                _HEADING + "\n"
                "You just listed a broad project scope. Before opening many files "
                "across components, decide whether a narrow lookup is sufficient or "
                "read-only graph-guided exploration would help. If the request spans "
                "several business capabilities and you have not verified the source "
                "path yet, prefer decision=explore. Use decision=direct when a narrow "
                "edit path is already known. Call route_architecture once with a "
                "concrete reason. Exploration also needs a "
                "specific goal and observed graph names. For a clearly local task, "
                "continue directly. Only the main Agent makes this decision."
            ),
        })
        state["architecture_route_checkpoint_sent"] = True
    if ctx.event == HookEvent.LLM_AFTER and state.get("architecture_route_checkpoint_sent"):
        if ctx.messages is not None:
            ctx.messages[:] = [
                message for message in ctx.messages
                if not (isinstance(message, dict) and message.get("role") == "system"
                        and str(message.get("content") or "").startswith(_HEADING))
            ]
    return None


def register_routing_checkpoint_hooks() -> None:
    global _registered
    if _registered:
        return
    hooks = get_hooks()
    hooks.register(HookEvent.TOOL_BATCH_AFTER, _routing_checkpoint, priority=50)
    hooks.register(HookEvent.LLM_BEFORE, _routing_checkpoint, priority=55)
    hooks.register(HookEvent.LLM_AFTER, _routing_checkpoint, priority=55)
    _registered = True
