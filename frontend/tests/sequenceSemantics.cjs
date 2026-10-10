const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const storage = new Map();
global.localStorage = {
  getItem: key => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: key => storage.delete(key),
};
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);

const { normalizeDiagram } = require('../src/utils/diagramNormalization.ts');
const { arrangeSequenceLayout, sequenceContentStartY } = require('../src/utils/sequenceLayout.ts');
const { fitStructuredFragments } = require('../src/utils/sequenceFragments.ts');
const { sequenceOperandView } = require('../src/utils/sequenceFragmentView.ts');
const { getDiagramChanges } = require('../src/utils/designChanges.ts');
const { useDiagramStore } = require('../src/stores/diagramStore.ts');
const { handleDesignElement, processDesignUpdated } = require('../src/services/designElementHandler.ts');
const { getMessageVisual } = require('../src/components/Canvas/seqRenderUtils.ts');

test('review lays out changed diagrams before publishing and preserves originals and unrelated diagrams', () => {
  const original = diagram();
  const candidate = structuredClone(original);
  candidate.messages[2].label = 'updated request';
  const unrelated = normalizeDiagram({ name: 'Unchanged', classes: [{ id: 'alone', position: { x: 900, y: 800 } }] });
  const originalJson = JSON.stringify(original);
  const candidateJson = JSON.stringify(candidate);
  useDiagramStore.setState({ project: {
    ...useDiagramStore.getState().project, diagrams: [original, unrelated], active_diagram_index: 0,
    grid_settings: { grid_visible: true, grid_size: 20, grid_color: '#e0e0e0', grid_thickness: 1, snap_to_grid: true },
  } });
  let published;
  const ui = {
    setGlobalOptimizationResult: (before, after, diffs) => {
      published = { before, after, diffs };
      assert.deepEqual(useDiagramStore.getState().project.diagrams[0], after['sequence:Flow']);
    },
    setRightPanelTab: () => {}, setRightPanelVisible: () => {},
  };
  const specs = [candidate, unrelated].map(data => ({ type: data.diagram_type, name: data.name, component_id: '', data }));
  // An omitted/empty change selector uses the full snapshot without arranging unchanged diagrams.
  processDesignUpdated(specs, [], ui, useDiagramStore.getState(), {
    'sequence:Flow': original, 'class:Unchanged': unrelated,
  }, []);
  assert.deepEqual(Object.keys(published.after), ['sequence:Flow']);
  assert.deepEqual(published.after['sequence:Flow'].messages, arrangeSequenceLayout(candidate.lifelines, candidate.messages, candidate.fragments).messages);
  assert.deepEqual(JSON.parse(published.diffs['sequence:Flow']).after, published.after['sequence:Flow']);
  assert.equal(JSON.stringify(original), originalJson);
  assert.equal(JSON.stringify(candidate), candidateJson);
  assert.deepEqual(published.before['sequence:Flow'], original);
  assert.deepEqual(useDiagramStore.getState().project.diagrams[1], unrelated);
  assert.deepEqual(published.after['sequence:Flow'].fragments[0].operands.map(o => [o.id, o.guard, o.message_ids]),
    original.fragments[0].operands.map(o => [o.id, o.guard, o.message_ids]));
});

test('review prepares class and component candidates for the same canvas and preview layout', () => {
  const { layoutReviewDiagram } = require('../src/utils/reviewLayout.ts');
  for (const type of ['class', 'component']) {
    const input = normalizeDiagram({
      name: type, diagram_type: type,
      classes: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }],
      components: [{ id: 'a', name: 'A', x: 0, y: 0 }, { id: 'b', name: 'B', x: 0, y: 0 }],
    });
    const before = JSON.stringify(input);
    const output = layoutReviewDiagram(input);
    assert.equal(JSON.stringify(input), before);
    if (type === 'class') assert.notDeepEqual(output.classes[0].position, output.classes[1].position);
    else assert.notDeepEqual([output.components[0].x, output.components[0].y], [output.components[1].x, output.components[1].y]);
  }
});

