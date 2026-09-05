import type { Graph } from '@antv/x6';

let activeGraph: Graph | null = null;

/** Register the graph currently rendered by the active canvas editor. */
export function registerCanvasGraph(graph: Graph): void {
  activeGraph = graph;
}

/** Clear the active graph only when it is the graph being disposed. */
export function unregisterCanvasGraph(graph: Graph): void {
  if (activeGraph === graph) {
    activeGraph = null;
  }
}

/** Return the graph owned by the currently visible diagram editor. */
export function getActiveCanvasGraph(): Graph | null {
  return activeGraph;
}
