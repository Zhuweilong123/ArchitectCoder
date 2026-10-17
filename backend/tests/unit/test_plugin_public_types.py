"""Public values retain JSON compatibility and reject malformed business fields."""
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from extensions.design_contract.plugin_api import ArtifactFacts, ContractEntity, ContractMapping
from extensions.evals.plugin_api import EvalBatch, EvalBatchRequest, EvalCase, EvalResult
from extensions.knowledge_graph.plugin_api import GraphExpansion
from extensions.memory.plugin_api import MemoryArchiveRequest


@pytest.mark.parametrize("field", ["required_tools", "forbidden_tools"])
@pytest.mark.parametrize("value", ["read_file", 1, 0, False, {}, [1]])
def test_trace_checker_requires_a_list_of_tool_names(field, value):
    with pytest.raises(ValidationError, match="must be a list of tool names"):
        EvalCase(id="typed-case", prompt="inspect", checkers=[{"type": "trace_policy", field: value}])


def test_eval_json_metadata_and_persisted_batch_round_trip():
    result = EvalResult(run_id="run", case_id="case", status="passed", passed=True,
                        metadata={"extra": {"counts": [1, None], "enabled": True}})
    request = EvalBatchRequest(case_ids=["case"], version="typed")
    batch = EvalBatch(batch_id="batch", version=request.version, case_ids=request.case_ids,
                      results=[result])
    restored = EvalBatch.model_validate_json(batch.model_dump_json())
    assert restored.results[0].metadata == result.metadata
    assert restored.case_ids == ["case"]
    with pytest.raises(ValidationError):
        EvalResult(run_id="run", case_id="case", status="passed", passed=True,
                   metadata={"not_serializable": object()})


def test_memory_archive_contract_preserves_typed_evidence():
    adapter = TypeAdapter(MemoryArchiveRequest)
    payload = {
        "project_id": "project", "user_message": "edit", "final_answer": "done",
        "tool_steps": [{"name": "apply_changes", "arguments": {"path": "a.py"},
                        "status": "success", "total_tokens": 3}],
        "conversation_history": [{"role": "user", "content": "edit"}],
        "resources": [{"resource_id": "file:a.py", "version": "hash"}],
    }
    request = adapter.validate_python(payload)
    assert request.tool_steps[0]["arguments"] == {"path": "a.py"}
    assert request.resources[0]["version"] == "hash"
    payload["tool_steps"][0]["total_tokens"] = "not-a-count"
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)


def test_contract_export_is_detached_json_with_structured_entities():
    facts = ArtifactFacts("project", "source", "collected",
        entities=(ContractEntity("entity", "source_class", "Service", attributes={"methods": ["run"]}),),
        mappings=(ContractMapping("mapping", test_entity_ids=("test",)),),
        metadata={"parser": {"version": 1}})
    payload = facts.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["mappings"][0]["test_entity_ids"] == ["test"]
    payload["entities"][0]["attributes"]["methods"].append("changed")
    assert facts.entities[0].attributes["methods"] == ["run"]


def test_graph_expansion_contract_checks_bounded_result_counts():
    adapter = TypeAdapter(GraphExpansion)
    assert adapter.validate_python({"error": "disabled"}) == {"error": "disabled"}
    payload = {"direction": "outgoing", "results": {
        "items": [{"id": "node"}], "total": 4, "returned": 1, "truncated": True,
    }}
    assert adapter.validate_python(payload) == payload
    payload["results"]["returned"] = "invalid"
    with pytest.raises(ValidationError):
        adapter.validate_python(payload)
