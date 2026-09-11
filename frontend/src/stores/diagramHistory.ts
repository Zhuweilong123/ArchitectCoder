import type { Project, UmlDiagram } from '../types/uml';
import { createDefaultDiagram } from '../types/uml';

export interface DiagramViewport {
  zoom: number;
  panX: number;
  panY: number;
}

export interface DiagramHistorySnapshot {
  diagram: UmlDiagram;
  timestamp: number;
}

export interface DiagramHistoryState {
  project: Project;
  viewport: DiagramViewport;
  undoStack: DiagramHistorySnapshot[];
  redoStack: DiagramHistorySnapshot[];
  lastOperationTime: number;
  lastMergeKey: string | null;
  maxHistorySteps: number;
  mergeWindowMs: number;
  isBatching: boolean;
  batchSnapshot: DiagramHistorySnapshot | null;
  isModified: boolean;
}

type HistoryPatch = Partial<Pick<
  DiagramHistoryState,
  | 'project'
  | 'viewport'
  | 'undoStack'
  | 'redoStack'
  | 'lastOperationTime'
  | 'lastMergeKey'
  | 'isBatching'
  | 'batchSnapshot'
  | 'isModified'
>>;

function activeDiagram(project: Project): UmlDiagram {
  return project.diagrams[project.active_diagram_index]
    || project.diagrams[0]
    || createDefaultDiagram(project.name || 'Untitled');
}

function cloneSnapshot(diagram: UmlDiagram, timestamp = Date.now()): DiagramHistorySnapshot {
  return {
    diagram: JSON.parse(JSON.stringify(diagram)),
    timestamp,
  };
}

function applyViewport(diagram: UmlDiagram, viewport: DiagramViewport): UmlDiagram {
  return {
    ...diagram,
    zoom: viewport.zoom,
    pan_x: viewport.panX,
    pan_y: viewport.panY,
  };
}

function replaceActiveDiagram(project: Project, diagram: UmlDiagram): Project {
  const index = project.active_diagram_index;
  return {
    ...project,
    diagrams: project.diagrams.map((item, itemIndex) => (
      itemIndex === index ? diagram : item
    )),
  };
}

export function pushHistorySnapshot(
  state: DiagramHistoryState,
  mergeKey?: string,
): HistoryPatch | null {
  if (state.isBatching) return null;
  const now = Date.now();
  if (
    mergeKey
    && state.lastMergeKey === mergeKey
    && now - state.lastOperationTime < state.mergeWindowMs
  ) {
    return { lastOperationTime: now };
  }
  const snapshot = cloneSnapshot(activeDiagram(state.project), now);
  return {
    undoStack: [...state.undoStack, snapshot].slice(-state.maxHistorySteps),
    redoStack: [],
    lastOperationTime: now,
    lastMergeKey: mergeKey ?? null,
  };
}

export function beginHistoryBatch(state: DiagramHistoryState): HistoryPatch | null {
  if (state.isBatching) return null;
  return {
    isBatching: true,
    batchSnapshot: cloneSnapshot(activeDiagram(state.project)),
  };
}

export function endHistoryBatch(state: DiagramHistoryState): HistoryPatch | null {
  if (!state.isBatching) return null;
  const baseline = state.batchSnapshot;
  const changed = Boolean(
    baseline
    && JSON.stringify(baseline.diagram) !== JSON.stringify(activeDiagram(state.project)),
  );
  const undoStack = changed && baseline
    ? [...state.undoStack, baseline].slice(-state.maxHistorySteps)
    : state.undoStack;
  return {
    isBatching: false,
    batchSnapshot: null,
    undoStack,
    redoStack: changed ? [] : state.redoStack,
    lastOperationTime: changed ? Date.now() : state.lastOperationTime,
    lastMergeKey: null,
  };
}

export function undoHistory(state: DiagramHistoryState): HistoryPatch | null {
  if (state.undoStack.length === 0) return null;
  const currentSnapshot = cloneSnapshot(activeDiagram(state.project));
  const undoStack = [...state.undoStack];
  const target = undoStack.pop()!;
  return {
    project: replaceActiveDiagram(state.project, applyViewport(target.diagram, state.viewport)),
    viewport: state.viewport,
    undoStack,
    redoStack: [...state.redoStack, currentSnapshot],
    isModified: true,
  };
}

export function redoHistory(state: DiagramHistoryState): HistoryPatch | null {
  if (state.redoStack.length === 0) return null;
  const currentSnapshot = cloneSnapshot(activeDiagram(state.project));
  const redoStack = [...state.redoStack];
  const target = redoStack.pop()!;
  return {
    project: replaceActiveDiagram(state.project, applyViewport(target.diagram, state.viewport)),
    viewport: state.viewport,
    undoStack: [...state.undoStack, currentSnapshot],
    redoStack,
    isModified: true,
  };
}
