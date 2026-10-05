const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { buildPluginGraph, wrapGraphLabel } = require('../src/components/PluginArchitecture/pluginGraph.ts');

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
