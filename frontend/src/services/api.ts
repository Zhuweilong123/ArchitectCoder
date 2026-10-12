import { normalizeTraceDetail, normalizeTraceReplay } from '../utils/traceNormalization';
import type { TraceDetail, TraceReplayResult, TraceReplayMode } from '../types/trace';
export type { TraceDetail, TraceReplayResult, TraceReplayMode, TraceReplayStep, TraceReplayTurn, TraceReplayStepToolCall } from '../types/trace';
/** API service – communicates with the FastAPI backend. */

import axios from 'axios';
import type { JsonObject } from '../types/json';
import type { CheckerConfig, CheckerResult, EvalProjectManifest, TraceSummary } from '../types/evaluation';
import type { UmlDiagram, Project } from '../types/uml';
import type { PluginExecutionPlan, PluginLoadReport, PluginRefreshResult } from '../types/plugins';

// Read auth token from Vite env var (VITE_API_TOKEN in .env.local)
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
  headers: API_TOKEN
    ? { Authorization: `Bearer ${API_TOKEN}` }
    : {},
});

export async function getPluginPlan(signal?: AbortSignal): Promise<PluginExecutionPlan> {
  const { data } = await api.get<PluginExecutionPlan>('/plugins/plan', { signal });
  if (data.schema_version !== 1 || !Array.isArray(data.plugins) || !Array.isArray(data.stages)) {
    throw new Error('Unsupported plugin execution plan');
  }
  return data;
}

export async function getPluginDiagnostics(signal?: AbortSignal): Promise<PluginLoadReport> {
  const { data } = await api.get<PluginLoadReport>('/plugins/diagnostics', { signal });
  return data;
}

export async function refreshPlugins(): Promise<PluginRefreshResult> {
  const { data } = await api.post<PluginRefreshResult>('/plugins/refresh', null, {
    timeout: 120000, validateStatus: (status) => status === 200 || status === 409,
  });
  return data;
}

// ─── Files ──────────────────────────────────────────────

export async function listDiagrams(): Promise<Array<{
  name: string; path: string; size: number; modified: string;
}>> {
  const { data } = await api.get<{ files: Array<{
  name: string; path: string; size: number; modified: string;
}> }>('/files/list');
  return data.files;
}

export async function saveDiagram(diagram: UmlDiagram, filename?: string): Promise<{
  success: boolean; filepath: string; filename: string;
}> {
  const params = filename ? `?filename=${encodeURIComponent(filename)}` : '';
  const { data } = await api.post<{
  success: boolean; filepath: string; filename: string;
}>(`/files/save${params}`, diagram);
  return data;
}

export async function openDiagram(filepath: string, safe = true): Promise<UmlDiagram> {
  const { data } = await api.get<{ diagram: UmlDiagram }>('/files/open', { params: { filepath, safe } });
  return data.diagram;
}

export async function exportMarkdown(diagram: UmlDiagram): Promise<string> {
  const { data } = await api.post<string>('/files/export/markdown', { diagram });
  return data;
}

// ─── Browse directories ────────────────────────────────

export interface BrowseResult {
  current: string;
  parent: string;
  dirs: Array<{ name: string; path: string }>;
  files: Array<{
    name: string; path: string; size: number; modified: string; type?: string;
  }>;
}

export async function browseDirectory(path?: string, safe = true): Promise<BrowseResult> {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  if (!safe) params.set('safe', 'false');
  const qs = params.toString();
  const { data } = await api.get<BrowseResult>(`/files/browse${qs ? '?' + qs : ''}`);
  return data;
}

export async function validateWorkspacePath(
  path: string,
  kind: 'directory' | 'file' = 'directory',
): Promise<{ valid: boolean; path: string; error?: string | null }> {
  const { data } = await api.get<{ valid: boolean; path: string; error?: string | null }>('/files/validate-workspace', {
    params: { path, kind },
  });
  return data;
}

// ─── UML Review ─────────────────────────────────────

export async function saveReview(review: {
  action: 'accept' | 'reject';
  comment: string;
  requirements: string;
  original_name: string;
  optimized_name: string;
  timestamp: string;
}): Promise<{ success: boolean; file: string }> {
  const { data } = await api.post<{ success: boolean; file: string }>('/files/save-review', review);
  return data;
}

// ─── Project (.umlproj) ─────────────────────────────────

export async function saveProject(
  project: Project, filename?: string, safe = true,
): Promise<{
  success: boolean; filepath: string; filename: string; revision: number;
}> {
  const params = new URLSearchParams();
  if (filename) params.set('filename', filename);
  params.set('safe', String(safe));
  if (project.revision !== undefined) {
    params.set('expected_revision', String(project.revision));
  }
  const { data } = await api.post<{
  success: boolean; filepath: string; filename: string; revision: number;
}>(`/files/save-project?${params.toString()}`, project);
  return data;
}

