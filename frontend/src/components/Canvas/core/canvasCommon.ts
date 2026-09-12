import type { Edge, Graph } from '@antv/x6';

export interface CanvasViewport {
  zoom: number;
  panX: number;
  panY: number;
}

export interface CanvasGridSettings {
  visible: boolean;
  size: number;
  color: string;
  thickness: number;
}

export interface CanvasEdgeEndpoint {
  id: string;
  source: string;
  target: string;
}

export interface EdgeSelectionPoint {
  x: number;
  y: number;
  altKey?: boolean;
}

export interface EdgeSelectionCycleState {
  point: { x: number; y: number } | null;
  ids: string[];
  index: number;
  timestamp: number;
}

export interface CanvasNodeRect {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

const ROUTER_CLEARANCE = 32;

/**
 * Shared Manhattan settings for diagrams whose edges must not cut through
 * unrelated nodes.  The default search budget is too small for a dense
 * project diagram and silently falls back to the non-obstacle-aware `orth`
 * router when exhausted.
 */
export function getObstacleAvoidingManhattanRouter() {
  return {
    name: 'manhattan' as const,
    args: {
      padding: ROUTER_CLEARANCE,
      step: 16,
      maxLoopCount: 20_000,
      // A terminal must be reachable; every other visible node stays an
      // obstacle in the route map.
      excludeTerminals: ['source', 'target'],
    },
  };
}

function isInsideExpandedRect(
  point: { x: number; y: number },
  node: CanvasNodeRect,
  clearance: number,
): boolean {
  return point.x >= node.x - clearance
    && point.x <= node.x + node.width + clearance
    && point.y >= node.y - clearance
    && point.y <= node.y + node.height + clearance;
}

interface CanvasPoint {
  x: number;
  y: number;
}

function getNodeCenter(node: CanvasNodeRect): CanvasPoint {
  return { x: node.x + node.width / 2, y: node.y + node.height / 2 };
}

function segmentIntersectsExpandedRect(
  start: CanvasPoint,
  end: CanvasPoint,
  node: CanvasNodeRect,
  clearance: number,
): boolean {
  const minX = node.x - clearance;
  const maxX = node.x + node.width + clearance;
  const minY = node.y - clearance;
  const maxY = node.y + node.height + clearance;
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  let enter = 0;
  let exit = 1;

  for (const [origin, delta, minimum, maximum] of [
    [start.x, dx, minX, maxX],
    [start.y, dy, minY, maxY],
  ] as Array<[number, number, number, number]>) {
    if (Math.abs(delta) < 0.001) {
      if (origin < minimum || origin > maximum) return false;
      continue;
    }
    const near = (minimum - origin) / delta;
    const far = (maximum - origin) / delta;
    enter = Math.max(enter, Math.min(near, far));
    exit = Math.min(exit, Math.max(near, far));
    if (enter > exit) return false;
  }
  return enter <= 1 && exit >= 0;
}

function normalizeRouteVertices(points: CanvasPoint[]): CanvasPoint[] {
  return points.filter((point, index) => (
    index === 0 || point.x !== points[index - 1].x || point.y !== points[index - 1].y
  ));
}

function routeLength(points: CanvasPoint[]): number {
  return points.slice(1).reduce((total, point, index) => (
    total + Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y)
  ), 0);
}

/**
 * Supply Manhattan with obstacle-safe turning points before it performs its
 * finer grid search. This keeps a route clear even when X6 needs its `orth`
 * fallback for a very dense diagram.
 */
