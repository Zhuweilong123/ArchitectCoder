"""Knowledge lifecycle regressions using real SQLite and workspace files."""

import asyncio
import json
from types import SimpleNamespace

from app.agent_base.ports.memory import (MemoryArchiveRequest, MemoryEventRequest, MemoryRecallRequest)
from extensions.memory.manager import MemoryManager
from extensions.memory.models import MemoryEntry, MemoryType
from extensions.memory.provider import SQLiteMemoryProvider


def provider(tmp_path, llm=None, **kwargs):
    return SQLiteMemoryProvider(llm=llm, settings=SimpleNamespace(agent_memory_db_path=str(tmp_path / 'memory.db')),
                                workspace_root=str(tmp_path), **kwargs)


async def remember(mgr, summary, refs=(), subject='uml:coverage', **kwargs):
    async def extract(_):
        return json.dumps([{'memory_type': 'insight', 'summary': summary, 'subject': subject,
                            'source_refs': [r['resource_id'] for r in refs]}])
    return await mgr.remember('p', 'context', 'agent_task', extract_fn=extract, resources=refs, **kwargs)


def test_trace_scenario_missing_class_then_partial_edit_stops_stale_recall(tmp_path):
    async def run():
        path = tmp_path / 'model.umlproj'
        path.write_text('old classes', encoding='utf-8')
        p = provider(tmp_path, project_file=str(path))
        read = {'name': 'read_file', 'arguments': {'path': str(path)}, 'status': 'success', 'observation': 'old classes'}
        observed = await p.observe(MemoryEventRequest('p', 'tool_observed', tool_steps=(read,), event_id='read-1'))
        mgr = p._manager()
        entries = await remember(mgr, 'UML class diagram missing DPQPPathPlanner', observed.resources)
        identifier = entries[0].id
        before = mgr.db.get('p', identifier).importance_score
        mgr.close()
        first = await p.recall(MemoryRecallRequest('p', 'DPQPPathPlanner class diagram'))
        assert identifier in first.memory_ids
        path.write_text('DPQPPathPlanner class present', encoding='utf-8')
        edit = {'name': 'apply_changes', 'arguments': {}, 'status': 'error',
                'changes': [{'path': str(path), 'operation': 'replace'}]}
        # Even partial/error batches can contain real mutation effects.
        changed = await p.observe(MemoryEventRequest('p', 'tool_observed', tool_steps=(edit,), event_id='edit-1'))
        assert changed.affected_count == 1
        after = await p.recall(MemoryRecallRequest('p', 'DPQPPathPlanner class diagram'))
        assert identifier not in after.memory_ids
        assert after.metadata['skipped'][0]['reason'] == 'needs_review'
        mgr = p._manager()
        old = mgr.db.get('p', identifier)
        assert old.summary == 'UML class diagram missing DPQPPathPlanner'
        assert old.importance_score == before
        assert old.access_count == 1
        assert mgr.reinforce(identifier, project_id='p') == 0
        await remember(mgr, 'UML class diagram contains DPQPPathPlanner', changed.resources)
        current = mgr.db.get('p', identifier)
        assert current.metadata['knowledge']['status'] == 'active'
        assert current.metadata['knowledge']['supersedes']['id'] == identifier
        versions = mgr.knowledge.versions('p', identifier)
        assert any(v['snapshot']['summary'].endswith('missing DPQPPathPlanner') and
                   v['snapshot']['metadata']['knowledge'].get('status') == 'superseded' for v in versions)
        mgr.close()
        final = await p.recall(MemoryRecallRequest('p', 'DPQPPathPlanner class diagram'))
        assert 'contains DPQPPathPlanner' in final.context_block
        assert 'missing DPQPPathPlanner' not in final.context_block
    asyncio.run(run())