export async function openProject(filepath: string, safe = true): Promise<Project> {
  const { data } = await api.get<{ project: Project }>('/files/open-project', { params: { filepath, safe } });
  return data.project;
}

export async function listProjects(): Promise<Array<{
  name: string; path: string; size: number; modified: string;
}>> {
  const { data } = await api.get<{ projects: Array<{
  name: string; path: string; size: number; modified: string;
}> }>('/files/list-projects');
  return data.projects;
}

// ─── Trace ─────────────────────────────────────────────

export interface TraceMeta {
  session_id: string;
  filename: string;
  date?: string;
  relative_path?: string;
  size: number;
  modified: string;
  events: number;
  first_ts_ms: number | null;
  last_ts_ms: number | null;
  title?: string;
  trace_type?: 'chat' | 'evaluation';
}


export interface TraceCaseProject {
  id: string;
  version: string;
  fixture: string;
  source_dir: string;
  test_dir: string;
}

export interface TraceCaseTurn {
  prompt: string;
  checkers: CheckerConfig[];
  hard_checkers: CheckerConfig[];
  metadata: JsonObject;
}

export interface TraceCaseDraft {
  draft_id: string;
  schema_version: string;
  status: string;
  created_at: string;
  session_id: string;
  trace_sha256: string;
  trace_summary: TraceSummary;
  workspace: Record<string, string>;
  candidate_checkers: CheckerConfig[];
  warnings: string[];
  case: {
    id: string;
    name: string;
    prompt: string;
    turns?: TraceCaseTurn[];
    project_id: string;
    checkers: CheckerConfig[];
    hard_checkers: CheckerConfig[];
    metadata: JsonObject;
  };
  validation?: EvalResult | null;
  capture?: {
    capture_id: string;
    project_id: string;
    fixture: string;
    sha256: string;
    file_count: number;
    manifest: EvalProjectManifest;
    origin?: string;
    baseline_confirmed?: boolean;
  } | null;
}

export async function listTraceCaseProjects(): Promise<TraceCaseProject[]> {
  const { data } = await api.get<{ projects: TraceCaseProject[] }>('/evals/trace-cases/projects');
  return data.projects;
}

export async function listTraceCaseDrafts(): Promise<TraceCaseDraft[]> {
  const { data } = await api.get<{ drafts: TraceCaseDraft[] }>('/evals/trace-cases/drafts');
  return data.drafts;
}

export async function getTraceCaseDraft(draftId: string): Promise<TraceCaseDraft> {
  const { data } = await api.get<TraceCaseDraft>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}`);
  return data;
}

export async function deleteTraceCaseDraft(draftId: string): Promise<{ draft_id: string; deleted: boolean }> {
  const { data } = await api.delete<{ draft_id: string; deleted: boolean }>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}`, { timeout: 15000 });
  return data;
}
export async function createTraceCaseDraft(req: {
  session_id: string;
  case_id?: string;
  name?: string;
  project_id?: string;
  suite?: string;
  include_trace_policy?: boolean;
}): Promise<TraceCaseDraft> {
  const { data } = await api.post<TraceCaseDraft>('/evals/trace-cases/drafts', req, { timeout: 15000 });
  return data;
}

export interface TraceCaseFixturePreview {
  draft_id: string;
  project_id: string;
  version: string;
  root: string;
  manifest: EvalProjectManifest;
  files: Array<{ path: string; size: number }>;
  file_count: number;
  total_bytes: number;
  truncated: boolean;
  project_exists: boolean;
  warnings: string[];
}

export async function previewTraceCaseFixture(draftId: string, req: {
  project_id?: string;
  version?: string;
} = {}): Promise<TraceCaseFixturePreview> {
  const { data } = await api.post<TraceCaseFixturePreview>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}/fixture-preview`, req, { timeout: 30000 });
  return data;
}

export async function captureTraceCaseFixture(draftId: string, req: {
  project_id?: string;
  version?: string;
} = {}): Promise<TraceCaseDraft> {
  const { data } = await api.post<TraceCaseDraft>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}/capture-fixture`, req, { timeout: 30000 });
  return data;
}
export async function reviewTraceCaseDraft(draftId: string, req: {
  name?: string;
  project_id?: string;
  checkers?: CheckerConfig[];
  hard_checkers?: CheckerConfig[];
  turns?: TraceCaseTurn[];
  fixture_state_confirmed?: boolean;
}): Promise<TraceCaseDraft> {
  const { data } = await api.put<TraceCaseDraft>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}`, req, { timeout: 15000 });
  return data;
}
export async function validateTraceCaseDraft(draftId: string): Promise<TraceCaseDraft> {
  const { data } = await api.post<TraceCaseDraft>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}/validate`, null, { timeout: 3600000 });
  return data;
}

