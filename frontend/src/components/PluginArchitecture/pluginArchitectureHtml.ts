import type { PluginExecutionPlan, PluginLoadReport } from '../../types/plugins';
import { buildPluginGraph, wrapGraphLabel, type GraphView, type PlanGraph, type PlanNode } from './pluginGraph';
import { COLORS, STAGE_LABELS, edgePath } from './graphPresentation';

interface ExportOptions {
  language?: string;
  view?: GraphView;
  filter?: string;
  loadReport?: PluginLoadReport | null;
}

const escapeHtml = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]!);

/** Portable graph viewer: all geometry and text are generated from the plan, with no network dependencies. */
export function createPluginArchitectureHtml(plan: PluginExecutionPlan, options: ExportOptions = {}): string {
  const en = options.language === 'en';
  const tx = (zh: string, english: string) => en ? english : zh;
  const stageLabel = (key: string) => STAGE_LABELS[key]?.[en ? 1 : 0] || key;
  const modeLabel = (key: string) => ({ observer: tx('观察', 'Observer'), transform: tx('数据处理', 'Transform'),
    control: tx('控制', 'Control'), service: tx('按需接口执行', 'On-demand service') } as Record<string, string>)[key] || key;
  const statusLabel = (key: string) => ({ discovered: tx('已发现', 'Discovered'), disabled: tx('已禁用', 'Disabled'),
    unavailable: tx('不可用', 'Unavailable'), loaded: tx('加载成功', 'Loaded'), not_loaded: tx('尚未实例化', 'Not instantiated') } as Record<string, string>)[key] || key;
  const branches = { continue: tx('继续下一轮', 'Next round'), 'no-tools': tx('无工具调用', 'No tools'),
    finish: tx('返回最终结果', 'Finalize'), blocked: tx('调用被阻断', 'Blocked'), end: tx('进入任务结束', 'Run end') };
  const filters = ['', 'core', ...plan.plugins.map((plugin) => plugin.name)];
  const filter = filters.includes(options.filter || '') ? options.filter || '' : '';
  const view = options.view === 'schedule' ? 'schedule' : 'organization';
  const loadReport = options.loadReport?.plan_id === plan.plan_id ? options.loadReport : null;
  const details = new Map<string, string>();
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
        [tx('状态', 'Status'), statusLabel(plugin.status)], ['Provider', plugin.provider || tx('核心内置', 'Built in')],
        [tx('来源', 'Source'), plugin.source], [tx('版本', 'Version'), plugin.version],
        [tx('内容指纹', 'Content fingerprint'), plugin.revision], [tx('能力槽位', 'Capability slot'), plugin.slot],
        [tx('必需插件', 'Required plugins'), plugin.dependencies], [tx('可选插件', 'Optional plugins'), plugin.optional_dependencies],
        [tx('插件参数', 'Plugin parameters'), plugin.config_keys],
        [tx('最近实例加载', 'Latest instance load'), runtime ? statusLabel(runtime.status) : undefined],
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
        [tx('类型', 'Mode'), modeLabel(item.mode)], ['Handler', item.handler],
        [tx('阶段内顺序', 'Stage order'), item.mode === 'service' ? tx('按请求匹配，继承当前操作阶段', 'Matched on demand; inherits the active phase') : item.order],
        [tx('声明优先级', 'Priority'), item.priority], [tx('先于', 'Before'), item.before], [tx('后于', 'After'), item.after],
        [tx('作用域', 'Scope'), item.scope], [tx('接口异常时', 'On failure'), item.mode === 'service'
          ? tx('记录并交给领域调用方处理', 'Record and propagate to the domain caller') : item.fail_closed ? tx('阻断执行', 'Block execution') : tx('记录并继续', 'Record and continue')],
      ]);
      if (plan.plugins.some((item) => item.name === node.contribution!.plugin) || item.plugin === 'core') html += link(`plugin:${item.plugin}`, item.plugin);
    }
    if (node.stage) {
      html += rowsHtml([[tx('允许的外部接口类型', 'Supported external modes'), node.stage.supported_modes.map(modeLabel)]]);
      html += `<h3>${tx('本阶段接口（按执行顺序）', 'Stage contributions in execution order')}</h3>`;
      html += node.stage.contributions.slice().sort((a, b) => a.order - b.order)
        .map((item) => link(`contribution:${item.id}`, `${item.order}. ${item.id}`)).join('') || `<p>${tx('没有贡献接口', 'No contributions')}</p>`;
    }
    if (node.kind === 'plugin') html += `<p>${tx('领域接口按请求执行，继承当前操作阶段。发现成功不代表已经实例化或通过健康检查。', 'Domain interfaces execute on demand and inherit the active phase. Discovery does not imply instantiation or health checks.')}</p>`;
    if (node.kind === 'interface') html += `<p>${tx('该接口尚未安装可用阶段绑定，请查看插件状态和声明。', 'No active binding is installed. Check the plugin status and declaration.')}</p>`;
    if (node.kind === 'execution') html += `<p>${tx('由核心执行器调用，前后阶段提供观察和处理接口。', 'The core executor performs this operation; surrounding phases expose observation and processing hooks.')}</p>`;
    return html;
  }

  function renderSvg(graph: PlanGraph, graphView: GraphView): string {
    const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
    const edges = graph.edges.map((edge) => {
      const source = nodes.get(edge.source); const target = nodes.get(edge.target);
      if (!source || !target) return '';
      const title = edge.label ? branches[edge.label] : `${source.label} → ${target.label}`;
      const lane = edge.label === 'continue' ? 6 : edge.label === 'no-tools' ? 16 : 26;
      const y = (source.y + target.y + source.height / 2 + target.height / 2) / 2;
      return `<g><path d="${edgePath(edge, source, target)}" fill="none" stroke="#8190a7" stroke-width="1.2" opacity="${edge.kind === 'binding' ? 0.4 : 0.8}" ${edge.kind === 'binding' || edge.kind === 'order' ? 'stroke-dasharray="5 4"' : ''} marker-end="url(#arrow)"><title>${escapeHtml(title)}</title></path>${edge.label ? `<text x="${lane - 3}" y="${y}" text-anchor="middle" transform="rotate(-90 ${lane - 3} ${y})" class="branch">${escapeHtml(title)}</text>` : ''}</g>`;
    }).join('');
    const markup = graph.nodes.map((node) => {
      details.set(node.id, nodeDetails(node));
      const label = node.kind === 'stage' || node.kind === 'execution' ? stageLabel(node.label) : node.label;
      const subtitle = node.contribution ? `${node.contribution.order}. ${node.contribution.plugin} · ${modeLabel(node.contribution.mode)}`
        : node.kind === 'plugin' ? statusLabel(node.plugin!.status) : node.kind === 'interface' ? tx('领域接口 · 未安装绑定', 'Domain interface · binding not installed')
          : node.kind === 'stage' ? node.label : tx('核心执行节点', 'Core execution');
      const color = node.plugin?.status === 'unavailable' ? '#be4242' : node.plugin?.status === 'disabled' ? '#9299a6' : COLORS[node.contribution?.mode || node.kind];
      return `<g class="node" data-node="${escapeHtml(node.id)}" transform="translate(${node.x},${node.y})" role="button" tabindex="0" aria-label="${escapeHtml(`${label} · ${subtitle}`)}" aria-pressed="false"><title>${escapeHtml(label)}</title><rect width="${node.width}" height="${node.height}" rx="9" fill="white" stroke="${color}" stroke-width="1.2"/><rect width="5" height="${node.height - 14}" y="7" rx="2" fill="${color}"/><text x="14" y="22" class="node-title">${wrapGraphLabel(label, node.width > 260 ? 38 : 28).map((line, index) => `<tspan x="14" dy="${index ? 16 : 0}">${escapeHtml(line)}</tspan>`).join('')}</text><text x="14" y="${node.height - 9}" class="node-subtitle">${escapeHtml(wrapGraphLabel(subtitle, node.width > 260 ? 48 : 34)[0])}</text></g>`;
    }).join('');
    const columns = graphView === 'organization' ? [tx('插件', 'Plugins'), tx('贡献 / 领域接口', 'Contributions / interfaces'), tx('生命周期阶段', 'Lifecycle stages')]
      .map((label, index) => `<text x="${[24, 316, 750][index]}" y="30" class="column">${label}</text>`).join('') : '';
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${graph.width} ${graph.height}" aria-label="${graphView === 'organization' ? tx('插件组织图', 'Plugin organization graph') : tx('生命周期调度图', 'Lifecycle schedule graph')}"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8190a7"/></marker></defs>${columns}${edges}${markup}</svg>`;
  }

  const graphs = (['organization', 'schedule'] as const).flatMap((graphView) => filters.map((pluginFilter) => {
    const graph = buildPluginGraph(plan, graphView, pluginFilter);
    return { view: graphView, filter: pluginFilter, width: graph.width, height: graph.height,
      svg: renderSvg(graph, graphView), nodes: graph.nodes.map(({ id, x, y, width, height }) => ({ id, x, y, width, height })), edges: graph.edges };
  }));
  const notificationHtml = (plan.notifications || []).map((stage) => `<section><h3>${escapeHtml(stageLabel(stage.stage))}</h3>${stage.contributions.map((item) => {
    const node: PlanNode = { id: `contribution:${item.id}`, kind: 'contribution', label: item.id, contribution: item, x: 0, y: 0, width: 0, height: 0 };
    details.set(node.id, nodeDetails(node));
    return link(node.id, `${item.plugin} · ${item.id}`);
  }).join('') || `<p>${tx('没有贡献接口', 'No contributions')}</p>`}</section>`).join('');
  const payload = JSON.stringify({ graphs, details: [...details], view, filter }).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
  const initial = graphs.find((graph) => graph.view === view && graph.filter === filter)!;
  const hint = tx('点击节点查看详情；拖动空白区域平移，滚动查看全图。', 'Select a node for details. Drag empty space to pan; scroll to explore.');
  return `<!doctype html>
<html lang="${en ? 'en' : 'zh-CN'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>ArchitectCoder · ${tx('插件架构', 'Plugin architecture')}</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px system-ui,-apple-system,'Segoe UI',sans-serif;color:#263449;background:#f4f7fb}header{background:white;padding:18px 24px;border-bottom:1px solid #dde4ed}h1{font-size:21px;margin:0 0 8px}p{line-height:1.6}header p{margin:6px 0;color:#637188}code{overflow-wrap:anywhere;font-size:11px}.tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:12px 24px;background:white;border-bottom:1px solid #dde4ed}button,select{font:inherit;color:inherit;background:white;border:1px solid #cfd9e6;border-radius:7px;padding:7px 10px}button{cursor:pointer}button:hover{background:#eff5ff}button:focus-visible,select:focus-visible{outline:2px solid #1677ff;outline-offset:2px}select{max-width:260px}output{min-width:48px;text-align:center}.legend{display:flex;gap:10px;flex-wrap:wrap;margin-left:auto}.legend span:before{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--color);margin-right:5px}.main{display:grid;grid-template-columns:minmax(0,1fr) 340px;height:70vh;min-height:420px;border-bottom:1px solid #dde4ed}#viewport{overflow:auto;background-image:radial-gradient(#d3dce9 1px,transparent 1px);background-size:20px 20px;padding:12px;cursor:grab;touch-action:pan-x pan-y}#canvas{width:max-content}svg{display:block;max-width:none}.node{cursor:pointer;outline:none}.node:hover>rect:first-of-type{fill:#edf4ff}.node[aria-pressed=true]>rect:first-of-type{stroke:#1677ff;stroke-width:2.5;fill:#edf4ff}.node:focus-visible>rect:first-of-type{stroke:#1677ff;stroke-width:3}.node-title{font-size:12px;font-weight:600;fill:#263449}.node-subtitle{font-size:11px;fill:#66768b}.column{font-size:14px;font-weight:600;fill:#5b6d85}.branch{font-size:9px;fill:#637188;stroke:#f4f7fb;stroke-width:3px;paint-order:stroke}aside{background:white;border-left:1px solid #dde4ed;padding:18px;overflow:auto;overflow-wrap:anywhere}aside h2{font-size:17px}aside h3{font-size:14px}dl{margin:16px 0;display:grid;grid-template-columns:110px minmax(0,1fr);font-size:12px}dt,dd{margin:0;padding:8px;border-bottom:1px solid #e5eaf1}dt{color:#637188;background:#f7f9fc}dd{white-space:pre-wrap}.error{background:#fff2f0;border:1px solid #ffccc7;border-radius:7px;padding:10px;margin:10px 0}.error p{margin:6px 0 0}.link{display:block;text-align:left;width:100%;margin:5px 0;overflow-wrap:anywhere;color:#176bcc}.notifications{margin:16px 24px}.notifications>summary{cursor:pointer;font-weight:600}.notifications section{background:white;border:1px solid #dde4ed;border-radius:8px;padding:12px;margin:8px 0}.notifications .link{width:auto;display:inline-block;margin:4px;max-width:100%}footer{padding:14px 24px;color:#637188;font-size:12px}@media(max-width:760px){.main{grid-template-columns:1fr;height:auto;min-height:0}#viewport{height:60vh;min-height:300px}aside{max-height:50vh;border-left:0;border-top:1px solid #dde4ed}.tools,header{padding:12px}.legend{margin-left:0}.notifications{margin:16px 12px}}
</style></head><body>
<header><h1>ArchitectCoder · ${tx('插件架构', 'Plugin architecture')}</h1><p>${tx('离线架构快照', 'Offline architecture snapshot')} · ${plan.plugins.length} ${tx('个插件', 'plugins')} · ${plan.stages.length} ${tx('个公共阶段', 'public phases')}</p><code>Plan ${escapeHtml(plan.plan_id)}</code><p>${hint}</p></header>
<div class="tools"><label>${tx('视图', 'View')} <select id="view"><option value="organization"${view === 'organization' ? ' selected' : ''}>${tx('组织图', 'Organization')}</option><option value="schedule"${view === 'schedule' ? ' selected' : ''}>${tx('调度图', 'Schedule')}</option></select></label><label>${tx('插件', 'Plugin')} <select id="filter">${filters.map((key) => `<option value="${escapeHtml(key)}"${filter === key ? ' selected' : ''}>${escapeHtml(key || tx('全部插件', 'All plugins'))}</option>`).join('')}</select></label><button id="out" aria-label="${tx('缩小', 'Zoom out')}">−</button><output id="zoom" aria-live="polite">100%</output><button id="in" aria-label="${tx('放大', 'Zoom in')}">+</button><button id="fit">${tx('适应宽度', 'Fit width')}</button><button id="reset">${tx('原始大小', 'Actual size')}</button><button id="clear">${tx('清除选择', 'Clear selection')}</button><div class="legend">${['observer', 'transform', 'control', 'service'].map((mode) => `<span style="--color:${COLORS[mode]}">${modeLabel(mode)}</span>`).join('')}</div></div>
<div class="main"><div id="viewport" tabindex="0" aria-label="${hint}"><div id="canvas">${initial.svg}</div></div><aside><strong>${tx('节点详情', 'Node details')}</strong><div id="detail"><p>${hint}</p></div></aside></div>
${notificationHtml ? `<details class="notifications"><summary>${tx('独立通知（异常、取消、审核、后台）', 'Separate notifications (errors, cancellation, review, background)')}</summary>${notificationHtml}</details>` : ''}
<footer>${tx('此文件保存导出时的架构和加载诊断。接口按需执行，run_end 表示执行区间结束。', 'This file captures the architecture and load diagnostics at export time. Services execute on demand; run_end closes the execution interval.')}</footer>
<script type="application/json" id="data">${payload}</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById('data').textContent);
  const details = new Map(data.details);
  const view = document.getElementById('view'), filter = document.getElementById('filter');
  const canvas = document.getElementById('canvas'), viewport = document.getElementById('viewport');
  const detail = document.getElementById('detail'), placeholder = detail.innerHTML;
  let graph, scale = 1, selected = '', drag = null;
  view.value = data.view; filter.value = data.filter;
  function resize() {
    const svg = canvas.querySelector('svg');
    svg.setAttribute('width', String(graph.width * scale)); svg.setAttribute('height', String(graph.height * scale));
    document.getElementById('zoom').textContent = Math.round(scale * 100) + '%';
  }
  function fit() { scale = Math.max(0.25, Math.min(1, (viewport.clientWidth - 24) / graph.width)); resize(); }
  function highlight() {
    const related = new Set([selected]);
    graph.edges.forEach(edge => { if (edge.source === selected) related.add(edge.target); if (edge.target === selected) related.add(edge.source); });
    canvas.querySelectorAll('[data-node]').forEach(node => {
      const id = node.getAttribute('data-node');
      node.setAttribute('aria-pressed', String(id === selected));
      node.style.opacity = !selected || related.has(id) || !graph.nodes.some(item => item.id === selected) ? '1' : '0.35';
    });
  }
  function draw() {
    graph = data.graphs.find(item => item.view === view.value && item.filter === filter.value);
    canvas.innerHTML = graph.svg; selected = ''; detail.innerHTML = placeholder; fit(); viewport.scrollTo(0, 0);
  }
  function select(id) {
    if (!details.has(id)) return;
    if (!graph.nodes.some(node => node.id === id) && data.graphs.some(item => item.view === view.value && item.filter === '' && item.nodes.some(node => node.id === id))) {
      filter.value = ''; draw();
    }
    if (id.startsWith('plugin:') && view.value !== 'organization') { view.value = 'organization'; filter.value = ''; draw(); }
    selected = id; detail.innerHTML = details.get(id); highlight();
    const node = graph.nodes.find(item => item.id === id);
    if (node) viewport.scrollTo({ left: Math.max(0, node.x * scale - 24), top: Math.max(0, node.y * scale - 24), behavior: 'smooth' });
  }
  view.addEventListener('change', draw); filter.addEventListener('change', draw);
  document.getElementById('in').addEventListener('click', () => { scale = Math.min(2, scale + 0.1); resize(); });
  document.getElementById('out').addEventListener('click', () => { scale = Math.max(0.25, scale - 0.1); resize(); });
  document.getElementById('fit').addEventListener('click', fit);
  document.getElementById('reset').addEventListener('click', () => { scale = 1; resize(); });
  document.getElementById('clear').addEventListener('click', () => { selected = ''; detail.innerHTML = placeholder; highlight(); });
  document.addEventListener('click', event => { const node = event.target.closest('[data-node], [data-target]'); if (node) select(node.getAttribute('data-node') || node.getAttribute('data-target')); });
  canvas.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { const node = event.target.closest('[data-node]'); if (node) { event.preventDefault(); select(node.getAttribute('data-node')); } } });
  viewport.addEventListener('pointerdown', event => { if (event.button !== 0 || event.target.closest('[data-node]')) return; drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop }; viewport.setPointerCapture(event.pointerId); });
  viewport.addEventListener('pointermove', event => { if (drag) { viewport.scrollLeft = drag.left - (event.clientX - drag.x); viewport.scrollTop = drag.top - (event.clientY - drag.y); } });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => viewport.addEventListener(name, () => { drag = null; }));
  new ResizeObserver(fit).observe(viewport);
  draw();
})();
</script></body></html>`;
}
