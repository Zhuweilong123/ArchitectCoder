import type { UmlDiagram } from '../types/uml';
import type { CompNode } from '../types/component';
import { orderArchitectureGraph, type ArchitectureEdge } from './architectureGraph';

/** Space occupied by a component's title and interface groups. */
export function getComponentHeaderHeight(component: CompNode): number {
  const groups = [component.provided_interfaces || [], component.required_interfaces || []];
  return 76 + groups.reduce((height, interfaces) => (
    height + (interfaces.length ? 24 + interfaces.length * 22 : 0)
  ), 0);
}

export function getComponentChildTop(component: CompNode): number {
  return getComponentHeaderHeight(component) + 36;
}

export function getComponentDividerTop(component: CompNode): number {
  return getComponentHeaderHeight(component) + 12;
}

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
  childrenByParent.forEach((children, parentId) => {
    const childIds = new Set(children.map((child) => child.id));
    const childOwner = (id: string): string | undefined => {
      let current = id;
      const seen = new Set<string>();
      while (componentById.has(current) && !seen.has(current)) {
        if (childIds.has(current)) return current;
        seen.add(current);
        const parent = componentById.get(current)!.parent_id;
        if (!parent || parent === parentId) break;
        current = parent;
      }
      return undefined;
    };
    const edges: ArchitectureEdge[] = (diagram.comp_relations || []).map((relation) => ({
      source: childOwner(relation.source) || '', target: childOwner(relation.target) || '',
    }));
    const order = new Map(children.map((child) => [child.id, child.x]));
    const rows = orderArchitectureGraph(children.map((child) => child.id), edges, order).rows;
    const orderedIds = [...rows.keys()].sort((a, b) => a - b).flatMap((level) => rows.get(level)!);
    children.sort((a, b) => orderedIds.indexOf(a.id) - orderedIds.indexOf(b.id));
  });

  const topLevel = components.filter((component) => (
    !component.parent_id || !componentById.has(component.parent_id)
  ));
  const topLevelIds = new Set(topLevel.map((component) => component.id));
  const sizes = new Map<string, { width: number; height: number }>();
  components.forEach((component) => {
    sizes.set(component.id, {
      width: component.width || (component.parent_id ? 150 : 200),
      height: Math.max(component.height || (component.parent_id ? 100 : 160),
        getComponentHeaderHeight(component)),
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
      height: Math.max(currentSize.height, gridHeight + getComponentChildTop(componentById.get(id)!) + 20),
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
  const edges: ArchitectureEdge[] = [];
  (diagram.comp_relations || []).forEach((relation) => {
    const source = ownerOf(relation.source);
    const target = ownerOf(relation.target);
    if (!topLevelIds.has(source) || !topLevelIds.has(target) || source === target) return;
    edges.push({ source, target });
  });
  const originalOrder = new Map(topLevel.map((component) => [component.id, component.y]));
  const ordered = orderArchitectureGraph(topLevel.map((component) => component.id), edges, originalOrder);
  const positions = new Map<string, { x: number; y: number }>();
  const rows = new Map<number, CompNode[]>();
  ordered.rows.forEach((ids, level) => {
    rows.set(level, ids.map((id) => componentById.get(id)!));
  });
  const orderedLevels = Array.from(rows.keys()).sort((a, b) => a - b);
  const columnGap = 160;
  const verticalGap = 70;
  const maxColumnHeight = Math.max(...orderedLevels.map((level) => {
    const row = rows.get(level) || [];
    return row.reduce((sum, component) => sum + sizes.get(component.id)!.height, 0)
      + Math.max(0, row.length - 1) * verticalGap;
  }));
  const layoutCenterY = Math.max(480, maxColumnHeight / 2 + 80);
  let nextX = 100;
  orderedLevels.forEach((level) => {
    const row = rows.get(level) || [];
    const totalHeight = row.reduce((sum, component) => sum + sizes.get(component.id)!.height, 0)
      + Math.max(0, row.length - 1) * verticalGap;
    let nextY = layoutCenterY - totalHeight / 2;
    const columnWidth = Math.max(...row.map((component) => sizes.get(component.id)!.width));
    row.forEach((component) => {
      const height = sizes.get(component.id)!.height;
      positions.set(component.id, { x: Math.max(100, nextX), y: nextY });
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
      const y = parentPosition.y + getComponentChildTop(parent)
        + rowHeights.slice(0, row).reduce((sum, height) => sum + height + gapY, 0);
      positions.set(child.id, { x, y });
      placeChildren(child.id);
    });
  };
  topLevel.forEach((component) => placeChildren(component.id));

  return {
    ...diagram,
    // The old turns belong to the old node positions. A new automatic layout
    // must also reroute edges, including previously adjusted ones.
    comp_relations: (diagram.comp_relations || []).map((relation) => ({
      ...relation,
      vertices: undefined,
    })),
    components: components.map((component) => ({
      ...component,
      ...(positions.get(component.id) || {}),
      ...(sizes.get(component.id) || {}),
    })),
  };
}