export async function publishTraceCaseDraft(draftId: string, req: {
  case_id?: string;
  name?: string;
  suite?: string;
} = {}): Promise<{ draft_id: string; case_id: string; path: string; case: EvalCaseInfo }> {
  const { data } = await api.post<{ draft_id: string; case_id: string; path: string; case: EvalCaseInfo }>(`/evals/trace-cases/drafts/${encodeURIComponent(draftId)}/publish`, req, { timeout: 15000 });
  return data;
}
export async function listTraces(signal?: AbortSignal): Promise<TraceMeta[]> {
  const { data } = await api.get<{ traces: TraceMeta[] }>('/trace/list', { signal });
  return data.traces;
}

export async function getTrace(sessionId: string, traceType?: TraceMeta['trace_type'], signal?: AbortSignal): Promise<TraceDetail> {
  const { data } = await api.get<unknown>(`/trace/${encodeURIComponent(sessionId)}`, {
    params: traceType ? { trace_type: traceType } : undefined,
    signal,
  });
  return normalizeTraceDetail(data);
}

export interface TraceHistoryEntry {
  role: 'user' | 'assistant' | 'summary';
  content: string;
  metadata?: Record<string, unknown>;
}

export async function getTraceHistory(sessionId: string): Promise<TraceHistoryEntry[]> {
  const { data } = await api.get<{ history: TraceHistoryEntry[] }>(`/trace/${encodeURIComponent(sessionId)}/history`);
  return data.history;
}

// ── Evaluation center ───────────────────────────────────────────────────────

export interface EvalCaseInfo {
  schema_version: string;
  tool_protocol_version: string;
  id: string;
  agent: string;
  name: string;
  prompt: string;
  project_id: string;
  checkers: CheckerConfig[];
  hard_checkers: CheckerConfig[];
  metadata: JsonObject;
}

export interface EvalSummary {
  total: number;
  completed: number;
  passed: number;
  failed: number;
  timeout: number;
  errors: number;
  pass_rate: number;
  average_score: number;
  average_duration_ms: number;
  total_tokens: number;
  total_tool_calls: number;
  prompt_tokens: number;
  cached_prompt_tokens: number;
  prompt_cache_requests: number;
  prompt_cache_hit_rate: number | null;
  prompt_prefix_chars: number;
  reused_prompt_prefix_chars: number;
  prompt_prefix_requests: number;
  prompt_prefix_reuse_rate: number | null;
  failure_categories?: Record<string, number>;
}

export interface EvalBaselineGroup {
  name: string;
  total: number;
  passed: number;
  failed: number;
  timeout: number;
  pass_rate: number;
  average_score: number;
}

export interface EvalBaseline {
  agent: string;
  label: string;
  version: string;
  model: string;
  captured_at: string;
  case_ids?: string[];
  case_count: number;
  passed: number;
  failed: number;
  timeout: number;
  errors?: number;
  pass_rate: number;
  average_score: number;
  total_duration_ms: number;
  total_tokens: number;
  total_tool_calls: number;
  groups: EvalBaselineGroup[];
}

export interface EvalRepositoryInfo {
  branch: string;
  commit: string;
  version: string;
  dirty: boolean;
}

export interface EvalResult {
  run_id: string;
  case_id: string;
  agent: string;
  status: string;
  passed: boolean;
  score: number;
  started_at: string;
  duration_ms: number;
  model: string;
  tool_calls: number;
  total_tokens: number;
  prompt_tokens?: number;
  cached_prompt_tokens?: number;
  prompt_cache_requests?: number;
  prompt_prefix_chars?: number;
  reused_prompt_prefix_chars?: number;
  prompt_prefix_requests?: number;
  trace_id?: string;
  trace_path?: string;
  checker_results: CheckerResult[];
  error: string;
  failure_category?: 'none' | 'agent_failure' | 'tool_failure' | 'environment_failure' | 'checker_failure' | 'timeout' | 'budget_exceeded';
}

export interface EvalBatch {
  batch_id: string;
  agent: string;
  suite: string;
  version: string;
  label: string;
  case_ids: string[];
  status: string;
  started_at: string;
  finished_at: string;
  current_case_id: string;
  results: EvalResult[];
  summary: EvalSummary;
  error: string;
  performance_result_id?: string;
  source_batch_ids?: string[];
}

