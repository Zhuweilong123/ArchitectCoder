import { RelationType, Stereotype, type Position, type UmlClass, type UmlDiagram } from '../types/uml';

/**
 * Geometry used by both the persisted class auto-layout and the canvas renderer.
 * Keeping the estimate here prevents a layout that looks valid in state from
 * being moved again by the renderer after long members expand a class node.
 */
export function getClassNodeSize(cls: UmlClass): { width: number; height: number } {
  const width = cls.size.width || 200;
  const maxChars = Math.max(18, Math.floor((width - 20) / 7));
  const wrappedRows = (rows: string[]) => rows.reduce((sum, row) => (
    sum + Math.max(1, Math.ceil(row.replace(/\s+/g, ' ').trim().length / maxChars))
  ), 0);
  const attributeRows = cls.attributes.map((attribute) => (
    `${attribute.visibility} ${attribute.name}: ${attribute.type}${attribute.default_value ? ` = ${attribute.default_value}` : ''}`
  ));
  const operationRows = cls.methods.map((method) => (
    `${method.visibility} ${method.name}(${method.params}): ${method.return_type}`
  ));
  const interfaceRows = (cls.provided_interfaces?.length ? 1 : 0)
    + (cls.required_interfaces?.length ? 1 : 0);
  const headerHeight = cls.stereotype !== Stereotype.CLASS ? 58 : 42;
  const interfaceHeight = interfaceRows ? 28 + interfaceRows * 16 : 0;
  const attributesHeight = 28 + Math.max(1, wrappedRows(attributeRows)) * 19;
  const operationsHeight = 28 + Math.max(1, wrappedRows(operationRows)) * 19;
  const noteLines = cls.note
    ? Math.max(1, Math.ceil(cls.note.replace(/\s+/g, ' ').trim().length / maxChars))
    : 0;
  const noteHeight = noteLines ? 12 + noteLines * 14 : 0;

  return {
    width,
    height: Math.max(
      cls.size.height || 150,
      headerHeight + interfaceHeight + attributesHeight + operationsHeight + noteHeight + 8,
    ),
  };
}

export interface ClassRenderLayout extends Position {
  width: number;
  height: number;
}

function rectanglesOverlap(first: ClassRenderLayout, second: ClassRenderLayout, gap: number): boolean {
  return first.x < second.x + second.width + gap
    && first.x + first.width + gap > second.x
    && first.y < second.y + second.height + gap
    && first.y + first.height + gap > second.y;
}

/** Resolve renderer-only growth while preserving the persisted auto-layout intent. */
export function resolveClassLayouts(classes: UmlClass[]): Map<string, ClassRenderLayout> {
  const gap = 36;
  const placed: ClassRenderLayout[] = [];
  const layouts = new Map<string, ClassRenderLayout>();
  const ordered = [...classes].sort((a, b) => (
    a.position.y - b.position.y || a.position.x - b.position.x || a.id.localeCompare(b.id)
  ));

  ordered.forEach((cls) => {
    const size = getClassNodeSize(cls);
    const desired = { x: cls.position.x, y: cls.position.y, ...size };
    let position = desired;
    const candidates = [
      desired,
      ...placed.flatMap((other) => [
        { ...desired, x: other.x - size.width - gap },
        { ...desired, x: other.x + other.width + gap },
        { ...desired, y: other.y + other.height + gap },
      ]),
    ];
    const valid = candidates
      .filter((candidate) => placed.every((other) => !rectanglesOverlap(candidate, other, 0)))
      .sort((a, b) => {
        const distance = (candidate: ClassRenderLayout) =>
          Math.abs(candidate.x - desired.x) + Math.abs(candidate.y - desired.y);
        return distance(a) - distance(b) || a.y - b.y || a.x - b.x;
      });
    if (valid.length > 0) position = valid[0];
    else if (placed.length > 0) {
      const bottom = Math.max(...placed.map((item) => item.y + item.height));
      position = { ...desired, y: bottom + gap };
    }
    layouts.set(cls.id, position);
    placed.push(position);
  });
  return layouts;
}

interface Group {
  id: number;
  members: string[];
  outgoing: Set<number>;
  incoming: Set<number>;
  level: number;
}

