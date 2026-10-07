const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { buildPluginGraph, wrapGraphLabel } = require('../src/components/PluginArchitecture/pluginGraph.ts');
const { graphCatalog, graphIssues, revealGraphNode, searchGraph } = require('../src/components/PluginArchitecture/graphExplorer.ts');
const { replayRuns, replaySteps, replayOperations, stepStatus, contributionSummary, stepExplanation } = require('../src/components/PluginArchitecture/replayModel.ts');

const contribution = (id, stage, order, priority = 10) => ({
  id, stage, plugin: 'trace', order, priority, handler: `trace:${id}`, mode: 'observer', before: [], after: [], scope: 'run', fail_closed: false,
});
const stages = ['run_start', 'round_before', 'model_before', 'model_after', 'tool_batch_before', 'tool_before',
  'tool_after', 'tool_batch_after', 'round_after', 'finalize', 'run_end', 'error', 'cancel'];
const plan = {
  schema_version: 1, plan_id: 'fixture', dispatch: 'sequential',
  plugins: [
    { name: 'trace', provider: 'trace:create', status: 'discovered', source: 'builtin', error: '', interfaces: ['create'], contributions: [] },
    { name: 'disabled', provider: 'missing:create', status: 'disabled', source: 'fixture', error: '', interfaces: ['query'], contributions: [] },
    { name: 'unavailable', provider: 'missing:create', status: 'unavailable', source: 'fixture', error: 'Missing module', interfaces: ['query'], contributions: [] },
  ],
  stages: stages.map((stage) => ({ stage, supported_modes: ['observer'], contributions: [contribution(`watch.${stage}`, stage, 1)] })),
};

test('replay separates interleaved tasks and preserves recorded order and repeated phases', () => {
  const events = [
    { event_type: 'user_message', message: 'legacy' },
    { event_type: 'lifecycle_stage', run_id: 'parent', stage: 'model_before', plan_id: 'fixture', ts_ms: 20 },
    { event_type: 'lifecycle_stage', run_id: 'child', stage: 'run_start', plan_id: 'fixture', ts_ms: 20 },
    { event_type: 'plugin_contribution', run_id: 'parent', stage: 'model_before', contribution_id: 'watch.model_before', plan_id: 'fixture', status: 'skipped', blocked_by: 'policy' },
    { event_type: 'llm_request', run_id: 'parent', ts_ms: 19 },
    { event_type: 'lifecycle_stage', run_id: 'parent', stage: 'model_before', plan_id: 'fixture', ts_ms: 30 },
  ];
  const before = JSON.stringify(events);
  const runs = replayRuns(events);
  assert.deepEqual(runs.map((run) => run.id), ['parent', 'child']);
  const steps = replaySteps(runs[0], plan);
  assert.deepEqual(steps.map((step) => step.event.ts_ms), [20, undefined, 19, 30]);
  assert.deepEqual(steps[1].nodeIds, ['stage:model_before', 'contribution:watch.model_before']);
  assert.equal(steps[1].event.blocked_by, 'policy');
  assert.deepEqual(steps[2].nodeIds, ['model-call']);
  assert.equal(JSON.stringify(events), before);
});

test('historical, mixed and missing plans retain details without misleading graph matches', () => {
  for (const planIds of [['old'], [], ['fixture', 'old']]) {
    const events = [{ event_type: 'lifecycle_stage', run_id: 'run', stage: 'model_before' },
      ...planIds.map((plan_id) => ({ event_type: 'plugin_contribution', run_id: 'run', plan_id, contribution_id: 'watch.model_before' }))];
    const steps = replaySteps(replayRuns(events)[0], plan);
    assert.ok(steps.length);
    assert.ok(steps.every((step) => !step.compatible && !step.nodeIds.length));
  }
  assert.deepEqual(replayRuns([{ event_type: 'llm_request', run_id: 'legacy' }]), []);
  assert.deepEqual(replaySteps({ id: 'unknown', events: [
    { event_type: 'plugin_contribution', plan_id: 'fixture', stage: 'unknown', contribution_id: 'removed' },
  ] }, plan)[0].nodeIds, []);
});

