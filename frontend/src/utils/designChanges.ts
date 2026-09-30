import type { UmlAttribute, UmlDiagram, UmlMethod } from '../types/uml';

export type ChangeStatus = 'added' | 'modified' | 'removed';
export type MemberSection = 'attributes' | 'methods' | 'provided' | 'required';
export interface MemberChange {
  section: MemberSection;
  status: ChangeStatus;
  label: string;
  beforeText?: string;
  afterText?: string;
}
export interface DesignChange {
  id: string;
  kind: 'class' | 'component' | 'lifeline' | 'message' | 'fragment' | 'relation';
  status: ChangeStatus;
  label: string;
  members: MemberChange[];
}
export interface DiagramComparison { original: UmlDiagram; optimized: UmlDiagram }

const value = (v: unknown) => v == null ? '' : String(v);
const sorted = (items: unknown[]) => items.map((item) => JSON.stringify(item)).sort();
const attribute = (a: UmlAttribute) => [a.name, a.type, a.visibility, value(a.default_value), !!a.is_static];
const method = (m: UmlMethod) => [m.name, m.params, m.return_type, m.visibility, !!m.is_static, !!m.is_abstract];
const attributeText = (a: UmlAttribute) => `${a.visibility} ${a.name}: ${a.type}${a.default_value ? ` = ${a.default_value}` : ''}`;
const methodText = (m: UmlMethod) => `${m.visibility} ${m.name}(${m.params}): ${m.return_type}`;

function memberChanges<T>(before: T[], after: T[], section: MemberSection,
  name: (item: T) => string, canonical: (item: T) => unknown, text: (item: T) => string): MemberChange[] {
  const remaining = [...after];
  const changes: MemberChange[] = [];
  const unmatched: T[] = [];
  // Match unchanged overloads first so adding an overload cannot mark its
  // existing siblings as modified simply because their array order changed.
  before.forEach((item) => {
    const exact = remaining.findIndex((candidate) => JSON.stringify(canonical(item)) === JSON.stringify(canonical(candidate)));
    if (exact >= 0) remaining.splice(exact, 1);
    else unmatched.push(item);
  });
  unmatched.forEach((item) => {
    const index = remaining.findIndex((candidate) => name(candidate) === name(item));
    if (index < 0) changes.push({ section, status: 'removed', label: name(item), beforeText: text(item) });
    else {
      const candidate = remaining.splice(index, 1)[0];
      changes.push({ section, status: 'modified', label: name(item), beforeText: text(item), afterText: text(candidate) });
    }
  });
  remaining.forEach((item) => changes.push({ section, status: 'added', label: name(item), afterText: text(item) }));
  return changes;
}

function interfaces(before: string[], after: string[], section: 'provided' | 'required') {
  return memberChanges(before, after, section, value, value, value);
}

