import type { UmlAttribute, UmlClass, UmlDiagram, UmlMethod, UmlRelation, Project } from '../types/uml';
import { RelationType, Stereotype, Visibility } from '../types/uml';
import type { SeqFragment, SeqLifeline, SeqMessage } from '../types/sequence';
import type { CompNode, CompRelation } from '../types/component';

type AnyRecord = Record<string, unknown>;

function record(value: unknown): AnyRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as AnyRecord
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ''): string {
  return value === undefined || value === null ? fallback : String(value);
}

function number(value: unknown, fallback: number, min?: number, max?: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  if (min !== undefined && parsed < min) return min;
  if (max !== undefined && parsed > max) return max;
  return parsed;
}

function boolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function enumValue<T extends string>(value: unknown, values: readonly T[], fallback: T): T {
  return values.includes(value as T) ? value as T : fallback;
}

function uniqueId(value: unknown, prefix: string, index: number, used: Set<string>): string {
  const base = text(value).trim() || `${prefix}_${index + 1}`;
  let id = base;
  let suffix = 2;
  while (used.has(id)) id = `${base}_${suffix++}`;
  used.add(id);
  return id;
}

function normalizeAttribute(value: unknown): UmlAttribute {
  const item = record(value);
  return {
    name: text(item.name),
    type: text(item.type),
    visibility: enumValue(item.visibility, Object.values(Visibility), Visibility.PUBLIC),
    default_value: text(item.default_value),
    is_static: boolean(item.is_static, false),
  };
}

function normalizeMethod(value: unknown): UmlMethod {
  const item = record(value);
  return {
    name: text(item.name),
    return_type: text(item.return_type, 'void'),
    params: text(item.params),
    visibility: enumValue(item.visibility, Object.values(Visibility), Visibility.PUBLIC),
    is_static: boolean(item.is_static, false),
    is_abstract: boolean(item.is_abstract, false),
  };
}

function normalizeClass(value: unknown, index: number, used: Set<string>): UmlClass {
  const item = record(value);
  return {
    id: uniqueId(item.id, 'class', index, used),
    name: text(item.name, 'Class'),
    stereotype: enumValue(item.stereotype, Object.values(Stereotype), Stereotype.CLASS),
    attributes: array(item.attributes).map(normalizeAttribute),
    methods: array(item.methods).map(normalizeMethod),
    position: {
      x: number(record(item.position).x ?? item.x, 100),
      y: number(record(item.position).y ?? item.y, 100),
    },
    size: {
      width: number(record(item.size).width ?? item.width, 200, 40),
      height: number(record(item.size).height ?? item.height, 150, 40),
    },
    note: text(item.note),
    provided_interfaces: array(item.provided_interfaces).map((v) => text(v)).filter(Boolean),
    required_interfaces: array(item.required_interfaces).map((v) => text(v)).filter(Boolean),
  };
}

function normalizeRelation(value: unknown, index: number, used: Set<string>): UmlRelation {
  const item = record(value);
  return {
    id: uniqueId(item.id, 'relation', index, used),
    source: text(item.source),
    target: text(item.target),
    type: enumValue(item.type, Object.values(RelationType), RelationType.ASSOCIATION),
    multiplicity_source: text(item.multiplicity_source),
    multiplicity_target: text(item.multiplicity_target),
    role_name: text(item.role_name),
    note: text(item.note),
  };
}

function normalizeLifeline(value: unknown, index: number, used: Set<string>): SeqLifeline {
  const item = record(value);
  return {
    id: uniqueId(item.id, 'lifeline', index, used),
    name: text(item.name, 'Participant'),
    class_ref: text(item.class_ref),
    x: number(item.x, 200 + index * 200),
    activations: array(item.activations)
      .map((v) => number(v, 0))
      .filter((v) => v >= 0),
  };
}

function normalizeMessage(value: unknown, index: number, used: Set<string>): SeqMessage {
  const item = record(value);
  const messageType = ['sync', 'async', 'return', 'simple', 'self'] as const;
  return {
    id: uniqueId(item.id, 'message', index, used),
    from_lifeline: text(item.from_lifeline),
    to_lifeline: text(item.to_lifeline),
    label: text(item.label, 'message()'),
    type: enumValue(item.type, messageType, 'sync'),
    order: number(item.order, index + 1, 1),
    y: number(item.y, 190 + (index + 1) * 45),
    note: text(item.note),
  };
}