def test_external_file_change_is_detected_without_tool_event_and_unrelated_memory_survives(tmp_path):
    async def run():
        a, b = tmp_path / 'a.py', tmp_path / 'b.py'
        a.write_text('a', encoding='utf-8')
        b.write_text('b', encoding='utf-8')
        p = provider(tmp_path)
        observed = await p.observe(MemoryEventRequest('p', 'source_observed', resources=(p._file_resource(a), p._file_resource(b))))
        mgr = p._manager()
        await remember(mgr, 'Alpha component observation', (observed.resources[0],), subject='alpha')
        await remember(mgr, 'Beta component observation', (observed.resources[1],), subject='beta')
        mgr.db.add(MemoryEntry('p', MemoryType.PREFERENCE, 'User prefers composition'))
        mgr.close()
        a.write_text('changed', encoding='utf-8')
        result = await p.recall(MemoryRecallRequest('p', 'Alpha Beta composition', top_k=8))
        assert 'Alpha component' not in result.context_block
        assert 'Beta component' in result.context_block
        assert 'User prefers composition' in result.context_block
    asyncio.run(run())


def test_generic_resource_version_changes_do_not_require_file_adapter(tmp_path):
    async def run():
        mgr = MemoryManager(str(tmp_path / 'memory.db'))
        resource = {'resource_id': 'api:catalog', 'kind': 'api', 'version': 'etag-1'}
        mgr.knowledge.observe('p', 'resource_observed', (resource,))
        await remember(mgr, 'Catalog API supports export', (resource,))
        assert await mgr.recall('p', 'Catalog API')
        mgr.knowledge.observe('p', 'resource_changed', ({**resource, 'version': 'etag-2'},))
        assert not await mgr.recall('p', 'Catalog API')
        assert len(mgr.db.list_by_project('p')) == 1
        mgr.close()
    asyncio.run(run())


def test_late_archive_cannot_replace_fresh_state(tmp_path):
    async def run():
        mgr = MemoryManager(str(tmp_path / 'memory.db'))
        old = {'resource_id': 'document:design', 'kind': 'document', 'version': '1'}
        fresh = {**old, 'version': '2'}
        mgr.knowledge.observe('p', 'resource_observed', (fresh,))
        await remember(mgr, 'New design has class', (fresh,))
        await remember(mgr, 'Old design missing class', (old,))
        assert mgr.db.list_by_project('p')[0].summary == 'New design has class'
        assert mgr.last_write_report['rejected'][0]['reason'] == 'stale_candidate'
        mgr.close()
    asyncio.run(run())


def test_legacy_observations_are_preserved_for_review(tmp_path):
    async def run():
        p = provider(tmp_path)
        mgr = p._manager()
        entry = MemoryEntry('p', MemoryType.INSIGHT, 'Legacy UML missing class')
        mgr.db.add(entry)
        mgr.db.add(MemoryEntry('p', MemoryType.DECISION, 'UML requires review'))
        mgr.close()
        result = await p.recall(MemoryRecallRequest('p', 'UML', top_k=8))
        assert 'Legacy UML' not in result.context_block
        assert 'UML requires review' in result.context_block
        mgr = p._manager()
        assert mgr.db.get('p', entry.id).metadata['knowledge']['status'] == 'needs_review'
        assert mgr.knowledge.versions('p', entry.id)
        mgr.close()
    asyncio.run(run())


def test_environment_lesson_is_scoped_and_not_used_after_environment_change(tmp_path):
    async def run():
        p = provider(tmp_path, environment_context={'filesystem_policy': 'workspace-only'})
        env = p._environment_resource()
        mgr = p._manager()
        mgr.knowledge.observe('p', 'source_observed', (env,))
        async def extract(_):
            return json.dumps([{'memory_type': 'operational_lesson', 'summary': 'Workspace tools cannot access parent',
                                'scope_kind': 'environment'}])
        await mgr.remember('p', 'context', 'agent_task', extract_fn=extract, resources=(env,), scope_context=p.scope_context)
        mgr.close()
        assert (await p.recall(MemoryRecallRequest('p', 'Workspace tools'))).memory_ids
        other = provider(tmp_path, environment_context={'filesystem_policy': 'unrestricted'})
        assert not (await other.recall(MemoryRecallRequest('p', 'Workspace tools'))).memory_ids
    asyncio.run(run())


