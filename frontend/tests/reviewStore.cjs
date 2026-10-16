const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
function harness() {
  let state, listener;
  const effects = { sent: [], logs: [], hidden: [], restored: [], discarded: [], notices: [] };
  const options = { delivery: 'sent', log: () => Promise.resolve({ success: true }),
    project: () => Promise.resolve({ revision: 2 }) };
  const ui = { originalDiagrams: { class: { name: 'before' } },
    originalDiagram: { name: 'before' }, optimizedDiagram: { name: 'after' },
    optimizeInstructions: 'requirements', setDesignComparisonVisible: v => effects.hidden.push(v) };
  const transport = {
    onAgentMessage: fn => { listener = fn; },
    discardReviewResponse: id => effects.discarded.push(id), setPendingUmlReviewId() {},
    sendReviewResponse: (id, text, decision) => {
      effects.sent.push({ id, text, decision });
      if (options.delivery === 'sent') listener({ event: 'review_resolved', review_id: id });
      return options.delivery;
    },
  };
  const exports = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(require.resolve('../src/stores/reviewStore.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, {
    exports, console,
    require: name => {
      if (name === 'zustand') return { create: init => {
        state = init(patch => { state = { ...state, ...patch }; }, () => state);
        return { getState: () => state };
      } };
      if (name === 'antd') return { message: Object.fromEntries(['success', 'warning', 'error', 'info'].map(
        kind => [kind, text => effects.notices.push({ kind, text })])) };
      if (name.endsWith('/agentChat')) return transport;
      if (name.endsWith('/api')) return {
        saveReview: input => { effects.logs.push(input); return options.log(); },
        saveProject: () => options.project(),
      };
      if (name.endsWith('/designElementHandler')) return {
        restoreOriginalsToCanvas: input => { effects.restored.push(input); return {}; },
      };
      if (name.endsWith('/uiStore')) return { useUiStore: { getState: () => ui } };
      if (name.endsWith('/diagramStore')) return { useDiagramStore: { getState: () => ({
        currentFilepath: 'design.umlproj', getProjectSnapshot: () => ({}), markSaved() {},
      }) } };
      throw new Error(name);
    },
  });
  const store = exports.useReviewStore;
  const show = (id = 42, auto = false) => store.getState().showReview({
    reviewId: id, reviewType: 'uml_diff', title: 'Design', auto,
  });
  show();
  return { store, show, effects, options, emit: event => listener(event) };
}

