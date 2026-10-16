const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const exportsObject = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(
  require.resolve('../src/components/AgentChat/chatHistoryRecovery.ts'), 'utf8',
), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
{ exports: exportsObject });
const { mergeChatHistory } = exportsObject;
const entry = (role, content, timestamp, message_id) => ({
  role, content, metadata: { timestamp, message_id },
});
const local = (id, role, content, timestamp = 0) => ({ id, role, content, timestamp });

test('restart recovers all turns from a cached suffix and retains tool steps', () => {
  const history = [entry('user', 'first', 10, 'u1'), entry('assistant', 'answer one', 20, 'a1'),
    { role: 'summary', content: 'internal checkpoint' },
    entry('user', 'second', 30, 'u2'), entry('assistant', 'answer two', 40, 'a2')];
  const cached = [local('user_30', 'user', 'second', 30),
    { ...local('agent_result_epoch_8', 'agent', 'answer two', 40), steps: [{ step: 1 }] }];
  const result = mergeChatHistory('session', history, cached);
  assert.deepEqual(Array.from(result, m => m.content), ['first', 'answer one', 'second', 'answer two']);
  assert.equal(result[0].timestamp, 10);
  assert.equal(result[3].id, 'agent_result_epoch_8');
  assert.equal(result[3].steps.length, 1);
  assert.deepEqual(Array.from(mergeChatHistory('session', history, result), m => m.id),
    Array.from(result, m => m.id));
});

test('identical requests in separate turns are not collapsed', () => {
  const history = [entry('user', 'check design', 10, 'u1'), entry('assistant', 'first check', 20, 'a1'),
    entry('user', 'check design', 30, 'u2'), entry('assistant', 'second check', 40, 'a2')];
  const result = mergeChatHistory('session', history,
    [local('user_30', 'user', 'check design', 30), local('agent_40', 'agent', 'second check', 40)]);
  assert.equal(result.length, 4);
  assert.equal(result[0].id, 'trace_u1');
  assert.equal(result[2].id, 'user_30');
});

test('completed durable answer replaces a cached streaming prefix', () => {
  const history = [entry('user', 'check', 10, 'u'), entry('assistant', 'full answer', 20, 'a')];
  const result = mergeChatHistory('session', history,
    [local('user', 'user', 'check', 10), local('stream_15', 'agent', 'full', 15)]);
  assert.equal(result.length, 2);
  assert.equal(result[1].content, 'full answer');
  assert.equal(result[1].id, 'trace_a');
});

test('recovery retains browser notices and messages sent during the request', () => {
  const history = [entry('user', 'check', 10000, 'u'), entry('assistant', 'answer', 20000, 'a')];
  const cached = [local('u', 'user', 'check', 10000), local('notice', 'system', 'review accepted', 15000),
    local('a', 'agent', 'answer', 20000), local('user_30000', 'user', 'check', 30000),
    local('stream_31000', 'agent', 'new partial', 31000)];
  assert.deepEqual(Array.from(mergeChatHistory('session', history, cached), m => m.content),
    ['check', 'review accepted', 'answer', 'check', 'new partial']);
});

test('new sessions and missing trace data retain the browser cache', () => {
  const cached = [local('user', 'user', 'new request')];
  assert.equal(mergeChatHistory('session', [], cached), cached);
});

test('older restored caches with synthetic load timestamps do not duplicate durable turns', () => {
  const history = [entry('user', 'question', 10, 'u'), entry('assistant', 'answer', 20, 'a')];
  const result = mergeChatHistory('session', history, [
    local('resume_99999_0', 'user', 'question', 99999),
    local('resume_99999_1', 'agent', 'answer', 99999),
  ]);
  assert.equal(result.length, 2);
  assert.equal(result[0].timestamp, 10);
});
