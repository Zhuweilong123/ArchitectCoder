import type { CompNode, CompRelation } from '../../../types/component';
import { getComponentDividerTop } from '../../../utils/componentLayout';
import {
  getObstacleAvoidingEdgeVertices, getSpacedEdgePorts, type CanvasNodeRect,
} from './canvasCommon';

type Point = { x: number; y: number };
type Ports = NonNullable<ReturnType<typeof getSpacedEdgePorts>>;
export interface ComponentOuterRoute { ports: Ports; vertices: Point[] }

export function getComponentDelegationPorts(parent: CompNode, child: CompNode, components: CompNode[], clearance = 12) {
  const dividerY = parent.y + getComponentDividerTop(parent);
  const inset = clearance + 4;
  const siblings = components.filter((component) => component.parent_id === parent.id && component.id !== child.id);
  const crosses = (start: Point, end: Point) => siblings.some((sibling) => {
    const left = sibling.x - clearance;
    const right = sibling.x + sibling.width + clearance;
    const top = sibling.y - clearance;
    const bottom = sibling.y + sibling.height + clearance;
    return start.x === end.x
      ? start.x > left && start.x < right && Math.max(start.y, end.y) > top && Math.min(start.y, end.y) < bottom
      : start.y > top && start.y < bottom && Math.max(start.x, end.x) > left && Math.min(start.x, end.x) < right;
  });
  const direct = { source: { x: child.x + child.width / 2, y: dividerY },
    target: { x: child.x + child.width / 2, y: child.y }, targetAnchor: 'top' as const };
  if (!crosses(direct.source, direct.target)) return direct;
  const middleY = child.y + child.height / 2;
  const sides = [
    { source: { x: child.x - inset, y: dividerY }, target: { x: child.x, y: middleY }, targetAnchor: 'left' as const },
    { source: { x: child.x + child.width + inset, y: dividerY },
      target: { x: child.x + child.width, y: middleY }, targetAnchor: 'right' as const },
  ];
  return sides.find(({ source, target }) => source.x > parent.x + inset && source.x < parent.x + parent.width - inset
    && !crosses(source, { x: source.x, y: target.y }) && !crosses({ x: source.x, y: target.y }, target)) || direct;
}

function sharesChannel(a: Point, b: Point, c: Point, d: Point): boolean {
  const horizontal = a.y === b.y && c.y === d.y;
  const vertical = a.x === b.x && c.x === d.x;
  if (!horizontal && !vertical) return false;
  const axis = horizontal ? 'x' : 'y';
  const crossAxis = horizontal ? 'y' : 'x';
  return Math.abs(a[crossAxis] - c[crossAxis]) < 14
    && Math.min(Math.max(a[axis], b[axis]), Math.max(c[axis], d[axis]))
      > Math.max(Math.min(a[axis], b[axis]), Math.min(c[axis], d[axis]));
}

/** Keep stubs in the free space between neighbours, including after a manual move. */
function fitStub(point: Point, outside: Point, nodeId: string, nodes: CanvasNodeRect[], clearance: number): Point {
  const horizontal = point.y === outside.y;
  const direction = horizontal ? Math.sign(outside.x - point.x) : Math.sign(outside.y - point.y);
  let distance = Math.abs(outside.x - point.x) + Math.abs(outside.y - point.y);
  nodes.filter((node) => node.id !== nodeId).forEach((node) => {
    const inSpan = horizontal
      ? point.y >= node.y - clearance && point.y <= node.y + node.height + clearance
      : point.x >= node.x - clearance && point.x <= node.x + node.width + clearance;
    if (!inSpan) return;
    const boundary = horizontal
      ? direction > 0 ? node.x : node.x + node.width
      : direction > 0 ? node.y : node.y + node.height;
    const gap = direction * (boundary - (horizontal ? point.x : point.y));
    if (gap > 0) distance = Math.min(distance, gap / 2);
  });
  return horizontal ? { x: point.x + direction * distance, y: point.y }
    : { x: point.x, y: point.y + direction * distance };
}

