import type { JsonObject, JsonValue } from './json';

/** Checker fields are optional while the user is editing an incomplete criterion. */
export interface CheckerEvidence {
  turn_index?: number;
  observed_tools?: string[];
  tool_calls?: number;
  tool_errors?: number;
}
export interface CheckerConfig {
  type: string;
  path?: string;
  text?: string;
  field?: string;
  expected?: JsonValue;
  args?: string[];
  timeout?: number;
  texts?: string[];
  groups?: Array<{ id: string; any_of: string[] }>;
  max_tool_calls?: number;
  required_tools?: string[];
  forbidden_tools?: string[];
  paths?: string[];
  diagram?: string;
  kind?: string;
  name?: string;
  names?: string[];
  source?: string;
  target?: string;
  relation_type?: string;
  class_name?: string;
  method?: string;
  params?: string[];
  return_type?: string;
  labels?: string[];
  source_turn_index?: number;
  _reason?: string;
  _evidence?: JsonObject & CheckerEvidence;
  // Custom providers may add serializable fields; executable values are forbidden.
  [field: string]: JsonValue | undefined;
}
export interface CheckerResult {
  checker: string;
  passed: boolean;
  score: number;
  message: string;
  details: JsonObject;
}
export interface EvalProjectManifest {
  id: string;
  version: string;
  fixture: string;
  base_fixture: string;
  entry_file: string;
  source_dir: string;
  test_dir: string;
  protected_paths: string[];
  allowed_write_paths: string[];
}
export interface TraceTurnSummary {
  source_turn_index: number;
  prompt: string;
  answer: string;
  tool_errors: number;
  failures: Array<{ tool_name: string; error: string }>;
}
export interface TraceSummary {
  events: number;
  tool_calls: number;
  turn_details: TraceTurnSummary[];
}
