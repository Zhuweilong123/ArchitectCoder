"""Memory-owned lifecycle policy; the host only publishes generic events."""
from __future__ import annotations

import asyncio
import logging
import re

from app.agent_base.core.extension_context import current_extension_context
from app.agent_base.ports.memory import (MemoryArchiveRequest, MemoryEventRequest, MemoryRecallRequest)

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task] = set()
_MEMORY_BLOCK = re.compile(r"<project_memory>.*?</project_memory>", re.DOTALL)


def _binding():
    context = current_extension_context()
    if context is None:
        return None, None, None
    return context, context.providers.get("memory"), context.state("memory")


async def _recall(provider, project_id, query):
    session = current_extension_context()
    options = session.metadata.get("plugin_options", {}).get("memory", {}) if session else {}
    return await provider.recall(MemoryRecallRequest(
        project_id, query, top_k=options.get("recall_top_k", getattr(provider, "recall_top_k", 3)),
        max_tokens=options.get("recall_max_tokens", getattr(provider, "recall_max_tokens", 500)),
    ))


async def prepare(context):
    session, provider, state = _binding()
    if provider is None or "sections" not in context.payload:
        return
    project_id = context.payload.get("project_id", "")
    state.clear()
    if not project_id:
        return
    try:
        result = await _recall(provider, project_id, context.payload.get("user_message", ""))
        if result.context_block:
            context.payload["sections"]["memory"] = result.context_block
        state["recall_report"] = result.metadata
    except Exception:
        logger.warning("[Memory] prepare recall failed", exc_info=True)


def run_start(context):
    session, provider, state = _binding()
    if provider is None or "user_message" not in context.payload:
        return
    state.clear()
    state.update(query=context.payload["user_message"], resources={}, dirty=False, run_id=context.run_id)


async def tool_after(context):
    session, provider, state = _binding()
    if provider is None or "result" not in context.payload or state.get("run_id") != context.run_id:
        return
    project_id = session.metadata.get("project_id", "")
    observe = getattr(provider, "observe", None)
    if not project_id or observe is None:
        return
    detail = {"name": context.tool_name, "arguments": context.tool_input,
              "observation": context.tool_output, **context.payload["result"]}
    state["dirty"] = state.get("dirty", False) or bool(detail.get("changes"))
    # Dispatch is awaited at each tool boundary, before the next mutation.
    result = await observe(MemoryEventRequest(
        project_id, "tool_observed", tool_steps=(detail,), run_id=context.run_id,
        trace_id=context.payload.get("trace_id", ""),
        event_id=f"{project_id}:{context.run_id}:{context.payload.get('event_id', '')}",
    ))
    resources = state.setdefault("resources", {})
    for resource in result.resources:
        resources[resource["resource_id"]] = dict(resource)
    state["dirty"] = state.get("dirty", False) or bool(detail.get("changes") or result.affected_count)


async def model_before(context):
    session, provider, state = _binding()
    if provider is None or not state.get("dirty") or context.messages is None or state.get("run_id") != context.run_id:
        return
    index = context.payload.get("current_user_index", -1)
    if not 0 <= index < len(context.messages):
        return
    message = context.messages[index]
    content = str(message.get("content") or "")
    if not _MEMORY_BLOCK.search(content):
        state["dirty"] = False
        return
    replacement = ""
    try:
        result = await _recall(provider, session.metadata.get("project_id", ""), state.get("query", ""))
        match = _MEMORY_BLOCK.search(result.context_block)
        replacement = match.group(0) if match else ""
        state["recall_report"] = result.metadata
    except Exception:
        logger.warning("[Memory] refresh failed; removing stale projection", exc_info=True)
    message["content"] = _MEMORY_BLOCK.sub(lambda _: replacement, content)
    state["dirty"] = False
    state["refreshed"] = message["content"] != content


def should_archive(checkpoint_status, tool_calls_detail, checkpoint=None, *, user_message="", final_answer=""):
    mutated = bool((checkpoint or {}).get("mutation_evidence")) or any(
        detail.get("changes") and detail.get("status") in {"success", "completed"}
        for detail in tool_calls_detail if isinstance(detail, dict)
    )
    discussion = bool(user_message.strip() and not tool_calls_detail and len(final_answer.strip()) >= 160)
    return checkpoint_status == "completed" and (mutated or discussion)


def task_after(context):
    session, provider, state = _binding()
    if provider is None:
        return
    data = context.payload
    if state.get("archive_scheduled"):
        return
    project_id = data.get("project_id", "")
    if not project_id or not should_archive(data.get("status", ""), data.get("tool_steps", []),
        data.get("checkpoint"), user_message=data.get("user_message", ""), final_answer=data.get("final_answer", "")):
        return
    request = MemoryArchiveRequest(
        project_id, data["user_message"], data["final_answer"],
        tool_steps=tuple(data.get("tool_steps", ())), run_id=context.run_id,
        trace_id=data.get("trace_id", ""), conversation_history=tuple(data.get("conversation_history", ())),
        terminal_status=data["status"], resources=tuple(state.get("resources", {}).values()),
    )
    state["archive_scheduled"] = True
    # The snapshot is frozen before background work; future runs cannot rebind evidence.
    task = asyncio.create_task(archive_task(provider, request))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def archive_task(memory, request):
    from app.agent_base.core.hooks import HookContext, HookEvent, get_hooks, get_runtime
    from app.agent_base.core.operations import operation_scope
    runtime = get_runtime()
    parent_id = runtime.run_operation_id if request.run_id and runtime.run_id == request.run_id else None
    async def publish(stage, **data):
        await get_hooks().aemit(stage, HookContext(stage, "memory", run_id=request.run_id,
            payload={"source": "memory_archive", **data}))
    status = "failed"
    with operation_scope("background", run_id=request.run_id, stage="finalize", scope="background",
                         parent_operation_id=parent_id or None) as operation:
        try:
            await publish(HookEvent.BACKGROUND_BEFORE)
            result = await memory.archive(request)
            status = "degraded" if result.metadata.get("degraded") else "completed"
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:
            logger.warning("[Memory] background archive failed", exc_info=True)
        finally:
            operation.status = status
            await publish(HookEvent.BACKGROUND_AFTER, status=status)