export interface EvalTrend {
  batch_id: string;
  agent: string;
  version: string;
  label: string;
  suite: string;
  status: string;
  started_at: string;
  finished_at: string;
  case_ids: string[];
  summary: EvalSummary;
}

export interface EvalArchive {
  archive_id: string;
  created_at: string;
  note: string;
  batch_id: string;
  agent: string;
  version: string;
  suite: string;
  started_at: string;
  finished_at: string;
  summary: EvalSummary;
}

export interface EvalPerformanceRun {
  result_id: string;
  file_name: string;
  source_path: string;
  version: string;
  suite: string;
  started_at: string;
  finished_at: string;
  result_count: number;
  summary: EvalSummary;
  archived: boolean;
  results?: EvalResult[];
}

export async function listEvalCases(): Promise<EvalCaseInfo[]> {
  const { data } = await api.get<{ cases: EvalCaseInfo[] }>('/evals/cases');
  return data.cases;
}

export async function getEvalBaseline(): Promise<EvalBaseline> {
  const { data } = await api.get<EvalBaseline>('/evals/baseline');
  return data;
}

export async function getEvalRepository(): Promise<EvalRepositoryInfo> {
  const { data } = await api.get<EvalRepositoryInfo>('/evals/repository');
  return data;
}

export async function archiveEvalBaseline(note = ''): Promise<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}> {
  const { data } = await api.post<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}>('/evals/baseline/archive', { note }, { timeout: 15000 });
  return data;
}

export async function startEvalBatch(req: {
  suite?: string;
  case_ids?: string[];
  version: string;
  label?: string;
}): Promise<EvalBatch> {
  const { data } = await api.post<EvalBatch>('/evals/runs', req, { timeout: 15000 });
  return data;
}

export async function getEvalBatch(batchId: string): Promise<EvalBatch> {
  const { data } = await api.get<EvalBatch>(`/evals/runs/${encodeURIComponent(batchId)}`, { timeout: 15000 });
  return data;
}

export async function deleteEvalBatch(batchId: string): Promise<{ batch_id: string }> {
  const { data } = await api.delete<{ batch_id: string }>(`/evals/runs/${encodeURIComponent(batchId)}`, { timeout: 15000 });
  return data;
}

export async function mergeEvalBatches(req: {
  batch_ids: string[];
  version?: string;
  label?: string;
}): Promise<EvalBatch> {
  const { data } = await api.post<EvalBatch>('/evals/runs/merge', req, { timeout: 15000 });
  return data;
}

export async function listEvalTrends(limit = 20): Promise<EvalTrend[]> {
  const { data } = await api.get<{ trends: EvalTrend[] }>('/evals/trends', { params: { limit } });
  return data.trends;
}

export async function archiveEvalBatch(batchId: string, note = ''): Promise<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}> {
  const { data } = await api.post<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}>('/evals/archives', { batch_id: batchId, note }, { timeout: 15000 });
  return data;
}

export async function listEvalArchives(limit = 20): Promise<EvalArchive[]> {
  const { data } = await api.get<{ archives: EvalArchive[] }>('/evals/archives', { params: { limit } });
  return data.archives;
}

export async function listEvalPerformanceResults(limit = 20): Promise<EvalPerformanceRun[]> {
  const { data } = await api.get<{ results: EvalPerformanceRun[] }>('/evals/performance', { params: { limit } });
  return data.results;
}

export async function getEvalPerformanceResult(resultId: string): Promise<EvalPerformanceRun> {
  const { data } = await api.get<EvalPerformanceRun>('/evals/performance/detail', { params: { result_id: resultId } });
  return data;
}

export async function deleteEvalPerformanceResult(resultId: string): Promise<{ result_id: string; path: string }> {
  const { data } = await api.delete<{ result_id: string; path: string }>('/evals/performance', { params: { result_id: resultId }, timeout: 15000 });
  return data;
}

export async function archiveEvalPerformanceResult(resultId: string, version = '', note = ''): Promise<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}> {
  const { data } = await api.post<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}>('/evals/performance/archive', { result_id: resultId, version, note }, { timeout: 15000 });
  return data;
}

export async function replayTrace(
  sessionId: string, mode: TraceReplayMode = 'mock', turn?: number,
  tool_policy?: 'readonly' | 'full',
): Promise<TraceReplayResult> {
  const { data } = await api.post<unknown>(
    `/trace/${encodeURIComponent(sessionId)}/replay`, null,
    {
      params: {
        mode,
        ...(turn ? { turn } : {}),
        ...(tool_policy ? { tool_policy } : {}),
      },
    },
  );
  return normalizeTraceReplay(data);
}
