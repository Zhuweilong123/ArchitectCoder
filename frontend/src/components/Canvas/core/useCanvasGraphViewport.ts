import { useEffect, type MutableRefObject, type RefObject } from 'react';
import type { Graph } from '@antv/x6';
import { useDiagramStore } from '../../../stores/diagramStore';
import { attachGraphViewport } from '../graphViewport';
import { syncCanvasViewport, type CanvasViewport } from './canvasCommon';

/**
 * Shared viewport lifecycle for all X6-backed diagram editors.
 *
 * Keeping this in one place prevents subtle differences in resize, zoom and
 * pan behavior between class, component and sequence diagrams.
 */
export function useCanvasGraphViewport(
  graphRef: MutableRefObject<Graph | null>,
  containerRef: RefObject<HTMLDivElement | null>,
  viewport: CanvasViewport,
): void {
  useEffect(() => {
    const graph = graphRef.current;
    const container = containerRef.current;
    if (!graph || !container) return undefined;

    return attachGraphViewport(graph, {
      container,
      zoom: viewport.zoom,
      panX: viewport.panX,
      panY: viewport.panY,
      onZoom: (zoom) => useDiagramStore.getState().setZoom(zoom),
      onPan: (x, y) => useDiagramStore.getState().setPan(x, y),
    });
  }, []); // graph and container are created by the preceding init effect

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    syncCanvasViewport(graph, { ...useDiagramStore.getState().viewport, zoom: viewport.zoom });
  }, [viewport.zoom]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    syncCanvasViewport(graph, { ...useDiagramStore.getState().viewport, panX: viewport.panX });
  }, [viewport.panX]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    syncCanvasViewport(graph, { ...useDiagramStore.getState().viewport, panY: viewport.panY });
  }, [viewport.panY]);
}
