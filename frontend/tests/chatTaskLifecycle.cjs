const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function harness() {
  const state = { busy: false, messages: [], recoveries: 0 };
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
    onSessionSync: () => { state.recoveries++; },
    setMessages: update => { state.messages = typeof update === 'function' ? update(state.messages) : update; },
    setBusy: value => { state.busy = value; },
    setCurrentSteps: value => { state.steps = value; }, setCurrentTodos: noop, setTodoPlanningMode: noop,
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
  assert.equal(state.recoveries, 2);
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

test('reconnect restores the full task history and merges the next progress by step', () => {
  const { state, handle } = harness();
  const steps = Array.from({ length: 29 }, (_, index) => ({
    event: 'progress', step: index + 1, tool_calls_detail: [], actions: ['read_file'],
  }));
  handle({ event: 'progress_snapshot', run_id: 'run', steps });
  handle(steps[28]);
  assert.equal(state.steps.length, 29);
  handle({ ...steps[28], step: 30 });
  assert.equal(state.steps.length, 30);
  handle({ event: 'done', result: 'finished', checkpoint: { status: 'partial' } });
  assert.equal(state.messages[0].steps.length, 30);
});

test('snapshot repairs the steps of an already displayed terminal result', () => {
  const { state, handle } = harness();
  handle({ event: 'done', event_epoch: 'epoch', event_seq: 10, result: 'finished' });
  handle({ event: 'progress_snapshot', run_id: 'run', terminal_result_id: 'agent_result_epoch_10',
    steps: [{ event: 'progress', step: 1 }, { event: 'progress', step: 2 }] });
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].steps.length, 2);
});

test('terminal replay after loading durable history updates identity without duplicating the reply', () => {
  const { state, handle } = harness();
  state.messages = [{ id: 'trace_saved', role: 'agent', content: 'finished', timestamp: 1 }];
  handle({ event: 'done', event_epoch: 'epoch', event_seq: 10, result: 'finished' });
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].id, 'agent_result_epoch_10');
});


test('a late review expiry cannot stop a newer task or expire its current review', () => {
  const { state, review, handle } = harness();
  review.expire = () => { throw new Error('must not expire the current review'); };
  handle({ event: 'run_started', run_id: 'new', status: 'running' });
  handle({ event: 'review_expired', review_id: 41 });
  handle({ event: 'review_timeout', review_id: 41, title: 'old', timeout: 60 });
  assert.equal(state.busy, true);
  assert.equal(review.status, 'pending');
  assert.equal(state.messages.length, 0);
});
