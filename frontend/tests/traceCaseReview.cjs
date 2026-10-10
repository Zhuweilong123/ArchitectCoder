const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');

for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText, filename);
}
require.extensions['.css'] = () => {};
const { draftTurns, savedReviewPayload, reviewPayload, hasHardCriteria, selectTurns } = require('../src/components/EvaluationCenter/traceCaseReview.ts');

const turn = (index, prompt, hard = []) => ({ prompt, checkers: [], hard_checkers: hard, metadata: { source_turn_index: index } });
const draft = () => ({
  draft_id: 'tcd_test', status: 'validated', session_id: 'source', warnings: [], candidate_checkers: [],
  trace_summary: { turns: 2, turn_details: [{ prompt: 'fix', source_turn_index: 1 }, { prompt: 'thanks', source_turn_index: 2 }] },
  case: { id: 'test', name: 'Original', project_id: 'baseline', prompt: '', turns: [turn(1, 'fix'), turn(2, 'thanks')],
    checkers: [], hard_checkers: [{ type: 'file_exists', path: 'src/main.py' }], metadata: {} },
  validation: { passed: true, score: 1, checker_results: [] },
});

test('restoring legacy single-turn drafts does not introduce unsaved edits', () => {
  const value = draft();
  value.case.prompt = 'fix';
  value.case.turns = [];
  const turns = draftTurns(value);
  assert.equal(turns[0].metadata.source_turn_index, 1);
  assert.deepEqual(reviewPayload(value.case.name, value.case.project_id, value.case.checkers,
    value.case.hard_checkers, turns, false), savedReviewPayload(value));
});

test('turn selection preserves edited prompts and criteria in original chronological order', () => {
  const criterion = { type: 'answer_contains_all', texts: ['fixed'] };
  const edited = [turn(3, 'edited target', [criterion]), turn(1, 'context')];
  const selected = selectTurns([3, 1, 3, 99], edited, [turn(1, 'old context'), turn(2, 'thanks'), turn(3, 'old target')]);
  assert.deepEqual(selected.map((item) => item.metadata.source_turn_index), [1, 3]);
  assert.equal(selected[1].prompt, 'edited target');
  assert.deepEqual(selected[1].hard_checkers, [criterion]);
  assert.equal(hasHardCriteria([], selected), true);
  assert.equal(hasHardCriteria([], [turn(2, 'thanks')]), false);
});

test('rendered workflow prevents stale publishing and saves edits before validation', async () => {
  const { JSDOM } = require('jsdom');
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/', pretendToBeVisual: true });
  // rc-overflow otherwise picks Node's MessageChannel, whose ports outlive DOM cleanup.
  const originalMessageChannel = global.MessageChannel;
  global.MessageChannel = dom.window.MessageChannel;
  global.window = dom.window;
  global.document = dom.window.document;
  global.navigator = dom.window.navigator;
  global.localStorage = dom.window.localStorage;
  global.HTMLElement = dom.window.HTMLElement;
  global.Element = dom.window.Element;
  global.SVGElement = dom.window.SVGElement;
  global.ShadowRoot = dom.window.ShadowRoot;
  global.getComputedStyle = (element) => dom.window.getComputedStyle(element);
  global.IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  const React = require('react');
  const { act } = React;
  const { createRoot } = require('react-dom/client');
  const apiPath = require.resolve('../src/services/api.ts');
  const resourcesPath = require.resolve('../src/services/evaluationCenterApi.ts');
  let saved = draft();
  let failSave = false;
  const calls = [];
  require.cache[apiPath] = { id: apiPath, filename: apiPath, loaded: true, exports: {
    async reviewTraceCaseDraft(id, request) {
      calls.push(['save', request]);
      if (failSave) throw new Error('Save failed');
      saved = { ...saved, status: 'review_ready', validation: null, case: { ...saved.case, ...request } };
      return saved;
    },
    async validateTraceCaseDraft(id) {
      calls.push(['validate', id]);
      saved = { ...saved, status: 'validated', validation: { passed: true, score: 1, checker_results: [] } };
      return saved;
    },
  } };
  require.cache[resourcesPath] = { id: resourcesPath, filename: resourcesPath, loaded: true, exports: {
    async loadEvaluationOverview() { return { cases: [], baseline: null, repository: { version: 'test', branch: 'test', commit: '0123456789abcdef' }, trends: [], archives: [], performanceRuns: [] }; },
    async loadTraceCaseFactoryResources() { return { traces: [{ session_id: 'source', events: 4 }], projects: [{ id: 'baseline', version: '1' }], drafts: [saved] }; },
  } };
  const { useUiStore } = require('../src/stores/uiStore.ts');
  useUiStore.setState({ evaluationVisible: true, interfaceLanguage: 'zh', traceCaseFactoryRequestedSessionId: 'source' });
  const EvaluationCenter = require('../src/components/EvaluationCenter/EvaluationCenter.tsx').default;
  const { ConfigProvider, message, Modal } = require('antd');
  const originalMessages = { success: message.success, error: message.error, warning: message.warning };
  // Toast animations are unrelated to the workflow and create global portals.
  for (const key of Object.keys(originalMessages)) message[key] = () => {};
  const root = createRoot(document.getElementById('root'));
  const button = (text) => [...document.querySelectorAll('button')].find((item) => item.textContent.replace(/\s/g, '') === text.replace(/\s/g, ''));
  try {
    await act(async () => { root.render(React.createElement(ConfigProvider, { theme: { token: { motion: false } } }, React.createElement(EvaluationCenter))); });
    await act(async () => { button('恢复').click(); });
    assert.equal(button('发布用例').disabled, false);
    const input = [...document.querySelectorAll('input')].find((item) => item.value === 'Original');
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, 'Edited');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    assert.equal(button('发布用例').disabled, true);
    assert.ok(document.body.textContent.includes('有未保存修改'));
    await act(async () => { button('保存并试运行').click(); });
    assert.deepEqual(calls.map(([type]) => type), ['save', 'validate']);
    assert.equal(calls[0][1].name, 'Edited');
    assert.equal(calls[0][1].turns[1].prompt, 'thanks');
    assert.equal(button('发布用例').disabled, false);
    failSave = true;
    calls.length = 0;
    await act(async () => {
      const name = [...document.querySelectorAll('input')].find((item) => item.value === 'Edited');
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(name, 'Unsaved');
      name.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await act(async () => { button('保存并试运行').click(); });
    assert.deepEqual(calls.map(([type]) => type), ['save']);
    assert.ok([...document.querySelectorAll('input')].some((item) => item.value === 'Unsaved'));
    assert.equal(button('发布用例').disabled, true);
  } finally {
    await act(async () => { Modal.destroyAll(); });
    await act(async () => { root.unmount(); });
    Object.assign(message, originalMessages);
    dom.window.close();
    global.MessageChannel = originalMessageChannel;
  }
});