test('skip explanation locates the nearest preceding decision in repeated phases', () => {
  const event = (data) => ({ event: { event_type: 'plugin_contribution', stage: 'tool_before', ...data }, index: 0, nodeIds: [], compatible: true });
  const steps = [
    event({ contribution_id: 'deny', status: 'executed', decision_action: 'veto', decision_reason: 'old' }),
    event({ contribution_id: 'deny', status: 'error', decision_action: 'veto', decision_reason: 'hook_error', decision_message: 'invalid config' }),
    event({ contribution_id: 'lower', status: 'skipped', blocked_by: 'deny' }),
    event({ contribution_id: 'deny', status: 'executed', decision_reason: 'future' }),
  ];
  assert.deepEqual(stepExplanation(steps, 2), { blockerIndex: 1, action: 'veto', reason: 'hook_error', message: 'invalid config', errorType: undefined, errorMessage: undefined, failureEffect: undefined });
  assert.equal(stepExplanation([event({ status: 'skipped', blocked_by: 'missing' })], 0).blockerIndex, -1);
  assert.equal(stepExplanation([{ ...steps[0], event: { ...steps[0].event, stage: 'model_before' } }, steps[2]], 1).blockerIndex, -1);
});

test('contribution totals exclude model latency and copied skip decisions; statuses keep failure semantics', () => {
  const steps = [
    { event_type: 'plugin_contribution', status: 'executed', duration_ms: 3, decision_action: 'veto' },
    { event_type: 'plugin_contribution', status: 'skipped', duration_ms: 0, decision_action: 'veto' },
    { event_type: 'plugin_contribution', status: 'error', duration_ms: 2, decision_action: 'stop' },
    { event_type: 'plugin_contribution', status: 'interrupted', duration_ms: 1 },
    { event_type: 'plugin_contribution', status: 'executed', duration_ms: NaN, mode: 'observer', decision_action: 'stop' },
    { event_type: 'llm_response', duration_ms: 10000 },
  ].map((event) => ({ event }));
  assert.deepEqual(contributionSummary(steps), { executed: 2, skipped: 1, error: 1, interrupted: 1, decisions: 2, durationMs: 6 });
  assert.equal(stepStatus({ event_type: 'llm_response', error: 'timeout' }), 'error');
  assert.equal(stepStatus({ event_type: 'tool_call' }), 'started');
  assert.equal(stepStatus({ event_type: 'tool_result', error: '' }), 'completed');
  assert.equal(stepStatus({ event_type: 'lifecycle_stage', stage_data: { status: 'cancelled' } }), 'cancelled');
});

function verifyGraph(graph) {
  const ids = new Set(graph.nodes.map((node) => node.id));
  assert.equal(ids.size, graph.nodes.length, 'node IDs must be unique');
  for (const edge of graph.edges) {
    assert.ok(ids.has(edge.source), `missing source ${edge.source}`);
    assert.ok(ids.has(edge.target), `missing target ${edge.target}`);
  }
  for (const node of graph.nodes) {
    assert.ok(node.x >= 0 && node.y >= 0);
    assert.ok(node.x + node.width <= graph.width);
    assert.ok(node.y + node.height <= graph.height);
    for (const other of graph.nodes) {
      if (other.id <= node.id) continue;
      assert.ok(node.x + node.width <= other.x || other.x + other.width <= node.x
        || node.y + node.height <= other.y || other.y + other.height <= node.y,
      `${node.id} overlaps ${other.id}`);
    }
  }
}

test('both graph views have bounded, non-overlapping nodes and valid edges', () => {
  const snapshot = JSON.stringify(plan);
  for (const view of ['organization', 'schedule']) verifyGraph(buildPluginGraph(plan, view));
  assert.equal(JSON.stringify(plan), snapshot, 'rendering must not mutate the backend snapshot');
});

test('scheduled interfaces connect to stages without duplicate domain nodes or invented service order', () => {
  const fixture = structuredClone(plan);
  fixture.plugins[0].interfaces = ['read_skill'];
  fixture.stages[0].contributions = [
    { ...contribution('trace.interface.read_skill', 'run_start', 1), mode: 'service', interface_id: 'trace.read_skill' },
    { ...contribution('trace.interface.query', 'run_start', 2), mode: 'service', interface_id: 'trace.query' },
  ];
  const organization = buildPluginGraph(fixture, 'organization', 'trace');
  verifyGraph(organization);
  assert.ok(!organization.nodes.some((node) => node.kind === 'interface'));
  assert.ok(organization.edges.some((edge) => edge.source === 'contribution:trace.interface.read_skill' && edge.target === 'stage:run_start'));
  const schedule = buildPluginGraph(fixture, 'schedule', 'trace');
  verifyGraph(schedule);
  assert.ok(!schedule.edges.some((edge) => edge.kind === 'order' && edge.source === 'contribution:trace.interface.read_skill'));
});

