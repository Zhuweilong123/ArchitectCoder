import type { Graph } from '@antv/x6';

interface CanvasPointerEvent {
  button?: number;
  clientX: number;
  clientY: number;
}

/** Close the contextual panel after a blank tap, while preserving canvas panning. */
export function attachBlankCanvasTap(graph: Graph, onTap: () => void): () => void {
  let start: { x: number; y: number } | null = null;
  const handleDown = ({ e }: { e: CanvasPointerEvent }) => {
    start = typeof e.button === 'number' && e.button !== 0
      ? null
      : { x: e.clientX, y: e.clientY };
  };
  const handleUp = ({ e }: { e: CanvasPointerEvent }) => {
    const origin = start;
    start = null;
    if (origin && Math.hypot(e.clientX - origin.x, e.clientY - origin.y) <= 6) {
      onTap();
    }
  };

  graph.on('blank:mousedown', handleDown);
  graph.on('blank:mouseup', handleUp);
  return () => {
    graph.off('blank:mousedown', handleDown);
    graph.off('blank:mouseup', handleUp);
  };
}
