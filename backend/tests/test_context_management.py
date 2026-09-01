from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.message import Message
from app.services.context_manager import (
    ContextBudget,
    ContextBudgetManager,
    HistoryCompactor,
    estimate_tokens,
)


def test_context_budget_preserves_current_input_and_caps_history():
    manager = ContextBudgetManager(
        ContextBudget(
            max_context_tokens=220,
            output_reserve_tokens=20,
            max_system_tokens=20,
            max_history_tokens=80,
            max_current_task_tokens=45,
            max_tool_tokens=30,
            max_history_turns=2,
        )
    )
    history = [
        {"role": "user", "content": f"old user message {index} " + "x" * 80}
        for index in range(6)
    ]
    result = manager.build_messages(
        "system " + "s" * 80,
        history,
        "CURRENT TASK: keep this phrase",
        context="workspace " + "w" * 160,
    )

    assert result.messages[0]["role"] == "system"
    assert result.messages[-1]["role"] == "user"
    assert "CURRENT TASK" in result.messages[-1]["content"]
    assert result.estimated_tokens <= 220 - 20
    assert result.truncated_current_task


def test_history_compactor_keeps_recent_messages_and_checkpoint():
    messages = [
        Message("first decision: use sqlite", "user"),
        Message("acknowledged", "assistant"),
        Message("second decision: run focused tests", "user"),
        Message("done", "assistant"),
        Message("latest question", "user"),
        Message("latest answer", "assistant"),
    ]
    result = HistoryCompactor().compact(
        messages,
        "existing checkpoint",
        max_turns=1,
        max_tokens=1000,
        summary_tokens=100,
    )

    assert [item["content"] for item in result.messages] == [
        "latest question", "latest answer"
    ]
    assert result.dropped_messages == 4
    assert "existing checkpoint" in result.summary
    assert "first decision" in result.summary


def test_react_agent_restores_summary_without_sending_it_as_chat_history():
    agent = ReActAgent.__new__(ReActAgent)
    agent._history = []
    agent.restore_history([
        {"role": "summary", "content": "checkpoint"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ])

    assert agent._history_summary == "checkpoint"
    assert [message.role for message in agent._history] == ["user", "assistant"]
    assert estimate_tokens("checkpoint") > 0


def test_fit_messages_drops_function_call_and_results_as_one_group():
    manager = ContextBudgetManager(
        ContextBudget(max_context_tokens=40, output_reserve_tokens=5)
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
        {
            "role": "assistant", "content": "inspect",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "large observation"},
    ]
    fitted, dropped = manager.fit_messages(messages, current_user_index=1)

    assert dropped == 2
    assert not any(message.get("role") in {"assistant", "tool"} for message in fitted)


def test_react_step_compaction_keeps_recent_pairs_and_checkpoint():
    manager = ContextBudgetManager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
    ]
    for index in range(5):
        messages.extend([
            {
                "role": "assistant", "content": f"step {index}",
                "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": f"call-{index}", "content": f"observation {index}"},
        ])

    compacted, current_index, dropped, _tokens = manager.compact_react_steps(
        messages, current_user_index=1, max_steps=2,
    )

    assert current_index == 1
    assert dropped == 6
    assert compacted[2]["role"] == "system"
    assert sum(1 for message in compacted if message.get("role") == "assistant") == 2
    assert all(
        message.get("role") != "tool" or any(
            call.get("id") == message.get("tool_call_id")
            for assistant in compacted
            if assistant.get("role") == "assistant"
            for call in assistant.get("tool_calls", [])
        )
        for message in compacted
    )
