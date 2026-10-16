from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.message import Message
from app.services.context_manager import (
    ContextBudget,
    ContextBudgetManager,
    HistoryCompactor,
    estimate_tokens,
    truncate_text,
)


def test_duplicate_user_text_keeps_the_new_task_anchor():
    manager = ContextBudgetManager()
    built = manager.build_messages("system", [
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "previous answer"},
    ], "continue")

    assert built.current_user_index == len(built.messages) - 1
    assert built.messages[built.current_user_index]["content"] == "continue"


def test_repeated_fitting_preserves_shifted_task_anchor():
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "current"}]
    counter = ContextBudgetManager(token_counter=len)
    manager = ContextBudgetManager(ContextBudget(
        max_context_tokens=counter.estimate_request_tokens(base),
    ), token_counter=len)
    first = manager.fit_messages([
        base[0], {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"}, base[1],
    ], current_user_index=3)
    assert first.current_user_index == 1
    assert first.dropped_messages == 2
    second = manager.fit_messages(first.messages + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "call", "content": "result"},
    ], current_user_index=first.current_user_index)
    assert second.messages == base
    assert second.current_user_index == 1
    assert second.dropped_messages == 2


def test_hard_limit_truncation_counts_serialization_and_reports_it():
    manager = ContextBudgetManager(ContextBudget(
        max_context_tokens=130, max_current_task_tokens=5000,
    ), token_counter=len)
    built = manager.build_messages("s", [], '\\"\n中文' * 100)

    assert built.truncated_current_task
    assert built.estimated_tokens <= 130
    assert built.messages[built.current_user_index]["content"]


def test_truncation_honors_tiny_budgets_with_non_character_counter():
    text = "abcdefghij" * 100
    for budget in range(1, 80):
        result = truncate_text(text, budget, lambda value: len(value) * 2)
        assert len(result) * 2 <= budget


def test_history_compaction_retains_complete_multi_tool_group():
    group = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": name, "type": "function", "function": {"name": "echo", "arguments": "{}"}}
            for name in ("a", "b")
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "one"},
        {"role": "tool", "tool_call_id": "b", "content": "two"},
    ]
    compacted = HistoryCompactor().compact(
        [{"role": "user", "content": "old"}] + group, max_turns=1,
    )
    assert compacted.messages == group
    assert compacted.dropped_messages == 1

    manager = ContextBudgetManager(ContextBudget(max_history_tokens=100), token_counter=len)
    built = manager.build_messages("s", group + [
        {"role": "assistant", "content": "done"},
    ], "next")
    assert [message["role"] for message in built.messages] == ["system", "assistant", "user"]
    assert built.dropped_messages == 3


def test_context_results_do_not_share_nested_tool_calls_with_input():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
    ]
    manager = ContextBudgetManager()
    results = [
        manager.fit_messages(messages).messages,
        manager.compact_tool_history(messages).messages,
        manager.prepare_history(messages).messages,
        manager.build_messages("s", messages, "next").messages[1:-1],
    ]
    for result in results:
        result[0]["tool_calls"][0]["function"]["arguments"] = '{"changed": true}'
        assert messages[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_default_context_uses_one_hard_limit_and_ratio_thresholds():
    budget = ContextBudget()

    assert budget.max_context_tokens == 256000
    assert budget.max_history_tokens == 200000
    assert budget.max_history_turns == 48
    assert budget.soft_threshold_ratio == 0.78125
    assert budget.compaction_trigger_ratio == 0.9


def test_context_compaction_trigger_includes_tool_schema_tokens():
    manager = ContextBudgetManager(ContextBudget(
        max_context_tokens=120,
        soft_threshold_ratio=0.5,
        compaction_trigger_ratio=0.75,
    ))
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
    ]
    tools = [{
        "type": "function",
        "function": {"name": "read_file", "description": "x" * 400},
    }]

    assert not manager.should_compact(messages)
    assert manager.should_compact(messages, tools=tools)


