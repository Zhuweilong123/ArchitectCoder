import type { PluginExecutionPlan } from '../../types/plugins';

export type ReplayEvent = Record<string, unknown>;
export interface ReplayRun { id: string; events: ReplayEvent[]; }
export interface ReplayStep { event: ReplayEvent; index: number; nodeIds: string[]; compatible: boolean; }

export function stepStatus(event: ReplayEvent): string {
  if (typeof event.status === 'string' && event.status) return event.status;
  if (event.error || event.error_message || event.event_type === 'error') return 'error';
  if (event.event_type === 'llm_request' || event.event_type === 'tool_call') return 'started';
  if (event.event_type === 'llm_response' || event.event_type === 'tool_result') return 'completed';
  const data = event.stage_data as ReplayEvent | undefined;
  return typeof data?.status === 'string' ? data.status : 'recorded';
}

export function contributionSummary(steps: ReplayStep[]) {
  const summary = { executed: 0, skipped: 0, error: 0, interrupted: 0, decisions: 0, durationMs: 0 };
  for (const { event } of steps) {
    if (event.event_type !== 'plugin_contribution') continue;
    const status = String(event.status);
    if (status === 'executed' || status === 'skipped' || status === 'error' || status === 'interrupted') summary[status]++;
    if (status !== 'skipped' && event.mode !== 'observer' && event.decision_action && event.decision_action !== 'continue') summary.decisions++;
    if (typeof event.duration_ms === 'number' && Number.isFinite(event.duration_ms) && event.duration_ms >= 0) summary.durationMs += event.duration_ms;
  }
  return summary;
}

// Match only earlier records in this task, so repeated phases resolve to the actual preceding decision.
export function stepExplanation(steps: ReplayStep[], index: number) {
  const event = steps[index]?.event || {};
  let blockerIndex = -1;
  if (event.blocked_by) {
    for (let i = index - 1; i >= 0; i--) {
      const previous = steps[i].event;
      if (previous.event_type === 'plugin_contribution' && previous.contribution_id === event.blocked_by
        && previous.stage === event.stage && previous.status !== 'skipped') { blockerIndex = i; break; }
    }
  }
  const blocker = blockerIndex >= 0 ? steps[blockerIndex].event : {};
  return {
    blockerIndex,
    action: event.decision_action || blocker.decision_action,
    reason: event.decision_reason || blocker.decision_reason,
    message: event.decision_message || blocker.decision_message,
    errorType: event.error_type,
    errorMessage: event.error_message || event.error,
    failureEffect: event.failure_effect,
  };
}

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
