/** Diagram store — manages Project state with multiple diagrams. */

import { create } from 'zustand';
import type { UmlDiagram, UmlClass, UmlRelation, Position, Size, Project } from '../types/uml';
import { createDefaultDiagram, createDefaultClass, createDefaultRelation, createDefaultProject } from '../types/uml';
import type { SeqLifeline, SeqMessage } from '../types/sequence';
import { createDefaultLifeline, createDefaultMessage, createDefaultFragment } from '../types/sequence';
import type { CompNode, CompRelation } from '../types/component';
import { createDefaultComponent, createDefaultCompRelation } from '../types/component';

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
  return createDefaultDiagram(project.name || 'Untitled');
}

// Helper: update the active diagram within a project
function _updateActiveDiagram(project: Project, updater: (d: UmlDiagram) => UmlDiagram): Project {
  const idx = project.active_diagram_index;
  return {
    ...project,
    diagrams: project.diagrams.map((d, i) => (i === idx ? updater(d) : d)),
  };
}

interface DiagramState {
  // Core state
  project: Project;
  selectedClassId: string | null;
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
  /** When true, pushSnapshot is a no-op (e.g. during streaming bulk updates). */
  isBatching: boolean;

  // ── Diagram access ────────────────────────────

  /** Convenience getter for the currently active diagram (placeholder for empty projects). */
  diagram: UmlDiagram;

  // ── Project actions ───────────────────────────

