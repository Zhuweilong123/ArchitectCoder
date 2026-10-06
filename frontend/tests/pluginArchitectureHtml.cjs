const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, filename);
const { createPluginArchitectureHtml } = require('../src/components/PluginArchitecture/pluginArchitectureHtml.ts');
const { buildPluginGraph } = require('../src/components/PluginArchitecture/pluginGraph.ts');

const stages = ['initialize', 'prepare', 'run_start', 'round_before', 'model_before', 'model_after', 'tool_batch_before',
  'tool_before', 'tool_after', 'tool_batch_after', 'round_after', 'finalize', 'run_end'];
const contribution = (id, stage, plugin = 'trace', mode = 'observer') => ({
  id, stage, plugin, mode, handler: 'trace:observe', order: 1, priority: 3, before: [], after: [], scope: 'run', fail_closed: false,
});
const plan = {
  schema_version: 1, plan_id: 'html-fixture', dispatch: 'sequential',
  plugins: [
    { name: 'trace', provider: 'trace:create', source: '/extensions/trace/plugin.json', status: 'discovered', error: '',
      version: '1.2.3', revision: 'fingerprint', interfaces: ['create'], contributions: [] },
    { name: 'disabled', provider: 'missing:create', source: 'fixture', status: 'disabled', error: '', interfaces: ['query'], contributions: [] },
  ],
  stages: stages.map(stage => ({ stage, supported_modes: ['observer', 'service'], contributions: [contribution(`watch.${stage}`, stage)] })),
  notifications: [{ stage: 'error', supported_modes: ['observer'], contributions: [contribution('watch.error', 'error')] }],
};
const report = { plan_id: plan.plan_id, plugins: [{ name: 'trace', status: 'unavailable', version: '1.2.3', revision: 'fingerprint', diagnostics: [
  { plugin: 'trace', component: 'provider', phase: 'factory', code: 'factory_failed', message: 'database unavailable', version: '1.2.3', revision: 'fingerprint', source: 'fixture', provider: 'trace:create' },
] }] };
const payload = html => JSON.parse(html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1]);

function viewer(html) {
  const data = payload(html);
  const decode = text => text.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  class Element {
    constructor() { this.events = {}; this.attrs = {}; this.style = {}; this.clientWidth = 1000; this.clientHeight = 600; this.scrollLeft = 0; this.scrollTop = 0; this.markup = ''; this.children = []; }
    addEventListener(name, fn) { this.events[name] = fn; }
    setAttribute(key, value) { this.attrs[key] = value; }
    getAttribute(key) { return this.attrs[key]; }
    closest(selector) { return [...selector.matchAll(/\[([^\]]+)\]/g)].some(match => this.attrs[match[1]] !== undefined) ? this : null; }
    appendChild(child) { this.children.push(child); }
    setPointerCapture() {}
    scrollTo(first, top) { if (typeof first === 'object') { this.scrollLeft = first.left; this.scrollTop = first.top; } else { this.scrollLeft = first; this.scrollTop = top; } }
    set innerHTML(value) {
      this.markup = value;
      this.children = [];
      this.svg = new Element();
      this.nodes = [...value.matchAll(/data-node="([^"]+)"/g)].map(match => { const node = new Element(); node.attrs['data-node'] = decode(match[1]); return node; });
      this.toggles = [...value.matchAll(/data-toggle="([^"]+)"/g)].map(match => { const node = new Element(); node.attrs['data-toggle'] = decode(match[1]); return node; });
    }
    get innerHTML() { return this.markup; }
    querySelector() { return this.svg; }
    querySelectorAll() { return this.nodes; }
    fire(name, event = {}) { this.events[name](event); }
  }
  const ids = Object.fromEntries(['data', 'view', 'filter', 'canvas', 'viewport', 'detail', 'zoom', 'in', 'out', 'fit', 'reset', 'clear', 'search', 'results', 'issues', 'expand', 'collapse', 'overview', 'notifications'].map(id => [id, new Element()]));
  ids.data.textContent = JSON.stringify(data); ids.detail.innerHTML = '<p>Select a node</p>';
  const document = new Element(); document.getElementById = id => ids[id];
  document.createElement = () => new Element();
  const script = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(script, { document, ResizeObserver: class { observe() {} } });
  const click = id => document.fire('click', { target: ids.canvas.nodes.find(node => node.getAttribute('data-node') === id) });
  const link = id => { const target = new Element(); target.attrs['data-target'] = id; document.fire('click', { target }); };
  const toggle = name => { const target = new Element(); target.attrs['data-toggle'] = name; document.fire('click', { target }); };
  const focus = name => { const target = new Element(); target.attrs['data-focus'] = name; document.fire('click', { target }); };
  return { ids, document, click, link, toggle, focus, Element };
}

