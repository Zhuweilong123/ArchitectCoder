import { RelationType, type Position, type UmlClass, type UmlDiagram } from '../types/uml';
import { getClassContentLayout } from './classContentLayout';
import { orderArchitectureGraph, type ArchitectureEdge } from './architectureGraph';

/**
 * Geometry used by both the persisted class auto-layout and the canvas renderer.
 * Keeping the estimate here prevents a layout that looks valid in state from
 * being moved again by the renderer after long members expand a class node.
 */
export function getClassNodeSize(cls: UmlClass): { width: number; height: number } {
  const width = cls.size.width || 200;
  return {
    width,
    height: Math.max(cls.size.height || 150, getClassContentLayout(cls, width).height),
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

/** Arrange one connected relation graph by its inheritance and ownership structure. */
function layoutConnectedClasses(diagram: UmlDiagram): Map<string, Position> {
  const classes = diagram.classes || [];
  if (!classes.length) return new Map();
  const byId = new Map(classes.map((cls) => [cls.id, cls]));
  const relations = diagram.relations.filter((relation) => (
    byId.has(relation.source) && byId.has(relation.target) && relation.source !== relation.target
  ));
  const hierarchy = relations.filter((relation) => (
    relation.type === RelationType.INHERITANCE || relation.type === RelationType.REALIZATION
  ));
  const hierarchyParticipants = new Set(hierarchy.flatMap((relation) => [
    relation.source, relation.target,
  ]));
  const structural = relations.filter((relation) => (
    (relation.type === RelationType.COMPOSITION || relation.type === RelationType.AGGREGATION)
    && !hierarchyParticipants.has(relation.source)
    && !hierarchyParticipants.has(relation.target)
  ));
  const structuralParticipants = new Set(structural.flatMap((relation) => [
    relation.source, relation.target,
  ]));
  const parent = new Map(classes.map((cls) => [cls.id, cls.id]));
  const root = (id: string): string => {
    const current = parent.get(id)!;
    if (current === id) return id;
    const result = root(current);
    parent.set(id, result);
    return result;
  };
  hierarchy.forEach(({ source, target }) => parent.set(root(source), root(target)));
  structural.forEach(({ source, target }) => {
    // Shared targets have multiple structural owners, so proximity alone would
    // falsely imply that every owner belongs to the same subsystem.
    if (structural.filter((relation) => relation.target === target).length !== 1) return;
    parent.set(root(target), root(source));
  });
  const groups = new Map<string, string[]>();
  classes.forEach((cls) => {
    const key = root(cls.id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(cls.id);
  });
  const groupOf = new Map<string, string>();
  groups.forEach((members, key) => members.forEach((id) => groupOf.set(id, key)));

  // A support class belongs near its consumers only when every relationship
  // reaches the same inheritance family. Never infer a persisted parent/module.
  classes.forEach((cls) => {
    const own = groupOf.get(cls.id)!;
    if (groups.get(own)!.length !== 1) return;
    const incident = relations.filter((relation) => (
      relation.source === cls.id || relation.target === cls.id
    ));
    const incoming = incident.filter((relation) => relation.target === cls.id);
    if (!incoming.length || incident.some((relation) => (
      relation.type !== RelationType.DEPENDENCY
    ))) return;
    const consumers = new Set(incoming.map((relation) => groupOf.get(relation.source)));
    if (consumers.size !== 1) return;
    const destination = [...consumers][0]!;
    if (destination === own || groups.get(destination)!.length < 2) return;
    if (incident.some((relation) => (
      groupOf.get(relation.source === cls.id ? relation.target : relation.source) !== destination
    ))) return;
    groups.get(own)!.splice(0, 1);
    groups.delete(own);
    groups.get(destination)!.push(cls.id);
    groupOf.set(cls.id, destination);
  });

  const sizes = new Map(classes.map((cls) => [cls.id, getClassNodeSize(cls)]));
  const originalOrder = new Map(classes.map((cls) => [cls.id, cls.position.x]));
  const localPositions = new Map<string, Position>();
  const groupSizes = new Map<string, { width: number; height: number }>();
  groups.forEach((members, groupId) => {
    const familyEdges: ArchitectureEdge[] = hierarchy
      .filter((relation) => members.includes(relation.source) && members.includes(relation.target))
      .map((relation) => ({ source: relation.target, target: relation.source, weight: 4 }));
    structural.filter((relation) => members.includes(relation.source) && members.includes(relation.target))
      .forEach((relation) => familyEdges.push({
        source: relation.source, target: relation.target, weight: 3,
      }));
    const ordered = orderArchitectureGraph(members, familyEdges, originalOrder);
    const support = members.filter((id) => (
      !hierarchyParticipants.has(id) && !structuralParticipants.has(id)
    ));
    support.forEach((id) => {
      const connected = relations.filter((relation) => relation.source === id || relation.target === id)
        .map((relation) => relation.source === id ? relation.target : relation.source)
        .filter((other) => members.includes(other) && other !== id);
      const level = Math.max(0, ...connected.map((other) => ordered.levels.get(other)!)) + 1;
      ordered.rows.get(ordered.levels.get(id)!)?.splice(
        ordered.rows.get(ordered.levels.get(id)!)!.indexOf(id), 1,
      );
      if (!ordered.rows.has(level)) ordered.rows.set(level, []);
      ordered.rows.get(level)!.push(id);
      ordered.levels.set(id, level);
    });
    const rows = [...ordered.rows.entries()].filter(([, row]) => row.length)
      .sort(([a], [b]) => a - b);
    const internalGapX = 64;
    const internalGapY = 84;
    const width = Math.max(...rows.map(([, row]) => row.reduce((sum, id) => (
      sum + sizes.get(id)!.width
    ), 0) + Math.max(0, row.length - 1) * internalGapX));
    let nextY = 0;
    rows.forEach(([, row]) => {
      const rowWidth = row.reduce((sum, id) => sum + sizes.get(id)!.width, 0)
        + Math.max(0, row.length - 1) * internalGapX;
      let nextX = (width - rowWidth) / 2;
      row.forEach((id) => {
        localPositions.set(id, { x: nextX, y: nextY });
        nextX += sizes.get(id)!.width + internalGapX;
      });
      nextY += Math.max(...row.map((id) => sizes.get(id)!.height)) + internalGapY;
    });
    groupSizes.set(groupId, { width, height: nextY - internalGapY });
  });

  const groupIds = [...groups.keys()];
  const groupEdges: ArchitectureEdge[] = relations
    .filter((relation) => !hierarchy.includes(relation))
    .map((relation) => ({
      source: groupOf.get(relation.source)!, target: groupOf.get(relation.target)!,
      weight: relation.type === RelationType.DEPENDENCY ? 1 : 3,
    }));
  const groupOrder = new Map(groupIds.map((id) => [id,
    Math.min(...groups.get(id)!.map((member) => originalOrder.get(member)!)),
  ]));
  const { levels, rows } = orderArchitectureGraph(groupIds, groupEdges, groupOrder);
  // A root used only by a later layer can sit just above its consumer. This
  // avoids a long edge spanning an otherwise unrelated middle layer.
  groupIds.forEach((id) => {
    if (groups.get(id)!.length !== 1) return;
    const incoming = groupEdges.filter((edge) => edge.target === id && edge.source !== id);
    const outgoing = groupEdges.filter((edge) => edge.source === id && edge.target !== id);
    if (incoming.length || outgoing.length !== 1) return;
    const nextLevel = (levels.get(outgoing[0].target) || 0) - 1;
    const oldLevel = levels.get(id)!;
    if (nextLevel <= oldLevel) return;
    const oldRow = rows.get(oldLevel)!;
    oldRow.splice(oldRow.indexOf(id), 1);
    if (!rows.has(nextLevel)) rows.set(nextLevel, []);
    rows.get(nextLevel)!.push(id);
    rows.get(nextLevel)!.sort((a, b) => groupOrder.get(a)! - groupOrder.get(b)!
      || a.localeCompare(b));
    levels.set(id, nextLevel);
  });
  const orderedLevels = [...rows.keys()].sort((a, b) => a - b);
  const positions = new Map<string, Position>();
  const startX = 120;
  const startY = 100;
  const horizontalGap = 170;
  const verticalGap = 100;
  const maxRowWidth = Math.max(...orderedLevels.map((level) => {
    const row = rows.get(level) || [];
    return row.reduce((sum, id) => sum + groupSizes.get(id)!.width, 0)
      + Math.max(0, row.length - 1) * horizontalGap;
  }));
  const centerX = Math.max(680, startX + maxRowWidth / 2);
  const degree = new Map(groupIds.map((id) => [id, 0]));
  groupEdges.forEach(({ source, target }) => {
    if (source === target) return;
    degree.set(source, (degree.get(source) || 0) + 1);
    degree.set(target, (degree.get(target) || 0) + 1);
  });
  let nextY = startY;
  orderedLevels.forEach((level) => {
    const row = rows.get(level) || [];
    if (!row.length) return;
    // Keep the best connected group on a shared vertical spine. Other groups
    // retain the requested clearance on either side of it.
    let nextX: number;
    if (groupIds.length <= 5 && row.length <= 2) {
      const anchorIndex = row.reduce((best, id, index) => (
        (degree.get(id) || 0) > (degree.get(row[best]) || 0) ? index : best
      ), 0);
      const beforeAnchor = row.slice(0, anchorIndex).reduce((sum, id) => (
        sum + groupSizes.get(id)!.width + horizontalGap
      ), 0);
      nextX = centerX - beforeAnchor - groupSizes.get(row[anchorIndex])!.width / 2;
    } else {
      const totalWidth = row.reduce((sum, id) => sum + groupSizes.get(id)!.width, 0)
        + Math.max(0, row.length - 1) * horizontalGap;
      nextX = centerX - totalWidth / 2;
    }
    row.forEach((id) => {
      groups.get(id)!.forEach((member) => {
        const local = localPositions.get(member)!;
        positions.set(member, { x: nextX + local.x, y: nextY + local.y });
      });
      nextX += groupSizes.get(id)!.width + horizontalGap;
    });
    nextY += Math.max(...row.map((id) => groupSizes.get(id)!.height)) + verticalGap;
  });
  const leftmost = Math.min(...[...positions.values()].map((point) => point.x));
  if (leftmost < startX) {
    const shift = startX - leftmost;
    positions.forEach((point, id) => positions.set(id, { ...point, x: point.x + shift }));
  }
  return positions;
}

/**
 * Independent relation graphs have independent layers. Pack their finished
 * bounds afterwards so a tall chain cannot push an unrelated pair far apart.
 */
export function layoutClasses(diagram: UmlDiagram): Map<string, Position> {
  const classes = diagram.classes || [];
  if (!classes.length) return new Map();
  const ids = new Set(classes.map((cls) => cls.id));
  const relations = (diagram.relations || []).filter((relation) => (
    ids.has(relation.source) && ids.has(relation.target)
  ));
  const parent = new Map(classes.map((cls) => [cls.id, cls.id]));
  const counts = new Map(classes.map((cls) => [cls.id, 1]));
  const root = (id: string): string => {
    let representative = id;
    while (parent.get(representative) !== representative) representative = parent.get(representative)!;
    while (id !== representative) {
      const next = parent.get(id)!;
      parent.set(id, representative);
      id = next;
    }
    return representative;
  };
  relations.forEach(({ source, target }) => {
    const from = root(source);
    const to = root(target);
    if (from === to) return;
    const [larger, smaller] = counts.get(from)! >= counts.get(to)!
      ? [from, to] : [to, from];
    parent.set(smaller, larger);
    counts.set(larger, counts.get(larger)! + counts.get(smaller)!);
  });
  const components = new Map<string, UmlClass[]>();
  classes.forEach((cls) => {
    const key = root(cls.id);
    if (!components.has(key)) components.set(key, []);
    components.get(key)!.push(cls);
  });

  const relatedIds = new Set(relations.flatMap(({ source, target }) => [source, target]));
  const linked = [...components.values()].filter((members) => (
    members.length > 1 || relatedIds.has(members[0].id)
  ));
  const isolated = [...components.values()].filter((members) => (
    members.length === 1 && !relatedIds.has(members[0].id)
  )).map((members) => members[0]);
  const positions = new Map<string, Position>();
  const startX = 120;
  const startY = 100;
  const maxRowRight = 1600;
  const blockGap = 130;
  const placedBlocks: ClassRenderLayout[] = [];
  let linkedRight = startX;
  let linkedBottom = startY;

  linked.forEach((members) => {
    const memberIds = new Set(members.map((cls) => cls.id));
    const local = layoutConnectedClasses({ ...diagram, classes: members,
      relations: relations.filter((relation) => (
        memberIds.has(relation.source) && memberIds.has(relation.target)
      )),
    });
    const left = Math.min(...members.map((cls) => local.get(cls.id)!.x));
    const top = Math.min(...members.map((cls) => local.get(cls.id)!.y));
    const width = Math.max(...members.map((cls) => (
      local.get(cls.id)!.x + getClassNodeSize(cls).width
    ))) - left;
    const height = Math.max(...members.map((cls) => (
      local.get(cls.id)!.y + getClassNodeSize(cls).height
    ))) - top;
    const candidatesX = [startX, ...placedBlocks.map((block) => block.x + block.width + blockGap)];
    const candidatesY = [startY, ...placedBlocks.map((block) => block.y + block.height + blockGap)];
    const candidates = candidatesX.flatMap((x) => candidatesY.map((y) => ({ x, y })))
      .filter(({ x, y }) => (
        (x + width <= maxRowRight || (x === startX && width > maxRowRight - startX))
        && placedBlocks.every((block) => (
          x >= block.x + block.width + blockGap
          || block.x >= x + width + blockGap
          || y >= block.y + block.height + blockGap
          || block.y >= y + height + blockGap
        ))
      ))
      .sort((a, b) => {
        const cost = ({ x, y }: Position) => (
          Math.max(linkedBottom, y + height)
          + 0.35 * Math.max(linkedRight, x + width)
          + y * 0.001 + x * 0.0001
        );
        return cost(a) - cost(b) || a.y - b.y || a.x - b.x;
      });
    const { x, y } = candidates[0] || { x: startX, y: linkedBottom + blockGap };
    members.forEach((cls) => {
      const point = local.get(cls.id)!;
      positions.set(cls.id, {
        x: x + point.x - left,
        y: y + point.y - top,
      });
    });
    placedBlocks.push({ x, y, width, height });
    linkedRight = Math.max(linkedRight, x + width);
    linkedBottom = Math.max(linkedBottom, y + height);
  });

  if (isolated.length) {
    const columns = Math.min(3, Math.ceil(Math.sqrt(isolated.length)));
    const gapX = 64;
    const gapY = 64;
    const columnWidths = Array.from({ length: columns }, () => 0);
    const rowHeights: number[] = [];
    isolated.forEach((cls, index) => {
      const size = getClassNodeSize(cls);
      columnWidths[index % columns] = Math.max(columnWidths[index % columns], size.width);
      const row = Math.floor(index / columns);
      rowHeights[row] = Math.max(rowHeights[row] || 0, size.height);
    });
    const gridWidth = columnWidths.reduce((sum, width) => sum + width, 0)
      + (columns - 1) * gapX;
    const gridX = linked.length && linkedRight + blockGap + gridWidth <= maxRowRight
      ? linkedRight + blockGap : startX;
    const gridY = linked.length && gridX === startX ? linkedBottom + blockGap : startY;
    isolated.forEach((cls, index) => {
      const row = Math.floor(index / columns);
      const column = index % columns;
      positions.set(cls.id, {
        x: gridX + columnWidths.slice(0, column).reduce((sum, width) => sum + width + gapX, 0),
        y: gridY + rowHeights.slice(0, row).reduce((sum, height) => sum + height + gapY, 0),
      });
    });
  }
  return positions;
}
