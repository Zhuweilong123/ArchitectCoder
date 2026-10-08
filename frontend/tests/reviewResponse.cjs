const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const vm = require('node:vm');

function service() {
  const sockets = [];
  class WebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    static CLOSED = 3;
    readyState = 0;
    sent = [];
    constructor() { sockets.push(this); }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    close() { this.readyState = 3; }
    open() { this.readyState = 1; this.onopen(); }
  }
  const exports = {};
  const context = vm.createContext({
    exports, WebSocket, URLSearchParams, console,
    window: { location: { protocol: 'http:', host: 'localhost' } },
    localStorage: { getItem: () => 'review-test', setItem() {} },
    setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
  });
  const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/services/agentChat.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInContext(code, context);
  return { api: exports, sockets };
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
