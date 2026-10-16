const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { normalizeTraceEvent, normalizeTraceDetail, normalizeTraceReplay } = require('../src/utils/traceNormalization.ts');

test('trace decoder keeps legacy records and provider JSON metadata without mutating input', () => {
  const raw = { session_id: 's', events: [{ event_type: 'llm_response', content: 'answer', custom: { ids: [1, null] } }, null, []] };
  const detail = normalizeTraceDetail(raw);
  assert.equal(detail.events.length, 1);
  assert.equal(detail.events[0].span_id, '');
  assert.deepEqual(detail.events[0].custom, { ids: [1, null] });
  assert.equal(Object.hasOwn(raw.events[0], 'span_id'), false);
});

test('trace decoder narrows malformed display fields and keeps unknown event kinds', () => {
  const event = normalizeTraceEvent({ event_type: 'provider_custom', step: 'bad', actions: 'bad', tools: [null], usage: { prompt_tokens: 'bad' } });
  assert.equal(event.event_type, 'provider_custom');
  assert.equal(event.step, undefined);
  assert.equal(event.actions, undefined);
  assert.deepEqual(event.tools, []);
  assert.equal(event.usage.prompt_tokens, undefined);
  for (const value of [null, [], 'bad', {}, { event_type: 4 }]) assert.equal(normalizeTraceEvent(value), null);
});

test('trace decoder supports multipart model messages and complete tool schemas', () => {
  const event = normalizeTraceEvent({ event_type: 'llm_request', messages: [{ role: 'user', content: ['hello', { type: 'text', text: 'world' }, null] }],
    tools: [{ type: 'function', function: { name: 'read', parameters: { type: 'object', properties: { path: { type: 'string' } } } } }] });
  assert.deepEqual(event.messages[0].content.map(part => typeof part === 'string' ? part : part.text), ['hello', 'world']);
  assert.equal(event.tools[0].function.parameters.properties.path.type, 'string');
});

test('invalid trace detail envelopes fail before entering the viewer', () => {
  for (const value of [null, {}, { session_id: 's', events: {} }]) assert.throws(() => normalizeTraceDetail(value), /Invalid trace detail/);
});

const replay = () => ({ session_id: 's', mode: 'mock', turns: [{ user_message: 'question', final_answer: 'answer', recorded_answer: null, matches: false }], all_matched: false });

test('legacy replay counters and optional steps are normalized without changing answers', () => {
  const value = normalizeTraceReplay(replay());
  assert.equal(value.executed_turns, 1);
  assert.equal(value.turns[0].final_answer, 'answer');
  assert.equal(value.turns[0].recorded_answer, null);
  assert.equal(value.llm_calls, null);
});

test('malformed replay cache records are rejected instead of crashing step rendering', () => {
  for (const value of [{}, { ...replay(), mode: 'unsupported' }, { ...replay(), turns: [{}] },
    { ...replay(), turns: [{ ...replay().turns[0], matches: 'true' }] },
    { ...replay(), turns: [{ ...replay().turns[0], steps: {} }] }]) {
    assert.throws(() => normalizeTraceReplay(value), /Invalid replay/);
  }
});

test('replay steps retain raw string arguments and structured JSON arguments', () => {
  const input = replay();
  input.turns[0].steps = [{ step: 1, tool_calls: [{ name: 'a', arguments: '{"x":1}', observation: 'ok' }, { name: 'b', arguments: { x: 1 }, observation: 'ok' }] }];
  const value = normalizeTraceReplay(input);
  assert.equal(value.turns[0].steps[0].tool_calls[0].arguments, '{"x":1}');
  assert.deepEqual(value.turns[0].steps[0].tool_calls[1].arguments, { x: 1 });
  assert.deepEqual(value.turns[0].steps[0].actions, []);
});

const { buildTurns } = require('../src/components/TraceViewer/traceTimeline.ts');
function turns(events) { return buildTurns(normalizeTraceDetail({ session_id: 's', events }).events); }

test('orphan request and response spans stay separate when correlation IDs are missing', () => {
  const result = turns([{ event_type: 'llm_request', model: 'first' }, { event_type: 'llm_request', model: 'second' },
    { event_type: 'llm_response', content: 'uncorrelated' }]);
  assert.equal(result[0].items.length, 3);
  assert.equal(result[0].items[0].response, undefined);
  assert.equal(result[0].items[1].response, undefined);
  assert.equal(result[0].items[2].response.content, 'uncorrelated');
});

test('background events remain attached to their original run after the next user turn', () => {
  const result = turns([{ event_type: 'user_message', run_id: 'first', message: 'one' },
    { event_type: 'background_scheduled', run_id: 'first', payload: { task_id: 'bg', source_run_id: 'first', owner: 'memory' } },
    { event_type: 'user_message', run_id: 'second', message: 'two' },
    { event_type: 'background_result', background_task_id: 'bg', run_id: 'first', status: 'completed' }]);
  assert.equal(result[0].items[0].kind, 'background');
  assert.equal(result[0].items[0].event.status, 'completed');
  assert.equal(result[1].items.length, 0);
});

test('nullable legacy step lists and invalid non-JSON objects are handled explicitly', () => {
  const input = replay(); input.turns[0].steps = null;
  assert.equal(normalizeTraceReplay(input).turns[0].steps, undefined);
  assert.equal(normalizeTraceEvent(new Date()), null);
});
