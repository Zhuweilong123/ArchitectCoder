import type { PluginExecutionPlan, PluginLoadReport } from '../../types/plugins';
import { buildPluginGraph, wrapGraphLabel, type GraphView, type PlanNode } from './pluginGraph';
import { graphCatalog, graphIssues, revealGraphNode, searchGraph } from './graphExplorer';
import { COLORS, STAGE_LABELS, edgePath } from './graphPresentation';
import { renderGraphSvg, type GraphPresentation } from './graphSvg';

interface ExportOptions {
  language?: string; view?: GraphView; filter?: string; loadReport?: PluginLoadReport | null;
  expandedPlugins?: string[]; query?: string;
}
const escapeHtml = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]!);

/** Single offline viewer using the same layout/search functions as the application. */
export function createPluginArchitectureHtml(plan: PluginExecutionPlan, options: ExportOptions = {}): string {
  const en = options.language === 'en';
  const tx = (zh: string, english: string) => en ? english : zh;
  const stageLabel = (key: string) => STAGE_LABELS[key]?.[en ? 1 : 0] || key;
  const labels: GraphPresentation = {
    stages: Object.fromEntries(Object.entries(STAGE_LABELS).map(([key, value]) => [key, value[en ? 1 : 0]])),
    modes: { observer: tx('观察', 'Observer'), transform: tx('数据处理', 'Transform'), control: tx('控制', 'Control'), service: tx('按需接口执行', 'On-demand service') },
    statuses: { discovered: tx('已发现', 'Discovered'), disabled: tx('已禁用', 'Disabled'), unavailable: tx('不可用', 'Unavailable'), loaded: tx('加载成功', 'Loaded'), not_loaded: tx('尚未实例化', 'Not instantiated') },
    colors: COLORS, branches: { continue: tx('继续下一轮', 'Next round'), 'no-tools': tx('无工具调用', 'No tools'), finish: tx('返回最终结果', 'Finalize'), blocked: tx('调用被阻断', 'Blocked'), end: tx('进入任务结束', 'Run end') },
    columns: [tx('插件', 'Plugins'), tx('贡献 / 领域接口', 'Contributions / interfaces'), tx('生命周期阶段', 'Lifecycle stages')],
    contributionUnit: tx('个贡献', 'contributions'), interfaceCaption: tx('领域接口 · 未安装绑定', 'Domain interface · binding not installed'),
    executionCaption: tx('核心执行节点', 'Core execution'), expand: tx('展开', 'Expand'), collapse: tx('收起', 'Collapse'),
    organization: tx('插件组织图', 'Plugin organization graph'), schedule: tx('生命周期调度图', 'Lifecycle schedule graph'), issues: {},
  };
  const catalog = graphCatalog(plan);
  const issues = graphIssues(plan, options.loadReport);
  for (const issue of issues) labels.issues[issue.nodeId] = (labels.issues[issue.nodeId] || 0) + 1;
  const filters = ['', 'core', ...plan.plugins.map((plugin) => plugin.name)];
  const filter = filters.includes(options.filter || '') ? options.filter || '' : '';
  const view = options.view === 'organization' ? 'organization' : 'schedule';
  const expandedPlugins = (options.expandedPlugins || []).filter((name) => filters.includes(name) && name !== '');
  const loadReport = options.loadReport?.plan_id === plan.plan_id ? options.loadReport : null;
  const link = (id: string, label: string) => `<button class="link" data-target="${escapeHtml(id)}">${escapeHtml(label)}</button>`;
  const rowsHtml = (rows: Array<[string, unknown]>) => `<dl>${rows.filter(([, value]) => value !== undefined && value !== '').map(([key, value]) =>
    `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(Array.isArray(value) ? value.join(', ') : value)}</dd>`).join('')}</dl>`;
  function nodeDetails(node: PlanNode): string {
    const title = node.kind === 'stage' || node.kind === 'execution' ? stageLabel(node.label) : node.label;
    let html = `<h2>${escapeHtml(title)}</h2>`;
    const plugin = node.plugin || plan.plugins.find((item) => item.name === node.contribution?.plugin);
    if (plugin) {
      const runtime = loadReport?.plugins.find((item) => item.name === plugin.name);
      html += rowsHtml([
        [tx('状态', 'Status'), labels.statuses[plugin.status]], ['Provider', plugin.provider || tx('核心内置', 'Built in')],
        [tx('来源', 'Source'), plugin.source], [tx('版本', 'Version'), plugin.version], [tx('内容指纹', 'Content fingerprint'), plugin.revision],
        [tx('能力槽位', 'Capability slot'), plugin.slot], [tx('必需插件', 'Required plugins'), plugin.dependencies],
        [tx('可选插件', 'Optional plugins'), plugin.optional_dependencies], [tx('插件参数', 'Plugin parameters'), plugin.config_keys],
        [tx('最近实例加载', 'Latest instance load'), runtime ? labels.statuses[runtime.status] || runtime.status : undefined],
      ]);
      const diagnostics = [...(plugin.diagnostics || []), ...(runtime?.diagnostics || [])];
      diagnostics.forEach((item) => { html += `<div class="error"><strong>${escapeHtml(`${item.component} · ${item.phase} · ${item.code}`)}</strong><p>${escapeHtml(item.message)}</p></div>`; });
      if (plugin.error && !diagnostics.length) html += `<div class="error">${escapeHtml(plugin.error)}</div>`;
    }
    if (node.contribution) {
      const item = node.contribution;
      html += rowsHtml([
        [tx('插件接口', 'Plugin interface'), item.interface_id], [tx('所属插件', 'Plugin'), item.plugin],
        [item.mode === 'service' ? tx('默认阶段', 'Default phase') : tx('阶段', 'Stage'), `${stageLabel(item.stage)} (${item.stage})`],
        [tx('类型', 'Mode'), labels.modes[item.mode]], ['Handler', item.handler],
        [tx('阶段内顺序', 'Stage order'), item.mode === 'service' ? tx('按请求匹配，继承当前操作阶段', 'Matched on demand; inherits the active phase') : item.order],
        [tx('声明优先级', 'Priority'), item.priority], [tx('先于', 'Before'), item.before], [tx('后于', 'After'), item.after],
        [tx('作用域', 'Scope'), item.scope], [tx('接口异常时', 'On failure'), item.mode === 'service' ? tx('记录并交给领域调用方处理', 'Record and propagate to the domain caller') : item.fail_closed ? tx('阻断执行', 'Block execution') : tx('记录并继续', 'Record and continue')],
      ]);
      html += link(`plugin:${item.plugin}`, item.plugin);
    }
    if (node.stage) {
      html += rowsHtml([[tx('允许的外部接口类型', 'Supported external modes'), node.stage.supported_modes.map((mode) => labels.modes[mode])]]);
      html += `<h3>${tx('本阶段接口（按执行顺序）', 'Stage contributions in execution order')}</h3>`;
      html += node.stage.contributions.slice().sort((a, b) => a.order - b.order).map((item) => link(`contribution:${item.id}`, `${item.order}. ${item.id}`)).join('') || `<p>${tx('没有贡献接口', 'No contributions')}</p>`;
    }
    if (node.kind === 'plugin' && plugin) {
      html += `<button data-toggle="${escapeHtml(plugin.name)}">${tx('展开／收起插件', 'Expand / collapse plugin')}</button>`;
      html += `<button data-focus="${escapeHtml(plugin.name)}">${tx('仅看此插件', 'Focus plugin')}</button>`;
      const bindings = catalog.filter((item) => item.contribution?.plugin === plugin.name || item.kind === 'interface' && item.plugin?.name === plugin.name);
      html += `<p>${plugin.interfaces.length} ${tx('个领域接口；关联阶段与贡献：', 'domain interfaces. Associated phases and contributions:')}</p>`;
      html += bindings.map((item) => link(item.id, `${item.contribution ? stageLabel(item.contribution.stage) + ' · ' : ''}${item.label}`)).join('');
      html += `<p>${tx('领域接口按请求执行，继承当前操作阶段。发现成功不代表已经实例化或通过健康检查。', 'Domain interfaces execute on demand and inherit the active phase. Discovery does not imply instantiation or health checks.')}</p>`;
    }
    if (node.kind === 'interface') html += `<p>${tx('该接口尚未安装可用阶段绑定，请查看插件状态和声明。', 'No active binding is installed. Check the plugin status and declaration.')}</p>`;
    return html;
  }
  const details = catalog.map((node) => [node.id, nodeDetails(node)]);
  const initial = buildPluginGraph(plan, view, filter, { expandedPlugins });
  const notificationHtml = (plan.notifications || []).map((stage) => `<section><h3>${escapeHtml(stageLabel(stage.stage))}</h3>${stage.contributions.map((item) => link(`contribution:${item.id}`, `${item.plugin} · ${item.id}`)).join('') || `<p>${tx('没有贡献接口', 'No contributions')}</p>`}</section>`).join('');
  const payload = JSON.stringify({ plan, catalog, details, labels, issues, view, filter, expandedPlugins, query: options.query || '',
    noMatches: tx('没有匹配的节点', 'No matching nodes'), sources: { declaration: tx('声明失败', 'Declaration'), load: tx('加载失败', 'Load') } })
    .replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  const hint = tx('从总览进入插件详情。搜索和异常定位会展开目标；拖动空白区域平移。', 'Explore the main flow, then drill into plugins. Search and issues reveal targets. Drag empty space to pan.');
  return `<!doctype html>
<html lang="${en ? 'en' : 'zh-CN'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>ArchitectCoder · ${tx('插件架构', 'Plugin architecture')}</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,-apple-system,'Segoe UI',sans-serif;color:#263449;background:#f4f7fb}header{background:white;padding:18px 24px;border-bottom:1px solid #dde4ed}h1{font-size:21px;margin:0 0 8px}p{line-height:1.6}header p{margin:6px 0;color:#637188}code{overflow-wrap:anywhere;font-size:11px}.tools,.roster{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 24px;background:white;border-bottom:1px solid #dde4ed}button,select,input{font:inherit;color:inherit;background:white;border:1px solid #cfd9e6;border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{background:#eff5ff}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid #1677ff;outline-offset:2px}select{max-width:270px}input{width:250px}output{min-width:48px;text-align:center}.legend{display:flex;gap:10px;flex-wrap:wrap;margin-left:auto}.legend span:before{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--color);margin-right:5px}.main{display:grid;grid-template-columns:minmax(0,1fr) 340px;height:70vh;min-height:420px;border-bottom:1px solid #dde4ed}#viewport{overflow:auto;background-image:radial-gradient(#d3dce9 1px,transparent 1px);background-size:20px 20px;padding:12px;cursor:grab;touch-action:pan-x pan-y}#canvas{width:max-content}svg{display:block;max-width:none}.node{cursor:pointer;outline:none}.node:hover>rect:first-of-type{fill:#edf4ff}.node[aria-pressed=true]>rect:first-of-type{stroke:#1677ff;stroke-width:2.5;fill:#edf4ff}.node:focus-visible>rect:first-of-type{stroke:#1677ff;stroke-width:3}.node-title{font-size:12px;font-weight:600;fill:#263449}.node-subtitle{font-size:11px;fill:#66768b}.column{font-size:14px;font-weight:600;fill:#5b6d85}.branch{font-size:9px;fill:#637188;stroke:#f4f7fb;stroke-width:3px;paint-order:stroke}.issue{fill:#cf1322;color:#cf1322;font-size:11px;font-weight:600}aside{background:white;border-left:1px solid #dde4ed;padding:18px;overflow:auto;overflow-wrap:anywhere}aside h2{font-size:17px}aside h3{font-size:14px}dl{margin:16px 0;display:grid;grid-template-columns:110px minmax(0,1fr);font-size:12px}dt,dd{margin:0;padding:8px;border-bottom:1px solid #e5eaf1}dt{color:#637188;background:#f7f9fc}dd{white-space:pre-wrap}.error{background:#fff2f0;border:1px solid #ffccc7;border-radius:7px;padding:10px;margin:10px 0}.error p{margin:6px 0 0}.link{display:block;text-align:left;width:100%;margin:5px 0;overflow-wrap:anywhere;color:#176bcc}.notifications{margin:16px 24px}.notifications>summary{cursor:pointer;font-weight:600}.notifications section{background:white;border:1px solid #dde4ed;border-radius:8px;padding:12px;margin:8px 0}.notifications .link{width:auto;display:inline-block;margin:4px;max-width:100%}footer{padding:14px 24px;color:#637188;font-size:12px}@media(max-width:760px){.main{grid-template-columns:1fr;height:auto;min-height:0}#viewport{height:60vh;min-height:300px}aside{max-height:50vh;border-left:0;border-top:1px solid #dde4ed}.tools,header,.roster{padding:12px}.legend{margin-left:0}.notifications{margin:16px 12px}}
</style></head><body>
<header><h1>ArchitectCoder · ${tx('插件架构', 'Plugin architecture')}</h1><p>${tx('离线架构快照', 'Offline architecture snapshot')} · ${plan.plugins.length} ${tx('个插件', 'plugins')} · ${plan.stages.length} ${tx('个公共阶段', 'public phases')}</p><code>Plan ${escapeHtml(plan.plan_id)}</code><p>${hint}</p></header>
<div class="tools"><button id="overview">${tx('主流程总览', 'Main flow')}</button><label>${tx('视图', 'View')} <select id="view"><option value="organization">${tx('组织图', 'Organization')}</option><option value="schedule">${tx('调度图', 'Schedule')}</option></select></label><label>${tx('插件', 'Plugin')} <select id="filter">${filters.map((key) => `<option value="${escapeHtml(key)}">${escapeHtml(key || tx('全部插件', 'All plugins'))}</option>`).join('')}</select></label><button id="expand">${tx('全部展开', 'Expand all')}</button><button id="collapse">${tx('全部收起', 'Collapse all')}</button><button id="out" aria-label="${tx('缩小', 'Zoom out')}">−</button><output id="zoom" aria-live="polite">100%</output><button id="in" aria-label="${tx('放大', 'Zoom in')}">+</button><button id="fit">${tx('适应宽度', 'Fit width')}</button><button id="reset">${tx('原始大小', 'Actual size')}</button><button id="clear">${tx('清除选择', 'Clear selection')}</button></div>
<div class="tools"><input id="search" type="search" aria-label="${tx('搜索接口', 'Search interfaces')}" placeholder="${tx('搜索插件、接口或贡献 ID', 'Search plugin, interface or contribution ID')}"><select id="results" aria-label="${tx('搜索结果', 'Search results')}" hidden></select><select id="issues" aria-label="${tx('定位异常', 'Locate issue')}"${issues.length ? '' : ' disabled'}><option value="">${tx(`定位异常（${issues.length}）`, `Locate issue (${issues.length})`)}</option>${issues.map((issue) => `<option value="${escapeHtml(issue.id)}">${escapeHtml(`${issue.source === 'declaration' ? tx('声明失败', 'Declaration') : tx('加载失败', 'Load')} · ${issue.plugin} · ${issue.code} · ${issue.message}`)}</option>`).join('')}</select><div class="legend">${Object.keys(labels.modes).map((mode) => `<span style="--color:${COLORS[mode]}">${labels.modes[mode]}</span>`).join('')}</div></div>
<div class="roster">${filters.filter(Boolean).map((name) => {
    const count = catalog.filter((node) => node.contribution?.plugin === name).length;
    return `<button data-focus="${escapeHtml(name)}">${escapeHtml(name)} · ${count}${labels.issues[`plugin:${name}`] ? `<span class="issue"> !${labels.issues[`plugin:${name}`]}</span>` : ''}</button>`;
  }).join('')}</div>
<div class="main"><div id="viewport" tabindex="0" aria-label="${hint}"><div id="canvas">${renderGraphSvg(initial, view, labels, edgePath, wrapGraphLabel)}</div></div><aside><strong>${tx('节点详情', 'Node details')}</strong><div id="detail"><p>${hint}</p></div></aside></div>
${notificationHtml ? `<details id="notifications" class="notifications"><summary>${tx('独立通知（异常、取消、审核、后台）', 'Separate notifications (errors, cancellation, review, background)')}</summary>${notificationHtml}</details>` : ''}
<footer>${tx('此文件保存导出时的架构和加载诊断。接口按需执行，run_end 表示执行区间结束。', 'This file captures the architecture and load diagnostics at export time. Services execute on demand; run_end closes the execution interval.')}</footer>
<script type="application/json" id="data">${payload}</script>
<script>
(() => {
  const build = ${buildPluginGraph.toString()}, render = ${renderGraphSvg.toString()}, path = ${edgePath.toString()}, wrap = ${wrapGraphLabel.toString()}, search = ${searchGraph.toString()}, reveal = ${revealGraphNode.toString()};
  const data = JSON.parse(document.getElementById('data').textContent), details = new Map(data.details);
  const view = document.getElementById('view'), filter = document.getElementById('filter'), canvas = document.getElementById('canvas'), viewport = document.getElementById('viewport');
  const detail = document.getElementById('detail'), placeholder = detail.innerHTML, query = document.getElementById('search'), results = document.getElementById('results');
  let graph, scale = 1, selected = '', drag = null, expanded = new Set(data.expandedPlugins);
  view.value = data.view; filter.value = data.filter; query.value = data.query;
  function resize() { const svg = canvas.querySelector('svg'); svg.setAttribute('width', String(graph.width * scale)); svg.setAttribute('height', String(graph.height * scale)); document.getElementById('zoom').textContent = Math.round(scale * 100) + '%'; }
  function fit() { scale = Math.max(0.25, Math.min(1, (viewport.clientWidth - 24) / graph.width)); resize(); }
  function center(id) { const name = id.startsWith('plugin:') ? id.slice(7) : ''; const node = graph.nodes.find(item => item.id === id) || (name && graph.nodes.find(item => item.plugin ? item.plugin.name === name : item.contribution && item.contribution.plugin === name)); if (node) viewport.scrollTo({ left: Math.max(0, (node.x + node.width / 2) * scale - viewport.clientWidth / 2), top: Math.max(0, (node.y + node.height / 2) * scale - viewport.clientHeight / 2), behavior: 'smooth' }); }
  function highlight() {
    const related = new Set([selected]); graph.edges.forEach(edge => { if (edge.source === selected) related.add(edge.target); if (edge.target === selected) related.add(edge.source); });
    canvas.querySelectorAll('[data-node]').forEach(node => { const id = node.getAttribute('data-node'); node.setAttribute('aria-pressed', String(id === selected)); node.style.opacity = !selected || related.has(id) || !graph.nodes.some(item => item.id === selected) ? '1' : '0.35'; });
  }
  function draw() { graph = build(data.plan, view.value, filter.value, { expandedPlugins: [...expanded] }); canvas.innerHTML = render(graph, view.value, data.labels, path, wrap); fit(); highlight(); center(selected); }
  function focus(name) { expanded.add(name); view.value = 'organization'; filter.value = name; selected = 'plugin:' + name; detail.innerHTML = details.get(selected) || placeholder; draw(); }
  function select(id) {
    const visible = graph.nodes.find(node => node.id === id);
    if (visible && id.startsWith('group:')) { focus(visible.plugin.name); return; }
    if (!details.has(id)) return;
    const node = data.catalog.find(item => item.id === id), name = node.plugin ? node.plugin.name : node.contribution ? node.contribution.plugin : '';
    const next = reveal(data.plan, data.catalog, id, { view: view.value, filter: filter.value, expandedPlugins: [...expanded] });
    view.value = next.view; filter.value = next.filter; expanded = new Set(next.expandedPlugins);
    selected = id; detail.innerHTML = details.get(id); draw();
    if (node.contribution && !data.plan.stages.some(stage => stage.stage === node.contribution.stage)) { const notifications = document.getElementById('notifications'); if (notifications) notifications.open = true; center('plugin:' + name); }
  }
  function toggle(name) { if (expanded.has(name)) expanded.delete(name); else expanded.add(name); selected = 'plugin:' + name; detail.innerHTML = details.get(selected) || placeholder; draw(); }
  function updateSearch() {
    const matches = search(data.catalog, query.value); results.innerHTML = ''; results.hidden = !query.value.trim();
    const prompt = document.createElement('option'); prompt.value = ''; prompt.textContent = matches.length ? '${tx('选择搜索结果', 'Select a result')}' + ' (' + matches.length + ')' : data.noMatches; results.appendChild(prompt);
    matches.forEach(node => { const option = document.createElement('option'); option.value = node.id; option.textContent = (node.contribution ? node.contribution.plugin : node.plugin ? node.plugin.name : node.kind) + ' · ' + node.label; results.appendChild(option); });
  }
  view.addEventListener('change', draw); filter.addEventListener('change', () => { selected = ''; detail.innerHTML = placeholder; draw(); viewport.scrollTo(0, 0); });
  query.addEventListener('input', updateSearch); results.addEventListener('change', () => select(results.value));
  document.getElementById('issues').addEventListener('change', event => { const issue = data.issues.find(item => item.id === event.target.value); if (issue) select(issue.nodeId); event.target.value = ''; });
  document.getElementById('expand').addEventListener('click', () => { expanded = new Set(['core', ...data.plan.plugins.map(plugin => plugin.name)]); draw(); });
  document.getElementById('collapse').addEventListener('click', () => { expanded.clear(); selected = ''; detail.innerHTML = placeholder; draw(); viewport.scrollTo(0, 0); });
  document.getElementById('overview').addEventListener('click', () => { expanded.clear(); selected = ''; view.value = 'schedule'; filter.value = ''; query.value = ''; detail.innerHTML = placeholder; updateSearch(); draw(); viewport.scrollTo(0, 0); });
  document.getElementById('in').addEventListener('click', () => { scale = Math.min(2, scale + 0.1); resize(); });
  document.getElementById('out').addEventListener('click', () => { scale = Math.max(0.25, scale - 0.1); resize(); });
  document.getElementById('fit').addEventListener('click', fit);
  document.getElementById('reset').addEventListener('click', () => { scale = 1; resize(); });
  document.getElementById('clear').addEventListener('click', () => { selected = ''; detail.innerHTML = placeholder; highlight(); });
  document.addEventListener('click', event => { const toggleNode = event.target.closest('[data-toggle]'); if (toggleNode) { toggle(toggleNode.getAttribute('data-toggle')); return; } const focusNode = event.target.closest('[data-focus]'); if (focusNode) { focus(focusNode.getAttribute('data-focus')); return; } const node = event.target.closest('[data-node], [data-target]'); if (node) select(node.getAttribute('data-node') || node.getAttribute('data-target')); });
  canvas.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { const node = event.target.closest('[data-toggle], [data-node]'); if (node) { event.preventDefault(); if (node.getAttribute('data-toggle')) toggle(node.getAttribute('data-toggle')); else select(node.getAttribute('data-node')); } } });
  viewport.addEventListener('pointerdown', event => { if (event.button !== 0 || event.target.closest('[data-node], [data-toggle]')) return; drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop }; viewport.setPointerCapture(event.pointerId); });
  viewport.addEventListener('pointermove', event => { if (drag) { viewport.scrollLeft = drag.left - (event.clientX - drag.x); viewport.scrollTop = drag.top - (event.clientY - drag.y); } });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => viewport.addEventListener(name, () => { drag = null; }));
  new ResizeObserver(fit).observe(viewport); draw(); updateSearch();
})();
</script></body></html>`;
}
