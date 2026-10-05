import type { PluginExecutionPlan } from '../../types/plugins';

export type ReplayEvent = Record<string, unknown>;
export interface ReplayRun { id: string; events: ReplayEvent[]; }
export interface ReplayStep { event: ReplayEvent; index: number; nodeIds: string[]; compatible: boolean; }

// Preserve JSONL order: timestamps can coincide, and nested agents interleave.
export function replayRuns(events: ReplayEvent[]): ReplayRun[] {
  const runs = new Map<string, ReplayEvent[]>();
  for (const event of events) {
    if (typeof event.run_id !== 'string' || !event.run_id) continue;
    const list = runs.get(event.run_id) || [];
    list.push(event); runs.set(event.run_id, list);
  }
  return [...runs].filter(([, list]) => list.some((event) =>
    event.event_type === 'lifecycle_stage' || event.event_type === 'plugin_contribution'))
    .map(([id, list]) => ({ id, events: list }));
}

export function replaySteps(run: ReplayRun, plan: PluginExecutionPlan): ReplayStep[] {
  const types = new Set(['lifecycle_stage', 'plugin_contribution', 'llm_request', 'llm_response', 'tool_call', 'tool_result', 'error']);
  const planIds = new Set(run.events.map((event) => event.plan_id).filter((id) => typeof id === 'string' && id));
  const compatible = planIds.size === 1 && planIds.has(plan.plan_id);
  const ids = new Set(plan.stages.flatMap((stage) => stage.contributions.map((item) => item.id)));
  return run.events.flatMap((event, index) => {
    if (!types.has(String(event.event_type))) return [];
    const nodeIds: string[] = [];
    if (compatible) {
      if (typeof event.stage === 'string' && plan.stages.some((stage) => stage.stage === event.stage)) nodeIds.push(`stage:${event.stage}`);
      if (typeof event.contribution_id === 'string' && ids.has(event.contribution_id)) nodeIds.push(`contribution:${event.contribution_id}`);
      if (event.event_type === 'llm_request' || event.event_type === 'llm_response') nodeIds.push('model-call');
      if (event.event_type === 'tool_call' || event.event_type === 'tool_result') nodeIds.push('tool-call');
    }
    return [{ event, index, nodeIds, compatible }];
  });
}
