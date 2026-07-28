/** API service – communicates with the FastAPI backend. */

import axios from 'axios';
import type { UmlDiagram, Project } from '../types/uml';
import type { PipelineState } from '../types/pipeline';

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

// ─── Pipeline ───────────────────────────────────────────

export async function createPipeline(diagramId: string, diagram: UmlDiagram): Promise<PipelineState> {
  const { data } = await api.post('/pipeline/create', {
    diagram_id: diagramId,
    diagram: diagram,
  });
  return data.pipeline;
}

export async function getPipeline(pipelineId: string): Promise<PipelineState> {
  const { data } = await api.get(`/pipeline/${pipelineId}`);
  return data.pipeline;
}

export async function confirmStage(
  pipelineId: string, stage: string, accepted: boolean, comment = ''
): Promise<PipelineState> {
  const { data } = await api.post(`/pipeline/${pipelineId}/confirm`, {
    stage, accepted, comment,
  });
  return data.pipeline;
}

export async function resumePipeline(
  pipelineId: string, diagram: UmlDiagram, language = 'python'
): Promise<{ events: unknown[]; pipeline: PipelineState }> {
  const { data } = await api.post(
    `/pipeline/${pipelineId}/resume`, diagram, { params: { language } }
  );
  return data;
}

export function createPipelineWs(
  pipelineId: string,
  diagram: UmlDiagram,
  language = 'python',
  autoConfirm = false,
  sourceDir = '',
  testDir = '',
  project?: Project,
  maxChangeRatio = 0,
): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tokenParam = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : '';
  const wsUrl = `${protocol}//${window.location.host}/api/pipeline/ws/${pipelineId}${tokenParam}`;
  const ws = new WebSocket(wsUrl);
  ws.onopen = () => {
    ws.send(JSON.stringify({
      diagram, language, auto_confirm: autoConfirm,
      source_dir: sourceDir,
      test_dir: testDir,
      project: project || {},
      max_change_ratio: maxChangeRatio,
    }));
  };
  return ws;
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

// ─── Global Optimization ───────────────────────────────

export async function optimizeProject(req: {
  class_diagram?: Record<string, unknown>;
  sequence_diagram?: Record<string, unknown>;
  component_diagram?: Record<string, unknown>;
  existing_diagrams?: Array<{type: string; name: string; component_id: string; data: any}>;
  instructions?: string;
}): Promise<Record<string, unknown>> {
  const { data } = await api.post('/llm/optimize-project', req, { timeout: 300000 });
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
