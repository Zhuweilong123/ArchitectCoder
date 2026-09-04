/** Diagram store — manages Project state with multiple diagrams. */

import { create } from 'zustand';
import type { UmlDiagram, UmlClass, UmlRelation, Position, Size, Project } from '../types/uml';
import { createDefaultDiagram, createDefaultClass, createDefaultRelation, createDefaultProject, RelationType } from '../types/uml';
import type { SeqLifeline, SeqMessage } from '../types/sequence';
import { createDefaultLifeline, createDefaultMessage, createDefaultFragment } from '../types/sequence';
import type { CompNode, CompRelation } from '../types/component';
import { createDefaultComponent, createDefaultCompRelation } from '../types/component';
import { normalizeDiagram, normalizeProject } from '../utils/diagramNormalization';

/** Clamp coordinate to valid canvas range. Falls back to a deterministic default if invalid. */
function clampCoord(val: number | undefined, def: number, min = 50, max = 3000): number {
  if (typeof val !== 'number' || isNaN(val) || val < min || val > max) return def;
  return val;
}

// Undo/Redo snapshot (per-diagram)
interface Snapshot {
  diagram: UmlDiagram;
  timestamp: number;
}

// Keep the empty-project fallback referentially stable. Selectors are allowed
// to return a placeholder, but must not allocate one on every store read.
const _emptyProjectDiagramCache = new WeakMap<Project, UmlDiagram>();

export interface ViewportState {
  zoom: number;
  panX: number;
  panY: number;
}

// Helper: get active diagram from project.
// Returns a safe fallback for empty projects so existing code doesn't need null checks.
function _activeDiagram(project: Project): UmlDiagram {
  const idx = project.active_diagram_index;
  if (idx >= 0 && idx < project.diagrams.length) {
    return project.diagrams[idx];
  }
  if (project.diagrams.length > 0) {
    console.warn('[Store] Invalid active_diagram_index', idx, project.diagrams.length);
    return project.diagrams[0];
  }
  // Empty project — return a placeholder so UI doesn't crash
  let placeholder = _emptyProjectDiagramCache.get(project);
  if (!placeholder) {
    placeholder = createDefaultDiagram(project.name || 'Untitled');
    _emptyProjectDiagramCache.set(project, placeholder);
  }
  return placeholder;
}

function _viewportFromDiagram(diagram: UmlDiagram): ViewportState {
  return {
    zoom: diagram.zoom || 1,
    panX: diagram.pan_x || 0,
    panY: diagram.pan_y || 0,
  };
}

function _applyViewport(diagram: UmlDiagram, viewport: ViewportState): UmlDiagram {
  return {
    ...diagram,
    zoom: viewport.zoom,
    pan_x: viewport.panX,
    pan_y: viewport.panY,
  };
}

// Helper: update the active diagram within a project
function _updateActiveDiagram(project: Project, updater: (d: UmlDiagram) => UmlDiagram): Project {
  const idx = project.active_diagram_index;
  return {
    ...project,
    diagrams: project.diagrams.map((d, i) => (i === idx ? updater(d) : d)),
  };
}

export interface DiagramState {
  // Core state
  project: Project;
  /** Viewport is kept separate so pan/zoom does not invalidate diagram content subscriptions. */
  viewport: ViewportState;
  selectedClassId: string | null;
  selectedClassIds: string[];
  selectedRelationId: string | null;
  isModified: boolean;
  currentFilepath: string | null;
  /** Directory used for new/unsaved projects and relative path display. */
  currentWorkspacePath: string | null;
  /** Whether the current workspace is inside the application safe root. */
  currentWorkspaceSafe: boolean;

  // History (per active diagram)
  undoStack: Snapshot[];
  redoStack: Snapshot[];
  lastOperationTime: number;
  lastMergeKey: string | null;
  maxHistorySteps: number;
  mergeWindowMs: number;
  /** When true, edits are accumulated into one undo transaction. */
  isBatching: boolean;
  /** Original diagram captured at the start of the current edit transaction. */
  batchSnapshot: Snapshot | null;

  // ── Diagram access ────────────────────────────

  // ── Project actions ───────────────────────────

  setProject: (project: Project) => void;
  /** Apply a computed project mutation while preserving the current viewport when possible. */
  applyProjectUpdate: (project: Project) => void;
  /** Return the project with the runtime viewport merged into the active diagram. */
  getProjectSnapshot: () => Project;
  markSaved: (revision: number) => void;
  newProject: (name?: string) => void;
  setActiveDiagram: (index: number) => void;
  addDiagram: (type?: string, name?: string, componentId?: string) => void;
  addDiagramsFromSpec: (specs: Array<{type: string; name: string; component_id: string; data: Record<string, unknown>}>) => void;
  removeDiagram: (index: number) => void;

  // ── Legacy diagram actions (kept for compatibility) ──

  setDiagram: (diagram: UmlDiagram) => void;
  newDiagram: (name?: string) => void;
  markModified: () => void;

  // ── Class operations ──────────────────────────

