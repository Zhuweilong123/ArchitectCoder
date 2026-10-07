"""Host model/history adaptation for a generic read-only analysis request."""
import asyncio
import logging

from app.agent_base.core.message import Message
from app.agent_base.ports.analysis import AnalysisRequest

logger = logging.getLogger(__name__)


class ReadOnlyAnalysisAdapter:
    def __init__(self, agent):
        self.agent = agent

    async def invoke(self, request: AnalysisRequest) -> str:
        agent = self.agent
        if request.evidence:
            if hasattr(agent, "add_message"):
                agent.add_message(Message(request.evidence, "summary", metadata={**request.metadata, "run_id": request.run_id}))
            elif hasattr(agent, "append_task_summary"):
                agent.append_task_summary(request.evidence)
        if not all(hasattr(agent, name) for name in ("llm", "tool_registry", "context_budget", "_build_fc_system_prompt")):
            return await agent.arun(request.prompt, allowed_tools=[])
        tools = (agent.tool_registry.get_openai_specs_for(request.allowed_tools)
                 if request.allowed_tools is not None else agent.tool_registry.get_openai_specs())
        built = agent.context_budget.build_messages(agent._build_fc_system_prompt(), list(getattr(agent, "_history", ())),
            request.prompt, history_summary=getattr(agent, "_history_summary", ""), tools=tools)
        response = await asyncio.wait_for(agent.llm.ainvoke_with_tools(
            messages=built.messages, tools=tools, tool_choice="auto",
            trace_context={"kind": request.metadata.get("kind", "analysis"), "run_id": request.run_id},
        ), timeout=float(getattr(agent, "llm_timeout_seconds", 120.0) or 120.0))
        if isinstance(response, dict):
            if response.get("tool_calls"):
                logger.warning("[Analysis] ignored model tool calls in a read-only request")
            analysis = str(response.get("content") or "")
        else:
            analysis = str(getattr(response, "content", "") or "")
        if analysis and hasattr(agent, "_record_turn"):
            agent._record_turn(request.prompt, analysis)
        return analysis
