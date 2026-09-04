/** API service – communicates with the FastAPI backend. */

import axios from 'axios';
import type { UmlDiagram, Project } from '../types/uml';

// Read auth token from Vite env var (VITE_API_TOKEN in .env.local)
const API_TOKEN = import.meta.env.VITE_API_TOKEN as string | undefined;

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
  headers: API_TOKEN
    ? { Authorization: `Bearer ${API_TOKEN}` }
    : {},
});

// ─── Files ──────────────────────────────────────────────

export async function listDiagrams(): Promise<Array<{
  name: string; path: string; size: number; modified: string;
}>> {
  const { data } = await api.get('/files/list');
  return data.files;
}

export async function saveDiagram(diagram: UmlDiagram, filename?: string): Promise<{
  success: boolean; filepath: string; filename: string;
}> {
  const params = filename ? `?filename=${encodeURIComponent(filename)}` : '';
  const { data } = await api.post(`/files/save${params}`, diagram);
  return data;
}

export async function openDiagram(filepath: string, safe = true): Promise<UmlDiagram> {
  const { data } = await api.get('/files/open', { params: { filepath, safe } });
  return data.diagram;
}

export async function newDiagram(name = 'Untitled'): Promise<UmlDiagram> {
  const { data } = await api.post('/files/new', null, { params: { name } });
  return data.diagram;
}

export async function exportMarkdown(diagram: UmlDiagram): Promise<string> {
  const { data } = await api.post('/files/export/markdown', { diagram });
  return data;
}

export async function uploadExcel(file: File): Promise<{
  filename: string; sheets: Record<string, unknown[]>; sheet_names: string[];
}> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await api.post('/files/upload/excel', form);
  return data;
}

// ─── LLM ────────────────────────────────────────────────

export async function getSupportedLanguages(): Promise<string[]> {
  const { data } = await api.get('/llm/languages');
  return data.languages;
}

export async function llmChat(prompt: string, systemPrompt?: string): Promise<string> {
  const { data } = await api.post('/llm/chat', {
    prompt,
    system_prompt: systemPrompt,
  });
  return data.content;
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
  const { data } = await api.get(`/files/browse${qs ? '?' + qs : ''}`);
  return data;
}

// ─── Review ────────────────────────────────────────────

// ─── TestHub ──────────────────────────────────────────

export async function listTestFiles(dir?: string): Promise<{
  files: Array<{ name: string; path: string; size: number; modified: string }>;
  testhub_dir: string;
}> {
  const { data } = await api.get('/testhub/list', { params: dir ? { dir } : {} });
  return data;
}

export async function loadTestFile(filename: string, dir?: string): Promise<{
  filename: string;
  sheets: Record<string, { headers: string[]; rows: Record<string, string>[] }>;
  sheet_names: string[];
  filepath: string;
}> {
  const params: Record<string, string> = { filename };
  if (dir) params.dir = dir;
  const { data } = await api.get('/testhub/load', { params });
  return data;
}

export async function saveTestFile(req: {
  filename: string;
  sheets: Record<string, { headers: string[]; rows: Record<string, string>[] }>;
}): Promise<{ success: boolean; filename: string }> {
  const { data } = await api.post('/testhub/save', req);
  return data;
}

export async function generateTestCode(req: {
  filename: string;
  sheets: Record<string, unknown>;
  language: string;
  mode: 'full' | 'incremental';
  changed_cases?: Array<Record<string, unknown>>;
}): Promise<{ files: Record<string, string>; language: string; mode: string }> {
  const { data } = await api.post('/testhub/generate-tests', req);
  return data;
}

export async function saveTestReview(req: {
  action: string;
  comment: string;
  filename: string;
  sheet: string;
  case_id: string;
  details: string;
}): Promise<{ success: boolean; file: string }> {
  const { data } = await api.post('/testhub/save-review', req);
  return data;
}

// ─── Unified Review ─────────────────────────────────────

export async function saveReview(review: {
  action: string;
  comment: string;
  requirements: string;
  original_name: string;
  optimized_name: string;
  timestamp: string;
  filename?: string;
  sheet?: string;
  case_id?: string;
  details?: string;
}): Promise<{ success: boolean; file: string }> {
  const { data } = await api.post('/files/save-review', review);
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
  const { data } = await api.post(`/files/save-project?${params.toString()}`, project);
  return data;
}

