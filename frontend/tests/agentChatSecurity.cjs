const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

function chatHarness() {
  const sockets = [];
  const timers = new Map();
  const events = [];
  class Socket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.readyState = Socket.CONNECTING;
      this.sent = [];
      sockets.push(this);
    }
    send(message) { this.sent.push(JSON.parse(message)); }
    open() { this.readyState = Socket.OPEN; this.onopen(); }
    close(code = 1006) {
      this.readyState = Socket.CLOSED;
      this.onclose({ code, reason: '' });
    }
  }
  const stored = new Map();
  const context = {
    exports: {}, WebSocket: Socket, URLSearchParams, TextEncoder, btoa,
    console: { warn() {}, log() {}, error() {} },
    window: { location: { protocol: 'https:', host: 'architectcoder.example.com' } },
    localStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) },
    setTimeout: callback => { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id), setInterval: () => 1, clearInterval() {},
  };
  const source = fs.readFileSync(require.resolve('../src/services/agentChat.ts'), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(js, context);
  context.exports.onAgentMessage(event => events.push(event));
  return { api: context.exports, sockets, timers, events };
}

test('browser socket credentials stay out of URLs and survive reconnects', () => {
  const { api, sockets, timers } = chatHarness();
  const credential = 'unicode-凭证-with-long-test-value';
  api.connectAgentChat(() => {}, credential);
  const first = sockets[0];
  const url = new URL(first.url);
  assert.equal(url.protocol, 'wss:');
  assert.equal(url.searchParams.has('token'), false);
  assert.equal(url.searchParams.size, 1);
  assert.equal(first.protocols[0], 'architectcoder');
  assert.equal(Buffer.from(first.protocols[1].slice(5), 'base64url').toString('utf8'), credential);
  first.open();
  api.sendAgentMessage('authenticated task');
  assert.equal(first.sent[0].message, 'authenticated task');
  first.close(1006);
  assert.equal(timers.size, 1);
  [...timers.values()][0]();
  const second = sockets[1];
  assert.equal(second.url, first.url);
  assert.deepEqual(second.protocols, first.protocols);
});

test('authentication rejection is visible and stops automatic reconnect', () => {
  const { api, sockets, timers, events } = chatHarness();
  api.connectAgentChat(() => {}, 'invalid-token');
  sockets[0].close(1008);
  assert.equal(timers.size, 0);
  const closed = events.find(event => event.event === 'ws_closed');
  assert.match(closed.message, /访问凭证/);
  assert.equal(closed.message.includes('invalid-token'), false);
});

test('local socket without credentials still uses the public application protocol', () => {
  const { api, sockets } = chatHarness();
  api.connectAgentChat(() => {});
  assert.deepEqual(Array.from(sockets[0].protocols), ['architectcoder']);
  assert.equal(new URL(sockets[0].url).searchParams.has('token'), false);
});
