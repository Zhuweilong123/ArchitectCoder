"""Main-Agent tool adapter for demand-driven architecture exploration."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from app.agent_base.core.hooks import get_runtime
from app.agent_base.ports.orchestration import (ExplorationDemand, ExplorationPort)
from app.agent_base.tools.async_tool import AsyncTool
from app.agent_base.tools.base import Tool, ToolParameter


class ExploreArchitectureTool(AsyncTool):
    """Expose the orchestration port without giving the Agent scheduling control."""

    def __init__(self, explorer: ExplorationPort):
        super().__init__(
            name="explore_architecture",
            description=(
                "Request bounded, read-only architecture and source exploration "
                "early in a multi-component change or when a dependency path is "
                "unclear. Names in the request or project map are sufficient: "
                "call before broad source reading. Provide the question and 1-4 "
                "graph search terms; the scheduler "
                "decides whether to delegate, partitions the graph, and returns "
                "evidence with unresolved areas. Avoid for greetings, simple local "
                "edits, direct test runs, and final verification."
            ),
        )
        self.explorer = explorer

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="goal", type="string",
                description="Specific question the exploration must answer",
            ),
            ToolParameter(
                name="search_queries", type="array",
                description="One to four observed class, component, method, or file names",
            ),
        ]

    def to_openai_schema(self) -> dict:
        schema = Tool.to_openai_schema(self)
        queries = schema["function"]["parameters"]["properties"]["search_queries"]
        queries.update(minItems=1, maxItems=4)
        return schema

    async def _execute(self, parameters: dict) -> str:
        goal = str(parameters.get("goal") or "").strip()[:1200]
        raw_queries = parameters.get("search_queries")
        if not goal or not isinstance(raw_queries, list):
            return "Error: goal and search_queries are required"
        queries = tuple(dict.fromkeys(
            query.strip()[:80]
            for item in raw_queries[:4]
            if isinstance(item, str) and (query := item.strip())
        ))
        if not queries:
            return "Error: provide at least one observed graph search term"

        runtime = get_runtime()
        if runtime.policy_metadata.get("architecture_scheduling_enabled") is False:
            return json.dumps({
                "status": "unavailable",
                "reason": "architecture scheduling disabled for this run",
            })
        run_id = runtime.run_id or f"exploration_{uuid.uuid4().hex}"
        report = await self.explorer.explore(ExplorationDemand(
            goal=goal,
            search_queries=queries,
            run_id=run_id,
            schedule_root_run_id=str(
                runtime.policy_metadata.get("architecture_schedule_root") or run_id
            ),
        ))
        return json.dumps(asdict(report), ensure_ascii=False)


class ArchitectureRouteTool(AsyncTool):
    """Record a scoped main-Agent decision and optionally run exploration."""

    def __init__(self, explorer: ExplorationPort):
        super().__init__(
            name="route_architecture",
            description=(
                "Use for a cross-component change or unclear dependency path before "
                "broad source reading. Choose direct when a narrow file/symbol lookup "
                "is sufficient, or explore to request bounded graph-guided read-only "
                "subagents. Record a concrete reason either way. Skip this checkpoint "
                "for clearly local edits. Graph names from the project map or a narrow "
                "lookup are sufficient; no bulk file reading is needed first."
            ),
        )
        self._explore_tool = ExploreArchitectureTool(explorer)

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="decision", type="string",
                          description="direct or explore"),
            ToolParameter(name="reason", type="string",
                          description="Brief task-specific reason for the routing decision"),
            ToolParameter(name="goal", type="string", required=False,
                          description="Specific question to answer when decision is explore"),
            ToolParameter(name="search_queries", type="array", required=False,
                          description="One to four observed graph names when decision is explore"),
        ]

    def to_openai_schema(self) -> dict:
        schema = Tool.to_openai_schema(self)
        properties = schema["function"]["parameters"]["properties"]
        properties["decision"]["enum"] = ["direct", "explore"]
        properties["search_queries"].update(minItems=1, maxItems=4)
        return schema

    async def _execute(self, parameters: dict) -> str:
        decision = str(parameters.get("decision") or "").strip().lower()
        reason = str(parameters.get("reason") or "").strip()[:300]
        if decision not in {"direct", "explore"} or not reason:
            return json.dumps({"status": "invalid", "reason":
                               "decision must be direct or explore and reason is required"})
        if get_runtime().policy_metadata.get("architecture_scheduling_enabled") is False:
            return json.dumps({"status": "unavailable", "reason":
                               "architecture scheduling disabled for this run"})
        if decision == "direct":
            return json.dumps({
                "status": "not_delegated", "decision": "direct",
                "decision_reason": reason,
                "next_step": "Use a narrow source or symbol lookup; reassess only if the scope expands.",
            }, ensure_ascii=False)
        result = await self._explore_tool._execute(parameters)
        try:
            report = json.loads(result)
        except (TypeError, ValueError):
            return result
        if not isinstance(report, dict):
            return result
        report.update(decision="explore", decision_reason=reason)
        return json.dumps(report, ensure_ascii=False)
