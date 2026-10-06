const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const ts = require('typescript');
const { JSDOM } = require('jsdom');

const renderingDom = new JSDOM('');
global.window = renderingDom.window;
global.document = renderingDom.window.document;
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
}).outputText, filename);
const { renderChatMarkdown, createChatReaderHtml, openChatReader } = require('../src/utils/chatReaderHtml.ts');
test.after(() => renderingDom.window.close());

test('reading page renders headings, nested lists, GFM tables, tasks and literal code', () => {
  const source = '# 设计方案\n\n**重点**与 *说明*\n\n- 一级\n  - 二级\n\n- [x] 已完成\n\n| 字段 | 含义 |\n| --- | --- |\n| id | 标识 |\n\n```ts\nconst text = "<script>";\n```\n\n[文档](https://example.com/docs)';
  const dom = new JSDOM(createChatReaderHtml(source, 'zh', 'agent_1'));
  const doc = dom.window.document;
  assert.equal(doc.querySelector('main h1').textContent, '设计方案');
  assert.equal(doc.querySelector('main strong').textContent, '重点');
  assert.equal(doc.querySelector('main ul ul li').textContent, '二级');
  assert.equal(doc.querySelector('main input').disabled, true);
  assert.equal(doc.querySelectorAll('main table th').length, 2);
  assert.equal(doc.querySelector('main pre code').textContent, 'const text = "<script>";\n');
  assert.equal(doc.querySelector('main a').rel, 'noopener noreferrer');
  assert.equal(doc.getElementById('copy').textContent, '复制原文');
  assert.equal(doc.getElementById('download').textContent, '下载 Markdown');
  dom.window.close();
});

test('untrusted replies cannot inject active markup or break out of the source JSON', () => {
  const source = '</script><script>window.pwned=true</script>\n\n<img src=x onerror="alert(1)"><iframe src="https://example.com"></iframe>\n\n[bad](javascript:alert%281%29) [file](file:///C:/secret) [local](../admin)\n\n<a href="javascript:alert(1)" onclick="alert(1)">raw link</a><input type="text" autofocus><style>body{display:none}</style>';
  const dom = new JSDOM(createChatReaderHtml(source, 'en', '../../reply'));
  const doc = dom.window.document;
  assert.equal(doc.querySelectorAll('script').length, 2);
  assert.equal(doc.querySelectorAll('main script, main img, main iframe, main style, main input').length, 0);
  assert.equal(doc.querySelectorAll('main [onclick], main [onerror], main [autofocus]').length, 0);
  assert.equal(doc.querySelectorAll('main a[href]').length, 0);
  const data = JSON.parse(doc.getElementById('source').textContent);
  assert.equal(data.content, source);
  assert.match(data.filename, /^ArchitectCoder-[a-zA-Z0-9_-]+\.md$/);
  const policy = doc.querySelector('meta[http-equiv="Content-Security-Policy"]').content;
  assert.match(policy, /script-src 'nonce-/);
  assert.match(policy, /connect-src 'none'/);
  dom.window.close();
});

function interactiveReader(source, clipboardWorks = true, legacyWorks = true) {
  const effects = { copied: null, legacy: null, downloaded: null, blob: null, timers: [], revoked: [] };
  const dom = new JSDOM(createChatReaderHtml(source, 'zh', 'agent_2'), {
    url: 'http://localhost/', runScripts: 'dangerously',
    beforeParse(win) {
      Object.defineProperty(win.navigator, 'clipboard', { value: {
        async writeText(text) { if (!clipboardWorks) throw new Error('Denied'); effects.copied = text; },
      } });
      win.document.execCommand = () => {
        effects.legacy = win.document.querySelector('textarea').value;
        return legacyWorks;
      };
      win.Blob = Blob;
      win.URL.createObjectURL = (blob) => { effects.blob = blob; return 'blob:reader-test'; };
      win.URL.revokeObjectURL = (url) => effects.revoked.push(url);
      win.setTimeout = (callback) => { effects.timers.push(callback); return effects.timers.length; };
      win.clearTimeout = () => {};
      win.HTMLAnchorElement.prototype.click = function () {
        effects.downloaded = { filename: this.download, href: this.href };
      };
    },
  });
  return { dom, effects };
}

test('copy and download preserve the exact Markdown, including Unicode, indentation and script-like text', async () => {
  const source = '# 原文\r\n\r\n```py\r\n\tprint("你好 </script>")\r\n```\r\n';
  const { dom, effects } = interactiveReader(source);
  const doc = dom.window.document;
  doc.getElementById('copy').click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(effects.copied, source);
  assert.equal(doc.getElementById('status').textContent, '已复制');
  doc.getElementById('download').click();
  assert.equal(await effects.blob.text(), source);
  assert.equal(effects.downloaded.filename, 'ArchitectCoder-agent_2.md');
  effects.timers.at(-1)();
  assert.deepEqual(effects.revoked, ['blob:reader-test']);
  dom.window.close();
});

test('copy falls back when clipboard access is denied and reports a real failure', async () => {
  for (const legacyWorks of [true, false]) {
    const { dom, effects } = interactiveReader('**原文**', false, legacyWorks);
    dom.window.document.getElementById('copy').click();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(effects.legacy, '**原文**');
    assert.equal(dom.window.document.querySelector('textarea'), null);
    assert.equal(dom.window.document.getElementById('copy').disabled, false);
    assert.match(dom.window.document.getElementById('status').textContent, legacyWorks ? /已复制/ : /复制失败/);
    dom.window.close();
  }
});

test('blocked popups are detected without creating a Blob URL', () => {
  const originalWindow = global.window;
  global.window = { open: () => null };
  try { assert.equal(openChatReader('# hello', 'en', 'agent_3'), false); }
  finally { global.window = originalWindow; }
});