function diagram() {
  return normalizeDiagram({
    name: 'Flow', diagram_type: 'sequence',
    lifelines: [{ id: 'caller', x: 100 }, { id: 'service', x: 400 }, { id: 'idle', x: 700 }],
    messages: [
      { id: 'ok', from_lifeline: 'service', to_lifeline: 'caller', type: 'return', order: 1, y: 280 },
      { id: 'fail', from_lifeline: 'service', to_lifeline: 'caller', type: 'return', order: 2, y: 400 },
      { id: 'outside', from_lifeline: 'caller', to_lifeline: 'service', order: 3, y: 500 },
    ],
    fragments: [{
      id: 'alt', type: 'alt', x: 80, width: 560, y_start: 200, y_end: 450, lifeline_ids: ['caller', 'service'],
      operands: [
        { id: 'success', guard: '[valid]', message_ids: ['ok'], y_start: 240, y_end: 320 },
        { id: 'failure', guard: '[else]', message_ids: ['fail'], y_start: 340, y_end: 440 },
      ],
    }],
  });
}

test('normalization preserves explicit operands without inferring legacy alternatives', () => {
  const d = diagram();
  assert.deepEqual(normalizeDiagram(d).fragments[0], d.fragments[0]);
  const old = { ...d.fragments[0] }; delete old.operands;
  assert.deepEqual(normalizeDiagram({ ...d, fragments: [old] }).fragments[0].operands, []);
});

test('layout uses membership even when the original frame encloses unrelated messages', () => {
  const d = diagram();
  d.fragments[0].y_end = 700;
  const result = arrangeSequenceLayout(d.lifelines, d.messages, d.fragments);
  const f = result.fragments[0], [first, second] = f.operands;
  assert.ok(first.y_end <= second.y_start);
  const byId = new Map(result.messages.map(m => [m.id, m]));
  assert.ok(first.y_start < byId.get('ok').y && byId.get('ok').y < first.y_end);
  assert.ok(second.y_start < byId.get('fail').y && byId.get('fail').y < second.y_end);
  assert.ok(byId.get('outside').y > f.y_end);
  assert.deepEqual(f.operands.map(o => o.message_ids), [['ok'], ['fail']]);
});

test('break layout covers idle enclosing participants', () => {
  const d = diagram();
  d.fragments[0] = { ...d.fragments[0], type: 'break', operands: [d.fragments[0].operands[1]], lifeline_ids: ['caller', 'service', 'idle'] };
  const result = arrangeSequenceLayout(d.lifelines, d.messages, d.fragments);
  const frame = result.fragments[0];
  result.lifelines.forEach(l => assert.ok(frame.x <= l.x + 70 && l.x + 70 <= frame.x + frame.width));
});

test('nested fragments fit their parent operand without copying child membership', () => {
  const d = diagram();
  d.fragments[0].operands[0].message_ids = [];
  d.fragments.push({ id: 'child', type: 'opt', x: 80, width: 560, y_start: 245, y_end: 315,
    parent_fragment_id: 'alt', parent_operand_id: 'success', lifeline_ids: ['caller', 'service'],
    operands: [{ id: 'child_op', guard: '[enabled]', message_ids: ['ok'], y_start: 260, y_end: 310 }] });
  const result = fitStructuredFragments(d.fragments, d.messages);
  const parent = result[0].operands[0], child = result[1];
  assert.ok(parent.y_start <= child.y_start && child.y_end <= parent.y_end);
  assert.deepEqual(parent.message_ids, []);
  const arranged = arrangeSequenceLayout(d.lifelines, d.messages, d.fragments);
  assert.ok(arranged.fragments[0].y_start >= sequenceContentStartY(d.lifelines));
  assert.ok(arranged.fragments[0].operands[0].y_end <= arranged.fragments[0].operands[1].y_start);
  assert.ok(arranged.fragments[0].x < arranged.fragments[1].x);
  assert.ok(arranged.fragments[0].x + arranged.fragments[0].width > arranged.fragments[1].x + arranged.fragments[1].width);
});

test('empty alternative keeps its own region before common continuation', () => {
  const d = diagram();
  d.messages = d.messages.filter(m => m.id !== 'fail');
  d.fragments[0].operands[1].message_ids = [];
  const arranged = arrangeSequenceLayout(d.lifelines, d.messages, d.fragments);
  const [success, empty] = arranged.fragments[0].operands;
  assert.ok(success.y_end <= empty.y_start);
  assert.ok(arranged.messages.find(m => m.id === 'outside').y > arranged.fragments[0].y_end);
  assert.deepEqual(empty.message_ids, []);
});

