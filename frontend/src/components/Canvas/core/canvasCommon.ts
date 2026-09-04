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
