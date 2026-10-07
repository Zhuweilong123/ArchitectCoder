import type { PlanPlugin, PluginContribution, PluginExecutionPlan, PluginStage } from '../../types/plugins';

export type GraphView = 'organization' | 'schedule';
export interface GraphOptions { expandedPlugins?: string[] }
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
  summary?: { contributions: number; interfaces: number; stages: string[]; expanded: boolean };
}
export interface PlanEdge {
  source: string;
  target: string;
  kind: 'binding' | 'flow' | 'branch' | 'order';
  label?: 'continue' | 'no-tools' | 'finish' | 'blocked' | 'end';
}
export interface PlanGraph { nodes: PlanNode[]; edges: PlanEdge[]; width: number; height: number }

// Self-contained so the offline viewer can use the exact same layout algorithm.
export function buildPluginGraph(plan: PluginExecutionPlan, view: GraphView, filter = '', options: GraphOptions = {}): PlanGraph {
  const stageId = (stage: string) => `stage:${stage}`;
  const contributionId = (id: string) => `contribution:${id}`;
  const ordered = (stage: PluginStage) => stage.contributions
    .filter((item) => !filter || item.plugin === filter).slice().sort((a, b) => a.order - b.order);
  const allContributions = plan.stages.flatMap(ordered);
  const allBindings = [...plan.stages, ...(plan.notifications || [])].flatMap((stage) => stage.contributions);
  const plugins: PlanPlugin[] = [
    { name: 'core', provider: '', source: 'builtin', status: 'discovered', error: '', interfaces: [],
      contributions: allBindings.filter((item) => item.plugin === 'core').map((item) => item.id) }, ...plan.plugins,
  ];
  const expanded = (name: string) => options.expandedPlugins === undefined || options.expandedPlugins.includes(name);
  const summary = (plugin: PlanPlugin, bindings = allBindings.filter((item) => item.plugin === plugin.name)) => ({
    contributions: bindings.length, interfaces: plugin.interfaces.length,
    stages: [...new Set(bindings.map((item) => item.stage))], expanded: expanded(plugin.name),
  });
  const nodes: PlanNode[] = [];
  const edges: PlanEdge[] = [];
  const add = (node: PlanNode) => { nodes.push(node); return node.id; };
  if (view === 'organization') {
    let y = 64;
    for (const plugin of plugins.filter((plugin) => !filter || plugin.name === filter)) {
      const contributions = allContributions.filter((item) => item.plugin === plugin.name);
      const interfaces = plugin.interfaces.filter((name) => !contributions.some((item) => item.interface_id === `${plugin.name}.${name}`));
      const count = expanded(plugin.name) ? contributions.length + interfaces.length : 0;
      const blockHeight = Math.max(88, count * 78);
      const parent = add({ id: `plugin:${plugin.name}`, kind: 'plugin', label: plugin.name,
        x: 24, y: y + (blockHeight - 66) / 2, width: 220, height: 66, plugin, summary: summary(plugin) });
      if (!expanded(plugin.name)) {
        for (const stage of new Set(contributions.map((item) => item.stage))) edges.push({ source: parent, target: stageId(stage), kind: 'binding' });
        y += blockHeight + 28;
        continue;
      }
      contributions.forEach((item, index) => {
        const child = add({ id: contributionId(item.id), kind: 'contribution', label: item.id,
          x: 316, y: y + index * 78, width: 310, height: 66, contribution: item });
        edges.push({ source: parent, target: child, kind: 'binding' });
        edges.push({ source: child, target: stageId(item.stage), kind: 'binding' });
      });
      interfaces.forEach((name, index) => {
        const child = add({ id: `interface:${plugin.name}:${index}`, kind: 'interface', label: name,
          x: 316, y: y + (index + contributions.length) * 78, width: 310, height: 66,
          plugin, interfaceName: name });
        edges.push({ source: parent, target: child, kind: 'binding' });
      });
      y += blockHeight + 28;
    }
    const stages = filter ? plan.stages.filter((stage) => ordered(stage).length) : plan.stages;
    stages.forEach((stage, index) => add({ id: stageId(stage.stage), kind: 'stage', label: stage.stage,
      x: 750, y: 64 + index * 104, width: 250, height: 66, stage }));
    return { nodes, edges, width: 1030, height: Math.max(y, 64 + stages.length * 104) };
  }

  let y = 40;
  for (const stage of plan.stages) {
    const contributions = ordered(stage);
    add({ id: stageId(stage.stage), kind: 'stage', label: stage.stage,
      x: 36, y, width: 250, height: 66, stage });
    const visible: PlanNode[] = [];
    const groups = new Set<string>();
    for (const item of contributions) {
      if (expanded(item.plugin)) {
        visible.push({ id: contributionId(item.id), kind: 'contribution', label: item.id,
          x: 0, y: 0, width: 310, height: 66, contribution: item });
      } else if (!groups.has(item.plugin)) {
        groups.add(item.plugin);
        const plugin = plugins.find((value) => value.name === item.plugin);
        if (plugin) visible.push({ id: `group:${stage.stage}:${item.plugin}`, kind: 'plugin', label: item.plugin,
          x: 0, y: 0, width: 310, height: 66, plugin,
          summary: summary(plugin, contributions.filter((value) => value.plugin === item.plugin)) });
      }
    }
    visible.forEach((node, index) => {
      const child = add({ ...node,
        x: 352 + (index % 2) * 346, y: y + Math.floor(index / 2) * 78, width: 310, height: 66,
      });
      edges.push({ source: stageId(stage.stage), target: child, kind: 'binding' });
      const previous = visible[index - 1]?.contribution;
      if (!groups.size && previous && node.contribution && previous.mode !== 'service' && node.contribution.mode !== 'service') edges.push({ source: contributionId(previous.id), target: child, kind: 'order' });
    });
    y += Math.max(110, Math.ceil(visible.length / 2) * 78 + 24);
    if (stage.stage === 'model_before' || stage.stage === 'tool_before') {
      add({ id: stage.stage !== 'tool_before' ? 'model-call' : 'tool-call', kind: 'execution',
        label: stage.stage !== 'tool_before' ? 'model-call' : 'tool-call', x: 36, y, width: 250, height: 66 });
      y += 104;
    }
  }
  const modelBefore = 'model_before';
  const modelAfter = 'model_after';
  const finalize = 'finalize';
  const spine = ['initialize', 'prepare', 'run_start', 'round_before', modelBefore, 'model-call', modelAfter,
    'tool_batch_before', 'tool_before', 'tool-call', 'tool_after', 'tool_batch_after', 'round_after'];
  const nodeId = (value: string) => value.endsWith('-call') ? value : stageId(value);
  const existing = new Set(nodes.map((node) => node.id));
  for (let index = 1; index < spine.length; index++) {
    const source = nodeId(spine[index - 1]); const target = nodeId(spine[index]);
    if (existing.has(source) && existing.has(target)) edges.push({ source, target, kind: 'flow' });
  }
  const branches: Array<[string, string, PlanEdge['label']]> = [
    [modelAfter, 'round_after', 'no-tools'], ['round_after', 'round_before', 'continue'],
    ['round_after', finalize, 'finish'], [finalize, 'run_end', 'end'],
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
