import type { JsonObject, JsonValue } from '../types/json';
import type { TraceEvent, TraceDetail, TraceModelMessage, TraceMessagePart, TraceModelToolCall, TraceToolSchema, TraceUsage,
  TraceReplayResult, TraceReplayStep, TraceReplayTurn } from '../types/trace';

function isJson(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJson);
  if (typeof value !== 'object' || value === null) return false;
  const prototype = Object.getPrototypeOf(value);
  return (prototype === Object.prototype || prototype === null) && Object.values(value).every(isJson);
}
export function traceJsonObject(value: unknown): JsonObject | undefined {
  return isJson(value) && value !== null && typeof value === 'object' && !Array.isArray(value) ? value : undefined;
}
const text = (value: unknown): string | undefined => typeof value === 'string' ? value : undefined;
const number = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const boolean = (value: unknown): boolean | undefined => typeof value === 'boolean' ? value : undefined;
const strings = (value: unknown): string[] | undefined => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined;
const objects = (value: unknown): JsonObject[] | undefined => Array.isArray(value) ? value.flatMap(item => {
  const object = traceJsonObject(item); return object ? [object] : [];
}) : undefined;
function toolCalls(value: unknown): TraceModelToolCall[] | undefined {
  return objects(value)?.map(item => {
    const fn = traceJsonObject(item.function);
    return { ...item, id: text(item.id), type: text(item.type), function: fn ? {
      ...fn, name: text(fn.name) || '', arguments: text(fn.arguments) || '',
    } : undefined };
  });
}
function messages(value: unknown): TraceModelMessage[] | undefined {
  return objects(value)?.map(item => ({ ...item, role: text(item.role) || '?',
    content: Array.isArray(item.content) ? item.content.flatMap<string | TraceMessagePart>(part => {
      if (typeof part === 'string') return [part];
      const block = traceJsonObject(part); return block ? [{ ...block, text: text(block.text), type: text(block.type) }] : [];
    }) : text(item.content) ?? null,
    tool_calls: toolCalls(item.tool_calls),
  }));
}
function tools(value: unknown): TraceToolSchema[] | undefined {
  return objects(value)?.map(item => {
    const fn = traceJsonObject(item.function);
    return { ...item, type: text(item.type), function: fn ? { ...fn, name: text(fn.name) || '?',
      description: text(fn.description), parameters: traceJsonObject(fn.parameters),
    } : undefined };
  });
}
function usage(value: unknown): TraceUsage | undefined {
  const item = traceJsonObject(value);
  return item ? { ...item, prompt_tokens: number(item.prompt_tokens), completion_tokens: number(item.completion_tokens),
    total_tokens: number(item.total_tokens), cached_tokens: number(item.cached_tokens) } : undefined;
}
export function normalizeTraceEvent(value: unknown): TraceEvent | null {
  const item = traceJsonObject(value);
  if (!item || !text(item.event_type)) return null;
  const payload = traceJsonObject(item.payload);
  return { ...item, event_type: text(item.event_type) || '', span_id: text(item.span_id) || '', tool_name: text(item.tool_name) || '',
    ts_ms: number(item.ts_ms), span_path: text(item.span_path), run_id: text(item.run_id),
    background_task_id: text(item.background_task_id), background_owner: text(item.background_owner),
    task_id: text(item.task_id), owner: text(item.owner), source_run_id: text(item.source_run_id),
    payload: payload ? { ...payload, task_id: text(payload.task_id), owner: text(payload.owner), source_run_id: text(payload.source_run_id) } : undefined,
    message: text(item.message), project_file: text(item.project_file), model: text(item.model), system_prompt: text(item.system_prompt),
    messages: messages(item.messages), tools: tools(item.tools), tool_calls: toolCalls(item.tool_calls), content: text(item.content), usage: usage(item.usage),
    temperature: number(item.temperature), max_tokens: number(item.max_tokens), tool_choice: text(item.tool_choice), response_format: traceJsonObject(item.response_format),
    duration_ms: number(item.duration_ms), arguments: traceJsonObject(item.arguments), observation: text(item.observation), error: text(item.error),
    fed_truncated: boolean(item.fed_truncated), fed_length: number(item.fed_length), actions: strings(item.actions), step: number(item.step), thought: text(item.thought),
    answer: text(item.answer), runtime: traceJsonObject(item.runtime), status: text(item.status), summary: text(item.summary), tool_call_count: number(item.tool_call_count),
    review_type: text(item.review_type), review_id: number(item.review_id), response: text(item.response), title: text(item.title), question: text(item.question),
    source: text(item.source), phase: text(item.phase), file_count: number(item.file_count), allowed: boolean(item.allowed), decision_message: text(item.decision_message),
    changed_paths: strings(item.changed_paths), paths: strings(item.paths), violations: objects(item.violations),
  };
}
export function normalizeTraceDetail(value: unknown): TraceDetail {
  const item = traceJsonObject(value);
  if (!item || typeof item.session_id !== 'string' || !Array.isArray(item.events)) throw new Error('Invalid trace detail');
  return { session_id: item.session_id, events: item.events.flatMap(event => {
    const normalized = normalizeTraceEvent(event); return normalized ? [normalized] : [];
  }) };
}
function step(value: unknown): TraceReplayStep {
  const item = traceJsonObject(value);
  if (!item) throw new Error('Invalid replay step');
  return { step: number(item.step) || 0, thought: text(item.thought) || '', actions: strings(item.actions) || [],
    is_final: boolean(item.is_final) || false, tool_calls: (objects(item.tool_calls) || []).map(call => ({
      name: text(call.name) || '', arguments: text(call.arguments) ?? traceJsonObject(call.arguments) ?? {}, observation: text(call.observation) || '',
    })) };
}
function turn(value: unknown): TraceReplayTurn {
  const item = traceJsonObject(value);
  if (!item || typeof item.user_message !== 'string' || typeof item.final_answer !== 'string' || typeof item.matches !== 'boolean') throw new Error('Invalid replay turn');
  for (const field of ['steps', 'recorded_steps']) if (item[field] !== undefined && item[field] !== null && !Array.isArray(item[field])) throw new Error('Invalid replay steps');
  return { user_message: item.user_message, final_answer: item.final_answer, matches: item.matches,
    recorded_answer: text(item.recorded_answer) ?? null, error: text(item.error) ?? null,
    steps: Array.isArray(item.steps) ? item.steps.map(step) : undefined,
    recorded_steps: Array.isArray(item.recorded_steps) ? item.recorded_steps.map(step) : undefined,
  };
}
export function normalizeTraceReplay(value: unknown): TraceReplayResult {
  const item = traceJsonObject(value);
  if (!item || typeof item.session_id !== 'string' || !['mock', 'rerun', 'live'].includes(text(item.mode) || '') || !Array.isArray(item.turns)
    || typeof item.all_matched !== 'boolean') throw new Error('Invalid replay result');
  return { session_id: item.session_id, mode: text(item.mode) || 'mock', turns: item.turns.map(turn), all_matched: item.all_matched,
    executed_turns: number(item.executed_turns) ?? item.turns.length, total_turns: number(item.total_turns) ?? item.turns.length,
    llm_calls: number(item.llm_calls) ?? null, llm_total: number(item.llm_total) ?? 0,
    tool_calls: number(item.tool_calls) ?? 0, tool_total: number(item.tool_total) ?? 0,
  };
}
