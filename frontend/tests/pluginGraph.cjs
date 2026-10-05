const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { buildPluginGraph, wrapGraphLabel } = require('../src/components/PluginArchitecture/pluginGraph.ts');
const { replayRuns, replaySteps, stepStatus, contributionSummary, stepExplanation } = require('../src/components/PluginArchitecture/replayModel.ts');

const contribution = (id, stage, order, priority = 10) => ({
  id, stage, plugin: 'trace', order, priority, handler: `trace:${id}`, mode: 'observer', before: [], after: [], scope: 'run', fail_closed: false,
});
const stages = ['run_start', 'round_before', 'llm_before', 'llm_after', 'tool_batch_before', 'tool_before',
  'tool_after', 'tool_batch_after', 'round_after', 'run_finalize', 'run_end', 'error', 'cancel'];
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
    { event_type: 'lifecycle_stage', run_id: 'parent', stage: 'llm_before', plan_id: 'fixture', ts_ms: 20 },
    { event_type: 'lifecycle_stage', run_id: 'child', stage: 'run_start', plan_id: 'fixture', ts_ms: 20 },
    { event_type: 'plugin_contribution', run_id: 'parent', stage: 'llm_before', contribution_id: 'watch.llm_before', plan_id: 'fixture', status: 'skipped', blocked_by: 'policy' },
    { event_type: 'llm_request', run_id: 'parent', ts_ms: 19 },
    { event_type: 'lifecycle_stage', run_id: 'parent', stage: 'llm_before', plan_id: 'fixture', ts_ms: 30 },
  ];
  const before = JSON.stringify(events);
  const runs = replayRuns(events);
  assert.deepEqual(runs.map((run) => run.id), ['parent', 'child']);
  const steps = replaySteps(runs[0], plan);
  assert.deepEqual(steps.map((step) => step.event.ts_ms), [20, undefined, 19, 30]);
  assert.deepEqual(steps[1].nodeIds, ['stage:llm_before', 'contribution:watch.llm_before']);
  assert.equal(steps[1].event.blocked_by, 'policy');
  assert.deepEqual(steps[2].nodeIds, ['model-call']);
  assert.equal(JSON.stringify(events), before);
});

test('historical, mixed and missing plans retain details without misleading graph matches', () => {
  for (const planIds of [['old'], [], ['fixture', 'old']]) {
    const events = [{ event_type: 'lifecycle_stage', run_id: 'run', stage: 'llm_before' },
      ...planIds.map((plan_id) => ({ event_type: 'plugin_contribution', run_id: 'run', plan_id, contribution_id: 'watch.llm_before' }))];
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
  assert.equal(stepExplanation([{ ...steps[0], event: { ...steps[0].event, stage: 'llm_before' } }, steps[2]], 1).blockerIndex, -1);
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
  fixture.stages[2].contributions = [contribution('second', 'llm_before', 2, 1000), contribution('first', 'llm_before', 1, 0)];
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
  fixture.stages[2].contributions = Array.from({ length: 19 }, (_, i) => contribution(`extra.${i}`, 'llm_before', i + 1));
  fixture.plugins[0].interfaces = Array.from({ length: 15 }, (_, i) => `query.${i}`);
  for (const view of ['organization', 'schedule']) verifyGraph(buildPluginGraph(fixture, view));
});

test('long Unicode node labels wrap and preserve their code points', () => {
  assert.deepEqual(wrapGraphLabel('观察模型调用阶段', 8), ['观察模型', '调用阶段']);
  assert.equal(wrapGraphLabel('a'.repeat(200)).length, 2);
  assert.ok(wrapGraphLabel('a'.repeat(200))[1].endsWith('…'));
});
