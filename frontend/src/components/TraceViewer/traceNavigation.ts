export interface SearchDocument { id: string; value: unknown; }
export interface TraceSearchHit { id: string; field: string; text: string; offset: number; length: number; }

/** Tool-call commentary and child-agent output belong to the execution process. */
export function traceConversationText(item: {
  kind: string;
  request?: Record<string, any>;
  response?: Record<string, any>;
}): string {
  if (item.kind !== 'llm' || item.response?.error || item.request?.background_task_id || item.response?.background_task_id) return '';
  const response = item.response;
  if (Array.isArray(response?.tool_calls) && response.tool_calls.length > 0) return '';
  const path = String(item.request?.span_path || response?.span_path || '');
  if (path.split('/').some((segment) => segment.toLowerCase().includes('subagent')
    || segment.toLowerCase() === 'child_agent')) return '';
  return String(response?.content || '');
}

/** Index original values, including nested subagent events and untruncated output. */
export function searchTrace(documents: SearchDocument[], query: string): TraceSearchHit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const hits: TraceSearchHit[] = [];
  const visit = (id: string, value: unknown, field: string) => {
    if (typeof value === 'string') {
      const lower = value.toLowerCase();
      let offset = lower.indexOf(needle);
      while (offset !== -1) {
        hits.push({ id, field, text: value, offset, length: needle.length });
        offset = lower.indexOf(needle, offset + needle.length);
      }
    } else if (Array.isArray(value)) {
      value.forEach((child, i) => visit(id, child, `${field}[${i}]`));
    } else if (value && typeof value === 'object') {
      Object.entries(value).forEach(([key, child]) => visit(id, child, field ? `${field}.${key}` : key));
    }
  };
  documents.forEach(({ id, value }) => visit(id, value, ''));
  return hits;
}

/** Only consecutive process rows are grouped; visible conversation boundaries stay in order. */
export function groupTraceRows<T>(rows: T[], isProcess: (row: T) => boolean): T[][] {
  const groups: T[][] = [];
  for (const row of rows) {
    if (isProcess(row) && groups.length && isProcess(groups[groups.length - 1][0])) {
      groups[groups.length - 1].push(row);
    } else groups.push([row]);
  }
  return groups;
}

export function traceErrorCount(value: unknown): number {
  if (!value || typeof value !== 'object') return 0;
  if (Array.isArray(value)) return value.reduce((count, child) => count + traceErrorCount(child), 0);
  const event = value as Record<string, unknown>;
  const failed = event.kind === 'error' || Boolean(event.error)
    || event.allowed === false || ['failed', 'timed_out', 'block'].includes(String(event.status));
  // An error Item wraps its event; count the event once.
  if (event.kind === 'error') return 1;
  return Number(failed) + Object.values(event).reduce<number>((count, child) => count + traceErrorCount(child), 0);
}
