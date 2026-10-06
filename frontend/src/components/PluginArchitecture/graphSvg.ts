import type { PlanEdge, PlanGraph, PlanNode, GraphView } from './pluginGraph';

export interface GraphPresentation {
  stages: Record<string, string>; modes: Record<string, string>; statuses: Record<string, string>;
  colors: Record<string, string>; branches: Record<string, string>; columns: string[];
  contributionUnit: string; interfaceCaption: string; executionCaption: string;
  expand: string; collapse: string; organization: string; schedule: string;
  issues: Record<string, number>;
}

/** Pure renderer shared with the offline runtime. All dependencies are explicit. */
export function renderGraphSvg(graph: PlanGraph, view: GraphView, labels: GraphPresentation,
  path: (edge: PlanEdge, source: PlanNode, target: PlanNode) => string,
  wrap: (label: string, maxUnits?: number) => string[]): string {
  const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]!);
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const edges = graph.edges.map((edge) => {
    const source = nodes.get(edge.source); const target = nodes.get(edge.target);
    if (!source || !target) return '';
    const title = edge.label ? labels.branches[edge.label] : `${source.label} → ${target.label}`;
    const lane = edge.label === 'continue' ? 6 : edge.label === 'no-tools' ? 16 : 26;
    const y = (source.y + target.y + source.height / 2 + target.height / 2) / 2;
    return `<g><path d="${path(edge, source, target)}" fill="none" stroke="#8190a7" stroke-width="1.2" opacity="${edge.kind === 'binding' ? 0.4 : 0.8}" ${edge.kind === 'binding' || edge.kind === 'order' ? 'stroke-dasharray="5 4"' : ''} marker-end="url(#arrow)"><title>${escape(title)}</title></path>${edge.label ? `<text x="${lane - 3}" y="${y}" text-anchor="middle" transform="rotate(-90 ${lane - 3} ${y})" class="branch">${escape(title)}</text>` : ''}</g>`;
  }).join('');
  const markup = graph.nodes.map((node) => {
    const title = node.kind === 'stage' || node.kind === 'execution' ? labels.stages[node.label] || node.label : node.label;
    const subtitle = node.contribution ? `${node.contribution.order}. ${node.contribution.plugin} · ${labels.modes[node.contribution.mode]}`
      : node.kind === 'plugin' ? `${labels.statuses[node.plugin!.status]} · ${node.summary?.contributions || 0} ${labels.contributionUnit}`
        : node.kind === 'interface' ? labels.interfaceCaption : node.kind === 'stage' ? node.label : labels.executionCaption;
    const failures = labels.issues[node.plugin ? `plugin:${node.plugin.name}` : node.id] || 0;
    const color = failures || node.plugin?.status === 'unavailable' ? '#be4242' : node.plugin?.status === 'disabled' ? '#9299a6' : labels.colors[node.contribution?.mode || node.kind];
    const toggle = node.kind === 'plugin' && node.summary ? `<g role="button" tabindex="0" data-toggle="${escape(node.plugin!.name)}" aria-label="${escape(`${node.summary.expanded ? labels.collapse : labels.expand} ${node.plugin!.name}`)}"><rect x="${node.width - 30}" y="8" width="22" height="22" rx="4" fill="#edf4ff"/><text x="${node.width - 19}" y="24" text-anchor="middle" fill="#176bcc">${node.summary.expanded ? '−' : '+'}</text></g>` : '';
    return `<g class="node" data-node="${escape(node.id)}" transform="translate(${node.x},${node.y})" role="button" tabindex="0" aria-label="${escape(`${title} · ${subtitle}`)}" aria-pressed="false"><title>${escape(title)}</title><rect width="${node.width}" height="${node.height}" rx="9" fill="white" stroke="${color}" stroke-width="1.2"/><rect width="5" height="${node.height - 14}" y="7" rx="2" fill="${color}"/><text x="14" y="22" class="node-title">${wrap(title, node.width > 260 ? 38 : 24).map((line, index) => `<tspan x="14" dy="${index ? 16 : 0}">${escape(line)}</tspan>`).join('')}</text><text x="14" y="${node.height - 9}" class="node-subtitle">${escape(wrap(subtitle, node.width > 260 ? 44 : 30)[0])}</text>${toggle}${failures ? `<text x="${node.width - 9}" y="${node.height - 9}" text-anchor="end" class="issue">!${failures}</text>` : ''}</g>`;
  }).join('');
  const columns = view === 'organization' ? labels.columns.map((label, index) => `<text x="${[24, 316, 750][index]}" y="30" class="column">${escape(label)}</text>`).join('') : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${graph.width} ${graph.height}" aria-label="${escape(view === 'organization' ? labels.organization : labels.schedule)}"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8190a7"/></marker></defs>${columns}${edges}${markup}</svg>`;
}