for (const decision of ['accept', 'reject']) {
  test(decision + ' remains retryable after send failure and produces no success log', async () => {
    const h = harness(); h.options.delivery = 'failed';
    assert.equal(await h.store.getState()[decision]('diff'), false);
    assert.equal(h.store.getState().status, 'pending');
    assert.equal(h.effects.logs.length, 0);
    assert.equal(h.effects.hidden.length, 0);
    assert.equal(h.effects.restored.length, 0);
    h.options.delivery = 'sent';
    assert.equal(await h.store.getState()[decision]('diff'), true);
    assert.equal(h.store.getState().status, decision === 'accept' ? 'accepted' : 'rejected');
    assert.equal(h.effects.logs.length, 1);
  });
  test(decision + ' stays queued until sent, ignores duplicate clicks and replayed requests', async () => {
    const h = harness(); h.options.delivery = 'queued';
    assert.equal(await h.store.getState()[decision]('chat'), true);
    assert.equal(h.store.getState().status, 'submitting');
    assert.equal(h.store.getState().delivery, 'queued');
    assert.equal(h.effects.logs.length, 0);
    assert.equal(h.effects.hidden.length, 0);
    h.show();
    assert.equal(await h.store.getState().accept('diff'), false);
    assert.equal(h.effects.sent.length, 1);
    h.emit({ event: 'review_resolved', review_id: 42 });
    await Promise.resolve();
    assert.equal(h.store.getState().status, decision === 'accept' ? 'accepted' : 'rejected');
    assert.equal(h.effects.logs.length, 1);
    assert.equal(h.effects.restored.length, decision === 'reject' ? 1 : 0);
    h.emit({ event: 'review_resolved', review_id: 42 });
    assert.equal(h.effects.logs.length, 1);
  });
}
test('log success is shown only after persistence; a save failure keeps the sent decision', async () => {
  const h = harness(), log = deferred(); h.options.log = () => log.promise;
  const action = h.store.getState().accept('diff');
  assert.equal(h.store.getState().status, 'accepted');
  assert.equal(h.effects.notices.filter(x => x.kind === 'success').length, 0);
  log.reject(new Error('disk unavailable'));
  assert.equal(await action, true);
  assert.equal(h.store.getState().status, 'accepted');
  assert.equal(h.effects.notices.filter(x => x.kind === 'success').length, 0);
  assert.match(h.effects.notices.at(-1).text, /日志保存失败/);
  assert.equal(await h.store.getState().accept('diff'), false);
  assert.equal(h.effects.sent.length, 1);
});
test('exhausted queued delivery returns to pending and can be retried', async () => {
  const h = harness(); h.options.delivery = 'queued';
  await h.store.getState().accept('chat');
  h.emit({ event: 'review_delivery_failed', review_id: 42 });
  assert.equal(h.store.getState().status, 'pending');
  h.options.delivery = 'sent';
  assert.equal(await h.store.getState().reject('diff'), true);
});
test('expired or replaced reviews discard queued decisions; stale events cannot resolve a new review', async () => {
  const h = harness(); h.options.delivery = 'queued';
  await h.store.getState().accept('chat');
  h.store.getState().expire('expired', 42);
  assert.equal(h.store.getState().status, 'expired');
  assert.equal(h.effects.discarded.at(-1), 42);
  h.show(43);
  h.emit({ event: 'review_resolved', review_id: 42 });
  h.store.getState().expire('old timeout', 42);
  assert.equal(h.store.getState().status, 'pending');
  assert.equal(h.effects.logs.length, 0);
});
test('a new review during fallback persistence prevents the old decision from being sent', async () => {
  const h = harness(), project = deferred(); h.show(42, true); h.options.project = () => project.promise;
  const action = h.store.getState().reject('diff');
  assert.equal(h.store.getState().status, 'submitting');
  assert.equal(await h.store.getState().accept('chat'), false);
  h.show(43); project.resolve({ revision: 2 });
  assert.equal(await action, false);
  assert.equal(h.effects.sent.length, 0);
  assert.equal(h.store.getState().reviewId, 43);
  assert.equal(h.store.getState().status, 'pending');
});
test('fallback persistence failure keeps the review retryable without sending', async () => {
  const h = harness(); h.show(42, true); h.options.project = () => Promise.reject(new Error('conflict'));
  assert.equal(await h.store.getState().reject('diff'), false);
  assert.equal(h.store.getState().status, 'pending');
  assert.equal(h.effects.sent.length, 0);
  assert.equal(h.effects.logs.length, 0);
});


test('sent decision waits for log persistence before announcing success', async () => {
  const h = harness(), log = deferred(); h.options.log = () => log.promise;
  const action = h.store.getState().accept('diff');
  assert.equal(h.effects.notices.filter(x => x.kind === 'success').length, 0);
  log.resolve({ success: true }); await action;
  assert.equal(h.effects.notices.filter(x => x.kind === 'success').length, 1);
  assert.match(h.effects.notices.at(-1).text, /评审已保存/);
});
test('a late log response cannot change or announce success for a newer review', async () => {
  const h = harness(), log = deferred(); h.options.log = () => log.promise;
  const action = h.store.getState().accept('diff');
  h.show(43); log.resolve({ success: true }); await action;
  assert.equal(h.store.getState().reviewId, 43);
  assert.equal(h.store.getState().status, 'pending');
  assert.equal(h.effects.notices.filter(x => x.kind === 'success').length, 0);
});