export function getObstacleAvoidingEdgeVertices(
  edge: CanvasEdgeEndpoint,
  edges: CanvasEdgeEndpoint[],
  nodes: CanvasNodeRect[],
): CanvasPoint[] {
  const source = nodes.find((node) => node.id === edge.source);
  const target = nodes.find((node) => node.id === edge.target);
  if (!source || !target) return [];

  const sourceCenter = getNodeCenter(source);
  const targetCenter = getNodeCenter(target);
  const obstacles = nodes.filter((node) => node.id !== edge.source && node.id !== edge.target);
  if (obstacles.length === 0) return getParallelEdgeVertices(edge, edges, nodes);

  const left = Math.min(...obstacles.map((node) => node.x)) - ROUTER_CLEARANCE;
  const right = Math.max(...obstacles.map((node) => node.x + node.width)) + ROUTER_CLEARANCE;
  const top = Math.min(...obstacles.map((node) => node.y)) - ROUTER_CLEARANCE;
  const bottom = Math.max(...obstacles.map((node) => node.y + node.height)) + ROUTER_CLEARANCE;
  // In a layered graph, the globally outer corridor can be needlessly long
  // (or blocked at the source row). Add a small set of corridors immediately
  // outside every obstacle so a route can take the nearest clear side.
  const localClearance = ROUTER_CLEARANCE + 8;
  const localCorridors = obstacles.flatMap((node) => [
    [{ x: node.x - localClearance, y: sourceCenter.y }, { x: node.x - localClearance, y: targetCenter.y }],
    [{ x: node.x + node.width + localClearance, y: sourceCenter.y }, { x: node.x + node.width + localClearance, y: targetCenter.y }],
    [{ x: sourceCenter.x, y: node.y - localClearance }, { x: targetCenter.x, y: node.y - localClearance }],
    [{ x: sourceCenter.x, y: node.y + node.height + localClearance }, { x: targetCenter.x, y: node.y + node.height + localClearance }],
  ]);
  const candidates: CanvasPoint[][] = [
    ...(sourceCenter.x === targetCenter.x || sourceCenter.y === targetCenter.y ? [[]] : []),
    [{ x: targetCenter.x, y: sourceCenter.y }],
    [{ x: sourceCenter.x, y: targetCenter.y }],
    [{ x: left, y: sourceCenter.y }, { x: left, y: targetCenter.y }],
    [{ x: right, y: sourceCenter.y }, { x: right, y: targetCenter.y }],
    [{ x: sourceCenter.x, y: top }, { x: targetCenter.x, y: top }],
    [{ x: sourceCenter.x, y: bottom }, { x: targetCenter.x, y: bottom }],
    ...localCorridors,
  ];

  const scored = candidates.map((vertices) => {
    const route = normalizeRouteVertices([sourceCenter, ...vertices, targetCenter]);
    const collisions = obstacles.reduce((count, node) => (
      route.slice(1).some((point, index) => segmentIntersectsExpandedRect(
        route[index], point, node, ROUTER_CLEARANCE,
      )) ? count + 1 : count
    ), 0);
    return {
      vertices: normalizeRouteVertices(vertices),
      collisions,
      // Prefer short paths, while retaining a modest penalty for turns when
      // paths have the same clearance.
      score: routeLength(route) + vertices.length * 16,
    };
  }).sort((a, b) => a.collisions - b.collisions || a.score - b.score);
  return scored[0]?.vertices || [];
}

/** Give parallel edges separate lanes so coincident relationships remain selectable. */
export function getParallelEdgeVertices(
  edge: CanvasEdgeEndpoint,
  edges: CanvasEdgeEndpoint[],
  nodes: CanvasNodeRect[],
  laneGap = 24,
): Array<{ x: number; y: number }> {
  const parallel = edges.filter((candidate) => (
    (candidate.source === edge.source && candidate.target === edge.target)
    || (candidate.source === edge.target && candidate.target === edge.source)
  ));
  if (parallel.length < 2) return [];
  const index = parallel.findIndex((candidate) => candidate.id === edge.id);
  if (index < 0) return [];

  const source = nodes.find((node) => node.id === edge.source);
  const target = nodes.find((node) => node.id === edge.target);
  if (!source || !target) return [];
  const sourceCenter = { x: source.x + source.width / 2, y: source.y + source.height / 2 };
  const targetCenter = { x: target.x + target.width / 2, y: target.y + target.height / 2 };
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  const length = Math.max(1, Math.sqrt(dx * dx + dy * dy));
  const laneOffset = (index - (parallel.length - 1) / 2) * laneGap;
  const waypoint = {
    x: (sourceCenter.x + targetCenter.x) / 2 - (dy / length) * laneOffset,
    y: (sourceCenter.y + targetCenter.y) / 2 + (dx / length) * laneOffset,
  };
  // A fixed vertex inside another node forces Manhattan to fail its partial
  // route and use the `orth` fallback, which is exactly how a line ends up
  // crossing a component/class.  Let the obstacle-aware router choose the
  // whole route when a parallel lane would be unsafe.
  const crossesThirdPartyNode = nodes.some((node) => (
    node.id !== edge.source
    && node.id !== edge.target
    && isInsideExpandedRect(waypoint, node, ROUTER_CLEARANCE)
  ));
  return crossesThirdPartyNode ? [] : [waypoint];
}