test('domain-only disabled plugins stay visible without invented lifecycle attachments', () => {
  const graph = buildPluginGraph(plan, 'organization', 'disabled');
  verifyGraph(graph);
  assert.deepEqual(graph.nodes.map((node) => node.kind), ['plugin', 'interface']);
  assert.equal(graph.nodes[0].plugin.status, 'disabled');
  assert.equal(graph.edges.length, 1);
  const failed = buildPluginGraph(plan, 'organization', 'unavailable');
  assert.equal(failed.nodes[0].plugin.error, 'Missing module');
});

test('schedule honors compiled order rather than re-sorting by priority', () => {
  const fixture = structuredClone(plan);
  fixture.stages[2].contributions = [contribution('second', 'model_before', 2, 1000), contribution('first', 'model_before', 1, 0)];
  const graph = buildPluginGraph(fixture, 'schedule', 'trace');
  verifyGraph(graph);
  const first = graph.nodes.find((node) => node.label === 'first');
  const second = graph.nodes.find((node) => node.label === 'second');
  assert.ok(first.x < second.x);
  assert.ok(graph.edges.some((edge) => edge.kind === 'order' && edge.source === first.id && edge.target === second.id));
  assert.ok(graph.edges.some((edge) => edge.label === 'continue'));
  assert.ok(graph.edges.some((edge) => edge.label === 'no-tools'));
});

test('large contribution sets and domain interfaces do not overlap', () => {
  const fixture = structuredClone(plan);
  fixture.stages[2].contributions = Array.from({ length: 19 }, (_, i) => contribution(`extra.${i}`, 'model_before', i + 1));
  fixture.plugins[0].interfaces = Array.from({ length: 15 }, (_, i) => `query.${i}`);
  for (const view of ['organization', 'schedule']) verifyGraph(buildPluginGraph(fixture, view));
});

test('long Unicode node labels wrap and preserve their code points', () => {
  assert.deepEqual(wrapGraphLabel('观察模型调用阶段', 8), ['观察模型', '调用阶段']);
  assert.equal(wrapGraphLabel('a'.repeat(200)).length, 2);
  assert.ok(wrapGraphLabel('a'.repeat(200))[1].endsWith('…'));
});

test('canonical plan renders thirteen public phases with model/tool nodes and no notification nodes', () => {
  const fixture = structuredClone(plan);
  fixture.stages = ['initialize', 'prepare', 'run_start', 'round_before', 'model_before', 'model_after',
    'tool_batch_before', 'tool_before', 'tool_after', 'tool_batch_after', 'round_after', 'finalize', 'run_end']
    .map((stage) => ({ stage, contributions: [], supported_modes: ['observer', 'service'] }));
  fixture.notifications = [{ stage: 'error', contributions: [contribution('watch.error', 'error', 1)] }];
  const graph = buildPluginGraph(fixture, 'schedule');
  verifyGraph(graph);
  assert.equal(graph.nodes.filter((node) => node.kind === 'stage').length, 13);
  assert.ok(graph.nodes.some((node) => node.id === 'model-call'));
  assert.ok(graph.nodes.some((node) => node.id === 'tool-call'));
  assert.ok(!graph.nodes.some((node) => node.id === 'stage:error'));
  const [step] = replaySteps({ id: 'parent', events: [{ event_type: 'runtime_notification', run_id: 'parent', stage: 'error', plan_id: 'fixture' }] }, fixture);
  assert.deepEqual(step.nodeIds, []);
});

test('operation tree merges end records and locates nested operations across tasks', () => {
  const operation = (run_id, operation_id, parent_operation_id, status) => ({ event_type: 'operation',
    run_id, operation_id, parent_operation_id, status, plan_id: 'fixture' });
  const runs = replayRuns([
    operation('parent', 'run', '', 'running'), operation('parent', 'tool', 'run', 'running'),
    operation('child', 'child-run', 'tool', 'running'), operation('child', 'model', 'child-run', 'running'),
    operation('child', 'model', 'child-run', 'failed'), operation('parent', 'tool', 'run', 'completed'),
    operation('parent', 'run', '', 'completed'), operation('parent', 'orphan', 'missing', 'completed'),
  ]);
  const tree = replayOperations(runs, plan);
  assert.equal(tree.length, 2);
  assert.equal(tree[0].event.status, 'completed');
  const model = tree[0].children[0].children[0].children[0];
  assert.equal(model.runId, 'child');
  assert.equal(model.event.status, 'failed');
  assert.equal(replaySteps(runs.find((run) => run.id === model.runId), plan)[model.stepIndex].event.operation_id, 'model');
  assert.equal(tree[1].key, 'orphan');
  // Malformed history must not create recursive UI trees.
  assert.equal(replayOperations(replayRuns([operation('parent', 'a', 'b', 'running'), operation('parent', 'b', 'a', 'running')]), plan).length, 2);
});