/** Tarjan condensation makes cyclic domain models layout as a group, not as a broken tail row. */
function condense(ids: string[], outgoing: Map<string, string[]>): Group[] {
  let index = 0;
  const indices = new Map<string, number>();
  const lowLinks = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const groups: Group[] = [];
  const groupByMember = new Map<string, number>();

  const visit = (id: string) => {
    indices.set(id, index);
    lowLinks.set(id, index);
    index += 1;
    stack.push(id);
    onStack.add(id);
    (outgoing.get(id) || []).forEach((next) => {
      if (!indices.has(next)) {
        visit(next);
        lowLinks.set(id, Math.min(lowLinks.get(id)!, lowLinks.get(next)!));
      } else if (onStack.has(next)) {
        lowLinks.set(id, Math.min(lowLinks.get(id)!, indices.get(next)!));
      }
    });
    if (lowLinks.get(id) !== indices.get(id)) return;
    const members: string[] = [];
    let member = '';
    do {
      member = stack.pop()!;
      onStack.delete(member);
      members.push(member);
    } while (member !== id);
    const group: Group = { id: groups.length, members, outgoing: new Set(), incoming: new Set(), level: 0 };
    groups.push(group);
    members.forEach((item) => groupByMember.set(item, group.id));
  };

  ids.forEach((id) => { if (!indices.has(id)) visit(id); });
  outgoing.forEach((targets, source) => targets.forEach((target) => {
    const fromGroup = groupByMember.get(source)!;
    const toGroup = groupByMember.get(target)!;
    if (fromGroup === toGroup) return;
    groups[fromGroup].outgoing.add(toGroup);
    groups[toGroup].incoming.add(fromGroup);
  }));
  return groups;
}

/**
 * Layer classes by the meaningful UML direction and use relation-aware ordering
 * within a layer. Inheritance remains top-down; other diagrams follow their
 * declared dependency direction. Cycles are condensed instead of being dumped
 * below the rest of the diagram.
 */
export function layoutClasses(diagram: UmlDiagram): Map<string, Position> {
  const classes = diagram.classes || [];
  const ids = new Set(classes.map((cls) => cls.id));
  const hierarchy = diagram.relations.filter((relation) => (
    (relation.type === RelationType.INHERITANCE || relation.type === RelationType.REALIZATION)
    && ids.has(relation.source) && ids.has(relation.target)
  ));
  const primary = hierarchy.length > 0
    ? hierarchy
    : diagram.relations.filter((relation) => ids.has(relation.source) && ids.has(relation.target));
  const hierarchyMode = hierarchy.length > 0;
  const outgoing = new Map(classes.map((cls) => [cls.id, [] as string[]]));
  const incoming = new Map(classes.map((cls) => [cls.id, [] as string[]]));
  primary.forEach((relation) => {
    const from = hierarchyMode ? relation.target : relation.source;
    const to = hierarchyMode ? relation.source : relation.target;
    const neighbors = outgoing.get(from);
    if (!neighbors || neighbors.includes(to)) return;
    neighbors.push(to);
    incoming.get(to)?.push(from);
  });

  const groups = condense(classes.map((cls) => cls.id), outgoing);
  const indegree = new Map(groups.map((group) => [group.id, group.incoming.size]));
  const queue = groups.filter((group) => indegree.get(group.id) === 0).map((group) => group.id);
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const group = groups[queue[cursor]];
    group.outgoing.forEach((targetId) => {
      const target = groups[targetId];
      target.level = Math.max(target.level, group.level + 1);
      const next = (indegree.get(targetId) || 0) - 1;
      indegree.set(targetId, next);
      if (next === 0) queue.push(targetId);
    });
  }

  const rows = new Map<number, UmlClass[]>();
  groups.forEach((group) => group.members.forEach((member) => {
    const cls = classes.find((item) => item.id === member)!;
    const row = rows.get(group.level) || [];
    row.push(cls);
    rows.set(group.level, row);
  }));
  const orderedLevels = [...rows.keys()].sort((a, b) => a - b);
  const positions = new Map<string, Position>();
  const centers = new Map<string, number>();
  const sizes = new Map(classes.map((cls) => [cls.id, getClassNodeSize(cls)]));
  const startX = 120;
  const startY = 100;
  const horizontalGap = 96;
  const verticalGap = 110;
  const maxRowWidth = Math.max(...orderedLevels.map((level) => {
    const row = rows.get(level) || [];
    return row.reduce((sum, cls) => sum + sizes.get(cls.id)!.width, 0)
      + Math.max(0, row.length - 1) * horizontalGap;
  }));
  const centerX = Math.max(680, startX + maxRowWidth / 2);
  let nextY = startY;
  orderedLevels.forEach((level) => {
    const row = rows.get(level) || [];
    row.sort((a, b) => {
      const barycenter = (cls: UmlClass) => {
        const upstream = incoming.get(cls.id) || [];
        const values = upstream.map((id) => centers.get(id)).filter((value): value is number => value !== undefined);
        return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : cls.position.x;
      };
      return barycenter(a) - barycenter(b) || a.position.x - b.position.x || a.id.localeCompare(b.id);
    });
    const totalWidth = row.reduce((sum, cls) => sum + sizes.get(cls.id)!.width, 0)
      + Math.max(0, row.length - 1) * horizontalGap;
    let nextX = Math.max(startX, centerX - totalWidth / 2);
    row.forEach((cls) => {
      const size = sizes.get(cls.id)!;
      positions.set(cls.id, { x: nextX, y: nextY });
      centers.set(cls.id, nextX + size.width / 2);
      nextX += size.width + horizontalGap;
    });
    nextY += Math.max(...row.map((cls) => sizes.get(cls.id)!.height)) + verticalGap;
  });
  return positions;
}