test('guards are native SVG text and branch separators are dashed', () => {
  const d = diagram();
  d.fragments[0].operands[0].guard = '[x < 3] <script>alert(1)</script>';
  const result = sequenceOperandView(d.fragments[0], 200, 560, '#333');
  assert.equal(result.attrs.operandGuard0.text, d.fragments[0].operands[0].guard);
  assert.equal(result.markup.find(m => m.selector === 'operandGuard0').tagName, 'text');
  assert.equal(result.attrs.operandSeparator1.strokeDasharray, '6 4');
  assert.equal(result.attrs.operandSeparator1.y1, 140);
});

test('asynchronous message uses an open arrow rather than a synchronous filled arrow', () => {
  const asyncMarker = getMessageVisual('async', 'light').marker;
  assert.equal(asyncMarker.fill, 'none');
  assert.equal(asyncMarker.open, true);
  assert.notEqual(getMessageVisual('sync', 'light').marker.fill, 'none');
});

test('review diff detects guard and membership changes but ignores operand coordinates', () => {
  const before = diagram(), after = structuredClone(before);
  after.fragments[0].operands[0].y_start += 1;
  assert.equal(getDiagramChanges(before, after).length, 0);
  after.fragments[0].operands[0].guard = '[revised]';
  assert.equal(getDiagramChanges(before, after)[0].kind, 'fragment');
  after.fragments[0].operands[0].guard = '[valid]';
  after.fragments[0].operands[0].message_ids = [];
  assert.equal(getDiagramChanges(before, after)[0].kind, 'fragment');
});

function load(d = diagram()) {
  useDiagramStore.getState().setProject({ name: 'Test', version: '1.0', revision: 0, diagrams: [d], active_diagram_index: 0 });
}
const active = () => useDiagramStore.getState().project.diagrams[0];

test('dragging an unrelated message into a branch does not assign membership', () => {
  load();
  useDiagramStore.getState().updateMessage('outside', { y: 285 });
  assert.deepEqual(active().fragments[0].operands.map(o => o.message_ids), [['ok'], ['fail']]);
});

test('deleting a message/lifeline cleans branch references and undo restores them', () => {
  load();
  useDiagramStore.getState().removeMessage('ok');
  assert.deepEqual(active().fragments[0].operands[0].message_ids, []);
  useDiagramStore.getState().undo();
  assert.deepEqual(active().fragments[0].operands[0].message_ids, ['ok']);
  useDiagramStore.getState().removeLifeline('service');
  assert.deepEqual(active().fragments[0].lifeline_ids, ['caller']);
  assert.ok(active().fragments[0].operands.every(o => !o.message_ids.length));
});

test('streaming output maps branch references, preserves Y and resolves a late parent', () => {
  load(normalizeDiagram({ name: 'Flow', diagram_type: 'sequence' }));
  const ids = new Map();
  const send = (type, data) => handleDesignElement(useDiagramStore.getState(), { type, data: JSON.stringify({ ...data, diagram_name: 'Flow' }) }, ids);
  send('lifeline', { id: 'L1', name: 'Caller', x: 100 });
  send('lifeline', { id: 'L2', name: 'Service', x: 400 });
  send('message', { id: 'M1', from_lifeline: 'L1', to_lifeline: 'L2', y: 290 });
  send('fragment', { id: 'F1', type: 'opt', y_start: 250, y_end: 340,
    lifeline_ids: ['L1', 'L2'], parent_fragment_id: 'F2', parent_operand_id: 'outer_op',
    operands: [{ id: 'inner_op', guard: '[enabled]', message_ids: ['M1'], y_start: 270, y_end: 330 }] });
  send('fragment', { id: 'F2', type: 'opt', y_start: 200, y_end: 400,
    lifeline_ids: ['L1', 'L2'], operands: [{ id: 'outer_op', guard: '[ready]', message_ids: [], y_start: 230, y_end: 390 }] });
  const child = active().fragments.find(f => f.id === ids.get('F1'));
  assert.equal(child.parent_fragment_id, ids.get('F2'));
  assert.deepEqual(child.lifeline_ids, [ids.get('L1'), ids.get('L2')]);
  assert.deepEqual(child.operands[0].message_ids, [ids.get('M1')]);
  assert.equal(active().messages[0].y, 290);
});
