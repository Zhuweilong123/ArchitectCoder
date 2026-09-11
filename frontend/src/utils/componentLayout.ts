import type { UmlDiagram } from '../types/uml';
import type { CompNode } from '../types/component';

/** Calculate component positions and dimensions without mutating store state. */
export function layoutComponents(diagram: UmlDiagram): UmlDiagram | null {
  const components = diagram.components || [];
  if (components.length < 2) return null;

  const componentById = new Map(components.map((component) => [component.id, component]));
  const childrenByParent = new Map<string, CompNode[]>();
  components.forEach((component) => {
    if (!component.parent_id || !componentById.has(component.parent_id)) return;
    const children = childrenByParent.get(component.parent_id) || [];
    children.push(component);
    childrenByParent.set(component.parent_id, children);
  });
  childrenByParent.forEach((children) => children.sort((a, b) => (
    a.y - b.y || a.x - b.x || a.id.localeCompare(b.id)
  )));

  const topLevel = components.filter((component) => (
    !component.parent_id || !componentById.has(component.parent_id)
  ));
  const topLevelIds = new Set(topLevel.map((component) => component.id));
  const sizes = new Map<string, { width: number; height: number }>();
  components.forEach((component) => {
    sizes.set(component.id, {
      width: component.width || (component.parent_id ? 150 : 200),
      height: component.height || (component.parent_id ? 100 : 160),
    });
  });

  const prepareSize = (id: string, visiting = new Set<string>()) => {
    if (visiting.has(id)) return sizes.get(id)!;
    const nextVisiting = new Set(visiting).add(id);
    const children = childrenByParent.get(id) || [];
    children.forEach((child) => prepareSize(child.id, nextVisiting));
    if (children.length === 0) return sizes.get(id)!;

    const columns = Math.min(3, children.length);
    const gapX = 16;
    const gapY = 18;
    const columnWidths = Array.from({ length: columns }, () => 0);
    const rowHeights: number[] = [];
    children.forEach((child, index) => {
      const size = sizes.get(child.id)!;
      const row = Math.floor(index / columns);
      const column = index % columns;
      columnWidths[column] = Math.max(columnWidths[column], size.width);
      rowHeights[row] = Math.max(rowHeights[row] || 0, size.height);
    });
    const gridWidth = columnWidths.reduce((sum, width) => sum + width, 0)
      + Math.max(0, columns - 1) * gapX;
    const gridHeight = rowHeights.reduce((sum, height) => sum + height, 0)
      + Math.max(0, rowHeights.length - 1) * gapY;
    const currentSize = sizes.get(id)!;
    sizes.set(id, {
      width: Math.max(currentSize.width, gridWidth + 40),
      height: Math.max(currentSize.height, gridHeight + 64),
    });
    return sizes.get(id)!;
  };
  topLevel.forEach((component) => prepareSize(component.id));

  const ownerOf = (id: string): string => {
    let current = id;
    const visited = new Set<string>();
    while (componentById.get(current)?.parent_id && !visited.has(current)) {
      visited.add(current);
      const parentId = componentById.get(current)?.parent_id || '';
      if (!componentById.has(parentId)) break;
      current = parentId;
    }
    return topLevelIds.has(current) ? current : id;
  };
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  const levels = new Map<string, number>();
  topLevel.forEach((component) => {
    outgoing.set(component.id, []);
    incoming.set(component.id, []);
    indegree.set(component.id, 0);
    levels.set(component.id, 0);
  });
  (diagram.comp_relations || []).forEach((relation) => {
    const source = ownerOf(relation.source);
    const target = ownerOf(relation.target);
    if (!topLevelIds.has(source) || !topLevelIds.has(target) || source === target) return;
    const neighbors = outgoing.get(source)!;
    if (neighbors.includes(target)) return;
    neighbors.push(target);
    incoming.get(target)?.push(source);
    indegree.set(target, (indegree.get(target) || 0) + 1);
  });

  const queue = topLevel
    .filter((component) => indegree.get(component.id) === 0)
    .map((component) => component.id);
  const processed = new Set<string>();
  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index];
    processed.add(current);
    const currentLevel = levels.get(current) || 0;
    outgoing.get(current)?.forEach((target) => {
      levels.set(target, Math.max(levels.get(target) || 0, currentLevel + 1));
      const nextIndegree = (indegree.get(target) || 0) - 1;
      indegree.set(target, nextIndegree);
      if (nextIndegree === 0) queue.push(target);
    });
  }
  if (processed.size < topLevel.length) {
    const maxLevel = Math.max(...Array.from(levels.values()));
    topLevel.forEach((component) => {
      if (!processed.has(component.id)) levels.set(component.id, maxLevel + 1);
    });
  }

  const positions = new Map<string, { x: number; y: number }>();
  const rows = new Map<number, CompNode[]>();
  topLevel.forEach((component) => {
    const row = rows.get(levels.get(component.id) || 0) || [];
    row.push(component);
    rows.set(levels.get(component.id) || 0, row);
  });
  const orderedLevels = Array.from(rows.keys()).sort((a, b) => a - b);
  const columnGap = 180;
  const verticalGap = 70;
  const maxColumnHeight = Math.max(...orderedLevels.map((level) => {
    const row = rows.get(level) || [];
    return row.reduce((sum, component) => sum + sizes.get(component.id)!.height, 0)
      + Math.max(0, row.length - 1) * verticalGap;
  }));
  const layoutCenterY = Math.max(480, maxColumnHeight / 2 + 80);
  let nextX = 100;
  const centerById = new Map<string, number>();
  orderedLevels.forEach((level) => {
    const row = rows.get(level) || [];
    row.sort((a, b) => {
      const averageCenter = (component: CompNode) => {
        const upstreamCenters = (incoming.get(component.id) || [])
          .map((upstreamId) => centerById.get(upstreamId))
          .filter((center): center is number => typeof center === 'number');
        return upstreamCenters.length > 0
          ? upstreamCenters.reduce((sum, center) => sum + center, 0) / upstreamCenters.length
          : component.y;
      };
      return averageCenter(a) - averageCenter(b) || a.y - b.y || a.id.localeCompare(b.id);
    });
    const totalHeight = row.reduce((sum, component) => sum + sizes.get(component.id)!.height, 0)
      + Math.max(0, row.length - 1) * verticalGap;
    let nextY = layoutCenterY - totalHeight / 2;
    const columnWidth = Math.max(...row.map((component) => sizes.get(component.id)!.width));
    row.forEach((component) => {
      const height = sizes.get(component.id)!.height;
      positions.set(component.id, { x: Math.max(100, nextX), y: nextY });
      centerById.set(component.id, nextY + height / 2);
      nextY += height + verticalGap;
    });
    nextX += columnWidth + columnGap;
  });

  const placeChildren = (parentId: string) => {
    const parent = componentById.get(parentId);
    const parentPosition = positions.get(parentId);
    const children = childrenByParent.get(parentId) || [];
    if (!parent || !parentPosition || children.length === 0) return;
    const columns = Math.min(3, children.length);
    const gapX = 16;
    const gapY = 18;
    const columnWidths = Array.from({ length: columns }, () => 0);
    const rowHeights: number[] = [];
    children.forEach((child, index) => {
      const size = sizes.get(child.id)!;
      const row = Math.floor(index / columns);
      const column = index % columns;
      columnWidths[column] = Math.max(columnWidths[column], size.width);
      rowHeights[row] = Math.max(rowHeights[row] || 0, size.height);
    });
    children.forEach((child, index) => {
      const row = Math.floor(index / columns);
      const column = index % columns;
      const x = parentPosition.x + 20
        + columnWidths.slice(0, column).reduce((sum, width) => sum + width + gapX, 0);
      const y = parentPosition.y + 44
        + rowHeights.slice(0, row).reduce((sum, height) => sum + height + gapY, 0);
      positions.set(child.id, { x, y });
      placeChildren(child.id);
    });
  };
  topLevel.forEach((component) => placeChildren(component.id));

  return {
    ...diagram,
    components: components.map((component) => ({
      ...component,
      ...(positions.get(component.id) || {}),
      ...(sizes.get(component.id) || {}),
    })),
  };
}