function normalizeFragment(value: unknown, index: number, used: Set<string>): SeqFragment {
  const item = record(value);
  const fragmentTypes = ['loop', 'alt', 'opt', 'break', 'par', 'critical', 'neg'] as const;
  const yStart = number(item.y_start, 200 + index * 120);
  return {
    id: uniqueId(item.id, 'fragment', index, used),
    type: enumValue(item.type, fragmentTypes, 'loop'),
    label: text(item.label),
    x: number(item.x, 80),
    width: number(item.width, 300, 60),
    y_start: yStart,
    y_end: Math.max(yStart + 60, number(item.y_end, yStart + 120)),
  };
}

function normalizeComponent(value: unknown, index: number, used: Set<string>): CompNode {
  const item = record(value);
  return {
    id: uniqueId(item.id, 'component', index, used),
    name: text(item.name, 'Component'),
    x: number(item.x, 150 + index * 200),
    y: number(item.y, 100 + index * 120),
    width: number(item.width, 200, 40),
    height: number(item.height, 160, 40),
    parent_id: text(item.parent_id),
    provided_interfaces: array(item.provided_interfaces).map((v) => text(v)).filter(Boolean),
    required_interfaces: array(item.required_interfaces).map((v) => text(v)).filter(Boolean),
  };
}

function normalizeComponentRelation(value: unknown, index: number, used: Set<string>): CompRelation {
  const item = record(value);
  return {
    id: uniqueId(item.id, 'component_relation', index, used),
    source: text(item.source),
    target: text(item.target),
    type: item.type === 'delegation' ? 'delegation' : 'dependency',
  };
}

export function normalizeDiagram(value: unknown): UmlDiagram {
  const item = record(value);
  const classIds = new Set<string>();
  const relationIds = new Set<string>();
  const lifelineIds = new Set<string>();
  const messageIds = new Set<string>();
  const fragmentIds = new Set<string>();
  const componentIds = new Set<string>();
  const componentRelationIds = new Set<string>();
  const classes = array(item.classes).map((v, i) => normalizeClass(v, i, classIds));
  const lifelines = array(item.lifelines).map((v, i) => normalizeLifeline(v, i, lifelineIds));
  const components = array(item.components).map((v, i) => normalizeComponent(v, i, componentIds));
  const classIdSet = new Set(classes.map((v) => v.id));
  const lifelineIdSet = new Set(lifelines.map((v) => v.id));
  const componentIdSet = new Set(components.map((v) => v.id));
  const relations = array(item.relations)
    .map((v, i) => normalizeRelation(v, i, relationIds))
    .filter((v) => classIdSet.has(v.source) && classIdSet.has(v.target));
  const messages = array(item.messages)
    .map((v, i) => normalizeMessage(v, i, messageIds))
    .filter((v) => lifelineIdSet.has(v.from_lifeline) && lifelineIdSet.has(v.to_lifeline));
  const fragments = array(item.fragments).map((v, i) => normalizeFragment(v, i, fragmentIds));
  const compRelations = array(item.comp_relations)
    .map((v, i) => normalizeComponentRelation(v, i, componentRelationIds))
    .filter((v) => componentIdSet.has(v.source) && componentIdSet.has(v.target));
  const diagramType = ['class', 'sequence', 'component'].includes(text(item.diagram_type))
    ? text(item.diagram_type)
    : 'class';

  return {
    version: text(item.version, '1.0'),
    name: text(item.name, 'Untitled'),
    diagram_type: diagramType,
    component_id: text(item.component_id),
    classes,
    relations,
    lifelines,
    messages,
    fragments,
    components,
    comp_relations: compRelations,
    grid_visible: boolean(item.grid_visible, true),
    grid_size: number(item.grid_size, 20, 4, 200),
    grid_color: text(item.grid_color, '#e0e0e0'),
    grid_thickness: number(item.grid_thickness, 1, 1, 10),
    snap_to_grid: boolean(item.snap_to_grid, true),
    zoom: number(item.zoom, 1, 0.1, 5),
    pan_x: number(item.pan_x, 0),
    pan_y: number(item.pan_y, 0),
  };
}

export function normalizeProject(value: unknown): Project {
  const item = record(value);
  const diagrams = array(item.diagrams).map(normalizeDiagram);
  const active = number(item.active_diagram_index, 0, 0);
  return {
    version: text(item.version, '1.0'),
    revision: number(item.revision, 0, 0),
    name: text(item.name, 'Untitled'),
    diagrams,
    active_diagram_index: diagrams.length > 0
      ? Math.min(Math.floor(active), diagrams.length - 1)
      : 0,
  };
}