  addClass: (position?: Position) => void;
  removeClass: (id: string) => void;
  updateClass: (id: string, updates: Partial<UmlClass>) => void;
  moveClass: (id: string, position: Position) => void;
  resizeClass: (id: string, size: Size) => void;
  selectClass: (id: string | null) => void;
  selectClasses: (ids: string[]) => void;
  alignClasses: (direction: 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom') => void;
  distributeClasses: (axis: 'horizontal' | 'vertical') => void;
  autoLayoutClasses: () => void;

  // ── Relation operations ────────────────────────

  addRelation: (source: string, target: string) => void;
  removeRelation: (id: string) => void;
  updateRelation: (id: string, updates: Partial<UmlRelation>) => void;
  selectRelation: (id: string | null) => void;

  // ── Sequence diagram operations ──────────────

  selectLifeline: (id: string | null) => void;
  selectMessage: (id: string | null) => void;
  selectedLifelineId: string | null;
  selectedMessageId: string | null;

  addLifeline: (x?: number) => void;
  removeLifeline: (id: string) => void;
  moveLifeline: (id: string, x: number) => void;
  updateLifeline: (id: string, updates: Partial<SeqLifeline>) => void;

  addMessage: (from: string, to: string) => void;
  removeMessage: (id: string) => void;
  updateMessage: (id: string, updates: Partial<SeqMessage>) => void;
  arrangeSequence: () => void;

  // ── Fragment operations (UML 2.5.1) ───────────
  addFragment: (y?: number) => void;
  removeFragment: (id: string) => void;
  updateFragment: (id: string, updates: Partial<import('../types/sequence').SeqFragment>) => void;

  // ── Component diagram operations ──────────────

  selectComponent: (id: string | null) => void;
  selectCompRelation: (id: string | null) => void;
  selectedComponentId: string | null;
  selectedCompRelationId: string | null;

  addComponent: (position?: { x: number; y: number }, parentId?: string) => void;
  removeComponent: (id: string) => void;
  moveComponent: (id: string, x: number, y: number) => void;
  updateComponent: (id: string, updates: Partial<CompNode>) => void;

  addCompRelation: (source: string, target: string) => void;
  removeCompRelation: (id: string) => void;
  updateCompRelation: (id: string, updates: Partial<CompRelation>) => void;

  // ── Grid ──────────────────────────────────────

  toggleGrid: () => void;
  setGridSize: (size: number) => void;
  setGridColor: (color: string) => void;
  setGridThickness: (thickness: number) => void;
  toggleSnapToGrid: () => void;

  // ── View ──────────────────────────────────────

  recenterCounter: number;
  triggerRecenter: () => void;

  setZoom: (zoom: number) => void;
  setPan: (x: number, y: number) => void;

  // ── Undo/Redo ─────────────────────────────────

  undo: () => void;
  redo: () => void;
  pushSnapshot: (operation: string, mergeKey?: string) => void;
  clearHistory: () => void;
  beginBatch: () => void;
  endBatch: () => void;

  // ── File ──────────────────────────────────────

  setCurrentFilepath: (path: string | null) => void;
  setCurrentWorkspacePath: (path: string | null, safe?: boolean) => void;
}

/** Canonical selector for the active diagram. Consumers should use this instead of the legacy mirror. */
export const selectActiveDiagram = (state: DiagramState): UmlDiagram => _activeDiagram(state.project);

const _initialProject = createDefaultProject();
const _initialFilepath = localStorage.getItem('currentFilepath');
const _initialDiagram = _activeDiagram(_initialProject);

export const useDiagramStore = create<DiagramState>((set, get) => ({
  project: _initialProject,
  viewport: _viewportFromDiagram(_initialDiagram),
  selectedClassId: null,
  selectedClassIds: [],
  selectedRelationId: null,
  selectedLifelineId: null,
  selectedMessageId: null,
  selectedComponentId: null,
  selectedCompRelationId: null,
  isModified: false,
  currentFilepath: _initialFilepath,
  currentWorkspacePath: null,
  currentWorkspaceSafe: true,
  undoStack: [],
  redoStack: [],
  lastOperationTime: 0,
  lastMergeKey: null,
  maxHistorySteps: 50,
  mergeWindowMs: 500,
  isBatching: false,
  batchSnapshot: null,
  recenterCounter: 0,

  // ── Project actions ───────────────────────────────────

  setProject: (project) => {
    const normalizedProject = normalizeProject(project);
    const activeDiagram = _activeDiagram(normalizedProject);
    const hasStoredViewport = Boolean(
      activeDiagram.pan_x || activeDiagram.pan_y || activeDiagram.zoom !== 1,
    );
    console.debug('[Store] setProject:', normalizedProject.name, normalizedProject.diagrams.length, 'diagrams');
    set({
      project: normalizedProject,
      viewport: _viewportFromDiagram(activeDiagram),
      isModified: false,
      undoStack: [],
      redoStack: [],
      // A persisted viewport must win over the default auto-center pass.
      recenterCounter: hasStoredViewport
        ? get().recenterCounter
        : get().recenterCounter + 1,
    });
  },

  applyProjectUpdate: (project) => {
    const state = get();
    const normalizedProject = normalizeProject(project);
    const activeDiagram = _activeDiagram(normalizedProject);
    const activeIndexChanged =
      normalizedProject.active_diagram_index !== state.project.active_diagram_index;
    set({
      project: normalizedProject,
      // Project mutations do not normally change the viewport. Rebuild it only
      // when the mutation switches the active diagram.
      viewport: activeIndexChanged ? _viewportFromDiagram(activeDiagram) : state.viewport,
      isModified: true,
    });
  },

  getProjectSnapshot: () => {
    const state = get();
    return _updateActiveDiagram(state.project, (diagram) => ({
      ...diagram,
      zoom: state.viewport.zoom,
      pan_x: state.viewport.panX,
      pan_y: state.viewport.panY,
    }));
  },

  newProject: (name) => {
    const project = createDefaultProject(name);
    console.debug('[Store] newProject:', project.name);
    localStorage.removeItem('currentFilepath');
    set({
      project,
      viewport: _viewportFromDiagram(_activeDiagram(project)),
      selectedClassId: null,
      selectedClassIds: [],
      selectedRelationId: null,
      isModified: false,
      currentFilepath: null,
      undoStack: [],
      redoStack: [],
    });
  },

  setActiveDiagram: (index) => {
    const state = get();
    console.debug('[Store] setActiveDiagram:', index);
    if (index >= 0 && index < state.project.diagrams.length) {
      set({
        project: { ...state.project, active_diagram_index: index },
        viewport: _viewportFromDiagram(state.project.diagrams[index]),
        selectedClassId: null,
        selectedClassIds: [],
        selectedRelationId: null,
        selectedLifelineId: null,
        selectedMessageId: null,
        selectedComponentId: null,
        selectedCompRelationId: null,
        undoStack: [],
        redoStack: [],
      });
    }
  },

  addDiagram: (type = 'class', name, componentId?) => {
    const state = get();
    const newD = createDefaultDiagram(name || `${type}_${state.project.diagrams.length + 1}`);
    newD.diagram_type = type;
    newD.component_id = componentId || '';
    const diagrams = [...state.project.diagrams, newD];
    console.debug('[Store] addDiagram:', type, newD.name, '→', diagrams.length, 'total');
    set({
      project: {
        ...state.project,
        diagrams,
        active_diagram_index: diagrams.length - 1,
      },
      viewport: _viewportFromDiagram(newD),
      selectedClassId: null,
      selectedClassIds: [],
      selectedRelationId: null,
      isModified: true,
      undoStack: [],
      redoStack: [],
    });
  },

  /** Batch-create or update diagrams from LLM optimization results.
   *  Each spec is matched by type+name: existing diagrams are updated,
   *  new ones are created. */
  addDiagramsFromSpec: (specs) => {
    const state = get();
    const diagrams = [...state.project.diagrams];
    const seen = new Set<string>();

    for (const spec of specs) {
      // Deduplicate by type+name within this batch
      const dkey = `${spec.type}:${spec.name}`;
      if (seen.has(dkey)) continue;
      seen.add(dkey);

      const existingIdx = diagrams.findIndex(
        d => (d.diagram_type || 'class') === spec.type && d.name === spec.name
      );
      if (existingIdx >= 0) {
        // Update existing diagram — merge data over original
        diagrams[existingIdx] = { ...diagrams[existingIdx], ...spec.data };
      } else {
        // Create new diagram tab
        const newD = createDefaultDiagram(spec.name);
        newD.diagram_type = spec.type;
        newD.component_id = spec.component_id || '';
        diagrams.push({ ...newD, ...spec.data });
      }
    }

    const normalizedProject = normalizeProject({
      ...state.project,
      diagrams,
      active_diagram_index: diagrams.length - 1,
    });
    const lastIdx = normalizedProject.diagrams.length - 1;
    console.debug('[Store] addDiagramsFromSpec:', specs.length, 'specs →', normalizedProject.diagrams.length, 'diagrams');
    set({
      project: normalizedProject,
      viewport: _viewportFromDiagram(lastIdx >= 0 ? normalizedProject.diagrams[lastIdx] : createDefaultDiagram()),
      selectedClassId: null, selectedRelationId: null,
      isModified: true, undoStack: [], redoStack: [],
    });
  },

  removeDiagram: (index) => {
    const state = get();
    if (state.project.diagrams.length === 0) return; // nothing to remove
    const diagrams = state.project.diagrams.filter((_, i) => i !== index);
    const newIdx = diagrams.length > 0 ? Math.min(index, diagrams.length - 1) : 0;
    console.debug('[Store] removeDiagram:', index, '→', diagrams.length, 'remaining');
    set({
      project: {
        ...state.project,
        diagrams,
        active_diagram_index: newIdx,
      },
      viewport: _viewportFromDiagram(diagrams.length > 0 ? diagrams[newIdx] : createDefaultDiagram()),
      isModified: true,
      undoStack: [],
      redoStack: [],
    });
  },

  // ── Legacy diagram actions ────────────────────────────

  setDiagram: (diagram) => {
    const normalizedDiagram = normalizeDiagram(diagram);
    console.debug('[Store] setDiagram: updating active diagram', normalizedDiagram.name);
    const project = _updateActiveDiagram(get().project, () => normalizedDiagram);
    const activeDiagram = _activeDiagram(project);
    set({ project, viewport: _viewportFromDiagram(activeDiagram), isModified: true });
  },

  newDiagram: (name) => {
    console.debug('[Store] newDiagram (legacy):', name);
    const project = createDefaultProject(name);
    localStorage.removeItem('currentFilepath');
    set({
      project,
      viewport: _viewportFromDiagram(_activeDiagram(project)),
      selectedClassId: null,
      selectedRelationId: null,
      isModified: false,
      currentFilepath: null,
      undoStack: [],
      redoStack: [],
    });
  },

  markModified: () => set({ isModified: true }),

  // ── Class operations ──────────────────────────────────

  addClass: (position) => {
    const state = get();
    const clsCount = _activeDiagram(state.project).classes.length;
    const validPos = {
      x: clampCoord(position?.x, 150 + (clsCount % 5) * 200),
      y: clampCoord(position?.y, 100 + Math.floor(clsCount / 5) * 200),
    };
    const newClass = createDefaultClass(validPos);
    get().pushSnapshot('add_class');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      classes: [...d.classes, newClass],
    }));
    set({
      project,
      selectedClassId: newClass.id,
      selectedClassIds: [newClass.id],
      selectedRelationId: null,
      isModified: true,
    });
  },