/** Route the whole overview together so unrelated edges cannot silently share a bus. */
export function routeComponentOuterEdges(
  relations: CompRelation[], nodes: CanvasNodeRect[], clearance = 24,
): Map<string, ComponentOuterRoute> {
  const known = new Set(nodes.map(({ id }) => id));
  const edges = relations.filter((relation) => relation.type !== 'delegation'
    && known.has(relation.source) && known.has(relation.target));
  const result = new Map<string, ComponentOuterRoute>();
  const occupied: Point[][] = [];
  const portsById = new Map(edges.map((edge) => {
    const ports = getSpacedEdgePorts(edge, edges, nodes, clearance + 16, 8, true, true)!;
    ports.sourceOutside = fitStub(ports.sourcePoint, ports.sourceOutside, edge.source, nodes, clearance);
    ports.targetOutside = fitStub(ports.targetPoint, ports.targetOutside, edge.target, nodes, clearance);
    return [edge.id, ports];
  }));
  const separateStub = (point: Point, outside: Point, nodeId: string): [Point, Point] => {
    const node = nodes.find(({ id }) => id === nodeId)!;
    const horizontal = point.y === outside.y;
    const minimum = horizontal ? node.y + 36 : node.x + 36;
    const maximum = horizontal ? node.y + node.height - 36 : node.x + node.width - 36;
    const axis = horizontal ? 'y' : 'x';
    const offsets = [0, ...Array.from({ length: Math.max(0, Math.ceil((maximum - minimum) / 18)) }, (_, i) => (
      [(i + 1) * 18, -(i + 1) * 18]
    )).flat()];
    for (const offset of offsets) {
      const candidate = { ...point, [axis]: point[axis] + offset };
      if (candidate[axis] < minimum || candidate[axis] > maximum) continue;
      const stub = fitStub(candidate, { ...outside, [axis]: outside[axis] + offset }, nodeId, nodes, clearance);
      if (!occupied.some((route) => route.slice(1).some((end, i) => (
        sharesChannel(candidate, stub, route[i], end)
      )))) return [candidate, stub];
    }
    return [point, outside];
  };
  const separatePorts = (edge: CompRelation) => {
    const ports = portsById.get(edge.id)!;
    [ports.sourcePoint, ports.sourceOutside] = separateStub(ports.sourcePoint, ports.sourceOutside, edge.source);
    [ports.targetPoint, ports.targetOutside] = separateStub(ports.targetPoint, ports.targetOutside, edge.target);
  };
  const reserve = (edge: CompRelation, vertices: Point[]) => {
    const ports = portsById.get(edge.id)!;
    result.set(edge.id, { ports, vertices });
    occupied.push([ports.sourcePoint, ...vertices, ports.targetPoint]);
  };
  // User-owned paths occupy their channels before automatic paths are chosen.
  edges.filter((edge) => Array.isArray(edge.vertices)).forEach((edge) => reserve(edge, edge.vertices!));
  const length = (edge: CompRelation) => {
    const { sourcePoint, targetPoint } = portsById.get(edge.id)!;
    return Math.abs(sourcePoint.x - targetPoint.x) + Math.abs(sourcePoint.y - targetPoint.y);
  };
  edges.filter((edge) => !Array.isArray(edge.vertices))
    .sort((a, b) => length(a) - length(b) || a.id.localeCompare(b.id)).forEach((edge) => {
      separatePorts(edge);
      const ports = portsById.get(edge.id)!;
      const inner = getObstacleAvoidingEdgeVertices(edge, edges, nodes, clearance, {
        source: ports.sourceOutside, target: ports.targetOutside, includeTerminals: true,
        traffic: { routes: occupied, spacing: 14 },
      });
      reserve(edge, [ports.sourceOutside, ...inner, ports.targetOutside]);
    });
  return result;
}

/** Containers containing either endpoint are traversable; their other children remain obstacles. */
export function componentRoutingObstacles(
  source: string, target: string, components: CompNode[], rects: CanvasNodeRect[],
): CanvasNodeRect[] {
  const byId = new Map(components.map((component) => [component.id, component]));
  const ancestors = new Set<string>();
  [source, target].forEach((id) => {
    const seen = new Set<string>();
    while (byId.get(id)?.parent_id && !seen.has(id)) {
      seen.add(id);
      id = byId.get(id)!.parent_id;
      ancestors.add(id);
    }
  });
  return rects.filter((rect) => !ancestors.has(rect.id) || rect.id === source || rect.id === target);
}
