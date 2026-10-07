const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const Module = require('node:module');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost', pretendToBeVisual: true });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'SVGElement', 'ShadowRoot', 'localStorage']) global[name] = dom.window[name];
const computedStyle = dom.window.getComputedStyle.bind(dom.window);
window.getComputedStyle = global.getComputedStyle = (element) => computedStyle(element);
global.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
global.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
global.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
const scrolled = [];
Element.prototype.scrollIntoView = function () { scrolled.push(this.id); };
for (const extension of ['.ts', '.tsx']) require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React, esModuleInterop: true },
}).outputText, filename);
require.extensions['.css'] = () => {};
const originalLoad = Module._load;
const events = [
  { event_type: 'user_message', message: '执行长任务' },
  { event_type: 'tool_call', span_id: 'tool-1', tool_name: 'inspect_project', arguments: { path: 'src' } },
  { event_type: 'tool_result', span_id: 'tool-1', observation: 'x'.repeat(8000) + '关键字' },
  { event_type: 'llm_request', span_id: 'llm-1', messages: [] },
  { event_type: 'llm_response', span_id: 'llm-1', content: '中途回复' },
  { event_type: 'tool_call', span_id: 'tool-2', tool_name: 'check_result', arguments: {} },
  { event_type: 'tool_result', span_id: 'tool-2', error: '校验失败' },
  { event_type: 'done', answer: '最终结果' },
];
Module._load = function (request, parent, isMain) {
  if (request === '../../services/api' && parent.filename.endsWith('TraceViewer.tsx')) return {
    listTraces: async () => [{ session_id: 'test-session', trace_type: 'chat', date: '2026-10-07', modified: '2026-10-07', events: events.length, size: 9000 }],
    getTrace: async () => ({ events }),
  };
  return originalLoad.call(this, request, parent, isMain);
};
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { useUiStore } = require('../src/stores/uiStore.ts');
const TraceViewer = require('../src/components/TraceViewer/TraceViewer.tsx').default;
Module._load = originalLoad;
const root = createRoot(document.getElementById('root'));
const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 80)); });
const button = (text) => [...document.querySelectorAll('button')].find(node => node.textContent.includes(text));

test('conversation folds processes; search reveals truncated output; error navigation opens its group', async () => {
  useUiStore.setState({ traceVisible: true, interfaceLanguage: 'zh' });
  await act(async () => { root.render(React.createElement(TraceViewer)); });
  await settle();
  assert.equal(document.querySelectorAll('.trace-process-group').length, 2);
  assert.equal(document.querySelectorAll('.trace-process-group .trace-tool').length, 0);
  assert.match(document.querySelector('.trace-timeline').textContent, /执行长任务.*中途回复.*最终结果/s);
  assert.match(document.querySelectorAll('.trace-process-group')[1].textContent, /1 处异常/);

  const input = document.querySelector('input[aria-label="搜索当前 Trace"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, '关键字');
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  await settle();
  assert.equal(document.querySelector('.trace-search-count').textContent, '1 / 1');
  assert.equal(document.querySelector('.trace-search-match mark').textContent, '关键字');
  assert.ok(document.getElementById('trace-item-0'));
  assert.ok(scrolled.includes('trace-item-0'));

  await act(async () => button('下一处错误').click());
  await settle();
  assert.ok(document.getElementById('trace-item-2'));
  assert.ok(scrolled.includes('trace-item-2'));

  await act(async () => button('收起过程').click());
  assert.equal(document.querySelectorAll('.trace-process-group .ant-collapse-content-active').length, 0);

  await act(async () => [...document.querySelectorAll('.ant-segmented-item')]
    .find(node => node.textContent === '详细时间线').click());
  assert.equal(document.querySelectorAll('.trace-process-group').length, 0);
  assert.equal(document.querySelectorAll('.trace-timeline .trace-tool').length, 2);
});

test.after(async () => { await act(async () => root.unmount()); dom.window.close(); });