test('single offline file includes both views, all filters, notifications and matching load diagnostics', () => {
  const html = createPluginArchitectureHtml(plan, { loadReport: report });
  const data = payload(html);
  const { ids } = viewer(html);
  for (const view of ['organization', 'schedule']) for (const filter of ['', 'core', 'trace', 'disabled']) {
    ids.view.value = view; ids.filter.value = filter; ids.view.fire('change');
    const expected = buildPluginGraph(plan, view, filter, { expandedPlugins: [] });
    assert.deepEqual(ids.canvas.nodes.map(node => node.attrs['data-node']), expected.nodes.map(node => node.id));
  }
  assert.ok(html.includes("connect-src 'none'"));
  assert.ok(!/<(?:script|link)[^>]+(?:src|href)=/.test(html));
  const details = new Map(data.details);
  assert.ok(details.get('plugin:trace').includes('database unavailable'));
  assert.ok(details.get('plugin:trace').includes('1.2.3'));
  assert.ok(details.get('plugin:trace').includes('fingerprint'));
  assert.ok(details.has('contribution:watch.error'));
  assert.equal(data.issues.length, 1);
  const stale = createPluginArchitectureHtml(plan, { loadReport: { ...report, plan_id: 'old' } });
  assert.ok(!stale.includes('database unavailable'));
});

test('untrusted plan text cannot escape the JSON, SVG or detail markup', () => {
  const attack = '</script><script>alert(1)</script><img src=x onerror=alert(2)>&"';
  const malicious = structuredClone(plan);
  malicious.plan_id = attack; malicious.plugins[0].name = attack;
  malicious.plugins[0].error = attack; malicious.plugins[0].source = attack;
  malicious.stages[0].contributions[0].id = attack;
  malicious.stages[0].contributions[0].handler = attack;
  const html = createPluginArchitectureHtml(malicious, { view: 'organization' });
  assert.ok(!html.includes(attack));
  assert.equal((html.match(/<script/g) || []).length, 2);
  const data = payload(html);
  assert.ok(html.includes('&lt;/script&gt;'));
  assert.ok(!/<img\b/.test(html));
  assert.ok(new Map(data.details).get(`plugin:${attack}`).includes('&lt;img'));
  viewer(html); // Embedded viewer script remains valid even with hostile labels.
});

test('viewer retains initial view/filter, switches graphs and selects nodes by pointer and keyboard', () => {
  const { ids, click, link } = viewer(createPluginArchitectureHtml(plan, { view: 'schedule', filter: 'trace', expandedPlugins: ['trace'] }));
  assert.equal(ids.view.value, 'schedule'); assert.equal(ids.filter.value, 'trace');
  click('contribution:watch.model_before');
  assert.ok(ids.detail.innerHTML.includes('trace:observe'));
  assert.equal(ids.canvas.nodes.find(node => node.attrs['data-node'] === 'contribution:watch.model_before').attrs['aria-pressed'], 'true');
  link('plugin:trace');
  assert.equal(ids.view.value, 'organization'); assert.equal(ids.filter.value, 'trace');
  assert.ok(ids.detail.innerHTML.includes('1.2.3'));
  ids.filter.value = 'disabled'; ids.filter.fire('change');
  assert.deepEqual(ids.canvas.nodes.map(node => node.attrs['data-node']), ['plugin:disabled']);
  let prevented = false;
  ids.canvas.fire('keydown', { key: 'Enter', target: ids.canvas.nodes[0], preventDefault() { prevented = true; } });
  assert.ok(prevented); assert.ok(ids.detail.innerHTML.includes('missing:create'));
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'interface:disabled:0'));
  link('contribution:watch.error');
  assert.ok(ids.detail.innerHTML.includes('watch.error'));
  ids.clear.fire('click'); assert.equal(ids.detail.innerHTML, '<p>Select a node</p>');
});

test('viewer supports zoom, fit width and background drag without panning node clicks', () => {
  const { ids, Element } = viewer(createPluginArchitectureHtml(plan));
  const initial = ids.zoom.textContent;
  ids.in.fire('click'); assert.notEqual(ids.zoom.textContent, initial);
  ids.reset.fire('click'); assert.equal(ids.zoom.textContent, '100%');
  ids.out.fire('click'); assert.equal(ids.zoom.textContent, '90%');
  ids.viewport.clientWidth = 500; ids.fit.fire('click'); assert.equal(ids.zoom.textContent, '46%');
  ids.viewport.scrollLeft = 50; ids.viewport.scrollTop = 100;
  ids.viewport.fire('pointerdown', { button: 0, target: new Element(), pointerId: 1, clientX: 100, clientY: 100 });
  ids.viewport.fire('pointermove', { clientX: 80, clientY: 60 });
  assert.equal(ids.viewport.scrollLeft, 70); assert.equal(ids.viewport.scrollTop, 140);
  ids.viewport.fire('pointercancel');
  ids.viewport.fire('pointerdown', { button: 0, target: ids.canvas.nodes[0], pointerId: 1, clientX: 100, clientY: 100 });
  ids.viewport.fire('pointermove', { clientX: 0, clientY: 0 });
  assert.equal(ids.viewport.scrollTop, 140);
});

test('English labels and invalid initial filters are handled', () => {
  const html = createPluginArchitectureHtml(plan, { language: 'en', filter: 'gone' });
  assert.ok(html.includes('lang="en"')); assert.ok(html.includes('Offline architecture snapshot'));
  assert.ok(html.includes('Fit width')); assert.equal(payload(html).filter, '');
  assert.ok(html.includes('Model before'));
});

