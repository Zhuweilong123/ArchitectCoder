"""Persisted checkpoints are untrusted input, including nested tool effects."""
import math
import pytest
from app.agent_base.host_api.payloads import parse_checkpoint, json_object

@pytest.mark.parametrize('checkpoint', [
    {'last_step': '3'}, {'resume_available': 1}, {'changed_files': 'src.py'},
    {'completed_items': [42]}, {'verification_results': [{'kind': 'test', 'scope': '.', 'passed': 'yes'}]},
    {'mutation_evidence': {'x': {'path': 'x', 'operation': 3}}},
    {'outcome': {'status': 'completed', 'stop_reason': 'model_answer', 'final_answer': 'ok'}},
    {'todos': [{'content': 'test', 'status': 'unknown'}]},
])
def test_checkpoint_rejects_wrong_declared_fields(checkpoint):
    with pytest.raises(ValueError, match='Invalid checkpoint field'):
        parse_checkpoint(checkpoint)

def test_checkpoint_preserves_json_extensions_without_mutating_input():
    saved = {'run_id': 'r1', 'status': 'paused', 'plugin_state': {'values': [1, None]},
             'verification_results': [{'kind': 'test', 'scope': '.', 'passed': True, 'provider_detail': 'retained'}]}
    restored = parse_checkpoint(saved)
    assert restored == saved
    restored['plugin_state']['values'].append(2)
    assert saved['plugin_state']['values'] == [1, None]

@pytest.mark.parametrize('value', [{'nested': object()}, {1: 'non-string key'}, {'number': math.nan}, {'number': math.inf}])
def test_serialization_rejects_non_json_values(value):
    with pytest.raises(ValueError, match='JSON'):
        json_object(value)

def test_sparse_legacy_checkpoint_can_be_restored():
    assert parse_checkpoint({'run_id': 'old'}) == {'run_id': 'old'}


def test_serialization_turns_dataclass_sequences_into_json_arrays():
    assert json_object({'violations': ({'code': 'example'},)}) == {'violations': [{'code': 'example'}]}
