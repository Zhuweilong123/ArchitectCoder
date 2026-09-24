import { useEffect, type MutableRefObject, type RefObject } from 'react';
import type { Graph } from '@antv/x6';
import { useDiagramStore } from '../../../stores/diagramStore';
import { useUiStore } from '../../../stores/uiStore';
import { attachGraphViewport } from '../graphViewport';
import { centerCanvasContent, syncCanvasViewport, type CanvasViewport } from './canvasCommon';

/** Center a newly populated canvas once, unless the user has a saved viewport. */
export function centerCanvasAfterFirstSync(
  graph: Graph,
  graphRef: MutableRefObject<Graph | null>,
  didFirstSync: MutableRefObject<boolean>,
  viewport: CanvasViewport,
): void {
  if (didFirstSync.current || graph.getNodes().length === 0
    || viewport.panX || viewport.panY || viewport.zoom !== 1) return;
  didFirstSync.current = true;
  setTimeout(() => {
    const current = graphRef.current;
    if (current) centerCanvasContent(current, useUiStore.getState().rightPanelWidth);
  }, 200);
}

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
  const recenterCounter = useDiagramStore((state) => state.recenterCounter);

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

  useEffect(() => {
    if (recenterCounter <= 0) return;
    setTimeout(() => {
      const graph = graphRef.current;
      if (graph) centerCanvasContent(graph, useUiStore.getState().rightPanelWidth);
    }, 100);
  }, [recenterCounter]);
}