test('folds, drill-down and overview share expansion state across views', () => {
  const { ids, toggle, focus } = viewer(createPluginArchitectureHtml(plan));
  assert.equal(ids.view.value, 'schedule');
  assert.ok(!ids.canvas.nodes.some(node => node.attrs['data-node'].startsWith('contribution:')));
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'group:prepare:trace'));
  toggle('trace');
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'contribution:watch.prepare'));
  ids.view.value = 'organization'; ids.view.fire('change');
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'contribution:watch.prepare'));
  toggle('trace');
  assert.ok(!ids.canvas.nodes.some(node => node.attrs['data-node'].startsWith('contribution:')));
  focus('trace');
  assert.equal(ids.filter.value, 'trace'); assert.ok(ids.detail.innerHTML.includes('domain') || ids.detail.innerHTML.includes('领域接口'));
  ids.overview.fire('click');
  assert.equal(ids.view.value, 'schedule'); assert.equal(ids.filter.value, '');
  assert.ok(!ids.canvas.nodes.some(node => node.attrs['data-node'].startsWith('contribution:')));
  ids.expand.fire('click');
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'contribution:watch.prepare'));
  ids.collapse.fire('click');
  assert.ok(!ids.canvas.nodes.some(node => node.attrs['data-node'].startsWith('contribution:')));
});

test('search reveals folded interfaces and notifications, and handles empty results', () => {
  const { ids } = viewer(createPluginArchitectureHtml(plan, { filter: 'disabled', query: 'watch.model_before' }));
  assert.equal(ids.search.value, 'watch.model_before');
  assert.equal(ids.results.children[1].value, 'contribution:watch.model_before');
  ids.results.value = ids.results.children[1].value; ids.results.fire('change');
  assert.equal(ids.filter.value, '');
  assert.equal(ids.canvas.nodes.find(node => node.attrs['data-node'] === 'contribution:watch.model_before').attrs['aria-pressed'], 'true');
  ids.search.value = 'watch.error'; ids.search.fire('input');
  ids.results.value = 'contribution:watch.error'; ids.results.fire('change');
  assert.ok(ids.detail.innerHTML.includes('watch.error')); assert.equal(ids.notifications.open, true);
  ids.search.value = 'query disabled'; ids.search.fire('input');
  assert.equal(ids.results.children[1].value, 'interface:disabled:0');
  ids.results.value = ids.results.children[1].value; ids.results.fire('change');
  assert.equal(ids.view.value, 'organization');
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'interface:disabled:0'));
  ids.search.value = 'no such interface'; ids.search.fire('input');
  assert.equal(ids.results.children.length, 1); assert.equal(ids.results.children[0].textContent, '没有匹配的节点');
  ids.search.value = ''; ids.search.fire('input'); assert.equal(ids.results.hidden, true);
  ids.filter.value = 'disabled'; ids.filter.fire('change');
  ids.results.value = 'stage:initialize'; ids.results.fire('change');
  assert.equal(ids.view.value, 'schedule'); assert.equal(ids.filter.value, '');
  assert.equal(ids.canvas.nodes.find(node => node.attrs['data-node'] === 'stage:initialize').attrs['aria-pressed'], 'true');
});

test('load issue locator reveals plugin diagnostics from a folded or filtered view', () => {
  const { ids } = viewer(createPluginArchitectureHtml(plan, { filter: 'disabled', loadReport: report }));
  const issue = payload(createPluginArchitectureHtml(plan, { loadReport: report })).issues[0];
  ids.issues.value = issue.id; ids.issues.fire('change', { target: ids.issues });
  assert.equal(ids.view.value, 'organization'); assert.equal(ids.filter.value, '');
  assert.ok(ids.detail.innerHTML.includes('database unavailable'));
  assert.equal(ids.canvas.nodes.find(node => node.attrs['data-node'] === 'plugin:trace').attrs['aria-pressed'], 'true');
  assert.ok(ids.canvas.innerHTML.includes('!1'));
  const stale = payload(createPluginArchitectureHtml(plan, { loadReport: { ...report, plan_id: 'stale' } }));
  assert.equal(stale.issues.length, 0);
});

test('production-minified export keeps its embedded layout and search dependencies', () => {
  const esbuild = require('esbuild');
  const bundle = esbuild.buildSync({ entryPoints: [require.resolve('../src/components/PluginArchitecture/pluginArchitectureHtml.ts')],
    bundle: true, write: false, format: 'cjs', platform: 'browser', minify: true, target: 'es2020' });
  const module = { exports: {} };
  vm.runInNewContext(bundle.outputFiles[0].text, { module, exports: module.exports });
  const { ids, toggle } = viewer(module.exports.createPluginArchitectureHtml(plan));
  toggle('trace');
  assert.ok(ids.canvas.nodes.some(node => node.attrs['data-node'] === 'contribution:watch.prepare'));
  ids.search.value = 'watch.error'; ids.search.fire('input');
  assert.equal(ids.results.children[1].value, 'contribution:watch.error');
});
