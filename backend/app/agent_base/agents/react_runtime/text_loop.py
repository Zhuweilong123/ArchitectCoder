"""Backward-compatible textual ReAct loop."""

from __future__ import annotations

import logging
import time

from ...convergence import ConvergenceController

logger = logging.getLogger(__name__)


def run_text_loop(agent, input_text: str, **kwargs) -> str:
    """Run the legacy Thought/Action protocol without affecting the FC loop."""
    agent.current_history = []
    current_step = 0
    tool_call_count = 0
    started_at = time.monotonic()
    convergence = ConvergenceController()

    logger.info("\n🤖 %s 开始处理问题: %s", agent.name, input_text)

    while True:
        current_step += 1
        if time.monotonic() - started_at >= agent.max_run_seconds:
            final_answer = "Execution time budget reached before the task converged."
            agent._record_turn(input_text, final_answer)
            return final_answer
        logger.info("\n--- 第 %d 步 ---", current_step)

        tools_desc = agent.tool_registry.get_tools_description()
        history_str = "\n".join(agent.current_history)
        prompt = agent.prompt_template.format(
            tools=tools_desc,
            question=input_text,
            history=history_str,
        )

        messages = [{"role": "user", "content": prompt}]
        response_text = agent.llm.invoke(messages, **kwargs)

        thought, action = agent._parse_output(response_text)
        logger.info("  Thought: %s", thought[:100] if thought else "无")
        if action:
            logger.info("  Action: %s", action)

        if action and action.startswith("Finish"):
            final_answer = agent._parse_action_input(action)
            agent._record_turn(input_text, final_answer)
            logger.info("🏁 %s 完成", agent.name)
            return final_answer

        if action:
            tool_name, tool_input = agent._parse_action(action)
            if tool_name:
                tool_call_count += 1
                if tool_call_count > agent.max_tool_calls:
                    final_answer = "Tool-call budget reached before the task converged."
                    agent._record_turn(input_text, final_answer)
                    return final_answer
                observation = agent.tool_registry.execute_tool(tool_name, tool_input)
                agent.current_history.append(f"Step {current_step}: Action: {action}")
                agent.current_history.append(
                    f"Step {current_step}: Observation: {observation}"
                )
                logger.info("  Observation: %s", observation[:100])
                decision = convergence.observe([{
                    "name": tool_name,
                    "arguments": tool_input,
                    "status": "success",
                    "observation": observation,
                }])
                if decision.action == "finalize":
                    final_answer = (
                        "The legacy text loop stopped after repeated non-progressing actions.\n\n"
                        f"Last observation: {observation}"
                    )
                    agent._record_turn(input_text, final_answer)
                    return final_answer
            else:
                agent.current_history.append(f"Step {current_step}: 无效的Action格式")
        else:
            agent.current_history.append(f"Step {current_step}: 未解析到Action")


__all__ = ["run_text_loop"]
