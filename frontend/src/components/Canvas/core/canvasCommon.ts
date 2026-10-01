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

type EdgeSide = 'left' | 'right' | 'top' | 'bottom';

/** Shared edge ports for class and top-level component diagrams. */
export function getSpacedEdgePorts(
  edge: CanvasEdgeEndpoint,
  edges: CanvasEdgeEndpoint[],
  nodes: CanvasNodeRect[],
  stub = 32,
  laneSpacing = 0,
  preferAlignedPort = false,
  spreadTargetLanes = false,
) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const source = byId.get(edge.source);
  const target = byId.get(edge.target);
  if (!source || !target) return null;
  const center = (node: CanvasNodeRect) => ({ x: node.x + node.width / 2, y: node.y + node.height / 2 });
  const sides = (from: CanvasNodeRect, to: CanvasNodeRect): [EdgeSide, EdgeSide] => {
    const a = center(from);
    const b = center(to);
    return Math.abs(b.x - a.x) >= Math.abs(b.y - a.y)
      ? (b.x >= a.x ? ['right', 'left'] : ['left', 'right'])
      : (b.y >= a.y ? ['bottom', 'top'] : ['top', 'bottom']);
  };
  const [sourceSide, targetSide] = sides(source, target);
  const offset = (node: CanvasNodeRect, side: EdgeSide, endpoint: 'source' | 'target') => {
    const peers = edges.filter((candidate) => {
      if (candidate[endpoint] !== node.id) return false;
      const from = byId.get(candidate.source);
      const to = byId.get(candidate.target);
      return !!from && !!to && sides(from, to)[endpoint === 'source' ? 0 : 1] === side;
    }).sort((a, b) => {
      const otherA = byId.get(a[endpoint === 'source' ? 'target' : 'source']);
      const otherB = byId.get(b[endpoint === 'source' ? 'target' : 'source']);
      const axis = side === 'left' || side === 'right' ? 'y' : 'x';
      return (otherA ? center(otherA)[axis] : 0) - (otherB ? center(otherB)[axis] : 0)
        || a.id.localeCompare(b.id);
    });
    const index = peers.findIndex((candidate) => candidate.id === edge.id);
    const span = side === 'left' || side === 'right' ? node.height : node.width;
    const limit = Math.max(0, span / 2 - 36);
    const axis = side === 'left' || side === 'right' ? 'y' : 'x';
    const alignedIndex = preferAlignedPort && peers.length > 1
      ? peers.reduce((best, candidate, candidateIndex) => {
        const other = byId.get(candidate[endpoint === 'source' ? 'target' : 'source'])!;
        const bestOther = byId.get(peers[best][endpoint === 'source' ? 'target' : 'source'])!;
        return Math.abs(center(other)[axis] - center(node)[axis])
          < Math.abs(center(bestOther)[axis] - center(node)[axis]) ? candidateIndex : best;
      }, 0)
      : -1;
    const alignedOther = alignedIndex >= 0
      ? byId.get(peers[alignedIndex][endpoint === 'source' ? 'target' : 'source'])! : null;
    const centered = alignedOther
      && Math.abs(center(alignedOther)[axis] - center(node)[axis]) <= 8;
    return {
      shift: Math.max(-limit, Math.min(limit,
        (index - (centered ? alignedIndex : (peers.length - 1) / 2)) * 18)),
      rank: Math.max(0, index),
    };
  };
  const port = (node: CanvasNodeRect, side: EdgeSide, shift: number) => {
    const middle = center(node);
    switch (side) {
      case 'left': return { x: node.x, y: middle.y + shift };
      case 'right': return { x: node.x + node.width, y: middle.y + shift };
      case 'top': return { x: middle.x + shift, y: node.y };
      case 'bottom': return { x: middle.x + shift, y: node.y + node.height };
    }
  };
  const outside = (point: CanvasPoint, side: EdgeSide, distance: number) => {
    switch (side) {
      case 'left': return { x: point.x - distance, y: point.y };
      case 'right': return { x: point.x + distance, y: point.y };
      case 'top': return { x: point.x, y: point.y - distance };
      case 'bottom': return { x: point.x, y: point.y + distance };
    }
  };
  const sourceOffset = offset(source, sourceSide, 'source');
  const targetOffset = offset(target, targetSide, 'target');
  const sourcePoint = port(source, sourceSide, sourceOffset.shift);
  const targetPoint = port(target, targetSide, targetOffset.shift);
  return {
    sourcePoint, targetPoint,
    sourceOutside: outside(sourcePoint, sourceSide, stub
      + (spreadTargetLanes ? sourceOffset.rank : Math.min(sourceOffset.rank, 4)) * laneSpacing),
    targetOutside: outside(targetPoint, targetSide, stub
      + (spreadTargetLanes ? targetOffset.rank * laneSpacing : 0)),
  };
}