/** Promote automatic Manhattan route turns to editable edge vertices. */
export function materializeEdgeRouteVertices(graph: Graph, edge: Edge): boolean {
  const view = graph.findViewByCell(edge) as any;
  const routePoints = view?.routePoints;
  if (!Array.isArray(routePoints) || routePoints.length < 3) return false;
  const points = routePoints
    .slice(1, -1)
    .map((point: any) => ({ x: Number(point.x), y: Number(point.y) }))
    .filter((point: { x: number; y: number }) => (
      Number.isFinite(point.x) && Number.isFinite(point.y)
    ))
    .filter((point: { x: number; y: number }, index: number, list: Array<{ x: number; y: number }>) => (
      index === 0 || point.x !== list[index - 1].x || point.y !== list[index - 1].y
    ));
  if (points.length === 0 || edge.getVertices().length >= points.length) return false;
  edge.setVertices(points, { ui: true, toolId: 'materialize-route' });
  return true;
}


/**
 * Resolve an edge click when several X6 edge paths occupy the same location.
 * Normal clicks preserve the native topmost-edge behavior. Alt/Option-click
 * cycles through every edge whose rendered path is within the hit tolerance.
 */
export function resolveEdgeSelection(
  graph: Graph,
  clickedEdge: Edge,
  point: EdgeSelectionPoint,
  cycle: EdgeSelectionCycleState,
  tolerance = 10,
): Edge {
  // Keep the common click path O(1). Geometry scanning is only needed when
  // the user explicitly asks to cycle through overlapping edges.
  if (!point.altKey) return clickedEdge;
  const candidates = graph.getEdges()
    .map((edge) => {
      const view = graph.findViewByCell(edge) as any;
      const closest = view?.getClosestPoint?.({ x: point.x, y: point.y });
      if (!closest) return null;
      const distance = Math.hypot(closest.x - point.x, closest.y - point.y);
      return distance <= tolerance ? { edge, distance } : null;
    })
    .filter((item): item is { edge: Edge; distance: number } => item !== null)
    .sort((a, b) => a.distance - b.distance || a.edge.id.localeCompare(b.edge.id));
  if (candidates.length <= 1) {
    cycle.point = { x: point.x, y: point.y };
    cycle.ids = candidates.map(({ edge }) => edge.id);
    cycle.index = 0;
    cycle.timestamp = Date.now();
    return clickedEdge;
  }

  const ids = candidates.map(({ edge }) => edge.id);
  const samePoint = cycle.point
    && Math.hypot(cycle.point.x - point.x, cycle.point.y - point.y) <= tolerance;
  const sameCandidates = samePoint && cycle.ids.length === ids.length
    && cycle.ids.every((id, index) => id === ids[index]);
  const clickedIndex = ids.indexOf(clickedEdge.id);
  const shouldCycle = Boolean(point.altKey) && sameCandidates
    && Date.now() - cycle.timestamp < 2000;
  const nextIndex = shouldCycle
    ? (cycle.index + 1) % ids.length
    : Math.max(0, clickedIndex);
  cycle.point = { x: point.x, y: point.y };
  cycle.ids = ids;
  cycle.index = nextIndex;
  cycle.timestamp = Date.now();
  return candidates[nextIndex]?.edge || clickedEdge;
}

/** Apply the persisted viewport without creating a scale/translate feedback loop. */
export function syncCanvasViewport(graph: Graph, viewport: CanvasViewport): void {
  if (Math.abs(graph.zoom() - viewport.zoom) > 0.001) {
    graph.zoomTo(viewport.zoom);
  }
  const translation = graph.translate();
  if (
    Math.abs(translation.tx - viewport.panX) > 0.5
    || Math.abs(translation.ty - viewport.panY) > 0.5
  ) {
    graph.translate(viewport.panX, viewport.panY);
  }
}

/** Center content while accounting for the visible right-side property panel. */
export function centerCanvasContent(graph: Graph, sidebarWidth = 0): void {
  const bbox = graph.getAllCellsBBox?.() || graph.getContentBBox?.() || {
    x: 0, y: 0, width: 0, height: 0,
  };
  graph.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
  const visibleWidth = graph.options.width - sidebarWidth;
  if (bbox.width < visibleWidth - 40) {
    graph.translate(graph.translate().tx - sidebarWidth / 2, graph.translate().ty);
  }
}

/** Apply grid visibility and visual settings consistently across all editors. */
export function syncCanvasGrid(graph: Graph, settings: CanvasGridSettings): void {
  try {
    if (!settings.visible) {
      graph.hideGrid();
      return;
    }
    graph.showGrid();
    graph.setGridSize(settings.size);
    (graph as any).drawGrid({
      size: settings.size,
      args: { color: settings.color, thickness: settings.thickness },
    });
  } catch {
    // The graph can be disposed while React is cleaning up an editor.
  }
}
