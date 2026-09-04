import type { Graph } from '@antv/x6';

interface GraphViewportOptions {
  container: HTMLElement;
  zoom: number;
  panX: number;
  panY: number;
  onZoom: (zoom: number) => void;
  onPan: (x: number, y: number) => void;
}

/** Bind an X6 graph to its container and persisted viewport state. */
export function attachGraphViewport(
  graph: Graph,
  options: GraphViewportOptions,
): () => void {
  let applyingViewport = false;

  const resize = () => {
    const width = Math.max(1, options.container.clientWidth);
    const height = Math.max(1, options.container.clientHeight);
    graph.resize(width, height);
  };

  const handleScale = ({ sx }: { sx: number }) => {
    if (!applyingViewport) options.onZoom(sx);
  };
  const handleTranslate = ({ tx, ty }: { tx: number; ty: number }) => {
    if (!applyingViewport) options.onPan(tx, ty);
  };

  graph.on('scale', handleScale);
  graph.on('translate', handleTranslate);

  applyingViewport = true;
  if (Math.abs(graph.zoom() - options.zoom) > 0.001) {
    graph.zoomTo(options.zoom);
  }
  const currentTranslation = graph.translate();
  if (
    Math.abs(currentTranslation.tx - options.panX) > 0.5
    || Math.abs(currentTranslation.ty - options.panY) > 0.5
  ) {
    graph.translate(options.panX, options.panY);
  }
  applyingViewport = false;
  resize();

  const observer = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(resize)
    : null;
  observer?.observe(options.container);

  return () => {
    observer?.disconnect();
    graph.off('scale', handleScale);
    graph.off('translate', handleTranslate);
  };
}