def test_unknown_source_ref_rejected_and_confirmed_fact_protected(tmp_path):
    async def run():
        mgr = MemoryManager(str(tmp_path / 'memory.db'))
        async def invalid(_):
            return json.dumps([{'memory_type': 'insight', 'summary': 'Invented fact', 'source_refs': ['file:invented']}])
        assert not await mgr.remember('p', 'context', 'task', extract_fn=invalid)
        assert mgr.last_write_report['rejected'][0]['reason'] == 'unknown_source_ref'
        await remember(mgr, 'User confirmed design', user_feedback='accepted')
        await remember(mgr, 'Model speculative replacement')
        assert mgr.db.list_by_project('p')[0].summary == 'User confirmed design'
        assert mgr.last_write_report['rejected'][0]['reason'] == 'confirmed_memory_protected'
        mgr.close()
    asyncio.run(run())


def test_repeated_event_does_not_duplicate_state_transition(tmp_path):
    mgr = MemoryManager(str(tmp_path / 'memory.db'))
    resource = {'resource_id': 'document:design', 'kind': 'document', 'version': '1'}
    first = mgr.knowledge.observe('p', 'resource_observed', (resource,), event_id='event-1')
    second = mgr.knowledge.observe('p', 'resource_observed', (resource,), event_id='event-1')
    assert first['event_id'] == second['event_id']
    assert second['duplicate'] is True
    assert mgr.db.conn.execute('SELECT count(*) FROM memory_events').fetchone()[0] == 1
    mgr.close()


def test_current_fc_request_drops_old_memory_after_mutation(tmp_path):
    from app.agent_base.agents.react_agent import ReActAgent
    from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
    from app.agent_base.tools.registry import ToolRegistry
    from app.core.capabilities import CapabilityPolicy

    async def run():
        target = tmp_path / 'model.py'
        target.write_text('value = 1\n', encoding='utf-8')
        p = provider(tmp_path)
        observed = await p.observe(MemoryEventRequest('p', 'source_observed', resources=(p._file_resource(target),)))
        mgr = p._manager()
        await remember(mgr, 'Model has value one', observed.resources)
        mgr.close()
        context = (await p.recall(MemoryRecallRequest('p', 'Model'))).context_block
        registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=[str(tmp_path)]))
        for tool in create_foundation_tools(workspace_root=str(tmp_path)):
            registry.register_tool(tool)
        class LLM:
            def __init__(self):
                self.requests = []
            async def ainvoke_with_tools(self, messages, tools, **kwargs):
                self.requests.append([dict(m) for m in messages])
                if len(self.requests) > 1:
                    return {'content': 'done', 'tool_calls': None}
                return {'content': '', 'tool_calls': [{'id': 'patch-1', 'type': 'function', 'function': {
                    'name': 'apply_changes', 'arguments': json.dumps({'changes': [{
                        'op': 'patch', 'path': str(target), 'old_text': 'value = 1', 'new_text': 'value = 2',
                    }]}),
                }}]}
        llm = LLM()
        agent = ReActAgent('Test', llm, registry)
        from app.agent_base.core.extension_context import ExtensionContext
        agent.extension_context = ExtensionContext(metadata={'project_id': 'p'})
        agent.extension_context.bind('memory', p)
        events = [e async for e in agent.arun_stream('Update the Model', context=context)]
        assert events[-1].final_answer == 'done'
        assert any('Model has value one' in str(m.get('content', '')) for m in llm.requests[0])
        assert not any('Model has value one' in str(m.get('content', '')) for m in llm.requests[1])
        assert target.read_text(encoding='utf-8') == 'value = 2\n'
        assert 'memory_refreshed_after_change' not in agent.last_context_report
    asyncio.run(run())