  removeClass: (id) => {
    const state = get();
    get().pushSnapshot('remove_class');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      classes: d.classes.filter((c) => c.id !== id),
      relations: d.relations.filter((r) => r.source !== id && r.target !== id),
    }));
    set({
      project,
      selectedClassId: state.selectedClassId === id ? null : state.selectedClassId,
      selectedClassIds: state.selectedClassIds.filter((classId) => classId !== id),
      isModified: true,
    });
  },

  updateClass: (id, updates) => {
    const state = get();
    get().pushSnapshot('update_class', `update_class:${id}`);
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      classes: d.classes.map((c) => (c.id === id ? { ...c, ...updates } : c)),
    }));
    set({ project, isModified: true });
  },

  moveClass: (id, position) => {
    const state = get();
    get().pushSnapshot('move_class', `move_class:${id}`);
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      classes: d.classes.map((c) => (c.id === id ? { ...c, position } : c)),
    }));
    set({ project, isModified: true });
  },

  resizeClass: (id, size) => {
    get().pushSnapshot('resize_class', `resize_class:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      classes: d.classes.map((c) => (c.id === id ? { ...c, size } : c)),
    }));
    set({ project, isModified: true });
  },

  selectClass: (id) => set({
    selectedClassId: id,
    selectedClassIds: id ? [id] : [],
    selectedRelationId: null,
  }),

  selectClasses: (ids) => {
    const state = get();
    const availableIds = new Set(_activeDiagram(state.project).classes.map((cls) => cls.id));
    const selectedClassIds = [...new Set(ids)].filter((id) => availableIds.has(id));
    set({
      selectedClassId: selectedClassIds[0] || null,
      selectedClassIds,
      selectedRelationId: null,
    });
  },

  alignClasses: (direction) => {
    const state = get();
    const selectedIds = state.selectedClassIds.length > 1
      ? state.selectedClassIds
      : state.selectedClassId ? [state.selectedClassId] : [];
    const classes = _activeDiagram(state.project).classes.filter((cls) => selectedIds.includes(cls.id));
    if (classes.length < 2) return;

    const minX = Math.min(...classes.map((cls) => cls.position.x));
    const minY = Math.min(...classes.map((cls) => cls.position.y));
    const maxRight = Math.max(...classes.map((cls) => cls.position.x + cls.size.width));
    const maxBottom = Math.max(...classes.map((cls) => cls.position.y + cls.size.height));
    const centerX = (minX + maxRight) / 2;
    const centerY = (minY + maxBottom) / 2;

    const project = _updateActiveDiagram(state.project, (diagram) => ({
      ...diagram,
      classes: diagram.classes.map((cls) => {
        if (!selectedIds.includes(cls.id)) return cls;
        const position = { ...cls.position };
        if (direction === 'left') position.x = minX;
        if (direction === 'center') position.x = centerX - cls.size.width / 2;
        if (direction === 'right') position.x = maxRight - cls.size.width;
        if (direction === 'top') position.y = minY;
        if (direction === 'middle') position.y = centerY - cls.size.height / 2;
        if (direction === 'bottom') position.y = maxBottom - cls.size.height;
        return { ...cls, position };
      }),
    }));
    get().pushSnapshot(`align_${direction}`);
    set({ project, isModified: true });
  },

  distributeClasses: (axis) => {
    const state = get();
    const selectedIds = state.selectedClassIds.length > 2
      ? state.selectedClassIds
      : state.selectedClassId ? [state.selectedClassId] : [];
    const classes = _activeDiagram(state.project).classes
      .filter((cls) => selectedIds.includes(cls.id))
      .sort((a, b) => axis === 'horizontal'
        ? a.position.x - b.position.x
        : a.position.y - b.position.y);
    if (classes.length < 3) return;

    const start = axis === 'horizontal' ? classes[0].position.x : classes[0].position.y;
    const last = classes[classes.length - 1];
    const end = axis === 'horizontal'
      ? last.position.x + last.size.width
      : last.position.y + last.size.height;
    const totalSize = classes.reduce((sum, cls) => sum + (
      axis === 'horizontal' ? cls.size.width : cls.size.height
    ), 0);
    const gap = (end - start - totalSize) / (classes.length - 1);
    const positions = new Map<string, number>();
    let cursor = start;
    classes.forEach((cls) => {
      positions.set(cls.id, cursor);
      cursor += (axis === 'horizontal' ? cls.size.width : cls.size.height) + gap;
    });

    const project = _updateActiveDiagram(state.project, (diagram) => ({
      ...diagram,
      classes: diagram.classes.map((cls) => {
        const coordinate = positions.get(cls.id);
        if (coordinate === undefined) return cls;
        return {
          ...cls,
          position: {
            ...cls.position,
            ...(axis === 'horizontal' ? { x: coordinate } : { y: coordinate }),
          },
        };
      }),
    }));
    get().pushSnapshot(`distribute_${axis}`);
    set({ project, isModified: true });
  },

  autoLayoutClasses: () => {
    const state = get();
    const diagram = _activeDiagram(state.project);
    if (diagram.classes.length < 2) return;

    const classIds = new Set(diagram.classes.map((cls) => cls.id));
    const hierarchyRelations = diagram.relations.filter((relation) => (
      (relation.type === RelationType.INHERITANCE || relation.type === RelationType.REALIZATION)
      && classIds.has(relation.source)
      && classIds.has(relation.target)
    ));
    const relations = hierarchyRelations.length > 0
      ? hierarchyRelations
      : diagram.relations.filter((relation) => (
        classIds.has(relation.source) && classIds.has(relation.target)
      ));
    const isHierarchyLayout = hierarchyRelations.length > 0;
    const outgoing = new Map<string, string[]>();
    const indegree = new Map<string, number>();
    const levels = new Map<string, number>();

    diagram.classes.forEach((cls) => {
      outgoing.set(cls.id, []);
      indegree.set(cls.id, 0);
      levels.set(cls.id, 0);
    });

    relations.forEach((relation) => {
      // UML inheritance is stored child -> parent, while the layout flows
      // parent -> child so base classes appear above derived classes.
      const from = isHierarchyLayout ? relation.target : relation.source;
      const to = isHierarchyLayout ? relation.source : relation.target;
      const neighbors = outgoing.get(from);
      if (!neighbors || !indegree.has(to) || neighbors.includes(to)) return;
      neighbors.push(to);
      indegree.set(to, (indegree.get(to) || 0) + 1);
    });

    const queue = diagram.classes
      .filter((cls) => indegree.get(cls.id) === 0)
      .map((cls) => cls.id);
    const processed = new Set<string>();
    for (let index = 0; index < queue.length; index += 1) {
      const currentId = queue[index];
      processed.add(currentId);
      const currentLevel = levels.get(currentId) || 0;
      outgoing.get(currentId)?.forEach((nextId) => {
        levels.set(nextId, Math.max(levels.get(nextId) || 0, currentLevel + 1));
        const nextIndegree = (indegree.get(nextId) || 0) - 1;
        indegree.set(nextId, nextIndegree);
        if (nextIndegree === 0) queue.push(nextId);
      });
    }

    // Cycles have no meaningful topological level; keep them together below
    // the resolved hierarchy so the result remains deterministic and usable.
    if (processed.size < diagram.classes.length) {
      const maxLevel = Math.max(...Array.from(levels.values()));
      diagram.classes.forEach((cls) => {
        if (!processed.has(cls.id)) levels.set(cls.id, maxLevel + 1);
      });
    }

    const rows = new Map<number, UmlClass[]>();
    diagram.classes.forEach((cls) => {
      const level = levels.get(cls.id) || 0;
      const row = rows.get(level) || [];
      row.push(cls);
      rows.set(level, row);
    });
    rows.forEach((row) => row.sort((a, b) => (
      a.position.y - b.position.y || a.position.x - b.position.x || a.id.localeCompare(b.id)
    )));

    const startX = 120;
    const startY = 100;
    const horizontalGap = 80;
    const verticalGap = 100;
    const rowY = new Map<number, number>();
    let nextY = startY;
    Array.from(rows.keys()).sort((a, b) => a - b).forEach((level) => {
      rowY.set(level, nextY);
      const rowHeight = Math.max(...(rows.get(level) || []).map((cls) => cls.size.height || 150));
      nextY += rowHeight + verticalGap;
    });

    const positions = new Map<string, Position>();
    rows.forEach((row, level) => {
      let nextX = startX;
      row.forEach((cls) => {
        positions.set(cls.id, { x: nextX, y: rowY.get(level) || startY });
        nextX += (cls.size.width || 200) + horizontalGap;
      });
    });

    const project = _updateActiveDiagram(state.project, (activeDiagram) => ({
      ...activeDiagram,
      classes: activeDiagram.classes.map((cls) => {
        const position = positions.get(cls.id);
        return position ? { ...cls, position } : cls;
      }),
    }));
    get().pushSnapshot('auto_layout');
    set({ project, isModified: true });
    get().triggerRecenter();
  },

  // ── Relation operations ────────────────────────────────

  addRelation: (source, target) => {
    const state = get();
    const newRel = createDefaultRelation(source, target);
    get().pushSnapshot('add_relation');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      relations: [...d.relations, newRel],
    }));
    set({
      project,
      selectedRelationId: newRel.id,
      selectedClassId: null,
      isModified: true,
    });
  },

  removeRelation: (id) => {
    const state = get();
    get().pushSnapshot('remove_relation');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      relations: d.relations.filter((r) => r.id !== id),
    }));
    set({
      project,
      selectedRelationId: state.selectedRelationId === id ? null : state.selectedRelationId,
      isModified: true,
    });
  },

  updateRelation: (id, updates) => {
    get().pushSnapshot('update_relation', `update_relation:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      relations: d.relations.map((r) => (r.id === id ? { ...r, ...updates } : r)),
    }));
    set({ project, isModified: true });
  },

  selectRelation: (id) => set({
    selectedRelationId: id,
    selectedClassId: null,
    selectedClassIds: [],
  }),

  // ── Sequence diagram operations ────────────────────────

  selectLifeline: (id) => set({ selectedLifelineId: id, selectedMessageId: null }),
  selectMessage: (id) => set({ selectedMessageId: id, selectedLifelineId: null }),

  addLifeline: (x) => {
    const state = get();
    const llCount = (_activeDiagram(state.project).lifelines || []).length;
    const validX = clampCoord(x, 200 + llCount * 200);
    const lifeline = createDefaultLifeline(validX);
    get().pushSnapshot('add_lifeline');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      lifelines: [...(d.lifelines || []), lifeline],
    }));
    console.debug('[Store] addLifeline:', lifeline.name, lifeline.id);
    set({
      project,
      selectedLifelineId: lifeline.id,
      isModified: true,
    });
  },

  removeLifeline: (id) => {
    get().pushSnapshot('remove_lifeline');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      lifelines: (d.lifelines || []).filter((l) => l.id !== id),
      messages: (d.messages || []).filter(
        (m) => m.from_lifeline !== id && m.to_lifeline !== id
      ),
    }));
    console.debug('[Store] removeLifeline:', id);
    set({
      project,
      selectedLifelineId: get().selectedLifelineId === id ? null : get().selectedLifelineId,
      isModified: true,
    });
  },

  moveLifeline: (id, x) => {
    get().pushSnapshot('move_lifeline', `move_lifeline:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      lifelines: (d.lifelines || []).map((l) => (l.id === id ? { ...l, x } : l)),
    }));
    set({ project, isModified: true });
  },

  updateLifeline: (id, updates) => {
    get().pushSnapshot('update_lifeline', `update_lifeline:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      lifelines: (d.lifelines || []).map((l) =>
        l.id === id ? { ...l, ...updates } : l
      ),
    }));
    set({ project, isModified: true });
  },

  addMessage: (from, to) => {
    const state = get();
    const order = (_activeDiagram(state.project).messages?.length || 0) + 1;
    const y = 150 + order * 40;  // LIFELINE_Y(120) + 30 + order*40
    const msg = createDefaultMessage(from, to, order, y);
    get().pushSnapshot('add_message');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      messages: [...(d.messages || []), msg],
    }));
    console.debug('[Store] addMessage:', msg.label, from, '→', to);
    set({
      project,
      selectedMessageId: msg.id,
      isModified: true,
    });
  },

  removeMessage: (id) => {
    get().pushSnapshot('remove_message');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      messages: (d.messages || []).filter((m) => m.id !== id),
    }));
    console.debug('[Store] removeMessage:', id);
    set({
      project,
      selectedMessageId: get().selectedMessageId === id ? null : get().selectedMessageId,
      isModified: true,
    });
  },

  updateMessage: (id, updates) => {
    get().pushSnapshot('update_message', `update_message:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => {
      const nextMessages = (d.messages || []).map((m) =>
        m.id === id ? { ...m, ...updates } : m
      );
      if (typeof updates.y !== 'number') return { ...d, messages: nextMessages };

      // Keep persisted order aligned with the visual timeline after an edge
      // is dragged. Stable tie-breaking prevents messages at the same Y from
      // swapping on every mouse move.
      const messages = [...nextMessages]
        .sort((a, b) => a.y - b.y || a.order - b.order || a.id.localeCompare(b.id))
        .map((message, index) => ({ ...message, order: index + 1 }));
      return { ...d, messages };
    });
    set({ project, isModified: true });
  },

  arrangeSequence: () => {
    const state = get();
    const diagram = _activeDiagram(state.project);
    const lifelines = [...(diagram.lifelines || [])]
      .sort((a, b) => a.x - b.x || a.id.localeCompare(b.id));
    const messages = [...(diagram.messages || [])]
      .sort((a, b) => a.y - b.y || a.order - b.order || a.id.localeCompare(b.id));
    if (lifelines.length === 0 && messages.length === 0) return;

    const lifelineX = new Map<string, number>();
    lifelines.forEach((lifeline, index) => lifelineX.set(lifeline.id, 160 + index * 220));

    const oldMessageY = new Map(messages.map((message) => [
      message.id,
      message.y || 150 + message.order * 40,
    ]));
    const messageY = new Map<string, number>();
    const arrangedMessages = messages.map((message, index) => {
      const y = 190 + index * 45;
      messageY.set(message.id, y);
      return { ...message, y, order: index + 1 };
    });

    const arrangedFragments = (diagram.fragments || []).map((fragment) => {
      const containedMessages = messages.filter((message) => {
        const y = oldMessageY.get(message.id) || 0;
        return y >= fragment.y_start && y <= fragment.y_end;
      });
      if (containedMessages.length === 0) return fragment;
      const minMessageY = Math.min(...containedMessages.map((message) => messageY.get(message.id) || 190));
      const maxMessageY = Math.max(...containedMessages.map((message) => messageY.get(message.id) || 190));
      return {
        ...fragment,
        y_start: Math.max(80, Math.min(fragment.y_start, minMessageY - 24)),
        y_end: Math.max(fragment.y_end, maxMessageY + 36),
      };
    });

    const project = _updateActiveDiagram(state.project, (activeDiagram) => ({
      ...activeDiagram,
      lifelines: (activeDiagram.lifelines || []).map((lifeline) => ({
        ...lifeline,
        x: lifelineX.get(lifeline.id) ?? lifeline.x,
      })),
      messages: arrangedMessages,
      fragments: arrangedFragments,
    }));
    get().pushSnapshot('arrange_sequence');
    set({ project, isModified: true });
    get().triggerRecenter();
  },

  // ── Fragment operations (UML 2.5.1) ─────────────────────
  addFragment: (y) => {
    const frag = createDefaultFragment(y || 150);
    get().pushSnapshot('add_fragment');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      fragments: [...(d.fragments || []), frag],
    }));
    console.log('[Store] addFragment:', frag.type, frag.id);
    set({ project, isModified: true });
  },

  removeFragment: (id) => {
    get().pushSnapshot('remove_fragment');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      fragments: (d.fragments || []).filter((f) => f.id !== id),
    }));
    set({ project, isModified: true });
  },

  updateFragment: (id, updates) => {
    get().pushSnapshot('update_fragment', `update_fragment:${id}`);
    const state = get();
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      fragments: (d.fragments || []).map((f) =>
        f.id === id ? { ...f, ...updates } : f
      ),
    }));
    set({ project, isModified: true });
  },

  pushSnapshot: (op, mergeKey) => {
    const state = get();
    if (state.isBatching) return;  // bulk/streaming update — suppress per-op snapshots
    const now = Date.now();
    // Merge consecutive ops with the same key (e.g. a drag, or typing in the
    // property panel) into a single undo step within the merge window.
    if (mergeKey && state.lastMergeKey === mergeKey &&
        (now - state.lastOperationTime) < state.mergeWindowMs) {
      set({ lastOperationTime: now });
      return;
    }
    const snapshot = {
      diagram: JSON.parse(JSON.stringify(_activeDiagram(state.project))),
      timestamp: now,
    };
    const newUndo = [...state.undoStack, snapshot].slice(-state.maxHistorySteps);
    set({ undoStack: newUndo, redoStack: [], lastOperationTime: now, lastMergeKey: mergeKey ?? null });
  },

  beginBatch: () => {
    const state = get();
    if (state.isBatching) return;
    set({
      isBatching: true,
      batchSnapshot: {
        diagram: JSON.parse(JSON.stringify(_activeDiagram(state.project))),
        timestamp: Date.now(),
      },
    });
  },

  endBatch: () => {
    const state = get();
    if (!state.isBatching) return;
    const baseline = state.batchSnapshot;
    const changed = Boolean(
      baseline
      && JSON.stringify(baseline.diagram) !== JSON.stringify(_activeDiagram(state.project)),
    );
    const undoStack = changed && baseline
      ? [...state.undoStack, baseline].slice(-state.maxHistorySteps)
      : state.undoStack;
    set({
      isBatching: false,
      batchSnapshot: null,
      undoStack,
      redoStack: changed ? [] : state.redoStack,
      lastOperationTime: changed ? Date.now() : state.lastOperationTime,
      lastMergeKey: null,
    });
  },

  // ── Component diagram operations ───────────────────────

  selectComponent: (id) => set({ selectedComponentId: id, selectedCompRelationId: null }),
  selectCompRelation: (id) => set({ selectedCompRelationId: id, selectedComponentId: null }),

  addComponent: (position, parentId = '') => {
    const state = get();
    const compCount = (_activeDiagram(state.project).components || []).length;
    const validX = clampCoord(position?.x, 150 + (compCount % 5) * 200);
    const validY = clampCoord(position?.y, 100 + Math.floor(compCount / 5) * 200);
    const c = createDefaultComponent(validX, validY, parentId);
    get().pushSnapshot('add_component');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: [...(d.components || []), c],
    }));
    console.log('[Store] addComponent:', c.name, c.id, parentId ? `(child of ${parentId})` : '');
    set({ project, selectedComponentId: c.id, isModified: true });
  },

  removeComponent: (id) => {
    const state = get();
    const removedIds = new Set([id]);
    let changed = true;
    while (changed) {
      changed = false;
      (_activeDiagram(state.project).components || []).forEach((component) => {
        if (component.parent_id && removedIds.has(component.parent_id) && !removedIds.has(component.id)) {
          removedIds.add(component.id);
          changed = true;
        }
      });
    }
    const removedRelationIds = new Set<string>();
    (_activeDiagram(state.project).comp_relations || []).forEach((relation) => {
      if (removedIds.has(relation.source) || removedIds.has(relation.target)) {
        removedRelationIds.add(relation.id);
      }
    });
    get().pushSnapshot('remove_component');
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      components: (d.components || []).filter((c) => !removedIds.has(c.id)),
      comp_relations: (d.comp_relations || []).filter((r) => !removedRelationIds.has(r.id)),
    }));
    set({
      project,
      selectedComponentId: state.selectedComponentId && removedIds.has(state.selectedComponentId)
        ? null : state.selectedComponentId,
      selectedCompRelationId: state.selectedCompRelationId && removedRelationIds.has(state.selectedCompRelationId)
        ? null : state.selectedCompRelationId,
      isModified: true,
    });
    console.debug('[Store] removeComponent:', id, '→ removed subtree:', removedIds.size);
  },

  moveComponent: (id, x, y) => {
    get().pushSnapshot('move_component', `move_component:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: (d.components || []).map((c) =>
        c.id === id ? { ...c, x, y } : c
      ),
    }));
    set({ project, isModified: true });
  },

  updateComponent: (id, updates) => {
    get().pushSnapshot('update_component', `update_component:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: (d.components || []).map((c) =>
        c.id === id ? { ...c, ...updates } : c
      ),
    }));
    set({ project, isModified: true });
  },

  addCompRelation: (source, target) => {
    const rel = createDefaultCompRelation(source, target);
    get().pushSnapshot('add_comp_relation');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      comp_relations: [...(d.comp_relations || []), rel],
    }));
    console.log('[Store] addCompRelation:', source, '→', target);
    set({ project, selectedCompRelationId: rel.id, isModified: true });
  },

  removeCompRelation: (id) => {
    get().pushSnapshot('remove_comp_relation');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      comp_relations: (d.comp_relations || []).filter((r) => r.id !== id),
    }));
    set({
      project,
      selectedCompRelationId: get().selectedCompRelationId === id ? null : get().selectedCompRelationId,
      isModified: true,
    });
  },

  updateCompRelation: (id, updates) => {
    get().pushSnapshot('update_comp_relation', `update_comp_relation:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      comp_relations: (d.comp_relations || []).map((r) =>
        r.id === id ? { ...r, ...updates } : r
      ),
    }));
    set({ project, isModified: true });
  },

  // ── Grid ──────────────────────────────────────────────

  toggleGrid: () => {
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      grid_visible: !d.grid_visible,
    }));
    set({ project });
  },

  setGridSize: (size) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_size: size }));
    set({ project });
  },

  setGridColor: (color) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_color: color }));
    set({ project });
  },

  setGridThickness: (thickness) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_thickness: thickness }));
    set({ project });
  },

  toggleSnapToGrid: () => {
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      snap_to_grid: !d.snap_to_grid,
    }));
    set({ project });
  },

  // ── View ──────────────────────────────────────────────

  triggerRecenter: () => {
    set((s) => ({ recenterCounter: s.recenterCounter + 1 }));
  },

  setZoom: (zoom) => {
    const nextZoom = Math.max(0.1, Math.min(5, zoom));
    set((state) => ({ viewport: { ...state.viewport, zoom: nextZoom } }));
  },

  setPan: (x, y) => {
    set((state) => ({ viewport: { zoom: state.viewport.zoom, panX: x, panY: y } }));
  },

  // ── Undo/Redo ─────────────────────────────────────────

  undo: () => {
    const state = get();
    if (state.undoStack.length === 0) return;
    const currentSnapshot: Snapshot = {
      diagram: JSON.parse(JSON.stringify(_activeDiagram(state.project))),
      timestamp: Date.now(),
    };
    const newUndo = [...state.undoStack];
    const target = newUndo.pop()!;
    const newRedo = [...state.redoStack, currentSnapshot];
    const restoredDiagram = _applyViewport(target.diagram, state.viewport);
    const project = _updateActiveDiagram(state.project, () => restoredDiagram);
    set({
      project,
      viewport: state.viewport,
      undoStack: newUndo,
      redoStack: newRedo,
      isModified: true,
    });
  },

  redo: () => {
    const state = get();
    if (state.redoStack.length === 0) return;
    const currentSnapshot: Snapshot = {
      diagram: JSON.parse(JSON.stringify(_activeDiagram(state.project))),
      timestamp: Date.now(),
    };
    const newRedo = [...state.redoStack];
    const target = newRedo.pop()!;
    const newUndo = [...state.undoStack, currentSnapshot];
    const restoredDiagram = _applyViewport(target.diagram, state.viewport);
    const project = _updateActiveDiagram(state.project, () => restoredDiagram);
    set({
      project,
      viewport: state.viewport,
      undoStack: newUndo,
      redoStack: newRedo,
      isModified: true,
    });
  },

  clearHistory: () => set({ undoStack: [], redoStack: [], lastOperationTime: 0, lastMergeKey: null }),

  setCurrentFilepath: (path) => {
    if (path) {
      localStorage.setItem('currentFilepath', path);
    } else {
      localStorage.removeItem('currentFilepath');
    }
    set({ currentFilepath: path });
  },

  markSaved: (revision) => {
    const state = get();
    const project = { ...get().getProjectSnapshot(), revision };
    const activeDiagram = _activeDiagram(project);
    set({ project, viewport: _viewportFromDiagram(activeDiagram), isModified: false });
  },
  setCurrentWorkspacePath: (path, safe = true) => set({
    currentWorkspacePath: path,
    currentWorkspaceSafe: safe,
  }),
}));

/** Non-reactive counterpart for event handlers and services. */
export const getActiveDiagram = (): UmlDiagram => selectActiveDiagram(useDiagramStore.getState());
