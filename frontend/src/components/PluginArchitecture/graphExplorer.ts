import type { PluginExecutionPlan, PluginLoadReport } from '../../types/plugins';
import type { PlanNode, GraphView } from './pluginGraph';
import type { ReplayStep } from './replayModel';

export interface GraphIssue {
  id: string; nodeId: string; plugin: string; source: 'declaration' | 'load' | 'execution';
  message: string; code: string; stepIndex?: number; compatible?: boolean;
}

/** Public/notification contributions and unbound interfaces share stable search IDs. */
export function graphCatalog(plan: PluginExecutionPlan): PlanNode[] {
  const bindings = [...plan.stages, ...(plan.notifications || [])].flatMap((stage) => stage.contributions);
  const core = { name: 'core', provider: '', source: 'builtin', status: 'discovered' as const, error: '', interfaces: [],
    contributions: bindings.filter((item) => item.plugin === 'core').map((item) => item.id) };
  const geometry = { x: 0, y: 0, width: 0, height: 0 };
  const nodes: PlanNode[] = [];
  for (const plugin of [core, ...plan.plugins]) {
    nodes.push({ ...geometry, id: `plugin:${plugin.name}`, kind: 'plugin', label: plugin.name, plugin });
    plugin.interfaces.filter((name) => !plan.stages.some((stage) => stage.contributions.some((item) => item.interface_id === `${plugin.name}.${name}`)))
      .forEach((name, index) => nodes.push({ ...geometry, id: `interface:${plugin.name}:${index}`, kind: 'interface', label: name, plugin, interfaceName: name }));
  }
  for (const item of bindings) nodes.push({ ...geometry, id: `contribution:${item.id}`, kind: 'contribution', label: item.id, contribution: item });
  for (const stage of [...plan.stages, ...(plan.notifications || [])]) nodes.push({ ...geometry, id: `stage:${stage.stage}`, kind: 'stage', label: stage.stage, stage });
  for (const id of ['model-call', 'tool-call']) nodes.push({ ...geometry, id, kind: 'execution', label: id });
  return nodes;
}

export function searchGraph(catalog: PlanNode[], query: string): PlanNode[] {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return catalog.filter((node) => {
    const text = [node.label, node.id, node.interfaceName, node.plugin?.name, node.contribution?.plugin,
      node.contribution?.interface_id, node.contribution?.handler, node.contribution?.stage].filter(Boolean).join(' ').toLocaleLowerCase();
    return words.every((word) => text.includes(word));
  });
}

export interface GraphFocus { view: GraphView; filter: string; expandedPlugins: string[] }

/** Reveal a search/detail/issue target even when its plugin or stage is hidden. */
export function revealGraphNode(plan: PluginExecutionPlan, catalog: PlanNode[], id: string, state: GraphFocus): GraphFocus {
  const node = catalog.find((item) => item.id === id);
  if (!node) return state;
  const name = node.plugin?.name || node.contribution?.plugin;
  let view = state.view, filter = state.filter;
  const expandedPlugins = [...state.expandedPlugins];
  if (name) {
    if (!expandedPlugins.includes(name)) expandedPlugins.push(name);
    if (filter && filter !== name) filter = '';
  }
  if (node.kind === 'plugin' || node.kind === 'interface'
    || node.contribution && !plan.stages.some((stage) => stage.stage === node.contribution!.stage)) view = 'organization';
  if (node.kind === 'execution' || node.kind === 'stage' && plan.stages.some((stage) => stage.stage === node.label)) { view = 'schedule'; filter = ''; }
  return { view, filter, expandedPlugins };
}

export function graphIssues(plan: PluginExecutionPlan, report?: PluginLoadReport | null, steps: ReplayStep[] = []): GraphIssue[] {
  const issues: GraphIssue[] = [];
  for (const plugin of plan.plugins) {
    const runtime = report?.plan_id === plan.plan_id ? report.plugins.find((item) => item.name === plugin.name) : undefined;
    for (const [source, diagnostics, failed, fallback] of [
      ['declaration', plugin.diagnostics || [], plugin.status === 'unavailable', plugin.error],
      ['load', runtime?.diagnostics || [], runtime?.status === 'unavailable', 'Provider unavailable'],
    ] as const) {
      diagnostics.forEach((item, index) => issues.push({ id: `${source}:${plugin.name}:${index}`, nodeId: `plugin:${plugin.name}`,
        plugin: plugin.name, source, message: item.message, code: item.code }));
      if (failed && !diagnostics.length) issues.push({ id: `${source}:${plugin.name}`, nodeId: `plugin:${plugin.name}`,
        plugin: plugin.name, source, message: fallback, code: 'unavailable' });
    }
  }
  // Failure locations are scoped to the selected task, with plan compatibility preserved.
  steps.forEach((step, stepIndex) => {
    const event = step.event;
    if (event.event_type !== 'plugin_contribution' || !['error', 'failed'].includes(String(event.status))) return;
    issues.push({ id: `execution:${stepIndex}`, nodeId: `contribution:${String(event.contribution_id)}`, plugin: String(event.plugin || ''),
      source: 'execution', code: String(event.error_type || event.status), message: String(event.error_message || event.error || event.status),
      stepIndex, compatible: step.compatible });
  });
  return issues;
}
