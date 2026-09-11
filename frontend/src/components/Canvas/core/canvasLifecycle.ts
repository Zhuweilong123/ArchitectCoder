import type { MutableRefObject } from 'react';
import type { Graph } from '@antv/x6';
import { registerCanvasGraph, unregisterCanvasGraph } from './canvasRegistry';

/** Register one active editor graph and keep the shared canvas registry in sync. */
export function registerCanvasGraphInstance(
  graph: Graph,
  graphRef: MutableRefObject<Graph | null>,
): void {
  graphRef.current = graph;
  registerCanvasGraph(graph);
}

/** Dispose editor-owned graph resources in one consistent order. */
export function disposeCanvasGraphInstance(
  graph: Graph,
  graphRef: MutableRefObject<Graph | null>,
  cleanup?: () => void,
): void {
  cleanup?.();
  unregisterCanvasGraph(graph);
  try { graph.dispose(); } catch { /* ignore */ }
  graphRef.current = null;
}