  setProject: (project: Project) => void;
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

const _initialProject = createDefaultProject();
const _initialFilepath = localStorage.getItem('currentFilepath');

export const useDiagramStore = create<DiagramState>((set, get) => ({
  project: _initialProject,
  diagram: _activeDiagram(_initialProject),
  selectedClassId: null,
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
  recenterCounter: 0,

  // ── Project actions ───────────────────────────────────

  setProject: (project) => {
    console.debug('[Store] setProject:', project.name, project.diagrams.length, 'diagrams');
    set({
      project,
      diagram: _activeDiagram(project),
      isModified: false,
      undoStack: [],
      redoStack: [],
      recenterCounter: get().recenterCounter + 1,
    });
  },

  newProject: (name) => {
    const project = createDefaultProject(name);
    console.debug('[Store] newProject:', project.name);
    localStorage.removeItem('currentFilepath');
    set({
      project,
      diagram: _activeDiagram(project),
      selectedClassId: null,
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
        diagram: state.project.diagrams[index],
        selectedClassId: null,
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
      diagram: newD,
      selectedClassId: null,
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

    const lastIdx = diagrams.length - 1;
    console.debug('[Store] addDiagramsFromSpec:', specs.length, 'specs →', diagrams.length, 'diagrams');
    set({
      project: { ...state.project, diagrams, active_diagram_index: lastIdx >= 0 ? lastIdx : 0 },
      diagram: lastIdx >= 0 ? diagrams[lastIdx] : createDefaultDiagram(),
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
      diagram: diagrams.length > 0 ? diagrams[newIdx] : createDefaultDiagram(),
      isModified: true,
      undoStack: [],
      redoStack: [],
    });
  },

  // ── Legacy diagram actions ────────────────────────────

  setDiagram: (diagram) => {
    console.debug('[Store] setDiagram: updating active diagram', diagram.name);
    const project = _updateActiveDiagram(get().project, () => diagram);
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  newDiagram: (name) => {
    console.debug('[Store] newDiagram (legacy):', name);
    const project = createDefaultProject(name);
    localStorage.removeItem('currentFilepath');
    set({
      project,
      diagram: _activeDiagram(project),
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
    const clsCount = state.diagram.classes.length;
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
      diagram: _activeDiagram(project),
      selectedClassId: newClass.id,
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
      diagram: _activeDiagram(project),
      selectedClassId: state.selectedClassId === id ? null : state.selectedClassId,
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  moveClass: (id, position) => {
    const state = get();
    get().pushSnapshot('move_class', `move_class:${id}`);
    const project = _updateActiveDiagram(state.project, (d) => ({
      ...d,
      classes: d.classes.map((c) => (c.id === id ? { ...c, position } : c)),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  resizeClass: (id, size) => {
    get().pushSnapshot('resize_class', `resize_class:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      classes: d.classes.map((c) => (c.id === id ? { ...c, size } : c)),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  selectClass: (id) => set({ selectedClassId: id, selectedRelationId: null }),

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
      diagram: _activeDiagram(project),
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
      diagram: _activeDiagram(project),
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  selectRelation: (id) => set({ selectedRelationId: id, selectedClassId: null }),

  // ── Sequence diagram operations ────────────────────────

  selectLifeline: (id) => set({ selectedLifelineId: id, selectedMessageId: null }),
  selectMessage: (id) => set({ selectedMessageId: id, selectedLifelineId: null }),

  addLifeline: (x) => {
    const state = get();
    const llCount = (state.diagram.lifelines || []).length;
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
      diagram: _activeDiagram(project),
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
      diagram: _activeDiagram(project),
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  updateLifeline: (id, updates) => {
    get().pushSnapshot('update_lifeline', `update_lifeline:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      lifelines: (d.lifelines || []).map((l) =>
        l.id === id ? { ...l, ...updates } : l
      ),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  addMessage: (from, to) => {
    const state = get();
    const order = (state.diagram.messages?.length || 0) + 1;
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
      diagram: _activeDiagram(project),
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
      diagram: _activeDiagram(project),
      selectedMessageId: get().selectedMessageId === id ? null : get().selectedMessageId,
      isModified: true,
    });
  },

  updateMessage: (id, updates) => {
    get().pushSnapshot('update_message', `update_message:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      messages: (d.messages || []).map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  removeFragment: (id) => {
    get().pushSnapshot('remove_fragment');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      fragments: (d.fragments || []).filter((f) => f.id !== id),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
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
      diagram: JSON.parse(JSON.stringify(state.diagram)),
      timestamp: now,
    };
    const newUndo = [...state.undoStack, snapshot].slice(-state.maxHistorySteps);
    set({ undoStack: newUndo, redoStack: [], lastOperationTime: now, lastMergeKey: mergeKey ?? null });
  },

  beginBatch: () => set({ isBatching: true }),
  endBatch: () => set({ isBatching: false, lastMergeKey: null }),

  // ── Component diagram operations ───────────────────────

  selectComponent: (id) => set({ selectedComponentId: id, selectedCompRelationId: null }),
  selectCompRelation: (id) => set({ selectedCompRelationId: id, selectedComponentId: null }),

  addComponent: (position, parentId = '') => {
    const state = get();
    const compCount = (state.diagram.components || []).length;
    const validX = clampCoord(position?.x, 150 + (compCount % 5) * 200);
    const validY = clampCoord(position?.y, 100 + Math.floor(compCount / 5) * 200);
    const c = createDefaultComponent(validX, validY, parentId);
    get().pushSnapshot('add_component');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: [...(d.components || []), c],
    }));
    console.log('[Store] addComponent:', c.name, c.id, parentId ? `(child of ${parentId})` : '');
    set({ project, diagram: _activeDiagram(project), selectedComponentId: c.id, isModified: true });
  },

  removeComponent: (id) => {
    get().pushSnapshot('remove_component');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: (d.components || []).filter((c) => c.id !== id),
      comp_relations: (d.comp_relations || []).filter(
        (r) => r.source !== id && r.target !== id
      ),
    }));
    set({
      project, diagram: _activeDiagram(project),
      selectedComponentId: get().selectedComponentId === id ? null : get().selectedComponentId,
      isModified: true,
    });
  },

  moveComponent: (id, x, y) => {
    get().pushSnapshot('move_component', `move_component:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: (d.components || []).map((c) =>
        c.id === id ? { ...c, x, y } : c
      ),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  updateComponent: (id, updates) => {
    get().pushSnapshot('update_component', `update_component:${id}`);
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      components: (d.components || []).map((c) =>
        c.id === id ? { ...c, ...updates } : c
      ),
    }));
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  addCompRelation: (source, target) => {
    const rel = createDefaultCompRelation(source, target);
    get().pushSnapshot('add_comp_relation');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      comp_relations: [...(d.comp_relations || []), rel],
    }));
    console.log('[Store] addCompRelation:', source, '→', target);
    set({ project, diagram: _activeDiagram(project), selectedCompRelationId: rel.id, isModified: true });
  },

  removeCompRelation: (id) => {
    get().pushSnapshot('remove_comp_relation');
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      comp_relations: (d.comp_relations || []).filter((r) => r.id !== id),
    }));
    set({
      project, diagram: _activeDiagram(project),
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
    set({ project, diagram: _activeDiagram(project), isModified: true });
  },

  // ── Grid ──────────────────────────────────────────────

  toggleGrid: () => {
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      grid_visible: !d.grid_visible,
    }));
    set({ project, diagram: _activeDiagram(project) });
  },

  setGridSize: (size) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_size: size }));
    set({ project, diagram: _activeDiagram(project) });
  },

  setGridColor: (color) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_color: color }));
    set({ project, diagram: _activeDiagram(project) });
  },

  setGridThickness: (thickness) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, grid_thickness: thickness }));
    set({ project, diagram: _activeDiagram(project) });
  },

  toggleSnapToGrid: () => {
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      snap_to_grid: !d.snap_to_grid,
    }));
    set({ project, diagram: _activeDiagram(project) });
  },

  // ── View ──────────────────────────────────────────────

  triggerRecenter: () => {
    set((s) => ({ recenterCounter: s.recenterCounter + 1 }));
  },

  setZoom: (zoom) => {
    const project = _updateActiveDiagram(get().project, (d) => ({
      ...d,
      zoom: Math.max(0.1, Math.min(5, zoom)),
    }));
    set({ project, diagram: _activeDiagram(project) });
  },

  setPan: (x, y) => {
    const project = _updateActiveDiagram(get().project, (d) => ({ ...d, pan_x: x, pan_y: y }));
    set({ project, diagram: _activeDiagram(project) });
  },

  // ── Undo/Redo ─────────────────────────────────────────

  undo: () => {
    const state = get();
    if (state.undoStack.length === 0) return;
    const currentSnapshot: Snapshot = {
      diagram: JSON.parse(JSON.stringify(state.diagram)),
      timestamp: Date.now(),
    };
    const newUndo = [...state.undoStack];
    const target = newUndo.pop()!;
    const newRedo = [...state.redoStack, currentSnapshot];
    const project = _updateActiveDiagram(state.project, () => target.diagram);
    set({
      project,
      diagram: _activeDiagram(project),
      undoStack: newUndo,
      redoStack: newRedo,
      isModified: true,
    });
  },

  redo: () => {
    const state = get();
    if (state.redoStack.length === 0) return;
    const currentSnapshot: Snapshot = {
      diagram: JSON.parse(JSON.stringify(state.diagram)),
      timestamp: Date.now(),
    };
    const newRedo = [...state.redoStack];
    const target = newRedo.pop()!;
    const newUndo = [...state.undoStack, currentSnapshot];
    const project = _updateActiveDiagram(state.project, () => target.diagram);
    set({
      project,
      diagram: _activeDiagram(project),
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
  setCurrentWorkspacePath: (path, safe = true) => set({
    currentWorkspacePath: path,
    currentWorkspaceSafe: safe,
  }),
}));
