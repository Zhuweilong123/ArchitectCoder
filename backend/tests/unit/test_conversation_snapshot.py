"""Public history snapshots keep rebuilds detached from Agent internals."""
from datetime import datetime
from types import SimpleNamespace
from app.agent_base.agents.react_agent import ReActAgent
from app.agent_base.core.message import Message
from app.services.agent_execution import recent_conversation_history


def agent():
    return ReActAgent('snapshot', SimpleNamespace(), SimpleNamespace())


def test_snapshot_preserves_summary_timestamps_and_metadata_without_shared_state():
    source = agent()
    timestamp = datetime(2026, 1, 1)
    source.add_message(Message('question', 'user', timestamp=timestamp, metadata={'refs': ['a']}))
    source.add_message(Message('answer', 'assistant'))
    source.append_task_summary('## Task execution checkpoint\n- completed')
    source._history_summary = 'older conversation'
    snapshot = source.export_conversation()
    replacement = agent()
    replacement.restore_conversation(snapshot)
    assert replacement.export_conversation() == snapshot
    snapshot.messages[0].metadata['refs'].append('b')
    assert source.export_conversation().messages[0].metadata['refs'] == ['a']
    assert replacement.export_conversation().messages[0].metadata['refs'] == ['a']
    assert replacement.export_conversation().messages[0].timestamp == timestamp


def test_recent_history_excludes_incomplete_turns_and_internal_summaries():
    source = agent()
    source.restore_history([
        {'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'answer'},
        {'role': 'summary', 'content': 'hidden', 'metadata': {'kind': 'task_execution'}},
        {'role': 'user', 'content': 'second'}, {'role': 'assistant', 'content': 'answer2'},
        {'role': 'user', 'content': 'pending'},
    ])
    assert recent_conversation_history(source, turns=1) == (
        {'role': 'user', 'content': 'second'}, {'role': 'assistant', 'content': 'answer2'})
    assert recent_conversation_history(source, turns=1, exclude_latest_turn=True) == (
        {'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'answer'})


def test_history_input_and_output_do_not_expose_mutable_agent_state():
    source = agent()
    message = Message('question', 'user', metadata={'refs': ['a']})
    source.add_message(message)
    message.content = 'changed input'
    message.metadata['refs'].append('input change')
    exported = source.get_history()
    exported[0].content = 'changed output'
    exported[0].metadata['refs'].append('output change')
    assert source.get_history()[0].content == 'question'
    assert source.get_history()[0].metadata == {'refs': ['a']}


def test_clear_history_removes_compressed_summary_from_next_request():
    source = agent()
    source.restore_history([
        {'role': 'summary', 'content': 'old compressed context'},
        {'role': 'user', 'content': 'old question'},
        {'role': 'assistant', 'content': 'old answer'},
    ])
    source.clear_history()
    snapshot = source.export_conversation()
    assert snapshot.messages == ()
    assert snapshot.summary == ''
    built = source.context_budget.build_messages('system', snapshot.messages, 'new question',
                                                history_summary=snapshot.summary)
    assert [message['content'] for message in built.messages] == ['system', 'new question']
