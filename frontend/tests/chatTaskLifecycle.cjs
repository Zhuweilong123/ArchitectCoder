const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function harness() {
  const state = { busy: false, messages: [] };
  const review = { status: 'pending', reviewId: 42, clear() { this.status = 'idle'; } };
  const exports = {};
  const source = fs.readFileSync(require.resolve('../src/components/AgentChat/agentChatEventHandler.ts'), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, {
    exports,
    require: name => name.includes('diagramStore')
      ? { useDiagramStore: { getState: () => ({ endBatch() {} }) } }
      : name.includes('reviewStore')
        ? { useReviewStore: { getState: () => review } } : {},
  });
  const noop = () => {};
  const handle = exports.createAgentChatEventHandler({
    setMessages: update => { state.messages = typeof update === 'function' ? update(state.messages) : update; },
    setBusy: value => { state.busy = value; },
    setCurrentSteps: noop, setCurrentTodos: noop, setTodoPlanningMode: noop,
    setStrategyAdvised: noop, setTodoExpanded: noop,
    liveStepsRef: { current: [] }, liveTodosRef: { current: [] },
    todoSeenInTaskRef: { current: false }, settleTodos: () => [], handleDesignElement: noop,
  });
  return { state, review, handle };
}

test('session sync restores running state and retains the current pending review', () => {
  const { state, review, handle } = harness();
  handle({ event: 'session_sync', running: true, stopping: false, pending_review_ids: [42] });
  assert.equal(state.busy, true);
  assert.equal(review.status, 'pending');
  handle({ event: 'session_sync', running: false, stopping: false, pending_review_ids: [] });
  assert.equal(state.busy, false);
  assert.equal(review.status, 'idle');
});

test('terminal snapshot on page refresh does not duplicate the persisted result', () => {
  const { state, handle } = harness();
  const done = { event: 'done', event_epoch: 'epoch', event_seq: 10,
    result: 'finished', checkpoint: { status: 'completed' } };
  handle(done);
  handle(done);
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].content, 'finished');
  assert.equal(state.busy, false);
});

test('rejected duplicate chat does not clear an active task', () => {
  const { state, handle } = harness();
  handle({ event: 'run_started', run_id: 'run', status: 'running' });
  handle({ event: 'error', message: 'Already running', running: true });
  assert.equal(state.busy, true);
  assert.equal(state.messages.length, 1);
});
