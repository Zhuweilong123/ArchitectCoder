const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, filename);
};
const { createProjectHtml } = require('../src/utils/projectHtml.ts');
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400" viewBox="0 0 800 400"><text x="20" y="40">示例</text></svg>';
const project = { name: '示例项目', active_diagram_index: 1, diagrams: [
  { name: '总览', diagram_type: 'component' }, { name: '类设计', diagram_type: 'class' },
  { name: '交互', diagram_type: 'sequence' },
] };

function openViewer(html) {
  class Element {
    constructor() { this.events = {}; this.style = {}; this.attrs = {}; this.hidden = false; this.textContent = ''; this.clientWidth = 1000; this.clientHeight = 600; this.naturalWidth = 800; this.naturalHeight = 400; this.classList = { add() {}, remove() {} }; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this.attrs[name] = value; }
    setPointerCapture() {}
    getBoundingClientRect() { return { left: 0, top: 0 }; }
    set src(value) { this.source = value; this.events.load(); }
    get src() { return this.source; }
    fire(name, event = {}) { this.events[name](event); }
  }
  const ids = Object.fromEntries(['data', 'viewport', 'diagram', 'error', 'zoom', 'name', 'type', 'in', 'out', 'fit', 'reset'].map((id) => [id, new Element()]));
  ids.data.textContent = html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1];
  const entries = JSON.parse(ids.data.textContent).map(() => new Element());
  const script = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script, {
    document: { getElementById: (id) => ids[id], querySelectorAll: () => entries },
    ResizeObserver: class { observe() {} },
  });
  return { ids, entries };
}

test('single-file export includes every diagram and escapes untrusted names', () => {
  const attack = '</script><script>alert(1)</script><img src=x onerror=alert(2)>&"';
  const html = createProjectHtml({ ...project, name: attack, diagrams: [{ name: attack }] }, [svg], 'zh');
  assert.ok(!html.includes(attack));
  assert.ok(html.includes('&lt;/script&gt;'));
  const { ids } = openViewer(html);
  assert.equal(ids.name.textContent, attack);
  assert.equal(decodeURIComponent(ids.diagram.src.split(',')[1]), svg);
  assert.ok(html.includes("connect-src 'none'"));
  assert.equal((html.match(/<script/g) || []).length, 2);
  assert.throws(() => createProjectHtml(project, [svg], 'en'));
  assert.throws(() => createProjectHtml({ diagrams: [] }, [], 'en'));
});

test('viewer opens active diagram, switches all types, zooms, pans and resets', () => {
  const { ids, entries } = openViewer(createProjectHtml(project, [svg, svg, svg], 'zh'));
  assert.equal(ids.name.textContent, '类设计');
  assert.equal(entries[1].attrs['aria-pressed'], 'true');
  assert.equal(ids.error.hidden, true);
  entries[2].fire('click');
  assert.equal(ids.type.textContent, '时序图');
  assert.equal(entries[1].attrs['aria-pressed'], 'false');
  entries[0].fire('click');
  assert.equal(ids.type.textContent, '组件图');
  ids.in.fire('click');
  assert.equal(ids.zoom.textContent, '125%');
  ids.out.fire('click');
  assert.equal(ids.zoom.textContent, '100%');
  ids.viewport.fire('pointerdown', { pointerId: 1, button: 0, clientX: 100, clientY: 100 });
  ids.viewport.fire('pointermove', { pointerId: 1, clientX: 140, clientY: 160 });
  assert.equal(ids.diagram.style.transform, 'translate(140px,160px) scale(1)');
  ids.viewport.fire('pointerup');
  ids.reset.fire('click');
  assert.equal(ids.diagram.style.transform, 'translate(100px,100px) scale(1)');
  let prevented = false;
  ids.viewport.fire('wheel', { deltaY: -100, clientX: 200, clientY: 100, preventDefault() { prevented = true; } });
  assert.ok(prevented);
  assert.equal(ids.zoom.textContent, '122%');
  ids.viewport.clientWidth = 400;
  ids.fit.fire('click');
  assert.equal(ids.zoom.textContent, '44%');
  assert.equal(ids.diagram.style.transform, 'translate(24px,212px) scale(0.44)');
  ids.diagram.fire('error');
  assert.equal(ids.error.hidden, false);
  entries[1].fire('click');
  assert.equal(ids.error.hidden, true);
});

test('English UI and out-of-range active index are supported', () => {
  const html = createProjectHtml({ ...project, active_diagram_index: 99 }, [svg, svg, svg], 'en');
  assert.ok(html.includes('lang="en"'));
  assert.ok(html.includes('Fit to window'));
  assert.equal(openViewer(html).ids.type.textContent, 'Sequence diagram');
});