def test_archive_captures_resources_scope_and_counts_updates(tmp_path):
    async def run():
        target = tmp_path / 'model.py'
        target.write_text('one', encoding='utf-8')
        class LLM:
            async def ainvoke(self, messages, **kwargs):
                text = messages[0]['content']
                catalog = json.loads(text.split('### 可用来源资源（由系统生成，resource_id 与 version 不得编造）\n')[1].split('\n\n### 最终模型回复')[0])
                file = next(r for r in catalog if r['kind'] == 'file')
                assert '"status": "success"' in text
                return json.dumps([{'memory_type': 'insight', 'subject': 'model:value', 'summary': 'Model value fact',
                                    'source_refs': [file['resource_id']], 'scope_kind': 'workspace'}])
        p = provider(tmp_path, llm=LLM())
        read = {'name': 'read_file', 'arguments': {'path': str(target)}, 'status': 'success', 'observation': 'one'}
        observation = await p.observe(MemoryEventRequest('p', 'tool_observed', tool_steps=(read,)))
        step = read
        request = MemoryArchiveRequest('p', 'Discuss model', 'Model result', tool_steps=(step,), run_id='r1', resources=observation.resources)
        first = await p.archive(request)
        second = await p.archive(request)
        assert first.stored_count == second.stored_count == 1
        assert first.metadata['inserted'] == 1
        assert second.metadata['updated'] == 1
        result = await p.recall(MemoryRecallRequest('p', 'Model'))
        assert result.memory_ids
        mgr = p._manager()
        entry = mgr.db.get('p', result.memory_ids[0])
        assert entry.metadata['knowledge']['scope']['kind'] == 'workspace'
        assert entry.metadata['provenance']['run_id'] == 'r1'
        assert entry.importance_score == 0.5
        mgr.close()
    asyncio.run(run())


def test_expired_memory_is_not_injected(tmp_path):
    async def run():
        mgr = MemoryManager(str(tmp_path / 'memory.db'))
        async def extract(_):
            return json.dumps([{'memory_type': 'decision', 'summary': 'Temporary API policy',
                                'valid_until': '2000-01-01T00:00:00Z'}])
        await mgr.remember('p', 'context', 'task', extract_fn=extract)
        assert not await mgr.recall('p', 'API policy')
        assert mgr.last_recall_report['skipped'][0]['reason'] == 'expired'
        assert len(mgr.db.list_by_project('p')) == 1
        mgr.close()
    asyncio.run(run())


def test_explicit_validation_can_restore_unchanged_fact_after_source_edit(tmp_path):
    async def run():
        p = provider(tmp_path)
        target = tmp_path / 'model.py'
        target.write_text('class A: pass\n', encoding='utf-8')
        old = p._file_resource(target)
        await p.observe(MemoryEventRequest('p', 'source_observed', resources=(old,)))
        mgr = p._manager()
        entry = (await remember(mgr, 'Model contains class A', (old,)))[0]
        mgr.close()
        target.write_text('class A:\n    pass\n', encoding='utf-8')
        current = p._file_resource(target)
        await p.observe(MemoryEventRequest('p', 'resource_changed', resources=(current,)))
        assert not (await p.recall(MemoryRecallRequest('p', 'Model class A'))).memory_ids
        incomplete = await p.observe(MemoryEventRequest('p', 'memory_validated', memory_ids=(entry.id,), reason='Read AST confirms class A'))
        assert incomplete.affected_count == 0
        restored = await p.observe(MemoryEventRequest('p', 'memory_validated', resources=(current,),
                                                    memory_ids=(entry.id,), reason='Read AST confirms class A', event_id='validation-1'))
        assert restored.affected_count == 1
        again = await p.observe(MemoryEventRequest('p', 'memory_validated', resources=(current,),
                                                 memory_ids=(entry.id,), reason='Read AST confirms class A', event_id='validation-1'))
        assert again.metadata['duplicate'] is True
        result = await p.recall(MemoryRecallRequest('p', 'Model class A'))
        assert entry.id in result.memory_ids
        assert '已确认' in result.context_block
    asyncio.run(run())


def test_same_subject_in_different_workspaces_is_not_overwritten(tmp_path):
    async def run():
        mgr = MemoryManager(str(tmp_path / 'memory.db'))
        async def extract(_):
            return json.dumps([{'memory_type': 'insight', 'subject': 'workspace:path', 'scope_kind': 'workspace',
                                'summary': 'Workspace design file location'}])
        for workspace in ('a', 'b'):
            await mgr.remember('p', 'context', 'task', extract_fn=extract, scope_context={'workspace': workspace})
        assert len(mgr.db.list_by_project('p')) == 2
        result = await mgr.recall('p', 'Workspace design', scope_context={'workspace': 'a'})
        assert len(result) == 1
        assert result[0].entry.metadata['knowledge']['scope']['id'] == 'a'
        mgr.close()
    asyncio.run(run())
