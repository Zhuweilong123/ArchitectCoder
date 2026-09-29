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
  let touchCenter: { x: number; y: number } | null = null;
  const previousTouchAction = options.container.style.touchAction;
  options.container.style.touchAction = 'none';

  const centerOfTwoTouches = (touches: TouchList) => ({
    x: (touches[0].clientX + touches[1].clientX) / 2,
    y: (touches[0].clientY + touches[1].clientY) / 2,
  });
  const handleTouchStart = (event: TouchEvent) => {
    if (event.touches.length !== 2) return;
    touchCenter = centerOfTwoTouches(event.touches);
    if (event.cancelable) event.preventDefault();
    // X6 treats touchstart as a node/blank mousedown. The second finger
    // should begin a viewport gesture rather than another cell drag.
    event.stopPropagation();
  };
  const handleTouchMove = (event: TouchEvent) => {
    if (!touchCenter || event.touches.length !== 2) return;
    const next = centerOfTwoTouches(event.touches);
    graph.translateBy(next.x - touchCenter.x, next.y - touchCenter.y);
    touchCenter = next;
    if (event.cancelable) event.preventDefault();
    event.stopPropagation();
  };
  const handleTouchEnd = (event: TouchEvent) => {
    if (event.touches.length < 2) touchCenter = null;
  };
  const handleWheel = (event: WheelEvent) => {
    // Trackpads report two-finger scrolling as wheel events. Leave modified
    // wheel events to X6's existing Ctrl/Cmd zoom handler.
    if (event.ctrlKey || event.metaKey) return;
    const unit = event.deltaMode === 1 ? 16
      : event.deltaMode === 2 ? Math.max(1, options.container.clientHeight) : 1;
    graph.translateBy(-event.deltaX * unit, -event.deltaY * unit);
    if (event.cancelable) event.preventDefault();
  };
  options.container.addEventListener('touchstart', handleTouchStart, { capture: true, passive: false });
  options.container.addEventListener('touchmove', handleTouchMove, { capture: true, passive: false });
  options.container.addEventListener('touchend', handleTouchEnd, true);
  options.container.addEventListener('touchcancel', handleTouchEnd, true);
  options.container.addEventListener('wheel', handleWheel, { passive: false });

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
  // X6 writes a pixel width to the graph root. Observe the parent as well so
  // layout changes (for example, collapsing the right panel) still trigger a
  // graph resize even when the root's inline width has not changed yet.
  observer?.observe(options.container);
  if (options.container.parentElement) {
    observer?.observe(options.container.parentElement);
  }

  return () => {
    options.container.removeEventListener('touchstart', handleTouchStart, true);
    options.container.removeEventListener('touchmove', handleTouchMove, true);
    options.container.removeEventListener('touchend', handleTouchEnd, true);
    options.container.removeEventListener('touchcancel', handleTouchEnd, true);
    options.container.removeEventListener('wheel', handleWheel);
    options.container.style.touchAction = previousTouchAction;
    observer?.disconnect();
    graph.off('scale', handleScale);
    graph.off('translate', handleTranslate);
  };
}
