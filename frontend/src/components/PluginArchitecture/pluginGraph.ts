import type { PlanPlugin, PluginContribution, PluginExecutionPlan, PluginStage } from '../../types/plugins';

export type GraphView = 'organization' | 'schedule';
export interface PlanNode {
  id: string;
  kind: 'plugin' | 'contribution' | 'stage' | 'interface' | 'execution';
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  plugin?: PlanPlugin;
  contribution?: PluginContribution;
  stage?: PluginStage;
  interfaceName?: string;
}
export interface PlanEdge {
  source: string;
  target: string;
  kind: 'binding' | 'flow' | 'branch' | 'order';
  label?: 'continue' | 'no-tools' | 'finish' | 'blocked' | 'end';
}
export interface PlanGraph { nodes: PlanNode[]; edges: PlanEdge[]; width: number; height: number }

const stageId = (stage: string) => `stage:${stage}`;
const contributionId = (id: string) => `contribution:${id}`;
const ordered = (stage: PluginStage, filter: string) => stage.contributions
  .filter((item) => !filter || item.plugin === filter).slice().sort((a, b) => a.order - b.order);

export function buildPluginGraph(plan: PluginExecutionPlan, view: GraphView, filter = ''): PlanGraph {
  const nodes: PlanNode[] = [];
  const edges: PlanEdge[] = [];
  const add = (node: PlanNode) => { nodes.push(node); return node.id; };
  if (view === 'organization') {
    const allContributions = plan.stages.flatMap((stage) => ordered(stage, filter));
    const plugins: PlanPlugin[] = [
      { name: 'core', provider: '', source: 'builtin', status: 'discovered' as const, error: '', interfaces: [],
        contributions: allContributions.filter((item) => item.plugin === 'core').map((item) => item.id) },
      ...plan.plugins,
    ].filter((plugin) => !filter || plugin.name === filter);
    let y = 64;
    for (const plugin of plugins) {
      const contributions = allContributions.filter((item) => item.plugin === plugin.name);
      const count = contributions.length + plugin.interfaces.length;
      const blockHeight = Math.max(88, count * 78);
      const parent = add({ id: `plugin:${plugin.name}`, kind: 'plugin', label: plugin.name,
        x: 24, y: y + (blockHeight - 66) / 2, width: 220, height: 66, plugin });
      contributions.forEach((item, index) => {
        const child = add({ id: contributionId(item.id), kind: 'contribution', label: item.id,
          x: 316, y: y + index * 78, width: 310, height: 66, contribution: item });
        edges.push({ source: parent, target: child, kind: 'binding' });
        edges.push({ source: child, target: stageId(item.stage), kind: 'binding' });
      });
      plugin.interfaces.forEach((name, index) => {
        const child = add({ id: `interface:${plugin.name}:${index}`, kind: 'interface', label: name,
          x: 316, y: y + (index + contributions.length) * 78, width: 310, height: 66,
          plugin, interfaceName: name });
        edges.push({ source: parent, target: child, kind: 'binding' });
      });
      y += blockHeight + 28;
    }
    const stages = filter ? plan.stages.filter((stage) => ordered(stage, filter).length) : plan.stages;
    stages.forEach((stage, index) => add({ id: stageId(stage.stage), kind: 'stage', label: stage.stage,
      x: 750, y: 64 + index * 104, width: 250, height: 66, stage }));
    return { nodes, edges, width: 1030, height: Math.max(y, 64 + stages.length * 104) };
  }

  let y = 40;
  for (const stage of plan.stages) {
    const contributions = ordered(stage, filter);
    add({ id: stageId(stage.stage), kind: 'stage', label: stage.stage,
      x: 36, y, width: 250, height: 66, stage });
    contributions.forEach((item, index) => {
      const child = add({ id: contributionId(item.id), kind: 'contribution', label: item.id,
        x: 352 + (index % 2) * 346, y: y + Math.floor(index / 2) * 78, width: 310, height: 66,
        contribution: item });
      edges.push({ source: stageId(stage.stage), target: child, kind: 'binding' });
      if (index > 0) edges.push({ source: contributionId(contributions[index - 1].id), target: child, kind: 'order' });
    });
    y += Math.max(110, Math.ceil(contributions.length / 2) * 78 + 24);
    if (stage.stage === 'llm_before' || stage.stage === 'tool_before') {
      add({ id: stage.stage === 'llm_before' ? 'model-call' : 'tool-call', kind: 'execution',
        label: stage.stage === 'llm_before' ? 'model-call' : 'tool-call', x: 36, y, width: 250, height: 66 });
      y += 104;
    }
  }
  const spine = ['run_start', 'round_before', 'llm_before', 'model-call', 'llm_after',
    'tool_batch_before', 'tool_before', 'tool-call', 'tool_after', 'tool_batch_after', 'round_after'];
  const nodeId = (value: string) => value.endsWith('-call') ? value : stageId(value);
  const existing = new Set(nodes.map((node) => node.id));
  for (let index = 1; index < spine.length; index++) {
    const source = nodeId(spine[index - 1]); const target = nodeId(spine[index]);
    if (existing.has(source) && existing.has(target)) edges.push({ source, target, kind: 'flow' });
  }
  const branches: Array<[string, string, PlanEdge['label']]> = [
    ['llm_after', 'round_after', 'no-tools'], ['round_after', 'round_before', 'continue'],
    ['round_after', 'run_finalize', 'finish'], ['run_finalize', 'run_end', 'end'],
    ['tool_before', 'tool_after', 'blocked'], ['error', 'run_end', 'end'], ['cancel', 'run_end', 'end'],
  ];
  branches.forEach(([source, target, label]) => {
    if (existing.has(stageId(source)) && existing.has(stageId(target))) {
      edges.push({ source: stageId(source), target: stageId(target), kind: 'branch', label });
    }
  });
  return { nodes, edges, width: 1040, height: y + 20 };
}

// SVG labels wrap on code points, counting wide characters to keep arbitrary plugin IDs legible.
export function wrapGraphLabel(label: string, maxUnits = 36): string[] {
  const lines: string[] = []; let line = ''; let units = 0;
  for (const char of label) {
    const width = char.charCodeAt(0) > 255 ? 2 : 1;
    if (units + width > maxUnits && line) { lines.push(line); line = ''; units = 0; }
    line += char; units += width;
  }
  if (line) lines.push(line);
  return lines.length > 2 ? [lines[0], `${lines[1].slice(0, -1)}…`] : lines;
}
