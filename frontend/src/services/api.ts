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

export async function generateCode(
  diagram: UmlDiagram, language: string
): Promise<{ language: string; files: Record<string, string> }> {
  const { data } = await api.post('/llm/generate-code', { diagram, language });
  return data;
}

export async function optimizeUml(
  diagram: UmlDiagram, instructions = ''
): Promise<{
  original: UmlDiagram;
  optimized: UmlDiagram;
  changes_summary: string;
  diff: string;
}> {
  const { data } = await api.post('/llm/optimize-uml', { diagram, instructions });
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
  const { data } = await api.get(`/files/browse${qs ? '?' + qs : ''}`);
  return data;
}

// ─── Review ────────────────────────────────────────────

// ─── Generated Code ───────────────────────────────────

export async function saveGeneratedCode(req: {
  project_name: string;
  language: string;
  source_files: Record<string, string>;
  test_files: Record<string, string>;
}): Promise<{ success: boolean; src_dir: string; test_dir: string }> {
  const { data } = await api.post('/files/save-generated', req);
  return data;
}

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
  success: boolean; filepath: string; filename: string;
}> {
  const params = new URLSearchParams();
  if (filename) params.set('filename', filename);
  params.set('safe', String(safe));
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
  role: 'user' | 'assistant';
  content: string;
}

export async function getTraceHistory(sessionId: string): Promise<TraceHistoryEntry[]> {
  const { data } = await api.get(`/trace/${encodeURIComponent(sessionId)}/history`);
  return data.history;
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

export async function replayTrace(
  sessionId: string, mode: 'mock' | 'rerun' = 'mock', turn?: number,
): Promise<TraceReplayResult> {
  const { data } = await api.post(
    `/trace/${encodeURIComponent(sessionId)}/replay`, null,
    { params: { mode, ...(turn ? { turn } : {}) } },
  );
  return data;
}
