import type { Graph } from '@antv/x6';

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