/** Avoid triggering X6 route work when a store sync already has these points. */
export function edgeVerticesEqual(
  current: Array<{ x: number; y: number }> | undefined,
  next: Array<{ x: number; y: number }> | undefined,
): boolean {
  if (current === next) return true;
  if (!current || !next || current.length !== next.length) return false;
  return current.every((point, index) => (
    point.x === next[index].x && point.y === next[index].y
  ));
}

const ROUTER_CLEARANCE = 32;

/**
 * Shared Manhattan settings for diagrams whose edges must not cut through
 * unrelated nodes.  The default search budget is too small for a dense
 * project diagram and silently falls back to the non-obstacle-aware `orth`
 * router when exhausted.
 */
export function getObstacleAvoidingManhattanRouter(clearance = ROUTER_CLEARANCE) {
  return {
    name: 'manhattan' as const,
    args: {
      padding: clearance,
      step: Math.min(16, Math.max(4, clearance)),
      maxLoopCount: 20_000,
      // A terminal must be reachable; every other visible node stays an
      // obstacle in the route map.
      excludeTerminals: ['source', 'target'],
      ...(clearance < ROUTER_CLEARANCE ? {
        // Component routes already have verified orthogonal waypoints. X6's
        // generic fallback and grid snapping can move them into a rectangle.
        fallbackRouter: (vertices: CanvasPoint[]) => vertices,
        snapToGrid: false,
      } : {}),
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

interface RouteTraffic {
  routes: CanvasPoint[][];
  spacing?: number;
  segments?: Array<{ start: CanvasPoint; end: CanvasPoint }>;
}

/** Prefer separate parallel channels; a crossing is cheaper than a shared segment. */
function routeTrafficCost(start: CanvasPoint, end: CanvasPoint, traffic?: RouteTraffic): number {
  if (!traffic) return 0;
  const horizontal = start.y === end.y;
  const axis = horizontal ? 'x' : 'y';
  const crossAxis = horizontal ? 'y' : 'x';
  const minimum = Math.min(start[axis], end[axis]);
  const maximum = Math.max(start[axis], end[axis]);
  const spacing = traffic.spacing || 14;
  let cost = 0;
  const tracks = traffic.segments || traffic.routes.flatMap((route) => route.slice(1).map((point, index) => ({
    start: route[index], end: point,
  })));
  tracks.forEach(({ start: before, end: point }) => {
    const parallel = horizontal ? before.y === point.y : before.x === point.x;
    if (parallel) {
      const distance = Math.abs(before[crossAxis] - start[crossAxis]);
      const overlap = Math.min(maximum, Math.max(before[axis], point[axis]))
        - Math.max(minimum, Math.min(before[axis], point[axis]));
      if (overlap > 0 && distance < spacing) cost += overlap * (spacing - distance) * 2;
    } else if (before[axis] === point[axis]
      && before[axis] > minimum && before[axis] < maximum
      && start[crossAxis] > Math.min(before[crossAxis], point[crossAxis])
      && start[crossAxis] < Math.max(before[crossAxis], point[crossAxis])) cost += 80;
  });
  return cost;
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

/** Find an orthogonal path when the short list of conventional bends is blocked. */
function findClearRoute(
  start: CanvasPoint,
  end: CanvasPoint,
  obstacles: CanvasNodeRect[],
  clearance: number,
  traffic?: RouteTraffic,
): CanvasPoint[] | null {
  const margin = clearance + 1;
  const spacing = traffic?.spacing || 14;
  const tracks = traffic?.routes.flatMap((route) => route.slice(1).map((point, index) => ({
    start: route[index], end: point,
  }))) || [];
  if (traffic) traffic = { ...traffic, segments: tracks };
  const xs = Array.from(new Set([
    start.x, end.x,
    ...obstacles.flatMap((node) => [node.x - margin, node.x + node.width + margin]),
    ...tracks.filter(({ start, end }) => start.x === end.x)
      .flatMap(({ start }) => [start.x - spacing, start.x + spacing]),
  ])).sort((a, b) => a - b);
  const ys = Array.from(new Set([
    start.y, end.y,
    ...obstacles.flatMap((node) => [node.y - margin, node.y + node.height + margin]),
    ...tracks.filter(({ start, end }) => start.y === end.y)
      .flatMap(({ start }) => [start.y - spacing, start.y + spacing]),
  ])).sort((a, b) => a - b);
  const width = xs.length;
  const height = ys.length;
  const startIndex = xs.indexOf(start.x) * height + ys.indexOf(start.y);
  const endIndex = xs.indexOf(end.x) * height + ys.indexOf(end.y);
  const point = (index: number): CanvasPoint => ({
    x: xs[Math.floor(index / height)], y: ys[index % height],
  });
  const blocked = new Uint8Array(width * height);
  for (let index = 0; index < blocked.length; index += 1) {
    if (obstacles.some((node) => isInsideExpandedRect(point(index), node, clearance))) {
      blocked[index] = 1;
    }
  }
  if (blocked[startIndex] || blocked[endIndex]) return null;

  // Each grid point has horizontal and vertical arrival states. The small
  // bend cost favours a readable route among equal-length alternatives.
  const stateCount = width * height * 3;
  const distances = new Float64Array(stateCount).fill(Number.POSITIVE_INFINITY);
  const previous = new Int32Array(stateCount).fill(-1);
  const heap: Array<{ state: number; score: number }> = [];
  const push = (state: number, score: number) => {
    heap.push({ state, score });
    for (let i = heap.length - 1; i > 0;) {
      const parent = Math.floor((i - 1) / 2);
      if (heap[parent].score <= heap[i].score) break;
      [heap[parent], heap[i]] = [heap[i], heap[parent]];
      i = parent;
    }
  };
  const pop = () => {
    const first = heap[0];
    const last = heap.pop();
    if (heap.length && last) {
      heap[0] = last;
      for (let i = 0;;) {
        const left = i * 2 + 1;
        const right = left + 1;
        if (left >= heap.length) break;
        const child = right < heap.length && heap[right].score < heap[left].score ? right : left;
        if (heap[i].score <= heap[child].score) break;
        [heap[i], heap[child]] = [heap[child], heap[i]];
        i = child;
      }
    }
    return first;
  };
  const initial = startIndex * 3;
  distances[initial] = 0;
  push(initial, 0);
  while (heap.length) {
    const entry = pop()!;
    const { state, score } = entry;
    if (score !== distances[state]) continue;
    const index = Math.floor(state / 3);
    if (index === endIndex) {
      const path: CanvasPoint[] = [];
      for (let current = state; current >= 0; current = previous[current]) {
        path.push(point(Math.floor(current / 3)));
      }
      path.reverse();
      return path.slice(1, -1).filter((middle, i, inner) => {
        const before = i === 0 ? start : inner[i - 1];
        const after = i === inner.length - 1 ? end : inner[i + 1];
        return (before.x !== middle.x || middle.x !== after.x)
          && (before.y !== middle.y || middle.y !== after.y);
      });
    }
    const x = Math.floor(index / height);
    const y = index % height;
    for (const [nextX, nextY, direction] of [
      [x - 1, y, 1], [x + 1, y, 1], [x, y - 1, 2], [x, y + 1, 2],
    ]) {
      if (nextX < 0 || nextX >= width || nextY < 0 || nextY >= height) continue;
      const nextIndex = nextX * height + nextY;
      if (blocked[nextIndex]) continue;
      const next = point(nextIndex);
      if (obstacles.some((node) => segmentIntersectsExpandedRect(
        point(index), next, node, clearance,
      ))) continue;
      const nextState = nextIndex * 3 + direction;
      const distance = score + Math.abs(next.x - xs[x]) + Math.abs(next.y - ys[y])
        + (state % 3 !== 0 && state % 3 !== direction ? (traffic ? 80 : 16) : 0)
        + routeTrafficCost(point(index), next, traffic);
      if (distance >= distances[nextState]) continue;
      distances[nextState] = distance;
      previous[nextState] = state;
      push(nextState, distance);
    }
  }
  return null;
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
  clearance = ROUTER_CLEARANCE,
  endpoints?: { source?: CanvasPoint; target?: CanvasPoint; includeTerminals?: boolean; traffic?: RouteTraffic },
): CanvasPoint[] {
  const source = nodes.find((node) => node.id === edge.source);
  const target = nodes.find((node) => node.id === edge.target);
  if (!source || !target) return [];

  const sourceCenter = endpoints?.source || getNodeCenter(source);
  const targetCenter = endpoints?.target || getNodeCenter(target);
  const obstacles = endpoints?.includeTerminals
    ? nodes
    : nodes.filter((node) => node.id !== edge.source && node.id !== edge.target);
  if (obstacles.length === 0) {
    if (endpoints?.source && endpoints.target) {
      return sourceCenter.x === targetCenter.x || sourceCenter.y === targetCenter.y ? []
        : [{ x: targetCenter.x, y: sourceCenter.y }];
    }
    return getParallelEdgeVertices(edge, edges, nodes);
  }

  const left = Math.min(...obstacles.map((node) => node.x)) - clearance;
  const right = Math.max(...obstacles.map((node) => node.x + node.width)) + clearance;
  const top = Math.min(...obstacles.map((node) => node.y)) - clearance;
  const bottom = Math.max(...obstacles.map((node) => node.y + node.height)) + clearance;
  // In a layered graph, the globally outer corridor can be needlessly long
  // (or blocked at the source row). Add a small set of corridors immediately
  // outside every obstacle so a route can take the nearest clear side.
  const localClearance = clearance + 8;
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
        route[index], point, node, clearance,
      )) ? count + 1 : count
    ), 0);
    const trafficCost = route.slice(1).reduce((cost, point, index) => (
      cost + routeTrafficCost(route[index], point, endpoints?.traffic)
    ), 0);
    return {
      vertices: normalizeRouteVertices(vertices),
      collisions,
      trafficCost,
      // Prefer short paths, while retaining a modest penalty for turns when
      // paths have the same clearance.
      score: routeLength(route) + vertices.length * (endpoints?.traffic ? 80 : 16) + trafficCost,
    };
  }).sort((a, b) => a.collisions - b.collisions || a.score - b.score);
  if (scored[0]?.collisions === 0) {
    // A clear short corridor already has the fewest useful bends. Avoid a
    // full visibility-grid search for every ordinary adjacent connection.
    const minimumLength = Math.abs(sourceCenter.x - targetCenter.x) + Math.abs(sourceCenter.y - targetCenter.y);
    if (endpoints?.traffic && !scored[0].trafficCost && scored[0].vertices.length <= 2
      && scored[0].score <= minimumLength + 160) return scored[0].vertices;
    if (endpoints?.includeTerminals) {
      const gridRoute = findClearRoute(sourceCenter, targetCenter, obstacles, clearance, endpoints.traffic);
      // An outer connection can use a narrow corridor, but a modest distance
      // saving is not worth several extra bends in an architecture overview.
      const outerBendCost = 150;
      const gridPoints = gridRoute ? [sourceCenter, ...gridRoute, targetCenter] : [];
      if (gridRoute && routeLength(gridPoints)
        + gridRoute.length * (endpoints.traffic ? 80 : outerBendCost)
        + gridPoints.slice(1).reduce((cost, point, index) => (
          cost + routeTrafficCost(gridPoints[index], point, endpoints.traffic)
        ), 0)
        < (endpoints.traffic ? scored[0].score
          : routeLength([sourceCenter, ...scored[0].vertices, targetCenter])
            + scored[0].vertices.length * outerBendCost)) return gridRoute;
    }
    return scored[0].vertices;
  }
  return findClearRoute(sourceCenter, targetCenter, obstacles, clearance, endpoints?.traffic)
    || scored[0]?.vertices || [];
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

/** The graph container already excludes the side panels in the flex layout. */
export function centerCanvasContent(graph: Graph): void {
  graph.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
}

/** Fit a replacement diagram to the actual canvas, then center it. */
export function fitCanvasContent(graph: Graph): void {
  if (graph.getCells().length === 0) return;
  graph.zoomToFit({ padding: 32, minScale: 0.1, maxScale: 1 });
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