export async function openProject(filepath: string, safe = true): Promise<Project> {
  const { data } = await api.get('/files/open-project', { params: { filepath, safe } });
  return data.project;
}

export async function listProjects(): Promise<Array<{
  name: string; path: string; size: number; modified: string;
}>> {
  const { data } = await api.get('/files/list-projects');
  return data.projects;
}

// ─── Trace ─────────────────────────────────────────────

export interface TraceMeta {
  session_id: string;
  filename: string;
  size: number;
  modified: string;
  events: number;
  first_ts_ms: number | null;
  last_ts_ms: number | null;
  title?: string;
}

export interface TraceDetail {
  session_id: string;
  events: Array<Record<string, any>>;
}

export async function listTraces(): Promise<TraceMeta[]> {
  const { data } = await api.get('/trace/list');
  return data.traces;
}

export async function getTrace(sessionId: string): Promise<TraceDetail> {
  const { data } = await api.get(`/trace/${encodeURIComponent(sessionId)}`);
  return data;
}

export interface TraceHistoryEntry {
  role: 'user' | 'assistant' | 'summary';
  content: string;
  metadata?: Record<string, unknown>;
}

export async function getTraceHistory(sessionId: string): Promise<TraceHistoryEntry[]> {
  const { data } = await api.get(`/trace/${encodeURIComponent(sessionId)}/history`);
  return data.history;
}

// ── Evaluation center ───────────────────────────────────────────────────────

export interface EvalCaseInfo {
  id: string;
  agent: string;
  name: string;
  prompt: string;
  project_id: string;
  checkers: Array<Record<string, any>>;
  hard_checkers: Array<Record<string, any>>;
  metadata: Record<string, any>;
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
  case_count: number;
  passed: number;
  failed: number;
  timeout: number;
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
  trace_id?: string;
  checker_results: Array<Record<string, any>>;
  error: string;
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
  summary: EvalSummary;
}

export async function listEvalCases(): Promise<EvalCaseInfo[]> {
  const { data } = await api.get('/evals/cases');
  return data.cases;
}

export async function getEvalBaseline(): Promise<EvalBaseline> {
  const { data } = await api.get('/evals/baseline');
  return data;
}

export async function getEvalRepository(): Promise<EvalRepositoryInfo> {
  const { data } = await api.get('/evals/repository');
  return data;
}

export async function archiveEvalBaseline(note = ''): Promise<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}> {
  const { data } = await api.post('/evals/baseline/archive', { note }, { timeout: 15000 });
  return data;
}

export async function startEvalBatch(req: {
  suite?: string;
  case_ids?: string[];
  version: string;
  label?: string;
}): Promise<EvalBatch> {
  const { data } = await api.post('/evals/runs', req, { timeout: 15000 });
  return data;
}

export async function getEvalBatch(batchId: string): Promise<EvalBatch> {
  const { data } = await api.get(`/evals/runs/${encodeURIComponent(batchId)}`, { timeout: 15000 });
  return data;
}

export async function listEvalTrends(limit = 20): Promise<EvalTrend[]> {
  const { data } = await api.get('/evals/trends', { params: { limit } });
  return data.trends;
}

export async function archiveEvalBatch(batchId: string, note = ''): Promise<{
  archive_id: string; created_at: string; path: string; batch_id: string;
}> {
  const { data } = await api.post('/evals/archives', { batch_id: batchId, note }, { timeout: 15000 });
  return data;
}

export async function listEvalArchives(limit = 20): Promise<EvalArchive[]> {
  const { data } = await api.get('/evals/archives', { params: { limit } });
  return data.archives;
}

export interface TraceReplayStepToolCall {
  name: string;
  arguments: Record<string, any> | string;
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

export async function replayTrace(
  sessionId: string, mode: TraceReplayMode = 'mock', turn?: number,
  tool_policy?: 'readonly' | 'full',
): Promise<TraceReplayResult> {
  const { data } = await api.post(
    `/trace/${encodeURIComponent(sessionId)}/replay`, null,
    {
      params: {
        mode,
        ...(turn ? { turn } : {}),
        ...(tool_policy ? { tool_policy } : {}),
      },
    },
  );
  return data;
}
