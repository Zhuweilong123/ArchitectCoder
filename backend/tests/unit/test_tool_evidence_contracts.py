"""Tool data and evidence must remain structured and faithful across consumers."""
import pytest
from app.agent_base.evidence import EvidenceLedger
from app.agent_base.execution_summary import build_task_execution_summary
from app.agent_base.tools.result import ToolResult, FileChange, CommandEvidence, VerificationEvidence
from app.agent_base.host_api.payloads import parse_checkpoint


def test_tool_result_rejects_opaque_data_instead_of_stringifying_it():
    with pytest.raises(ValueError, match='JSON'):
        ToolResult.from_value(object())
    with pytest.raises(ValueError, match='JSON'):
        ToolResult(data={'opaque': object()})
    assert ToolResult.from_value({'items': [1, None]}).data == {'items': [1, None]}


def test_result_data_and_effects_are_detached_snapshots():
    data = {'items': [1]}
    result = ToolResult(data=data, execution_evidence={'file_query': {'paths': ['a.py']}})
    data['items'].append(2)
    assert result.data == {'items': [1]}
    effects = result.effects()
    effects['execution_evidence']['file_query']['paths'].append('b.py')
    assert result.execution_evidence['file_query']['paths'] == ['a.py']


def test_ledger_is_detached_from_later_effect_mutations():
    effects = {'changes': [{'path': 'a.py', 'operation': 'create', 'after_hash': 'abcd'}]}
    record = EvidenceLedger().record(call_id='c', tool_name='apply_changes', arguments={}, observation='created', effects=effects)
    effects['changes'][0]['path'] = 'changed.py'
    assert record.to_dict()['effects']['changes'][0]['path'] == 'a.py'
    snapshot = record.to_dict()
    snapshot['effects']['changes'].clear()
    assert len(record.to_dict()['effects']['changes']) == 1


def test_tool_effect_payload_round_trips_through_checkpoint_validation():
    result = ToolResult(data='ok', changes=[FileChange('a.py', 'create')], execution=CommandEvidence('pytest', '.', 0),
                        verification=VerificationEvidence('test', '.', True),
                        execution_evidence={'call_attempt': {'call_id': 'c', 'step': 1, 'status': 'success', 'exact_retry_of': None}})
    record = EvidenceLedger().record(call_id='c', tool_name='run_program', arguments={}, observation='ok', effects=result.effects())
    detail = {'name': 'run_program', 'arguments': {}, 'evidence': record.to_dict(), **result.effects()}
    assert parse_checkpoint({'tool_calls': [detail]})['tool_calls'][0] == detail


def test_invalid_optional_provider_sections_do_not_break_evidence_or_summary():
    record = EvidenceLedger().record(call_id='c', tool_name='get_project_map', arguments={},
                                    observation='{"stats":42,"files":[],"diagrams":[]}')
    assert 'diagrams=0' in record.render()
    record = EvidenceLedger().record(call_id='d', tool_name='compare_design_code', arguments={},
                                    observation='{"summary":null,"items":[{"detail":4}]}')
    assert record.detail.startswith('result_sha=')
    summary = build_task_execution_summary([], {'contract_check': {'violations': 42}}, 'partial')
    assert '0 finding(s)' in summary


@pytest.mark.parametrize('first,second', [({'enabled': True}, {'enabled': 1}), ({'offset': 1}, {'offset': 1.0}), ('invalid-a', 'invalid-b')])
def test_retry_summary_uses_the_same_argument_identity_as_native_execution(first, second):
    calls = [{'name': 'tool', 'arguments': first, 'status': 'error'}, {'name': 'tool', 'arguments': second, 'status': 'success'}]
    assert 'no recorded retry' in build_task_execution_summary(calls, {}, 'completed')


def test_retry_identity_ignores_object_key_order_and_keeps_step_boundaries():
    calls = [
        {'name': 'tool', 'arguments': {'a': 1, 'b': 2}, 'status': 'error', 'execution_evidence': {'call_attempt': {'step': 1}}},
        {'name': 'tool', 'arguments': {'b': 2, 'a': 1}, 'status': 'success', 'execution_evidence': {'call_attempt': {'step': 1}}},
        {'name': 'tool', 'arguments': {'b': 2, 'a': 1}, 'status': 'success', 'execution_evidence': {'call_attempt': {'step': 2}}},
    ]
    summary = build_task_execution_summary(calls, {}, 'completed')
    assert 'exact retries=[3] success' in summary
    assert 'exact retries=[2]' not in summary


@pytest.mark.parametrize('evidence', [{'file_query': {'opaque': object()}}, {'call_attempt': {'call_id': 'c', 'step': '1', 'status': 'success', 'exact_retry_of': None}}])
def test_invalid_tool_evidence_fails_at_the_result_boundary(evidence):
    with pytest.raises(ValueError):
        ToolResult(data='ok', execution_evidence=evidence)