/** Compare domain content only. Positions, sizes, routing and folding are view state. */
export function getDiagramChanges(original: UmlDiagram, optimized: UmlDiagram, includeMembers = true): DesignChange[] {
  const changes: DesignChange[] = [];
  const compare = <T extends { id: string }>(before: T[], after: T[], kind: DesignChange['kind'],
    canonical: (item: T) => unknown, label: (item: T) => string,
    details?: (a: T, b: T) => MemberChange[]) => {
    const oldById = new Map(before.map((item) => [item.id, item]));
    const newById = new Map(after.map((item) => [item.id, item]));
    before.forEach((item) => {
      const updated = newById.get(item.id);
      if (!updated) changes.push({ id: item.id, kind, status: 'removed', label: label(item), members: [] });
      else if (JSON.stringify(canonical(item)) !== JSON.stringify(canonical(updated))) {
        changes.push({ id: item.id, kind, status: 'modified', label: label(updated), members: includeMembers ? details?.(item, updated) || [] : [] });
      }
    });
    after.forEach((item) => {
      if (!oldById.has(item.id)) changes.push({ id: item.id, kind, status: 'added', label: label(item), members: [] });
    });
  };
  compare(original.classes || [], optimized.classes || [], 'class',
    (c) => [c.name, c.stereotype || 'class', sorted((c.attributes || []).map(attribute)), sorted((c.methods || []).map(method)),
      sorted(c.provided_interfaces || []), sorted(c.required_interfaces || []), value(c.note)], (c) => c.name,
    (a, b) => [
      ...memberChanges(a.attributes || [], b.attributes || [], 'attributes', (i) => i.name, attribute, attributeText),
      ...memberChanges(a.methods || [], b.methods || [], 'methods', (i) => i.name, method, methodText),
      ...interfaces(a.provided_interfaces || [], b.provided_interfaces || [], 'provided'),
      ...interfaces(a.required_interfaces || [], b.required_interfaces || [], 'required'),
    ]);
  compare(original.components || [], optimized.components || [], 'component',
    (c) => [c.name, value(c.parent_id), sorted(c.provided_interfaces || []), sorted(c.required_interfaces || [])], (c) => c.name,
    (a, b) => [...interfaces(a.provided_interfaces || [], b.provided_interfaces || [], 'provided'),
      ...interfaces(a.required_interfaces || [], b.required_interfaces || [], 'required')]);
  compare(original.lifelines || [], optimized.lifelines || [], 'lifeline',
    (c) => [c.name, value(c.class_ref)], (c) => c.name);
  // The numeric order can be renumbered during layout. Compare its ordinal
  // position instead, which still detects actual interaction-order changes.
  const orders = (d: UmlDiagram) => new Map([...(d.messages || [])].sort((a, b) => a.order - b.order).map((m, i) => [m.id, i]));
  const oldOrder = orders(original), newOrder = orders(optimized);
  compare(original.messages || [], optimized.messages || [], 'message',
    (m) => [m.from_lifeline, m.to_lifeline, m.label, m.type, value(m.note)], (m) => m.label || m.id);
  const oldMessages = new Map((original.messages || []).map((m) => [m.id, m]));
  const commonOld = (original.messages || []).filter((m) => newOrder.has(m.id)).sort((a, b) => a.order - b.order).map((m) => m.id);
  const commonNew = (optimized.messages || []).filter((m) => oldOrder.has(m.id)).sort((a, b) => a.order - b.order).map((m) => m.id);
  commonNew.forEach((id, i) => {
    if (id !== commonOld[i] && oldMessages.has(id) && !changes.some((change) => change.id === id)) {
      changes.push({ id, kind: 'message', status: 'modified', label: oldMessages.get(id)!.label, members: [] });
    }
  });
  compare(original.fragments || [], optimized.fragments || [], 'fragment', (f) => [f.type, f.label], (f) => `${f.type} ${f.label}`);
  const names = new Map([...original.classes || [], ...optimized.classes || [], ...original.components || [], ...optimized.components || []].map((c) => [c.id, c.name]));
  const relationLabel = (r: { source: string; target: string; type: string }) => `${names.get(r.source) || r.source} → ${names.get(r.target) || r.target} (${r.type})`;
  compare(original.relations || [], optimized.relations || [], 'relation',
    (r) => [r.source, r.target, r.type, value(r.multiplicity_source), value(r.multiplicity_target), value(r.role_name), value(r.note)], relationLabel);
  compare(original.comp_relations || [], optimized.comp_relations || [], 'relation', (r) => [r.source, r.target, r.type], relationLabel);
  return changes;
}

export function findDiagramComparison(diagram: UmlDiagram, originals: Record<string, UmlDiagram>, optimizeds: Record<string, UmlDiagram>,
  original: UmlDiagram | null, optimized: UmlDiagram | null): DiagramComparison | null {
  const matches = (d: UmlDiagram | undefined | null) => d && d.name === diagram.name && (d.diagram_type || 'class') === (diagram.diagram_type || 'class');
  for (const key of Object.keys(optimizeds)) {
    if (originals[key] && (matches(originals[key]) || matches(optimizeds[key]))) return { original: originals[key], optimized: optimizeds[key] };
  }
  return original && optimized && (matches(original) || matches(optimized)) ? { original, optimized } : null;
}
