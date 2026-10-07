"""Exercise plugin dispatch through host boundaries, including failure isolation."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_base.core import hooks
from app.agent_base.core.extension_context import ExtensionContext, extension_request, extension_scope, publish_task_result
from app.agent_base.core.hooks import AgentRuntime, HookContext, HookEvent, HookRegistry, reset_runtime, set_runtime
from app.agent_base.core.lifecycle import discover_plan, install_plan
from extensions.memory.plugin_api import (MemoryArchiveResult, MemoryEventResult, MemoryRecallResult)
from app.agent_base.core.plugins import PluginManager
from extensions.memory.contributions import _background_tasks
from extensions.memory.provider import SQLiteMemoryProvider


@pytest.fixture
def isolated_hooks(monkeypatch):
    registry = HookRegistry()
    monkeypatch.setattr(hooks, '_registry', registry)
    token = set_runtime(AgentRuntime(run_id='run'))
    try:
        yield registry
    finally:
        reset_runtime(token)


def session(provider):
    result = ExtensionContext(metadata={'project_id': 'project'})
    result.bind('memory', provider)
    return result


def test_host_execution_has_no_memory_policy_dependencies():
    root = Path(__file__).resolve().parents[2]
    for file in ['app/agent_base/agents/react_runtime/fc_loop.py',
                 'app/agent_base/agents/react_runtime/tool_round_executor.py',
                 'app/services/agent_execution.py', 'app/services/chat_session.py']:
        code = (root / file).read_text(encoding='utf-8')
        for marker in ['core.memory', 'extensions.memory', 'memory_provider', 'memory_event',
                       'memory_resources', '<project_memory>', 'should_archive_task_memory']:
            assert marker not in code, (file, marker)


def test_prompt_builder_accepts_an_unrelated_plugin_without_host_changes(isolated_hooks, monkeypatch):
    import sys
    from types import ModuleType
    from app.agent_base.assembly import DevPromptBuilder
    from app.agent_base.core.extension_context import current_extension_context
    from app.agent_base.core.plugins import PluginSpec
    module = ModuleType('boundary_notes')
    def prepare(context):
        context.payload['sections']['notes'] = current_extension_context().providers['notes'].text
    module.prepare = prepare
    monkeypatch.setitem(sys.modules, 'boundary_notes', module)
    spec = PluginSpec('notes', 'notes_enabled', 'notes_provider', 'boundary_notes:create', (), contributions=(
        {'id': 'notes.prepare', 'stage': 'prepare', 'handler': 'boundary_notes:prepare', 'mode': 'transform'},
    ))
    manager = PluginManager((spec,))
    monkeypatch.setattr('app.agent_base.core.plugins.get_plugin_manager', lambda: manager)
    context = ExtensionContext()
    context.bind('notes', SimpleNamespace(text='notes supplied by another extension'))
    builder = DevPromptBuilder(extension_context=context)
    content = asyncio.run(builder.build_context('', '', '', 'hello'))
    assert 'notes supplied by another extension' in content
    assert 'notes' in builder.last_context_report['sections']
    assert 'memory' not in builder.last_context_report['sections']


@pytest.mark.parametrize('compiled', [False, True])
def test_prepare_is_dispatched_by_manifest_and_respects_options(isolated_hooks, compiled):
    requests = []
    async def recall(request):
        requests.append(request)
        return MemoryRecallResult(context_block='<project_memory>fact</project_memory>')
    context = session(SimpleNamespace(recall=recall))
    context.metadata['plugin_options']['memory'] = {'recall_top_k': 2, 'recall_max_tokens': 120}
    if compiled:
        from backend.config import get_settings
        manager = PluginManager()
        spec = manager.get_spec('memory')
        install_plan(discover_plan(PluginManager((spec,)), get_settings()), isolated_hooks)
    async def run():
        with extension_scope(context):
            event = HookContext(HookEvent.PREPARE, 'Test', payload={
                'project_id': 'project', 'user_message': 'query', 'sections': {},
            })
            await hooks.get_hooks().aemit(HookEvent.PREPARE, event)
            assert event.payload['sections']['memory'] == '<project_memory>fact</project_memory>'
    asyncio.run(run())
    assert len(requests) == 1
    assert (requests[0].top_k, requests[0].max_tokens) == (2, 120)


def test_per_tool_evidence_is_captured_before_next_mutation(tmp_path, isolated_hooks):
    from app.agent_base.agents.react_runtime.tool_round_executor import ToolRoundExecutor
    from app.agent_base.tools.my_tools.foundation_tools import create_foundation_tools
    from app.agent_base.tools.registry import ToolRegistry
    from app.core.capabilities import CapabilityPolicy
    provider = SQLiteMemoryProvider(llm=None,
        settings=SimpleNamespace(agent_memory_db_path=str(tmp_path / 'memory.db')), workspace_root=str(tmp_path))
    context = session(provider)
    registry = ToolRegistry(policy=CapabilityPolicy(workspace_roots=[str(tmp_path)]))
    for tool in create_foundation_tools(workspace_root=str(tmp_path)):
        registry.register_tool(tool)
    target = tmp_path / 'value.py'
    target.write_text('value = 1\n', encoding='utf-8')
    calls = [{'id': f'patch-{value}', 'function': {'name': 'apply_changes', 'arguments': json.dumps({
        'changes': [{'op': 'replace', 'path': str(target), 'content': f'value = {value}\n'}],
    })}} for value in (2, 3)]
    async def run():
        with extension_scope(context):
            await hooks.get_hooks().aemit(HookEvent.RUN_START, HookContext(
                HookEvent.RUN_START, 'Test', run_id='run', payload={'user_message': 'edit'}))
            result = await ToolRoundExecutor(registry, agent_name='Test').execute(calls, step=1)
            assert all(detail['status'] == 'success' for detail in result.details), result.details
            assert all('memory_resources' not in detail and 'memory_event' not in detail for detail in result.details)
    asyncio.run(run())
    manager = provider._manager()
    events = manager.db.conn.execute("SELECT payload FROM memory_events WHERE event_type='resource_changed' ORDER BY created_at").fetchall()
    versions = [json.loads(row[0])['resources'][0]['version'] for row in events]
    assert versions == [hashlib.sha256(f'value = {value}\n'.encode()).hexdigest() for value in (2, 3)]
    manager.close()


def test_authoritative_task_event_controls_archive_and_preserves_review_evidence(isolated_hooks):
    archived = []
    async def archive(request):
        archived.append(request)
        return MemoryArchiveResult(stored_count=1)
    agent = SimpleNamespace(name='Test', extension_context=session(SimpleNamespace(archive=archive)))
    @extension_request
    async def execute(agent, run_id):
        from app.agent_base.core.extension_context import current_extension_context
        current_extension_context().state('memory')['resources'] = {
            'api:item': {'resource_id': 'api:item', 'version': 'old-etag'},
        }
        await hooks.get_hooks().aemit(HookEvent.FINALIZE, HookContext(HookEvent.FINALIZE, 'Test', run_id=run_id))
        await publish_task_result(agent, run_id=run_id, status='waiting_approval')
    async def run():
        await execute(agent, 'review-run')
        assert not archived
        assert 'review-run' in agent.extension_runs
        payload = dict(project_id='project', user_message='edit', final_answer='done',
                       checkpoint={'mutation_evidence': [{}]}, tool_steps=[])
        await publish_task_result(agent, run_id='review-run', status='completed', **payload)
        await asyncio.gather(*tuple(_background_tasks))
        assert 'review-run' not in agent.extension_runs
        assert archived[0].resources[0]['version'] == 'old-etag'
        await publish_task_result(agent, run_id='partial-run', status='partial', **payload)
        assert len(archived) == 1
    asyncio.run(run())


def test_observe_failure_still_removes_known_stale_context(isolated_hooks):
    async def observe(request):
        raise RuntimeError('store unavailable')
    async def recall(request):
        raise RuntimeError('store unavailable')
    context = session(SimpleNamespace(observe=observe, recall=recall))
    async def run():
        with extension_scope(context):
            registry = hooks.get_hooks()
            await registry.aemit(HookEvent.RUN_START, HookContext(HookEvent.RUN_START, 'Test', run_id='run',
                payload={'user_message': 'edit'}))
            await registry.atrigger(HookEvent.TOOL_AFTER, HookContext(HookEvent.TOOL_AFTER, 'Test', run_id='run',
                tool_name='apply_changes', tool_input={}, payload={'result': {'changes': [{'path': 'a.py'}]}}))
            messages = [{'role': 'user', 'content': '<project_memory>old fact</project_memory>\nrequest'}]
            await registry.atrigger(HookEvent.MODEL_BEFORE, HookContext(HookEvent.MODEL_BEFORE, 'Test',
                run_id='run', messages=messages, payload={'current_user_index': 0}))
            assert messages[0]['content'] == '\nrequest'
    asyncio.run(run())


def test_concurrent_runs_keep_resources_and_dirty_state_separate(isolated_hooks):
    async def observe(request):
        await asyncio.sleep(0)
        return MemoryEventResult(resources=({'resource_id': request.run_id, 'version': 'v1'},))
    base = session(SimpleNamespace(observe=observe))
    async def run_one(run_id):
        local = base.fork()
        with extension_scope(local):
            registry = hooks.get_hooks()
            await registry.aemit(HookEvent.RUN_START, HookContext(HookEvent.RUN_START, 'Test', run_id=run_id,
                payload={'user_message': run_id}))
            await registry.atrigger(HookEvent.TOOL_AFTER, HookContext(HookEvent.TOOL_AFTER, 'Test', run_id=run_id,
                tool_name='read_file', tool_input={}, payload={'result': {'changes': []}, 'event_id': run_id}))
            return local.state('memory')
    async def run():
        first, second = await asyncio.gather(run_one('a'), run_one('b'))
        assert set(first['resources']) == {'a'} and set(second['resources']) == {'b'}
        assert first['query'] == 'a' and second['query'] == 'b'
        assert not first['dirty'] and not second['dirty']
        assert base.state('memory') == {}
    asyncio.run(run())