def test_context_budget_preserves_current_input_and_caps_history():
    manager = ContextBudgetManager(
        ContextBudget(
            max_context_tokens=220,
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
    assert result.estimated_tokens <= 220
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


def test_react_agent_appends_bounded_task_summary_to_internal_context():
    agent = ReActAgent.__new__(ReActAgent)
    agent._history = []
    agent._history_summary = "old checkpoint"
    agent.context_budget = ContextBudgetManager(
        ContextBudget(max_summary_tokens=40)
    )

    agent.append_task_summary("## Task execution checkpoint\n- Status: partial\n- Pending: run pytest")

    assert agent._history_summary == "old checkpoint"
    assert agent._history[-1].role == "summary"
    assert "Task execution checkpoint" in agent._history[-1].content
    assert "run pytest" in agent._history[-1].content
    assert estimate_tokens(agent._history[-1].content) > 0


def test_context_budget_keeps_task_summary_adjacent_and_model_compatible():
    manager = ContextBudgetManager(ContextBudget(max_history_tokens=500))
    result = manager.build_messages(
        "system",
        [
            Message("first task", "user"),
            Message("first answer", "assistant"),
            Message("## Task execution checkpoint\n- first task completed", "summary"),
        ],
        "second task",
    )

    assert [message["role"] for message in result.messages] == [
        "system", "user", "assistant", "system", "user",
    ]
    assert "first task completed" in result.messages[3]["content"]
    assert result.messages[3]["content"].startswith("## Task execution checkpoint")


def test_fit_messages_drops_function_call_and_results_as_one_group():
    manager = ContextBudgetManager(
    ContextBudget(max_context_tokens=40)
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
    result = manager.fit_messages(messages, current_user_index=1)
    fitted, dropped = result.messages, result.dropped_messages

    assert dropped == 2
    assert not any(message.get("role") in {"assistant", "tool"} for message in fitted)


def test_react_step_compaction_checkpoints_pairs_when_target_cannot_fit_one():
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

    compacted, current_index, dropped, _tokens = manager.compact_tool_history(
        messages, current_user_index=1, target_tokens=1,
    )

    assert current_index == 1
    assert dropped == 10
    assert compacted[2]["role"] == "system"
    assert "read_file" in compacted[2]["content"]
    assert sum(1 for message in compacted if message.get("role") == "assistant") == 0
    assert all(
        message.get("role") != "tool" or any(
            call.get("id") == message.get("tool_call_id")
            for assistant in compacted
            if assistant.get("role") == "assistant"
            for call in assistant.get("tool_calls", [])
        )
        for message in compacted
    )


def test_react_step_compaction_prefers_structured_evidence_over_raw_prefix():
    manager = ContextBudgetManager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
    ]
    for index in range(3):
        messages.extend([
            {
                "role": "assistant", "content": f"step {index}",
                "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": f"call-{index}", "content": "RAW PREFIX MUST NOT BE USED"},
        ])

    compacted, _, _, _ = manager.compact_tool_history(
        messages,
        current_user_index=1,
        target_tokens=1,
        evidence_by_call={"call-0": "[E1] read_file (ok); file=src/a.py; lines 21-40; observation_sha=abc"},
    )

    checkpoint = next(message["content"] for message in compacted if message.get("role") == "system" and "Tool execution checkpoint" in message.get("content", ""))
    assert "file=src/a.py" in checkpoint
    assert "RAW PREFIX MUST NOT BE USED" not in checkpoint


def test_react_step_compaction_never_falls_back_to_raw_when_evidence_is_missing():
    manager = ContextBudgetManager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
    ]
    for index in range(3):
        messages.extend([
            {"role": "assistant", "content": f"step {index}", "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": "RAW SECRETLY LARGE OBSERVATION"},
        ])

    compacted, _, _, _ = manager.compact_tool_history(
        messages, current_user_index=1, target_tokens=1, evidence_by_call={},
    )

    checkpoint = next(message["content"] for message in compacted if message.get("role") == "system" and "Tool execution checkpoint" in message.get("content", ""))
    assert "structured evidence unavailable" in checkpoint
    assert "RAW SECRETLY LARGE OBSERVATION" not in checkpoint


def test_react_step_compaction_replaces_prior_checkpoint_instead_of_accumulating():
    manager = ContextBudgetManager()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "current task"},
        {"role": "system", "content": "## Tool execution checkpoint\nprior evidence"},
    ]
    for index in range(3):
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": f"result {index}"},
        ])

    compacted, _, _, _ = manager.compact_tool_history(messages, current_user_index=1, target_tokens=1)

    assert sum(
        message.get("role") == "system" and message.get("content", "").startswith("## Tool execution checkpoint")
        for message in compacted
    ) == 1
    checkpoint = next(message["content"] for message in compacted if message.get("content", "").startswith("## Tool execution checkpoint"))
    assert checkpoint.count("## Tool execution checkpoint") == 1