test('collapsed plugin summaries preserve bindings, counts and unavailable plugins', () => {
  const fixture = structuredClone(plan);
  fixture.stages[2].contributions = Array.from({ length: 80 }, (_, i) => contribution(`extra.${i}`, 'model_before', i + 1));
  const before = JSON.stringify(fixture);
  for (const view of ['organization', 'schedule']) {
    const full = buildPluginGraph(fixture, view);
    const folded = buildPluginGraph(fixture, view, '', { expandedPlugins: [] });
    verifyGraph(folded);
    assert.ok(folded.height < full.height);
    assert.ok(!folded.nodes.some(node => node.kind === 'contribution'));
    if (view === 'organization') {
      assert.ok(folded.edges.some(edge => edge.source === 'plugin:trace' && edge.target === 'stage:model_before'));
      assert.equal(folded.nodes.find(node => node.id === 'plugin:unavailable').plugin.status, 'unavailable');
    } else {
      const group = folded.nodes.find(node => node.id === 'group:model_before:trace');
      assert.equal(group.summary.contributions, 80);
      assert.ok(!folded.edges.some(edge => edge.kind === 'order'));
    }
    const expanded = buildPluginGraph(fixture, view, '', { expandedPlugins: ['trace'] });
    verifyGraph(expanded); assert.ok(expanded.nodes.some(node => node.id === 'contribution:extra.79'));
  }
  assert.equal(JSON.stringify(fixture), before);
});

test('catalog searches public, notification and unbound interfaces without losing identity', () => {
  const fixture = structuredClone(plan);
  fixture.notifications = [{ stage: 'background_after', supported_modes: ['observer'], contributions: [contribution('archive.finished', 'background_after', 1)] }];
  const catalog = graphCatalog(fixture);
  assert.equal(new Set(catalog.map(node => node.id)).size, catalog.length);
  assert.equal(searchGraph(catalog, 'ARCHIVE trace')[0].id, 'contribution:archive.finished');
  assert.equal(searchGraph(catalog, 'query disabled')[0].id, 'interface:disabled:0');
  assert.ok(searchGraph(catalog, 'model_before trace').some(node => node.id === 'contribution:watch.model_before'));
  assert.deepEqual(searchGraph(catalog, ''), []);
});

test('issues separate declaration/load/execution and ignore stale runtime reports and ordinary cancellation', () => {
  const report = { plan_id: plan.plan_id, plugins: [{ name: 'trace', status: 'loaded', diagnostics: [{ code: 'router_failed', message: 'route error' }] }] };
  const steps = [{ compatible: false, event: { event_type: 'plugin_contribution', status: 'error', plugin: 'trace', contribution_id: 'watch.model_before', error_message: 'old failure' } },
    { compatible: true, event: { event_type: 'plugin_contribution', status: 'interrupted', plugin: 'trace' } }];
  const issues = graphIssues(plan, report, steps);
  assert.deepEqual(issues.map(issue => issue.source), ['load', 'declaration', 'execution']);
  assert.equal(issues[2].compatible, false); assert.equal(issues[2].stepIndex, 0);
  assert.equal(graphIssues(plan, { ...report, plan_id: 'stale' }).length, 1);
});

test('search reveal clears conflicting filters and shows hidden public stages and interfaces', () => {
  const catalog = graphCatalog(plan);
  const folded = { view: 'organization', filter: 'disabled', expandedPlugins: [] };
  const stage = revealGraphNode(plan, catalog, 'stage:model_before', folded);
  assert.deepEqual(stage, { view: 'schedule', filter: '', expandedPlugins: [] });
  const target = revealGraphNode(plan, catalog, 'contribution:watch.model_before', folded);
  assert.equal(target.filter, ''); assert.deepEqual(target.expandedPlugins, ['trace']);
  assert.ok(buildPluginGraph(plan, target.view, target.filter, target).nodes.some(node => node.id === 'contribution:watch.model_before'));
  assert.deepEqual(folded.expandedPlugins, []);
});

test('mixed folded/expanded stage groups do not invent sequential edges', () => {
  const fixture = structuredClone(plan);
  fixture.stages[2].contributions = [contribution('trace.first', 'model_before', 1),
    { ...contribution('core.middle', 'model_before', 2), plugin: 'core' }, contribution('trace.last', 'model_before', 3)];
  const graph = buildPluginGraph(fixture, 'schedule', '', { expandedPlugins: ['trace'] });
  verifyGraph(graph);
  assert.ok(graph.nodes.some(node => node.id === 'group:model_before:core'));
  assert.ok(!graph.edges.some(edge => edge.kind === 'order' && edge.source === 'contribution:trace.first'));
});
