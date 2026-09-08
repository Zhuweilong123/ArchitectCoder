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
  return [{
    x: (sourceCenter.x + targetCenter.x) / 2 - (dy / length) * laneOffset,
    y: (sourceCenter.y + targetCenter.y) / 2 + (dx / length) * laneOffset,
  }];
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
