"""SQLite-backed adapter for the core Agent ``MemoryPort``."""

from __future__ import annotations

import json
import os
import hashlib
from dataclasses import asdict, is_dataclass
from typing import Any

from app.agent_base.host_api.environment import project_storage

from .plugin_api import (MemoryArchiveRequest, MemoryArchiveResult, MemoryRecallRequest, MemoryRecallResult, MemoryEventRequest, MemoryEventResult)

from .manager import MemoryManager


def _memory_db_path(settings, project_file: str = "", workspace_root: str = "") -> str:
    configured = str(getattr(settings, "agent_memory_db_path", "") or "").strip()
    if configured:
        return os.path.normpath(os.path.abspath(configured))
    storage = project_storage(project_file, workspace_root=workspace_root)
    return str((storage.state_dir / "memories.db")) if storage else ""


def _format_tool_steps(tool_steps: tuple[dict[str, Any], ...]) -> str:
    steps: list[dict[str, Any]] = []
    for item in tool_steps or ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "?"))[:80]
        args = item.get("arguments", {})
        observation = str(item.get("observation", ""))
        args_text = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        step = {"tool": name, "arguments": args_text, "result": observation,
                "status": str(item.get("status", "unknown"))[:24]}

        # Bound each serialized step, including JSON escaping and field names.
        serialized = json.dumps(step, ensure_ascii=False)
        while len(serialized) > 500:
            field = max(("arguments", "result", "tool"), key=lambda key: len(step[key]))
            excess = len(serialized) - 500
            step[field] = step[field][:max(0, len(step[field]) - max(1, excess))]
            serialized = json.dumps(step, ensure_ascii=False)

        steps.append(step)
    return json.dumps(steps, ensure_ascii=False)


