const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const vm = require('node:vm');

function service(options = {}) {
  const sockets = [];
  const timers = [];
  class WebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    static CLOSED = 3;
    readyState = 0;
    sent = [];
    constructor() { if (options.failCreate) throw new Error('connection unavailable'); sockets.push(this); }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    close() { this.readyState = 3; }
    open() { this.readyState = 1; this.onopen(); }
  }
  const exports = {};
  const context = vm.createContext({
    exports, WebSocket, URLSearchParams, console,
    window: { location: { protocol: 'http:', host: 'localhost' } },
    localStorage: { getItem: () => 'review-test', setItem() {} },
    setInterval: () => 1, clearInterval() {}, setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
  });
  const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/services/agentChat.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInContext(code, context);
  return { api: exports, sockets, timers };
}

for (const decision of ['accept', 'reject']) {
  for (const queued of [false, true]) {
    test(`${decision} preserves multiline review feedback ${queued ? 'after reconnect' : 'on live connection'}`, () => {
      const { api, sockets } = service();
      const comment = '补充泊车超时分支。\n保留现有调用顺序。';
      if (!queued) {
        api.connectAgentChat(() => {});
        sockets[0].open();
      }
      assert.equal(api.sendReviewResponse(17, comment, decision), queued ? 'queued' : 'sent');
      if (queued) sockets[0].open();
      assert.equal(sockets[0].sent.length, 1);
      assert.equal(sockets[0].sent[0].feedback, comment);
      assert.equal(sockets[0].sent[0].response, comment);
      assert.equal(sockets[0].sent[0].decision, decision);
      assert.equal(sockets[0].sent[0].review_id, 17);
      api.disconnectAgentChat();
    });
  }
}


test('live send exception returns failed and retains the review for retry', () => {
  const { api, sockets } = service(), events = [];
  api.onAgentMessage(event => events.push(event));
  api.connectAgentChat(() => {}); sockets[0].open(); api.setPendingUmlReviewId(42);
  const send = sockets[0].send;
  sockets[0].send = () => { throw new Error('socket unavailable'); };
  assert.equal(api.sendReviewResponse(42, 'accept', 'accept'), 'failed');
  assert.equal(api.getPendingUmlReviewId(), 42);
  assert.equal(events.filter(e => e.event === 'review_resolved').length, 0);
  sockets[0].send = send;
  assert.equal(api.sendReviewResponse(42, 'accept', 'accept'), 'sent');
  assert.equal(events.filter(e => e.event === 'review_resolved').length, 1);
  api.disconnectAgentChat();
});
test('connection creation failure returns failed instead of throwing', () => {
  const { api } = service({ failCreate: true });
  api.setPendingUmlReviewId(42);
  assert.equal(api.sendReviewResponse(42, 'accept', 'accept'), 'failed');
  assert.equal(api.getPendingUmlReviewId(), 42);
});
test('queued send exception notifies failure and permits a fresh retry', () => {
  const { api, sockets } = service(), events = [];
  api.onAgentMessage(event => events.push(event));
  api.setPendingUmlReviewId(42);
  assert.equal(api.sendReviewResponse(42, 'accept', 'accept'), 'queued');
  const send = sockets[0].send;
  sockets[0].send = () => { throw new Error('socket unavailable'); };
  sockets[0].open();
  assert.equal(events.filter(e => e.event === 'review_resolved').length, 0);
  assert.equal(events.filter(e => e.event === 'review_delivery_failed').length, 1);
  assert.equal(api.getPendingUmlReviewId(), 42);
  sockets[0].send = send;
  assert.equal(api.sendReviewResponse(42, 'accept', 'accept'), 'sent');
  api.disconnectAgentChat();
});
test('discarded queued reviews are never sent on reconnect', () => {
  const { api, sockets } = service();
  api.sendReviewResponse(42, 'accept', 'accept');
  api.discardReviewResponse(42); sockets[0].open();
  assert.equal(sockets[0].sent.length, 0);
  api.disconnectAgentChat();
});
test('transient reconnect keeps the queued reply until a successful send', () => {
  const { api, sockets, timers } = service();
  api.connectAgentChat(() => {}, undefined, false);
  api.sendReviewResponse(42, 'accept', 'accept');
  sockets[0].close(); sockets[0].onclose({ code: 1006 });
  timers.shift()(); sockets[1].open();
  assert.equal(sockets[1].sent.length, 1);
  assert.equal(sockets[1].sent[0].review_id, 42);
  api.disconnectAgentChat();
});
test('authentication rejection reports queued delivery failure', () => {
  const { api, sockets, timers } = service(), events = [];
  api.connectAgentChat(() => {}, undefined, false);
  api.onAgentMessage(event => events.push(event));
  api.sendReviewResponse(42, 'accept', 'accept');
  sockets[0].close(); sockets[0].onclose({ code: 1008 });
  assert.equal(events.filter(e => e.event === 'review_delivery_failed').length, 1);
  assert.equal(timers.length, 0);
  api.disconnectAgentChat();
});
test('reconnect exhaustion reports failure and does not later resend the stale reply', () => {
  const { api, sockets, timers } = service(), events = [];
  api.connectAgentChat(() => {}, undefined, false);
  api.onAgentMessage(event => events.push(event));
  api.sendReviewResponse(42, 'accept', 'accept');
  for (let attempt = 0; attempt <= 5; attempt++) {
    sockets[attempt].close(); sockets[attempt].onclose({ code: 1006 });
    if (attempt < 5) timers.shift()();
  }
  assert.equal(events.filter(e => e.event === 'review_delivery_failed').length, 1);
  api.connectAgentChat(() => {}); sockets.at(-1).open();
  assert.equal(sockets.at(-1).sent.length, 0);
  api.disconnectAgentChat();
});
