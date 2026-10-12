import type { TraceEvent } from '../../types/trace';

export interface LlmItem { kind: 'llm'; request: TraceEvent; response?: TraceEvent; }
export interface ToolItem { kind: 'tool'; call: TraceEvent; result?: TraceEvent; subagent?: SubagentItem; }
export interface StepItem { kind: 'step'; event: TraceEvent; }
export interface DoneItem { kind: 'done'; event: TraceEvent; }
export interface ErrorItem { kind: 'error'; event: TraceEvent; }
export interface SummaryItem { kind: 'summary'; event: TraceEvent; }
export interface ReviewItem { kind: 'review'; event: TraceEvent; }
export interface LifecycleItem { kind: 'lifecycle'; event: TraceEvent; }
export interface SubagentItem {
  kind: 'subagent';
  spanPath: string;
  items: Item[];
}
export interface BackgroundItem {
  kind: 'background';
  taskId: string;
  owner: string;
  event: TraceEvent;
  items: Item[];
}

export type Item = BackgroundItem | LlmItem | ToolItem | StepItem | DoneItem | ErrorItem | SummaryItem | ReviewItem | LifecycleItem | SubagentItem;

export interface Turn { id: number; userMessage: string; projectFile: string; items: Item[]; }
export function buildTurns(events: TraceEvent[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn = { id: 0, userMessage: '', projectFile: '', items: [] };
  turns.push(cur);
  const llmBySpan = new Map<string, LlmItem>();
  const toolBySpan = new Map<string, ToolItem>();
  const turnByRun = new Map<string, Turn>();
  const backgrounds = new Map<string, BackgroundItem>();
  const recordedBackgrounds = new Set(events.map((event) => event.background_task_id).filter(Boolean));
  const backgroundFor = (taskId: string, owner: string, runId: string) => {
    let item = backgrounds.get(taskId);
    if (!item) {
      item = { kind: 'background', taskId, owner, event: { event_type: 'background_pending', span_id: '', tool_name: '', status: 'pending' }, items: [] };
      backgrounds.set(taskId, item);
      (turnByRun.get(runId) || cur).items.push(item);
    }
    return item;
  };

  for (const ev of events) {
    if (ev.event_type === 'background_scheduled') {
      const data = ev.payload || ev;
      // Legacy scheduling events refer to a separate file, not an attached task.
      if (data.task_id && recordedBackgrounds.has(data.task_id)) {
        backgroundFor(data.task_id, data.owner || '', data.source_run_id || ev.run_id || '');
      }
      continue;
    }
    const background = ev.background_task_id
      ? backgroundFor(ev.background_task_id, ev.background_owner || '', ev.run_id || '') : undefined;
    if (background && ['background_started', 'background_result'].includes(ev.event_type)) {
      background.event = ev;
      continue;
    }
    const items = background ? background.items : cur.items;
    switch (ev.event_type) {
      case 'user_message':
        cur = { id: turns.length, userMessage: ev.message || '', projectFile: ev.project_file || '', items: [] };
        turns.push(cur);
        if (ev.run_id) turnByRun.set(ev.run_id, cur);
        break;
      case 'llm_request': {
        const item: LlmItem = { kind: 'llm', request: ev };
        if (ev.span_id) llmBySpan.set(ev.span_id, item);
        items.push(item);
        break;
      }
      case 'llm_response': {
        const item = ev.span_id ? llmBySpan.get(ev.span_id) : undefined;
        if (item) item.response = ev;
        else items.push({ kind: 'llm', request: ev, response: ev });
        break;
      }
      case 'tool_call': {
        const item: ToolItem = { kind: 'tool', call: ev };
        if (ev.span_id) toolBySpan.set(ev.span_id, item);
        items.push(item);
        break;
      }
      case 'tool_result': {
        const item = ev.span_id ? toolBySpan.get(ev.span_id) : undefined;
        if (item) item.result = ev;
        else items.push({ kind: 'tool', call: ev, result: ev });
        break;
      }
      case 'agent_step':
        items.push({ kind: 'step', event: ev });
        break;
      case 'done':
        items.push({ kind: 'done', event: ev });
        break;
      case 'error':
        items.push({ kind: 'error', event: ev });
        break;
      case 'task_summary':
        items.push({ kind: 'summary', event: ev });
        break;
      case 'review_request':
      case 'review_response':
        items.push({ kind: 'review', event: ev });
        break;
      case 'contract_check':
      case 'candidate_artifact':
      case 'candidate_rollback':
      case 'candidate_restore':
      case 'changes_committed':
      case 'contract_graph_sync':
        items.push({ kind: 'lifecycle', event: ev });
        break;
      default:
        // session_start / session_end / review_* / kg_inject：暂不在时间轴单独渲染
        break;
    }
  }
  return turns
    .filter((t) => t.items.length > 0 || t.userMessage)
    .map((turn) => ({ ...turn, items: groupSubagentItems(turn.items) }));
}

function eventSpanPath(item: Item): string {
  if (item.kind === 'llm') return String(item.request.span_path || '');
  if (item.kind === 'tool') return String(item.call.span_path || '');
  return '';
}

function isSubagentEvent(item: Item): boolean {
  const path = eventSpanPath(item);
  return path.split('/').some((segment) => segment.toLowerCase().includes('subagent') || segment.toLowerCase() === 'child_agent');
}

function groupSubagentItems(items: Item[]): Item[] {
  const grouped: Item[] = [];
  const pending: SubagentItem[] = [];
  let activeChildItems: Item[] = [];
  let lastSpawnTool: ToolItem | undefined;

  const flushChildItems = () => {
    if (activeChildItems.length === 0) return;
    const subagent: SubagentItem = {
      kind: 'subagent',
      spanPath: eventSpanPath(activeChildItems[0]) || 'subagent',
      items: activeChildItems,
    };
    // Depending on when the trace sink flushes events, the parent tool_call can
    // be recorded either before or after the nested child LLM events.
    if (lastSpawnTool && !lastSpawnTool.subagent) lastSpawnTool.subagent = subagent;
    else pending.push(subagent);
    activeChildItems = [];
  };

  for (const item of items) {
    if (isSubagentEvent(item)) {
      activeChildItems.push(item);
      continue;
    }
    flushChildItems();
    if (item.kind === 'tool' && ['spawn_subagent', 'route_architecture'].includes(item.call.tool_name)) {
      lastSpawnTool = item;
      const waiting = pending.shift();
      if (waiting) item.subagent = waiting;
    }
    grouped.push(item);
  }
  flushChildItems();

  // Keep unmatched child spans visible instead of dropping them. This also
  // supports older traces that do not contain the parent tool_call event.
  for (const orphan of pending) grouped.push(orphan);
  return grouped;
}

// ── 渲染子组件 ────────────────────────────────────────
