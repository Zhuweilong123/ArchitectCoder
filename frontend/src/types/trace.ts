import type { JsonObject } from './json';

export type TraceMessagePart = { text?: string; type?: string };
export type TraceModelToolCall = { id?: string; type?: string; function?: { name: string; arguments: string } };
export type TraceModelMessage = { role: string; content?: string | Array<string | TraceMessagePart> | null; tool_calls?: TraceModelToolCall[] };
export type TraceToolSchema = { type?: string; function?: { name: string; description?: string; parameters?: JsonObject } };
export type TraceUsage = { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; cached_tokens?: number };
export type TraceSchedulingPayload = { task_id?: string; owner?: string; source_run_id?: string };

/** Known presentation fields; unrecognized JSON fields are preserved by the decoder. */
export type TraceEvent = {
  event_type: string;
  span_id: string;
  tool_name: string;
  ts_ms?: number;
  span_path?: string;
  run_id?: string;
  background_task_id?: string;
  background_owner?: string;
  task_id?: string;
  owner?: string;
  source_run_id?: string;
  payload?: TraceSchedulingPayload;
  message?: string;
  project_file?: string;
  model?: string;
  system_prompt?: string;
  messages?: TraceModelMessage[];
  tools?: TraceToolSchema[];
  tool_calls?: TraceModelToolCall[];
  content?: string;
  usage?: TraceUsage;
  temperature?: number;
  max_tokens?: number;
  tool_choice?: string;
  response_format?: JsonObject;
  duration_ms?: number;
  arguments?: JsonObject;
  observation?: string;
  error?: string;
  fed_truncated?: boolean;
  fed_length?: number;
  actions?: string[];
  step?: number;
  thought?: string;
  answer?: string;
  runtime?: JsonObject;
  status?: string;
  summary?: string;
  tool_call_count?: number;
  review_type?: string;
  review_id?: number;
  response?: string;
  title?: string;
  question?: string;
  source?: string;
  phase?: string;
  file_count?: number;
  allowed?: boolean;
  decision_message?: string;
  changed_paths?: string[];
  paths?: string[];
  violations?: JsonObject[];
};
export interface TraceDetail { session_id: string; events: TraceEvent[]; }

export interface TraceReplayStepToolCall {
  name: string;
  arguments: JsonObject | string;
  observation: string;
}

export interface TraceReplayStep {
  step: number;
  thought: string;
  actions: string[];
  tool_calls: TraceReplayStepToolCall[];
  is_final: boolean;
}

export interface TraceReplayTurn {
  user_message: string;
  final_answer: string;
  recorded_answer: string | null;
  matches: boolean;
  error?: string | null;
  steps?: TraceReplayStep[];
  recorded_steps?: TraceReplayStep[];
}

export interface TraceReplayResult {
  session_id: string;
  mode: string;
  turns: TraceReplayTurn[];
  executed_turns: number;
  total_turns: number;
  llm_calls: number | null;
  llm_total: number;
  tool_calls: number;
  tool_total: number;
  all_matched: boolean;
}

export type TraceReplayMode = 'mock' | 'rerun' | 'live';
