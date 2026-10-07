const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { searchTrace, groupTraceRows, traceErrorCount } = require('../src/components/TraceViewer/traceNavigation.ts');

test('search finds occurrences beyond truncation and inside nested subagents', () => {
  const longOutput = 'x'.repeat(8000) + 'Needle / needle';
  const hits = searchTrace([
    { id: 'turn-1', value: { message: '查找 Needle' } },
    { id: 'tool-1', value: { subagent: { items: [{ result: { observation: longOutput } }] } } },
  ], ' needle ');
  assert.equal(hits.length, 3);
  assert.deepEqual(hits.map(hit => hit.id), ['turn-1', 'tool-1', 'tool-1']);
  assert.equal(hits[1].field, 'subagent.items[0].result.observation');
  assert.equal(hits[1].offset, 8000);
  assert.equal(hits[1].text, longOutput);
  assert.equal(hits[2].text.slice(hits[2].offset, hits[2].offset + hits[2].length), 'needle');
});

test('search treats punctuation literally and handles empty and missing values', () => {
  const docs = [{ id: 'a', value: { args: { path: 'src/[task].ts' }, result: null } }];
  assert.equal(searchTrace(docs, '[task]').length, 1);
  assert.deepEqual(searchTrace(docs, '  '), []);
  assert.deepEqual(searchTrace(docs, 'missing'), []);
  assert.equal(searchTrace([{ id: 'zh', value: { text: '工具返回：校验失败' } }], '校验失败').length, 1);
});

test('process groups preserve assistant, error and turn boundaries', () => {
  const rows = ['user1', 'tool1', 'tool2', 'assistant', 'tool3', 'error', 'tool4', 'done', 'user2', 'tool5'];
  const groups = groupTraceRows(rows, row => row.startsWith('tool'));
  assert.deepEqual(groups, [['user1'], ['tool1', 'tool2'], ['assistant'], ['tool3'], ['error'], ['tool4'], ['done'], ['user2'], ['tool5']]);
  assert.deepEqual(groups.flat(), rows);
});

test('error navigation includes nested failures and blocked lifecycle events', () => {
  assert.equal(traceErrorCount({ kind: 'tool', result: { error: 'failed' }, subagent: { items: [
    { kind: 'llm', response: { error: 'timeout' } }, { kind: 'error', event: { event_type: 'error', message: 'error' } },
  ] } }), 3);
  assert.equal(traceErrorCount({ kind: 'lifecycle', event: { allowed: false, status: 'block' } }), 1);
  assert.equal(traceErrorCount({ kind: 'tool', result: { observation: 'normal result' } }), 0);
});