class SQLiteMemoryProvider:
    """Keep the existing MemoryManager implementation behind the core port."""

    def __init__(self, *, llm, settings, **kwargs):
        self.llm = llm
        self.settings = settings
        self.db_path = _memory_db_path(
            settings, str(kwargs.get("project_file") or ""),
            str(kwargs.get("workspace_root") or ""),
        )
        from app.agent_base.host_api.environment import workspace_paths as WorkspacePathResolver
        self.paths = WorkspacePathResolver(
            str(kwargs.get("workspace_root") or ""), str(kwargs.get("source_dir") or ""),
            str(kwargs.get("test_dir") or ""), str(kwargs.get("design_dir") or ""),
        )
        self.project_file = str(kwargs.get("project_file") or "")
        environment = kwargs.get("environment_context")
        env_data = asdict(environment) if is_dataclass(environment) else dict(environment or {})
        self.environment_version = hashlib.sha256(json.dumps(env_data, sort_keys=True, default=str).encode()).hexdigest()
        self.scope_context = {"environment": self.environment_version}
        if self.paths.workspace:
            self.scope_context["workspace"] = os.path.normcase(str(self.paths.workspace))
        self.recall_top_k = max(1, int(getattr(settings, "agent_memory_recall_top_k", 3)))
        self.recall_max_tokens = max(1, int(getattr(settings, "agent_memory_recall_max_tokens", 500)))

    def _manager(self) -> MemoryManager:
        if not self.db_path:
            raise ValueError("project path is required for local memory")
        return MemoryManager(db_path=self.db_path)

    @staticmethod
    def _trace(event, **fields):
        from app.agent_base.host_api.services import get_host_services
        get_host_services().emit_event(event, fields)

    def _file_resource(self, path):
        try:
            canonical = self.paths.resolve(str(path))
            digest = hashlib.sha256()
            if not canonical.exists():
                version = "missing"
            elif not canonical.is_file():
                return None
            else:
                with canonical.open("rb") as stream:
                    for block in iter(lambda: stream.read(65536), b""):
                        digest.update(block)
                version = digest.hexdigest()
            return {"resource_id": "file:" + os.path.normcase(str(canonical)), "kind": "file",
                    "path": str(canonical), "version": version}
        except (OSError, ValueError):
            return None

    def _environment_resource(self):
        return {"resource_id": "environment:runtime", "kind": "environment", "version": self.environment_version}

    def _current_resources(self, manager, project_id):
        resources = {"environment:runtime": self._environment_resource()}
        if self.project_file:
            resource = self._file_resource(self.project_file)
            if resource:
                resources[resource["resource_id"]] = resource
        for entry in manager.db.list_by_project(project_id):
            for ref in entry.metadata.get("knowledge", {}).get("sources", []):
                if ref.get("kind") != "file":
                    continue
                if ref.get("resource_id") in resources:
                    continue
                resource = self._file_resource(ref.get("path", ""))
                if resource:
                    resources[resource["resource_id"]] = resource
                else:
                    # Loss of permission/readability is not evidence the fact is false.
                    resources[ref["resource_id"]] = {**ref, "version": "unavailable"}
        return tuple(resources.values())

    async def observe(self, request: MemoryEventRequest) -> MemoryEventResult:
        if not self.db_path:
            return MemoryEventResult()
        manager = self._manager()
        try:
            resources = {r["resource_id"]: dict(r) for r in request.resources if r.get("resource_id") and r.get("version")}
            stale_attestation = False
            for key, supplied in list(resources.items()):
                if supplied.get("kind") != "file":
                    continue
                actual = self._file_resource(supplied.get("path", ""))
                if actual is None or actual["resource_id"] != key:
                    resources[key] = {**supplied, "version": "unavailable"}
                    stale_attestation = True
                else:
                    stale_attestation |= actual["version"] != supplied["version"]
                    resources[key] = actual
            changed = request.event_type == "resource_changed"
            for step in request.tool_steps:
                args = step.get("arguments", {})
                if not isinstance(args, dict):
                    continue
                paths = []
                for change in step.get("changes", []) or []:
                    if change.get("path"):
                        paths.append(change["path"])
                        changed = True
                if step.get("name") == "read_file" and step.get("status") in {"success", "completed"}:
                    paths.append(args.get("path", ""))
                for path in paths:
                    resource = self._file_resource(path)
                    if resource:
                        resources[resource["resource_id"]] = resource
            observation = manager.knowledge.observe(
                request.project_id, "resource_changed" if changed else request.event_type,
                tuple(resources.values()), run_id=request.run_id, trace_id=request.trace_id,
                event_id=request.event_id, reason=request.reason,
                evidence=json.loads(_format_tool_steps(request.tool_steps)),
            )
            result = observation
            if not observation.get("duplicate") and request.event_type in {"memory_validated", "user_confirmed"} and stale_attestation:
                result = {**observation, "skipped": [{"reason": "stale_validation_evidence"}]}
            elif not observation.get("duplicate") and request.event_type in {"memory_validated", "user_confirmed"}:
                result = manager.knowledge.validate(
                    request.project_id, request.memory_ids, tuple(resources.values()),
                    event_type=request.event_type, reason=request.reason, run_id=request.run_id, trace_id=request.trace_id,
                )
            self._trace("memory_observed", project_id=request.project_id, **result, resources=list(resources.values()), source_run_id=request.run_id)
            return MemoryEventResult(len(result["affected"]), tuple(resources.values()), result)
        finally:
            manager.close()

    async def recall(self, request: MemoryRecallRequest) -> MemoryRecallResult:
        if not self.db_path:
            return MemoryRecallResult()
        manager = self._manager()
        try:
            legacy = manager.knowledge.migrate_legacy(request.project_id)
            refreshed = manager.knowledge.observe(request.project_id, "source_checked", self._current_resources(manager, request.project_id))
            results = await manager.recall(
                request.project_id,
                request.query,
                top_k=request.top_k or self.recall_top_k,
                max_tokens=request.max_tokens or self.recall_max_tokens,
                scope_context={**self.scope_context, **request.scope_context},
            )
            self._trace("memory_recalled", project_id=request.project_id, query=request.query,
                        legacy_needs_review=legacy, rechecked=refreshed["affected"], **manager.last_recall_report)
            context = manager.inject_memories("", results).strip()
            return MemoryRecallResult(
                context_block=context,
                memory_ids=tuple(result.entry.id for result in results),
                token_count=max(0, len(context) // 2),
                metadata={"provider": "sqlite", "count": len(results), **manager.last_recall_report},
            )
        finally:
            manager.close()

    async def archive(self, request: MemoryArchiveRequest) -> MemoryArchiveResult:
        if not self.db_path:
            return MemoryArchiveResult(metadata={"provider": "sqlite", "skipped": "no_project"})
        if self.llm is None:
            return MemoryArchiveResult(metadata={"provider": "sqlite", "skipped": "no_llm"})
        manager = self._manager()
        try:
            resources = {r["resource_id"]: dict(r) for r in request.resources if r.get("resource_id") and r.get("version")}
            environment = self._environment_resource()
            resources[environment["resource_id"]] = environment
            manager.knowledge.observe(request.project_id, "source_checked", self._current_resources(manager, request.project_id))
            tool_execution_summary = _format_tool_steps(request.tool_steps)
            conversation_history = json.dumps([
                {"role": item["role"], "content": item.get("content", "")}
                for item in request.conversation_history
                if item.get("role") in {"user", "assistant"}
            ], ensure_ascii=False)

            async def extract(prompt: str) -> str:
                # Keep background extraction visible in trace without making
                # it part of the foreground Agent turn.
                from app.agent_base.host_api.services import get_host_services
                with get_host_services().trace_span("MemoryArchive"):
                    return await self.llm.ainvoke(
                        [{"role": "user", "content": prompt}],
                        max_tokens=None,
                    )

            await manager.remember(
                project_id=request.project_id,
                context=f"对话 Agent 任务: {request.user_message[:100]}",
                llm_call_type="agent_task",
                user_input=request.user_message,
                tool_execution_summary=tool_execution_summary,
                final_answer=request.final_answer,
                conversation_history=conversation_history,
                extract_fn=extract,
                source_run_id=request.run_id,
                source_trace_id=request.trace_id,
                resources=tuple(resources.values()),
                scope_context=self.scope_context,
            )
            self._trace("memory_archived", project_id=request.project_id, source_run_id=request.run_id,
                        terminal_status=request.terminal_status, **manager.last_write_report)
            return MemoryArchiveResult(
                stored_count=manager.last_write_report.get("inserted", 0) + manager.last_write_report.get("updated", 0),
                metadata={"provider": "sqlite", **manager.last_write_report,
                          "degraded": manager.last_write_report.get("skipped") == "extraction_failed"},
            )
        finally:
            manager.close()

    async def reinforce(self, memory_ids: tuple[str, ...], project_id: str = "") -> None:
        if not self.db_path or not memory_ids or not project_id:
            return
        manager = self._manager()
        try:
            manager.reinforce(list(memory_ids), project_id=project_id)
        finally:
            manager.close()

    def close(self) -> None:
        # Managers are scoped to individual operations; retained for the port's
        # optional lifecycle hook.
        return None


def create(*, llm, settings, **kwargs):
    return SQLiteMemoryProvider(llm=llm, settings=settings, **kwargs)
