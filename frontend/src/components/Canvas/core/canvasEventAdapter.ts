import type { Edge, Graph, Node } from '@antv/x6';

interface MutableFlag {
  current: boolean;
}

export interface CanvasEventAdapterOptions {
  graph: Graph;
  isInternalUpdate: MutableFlag;
  onNodeClick?: (node: Node) => void;
  onBlankClick?: () => void;
  onNodeMoved?: (node: Node) => void;
  onNodeResized?: (node: Node) => void;
  onEdgeClick?: (edge: Edge) => void;
  onNewEdge?: (edge: Edge, sourceId: string, targetId: string) => void;
  onEdgeRemoved?: (edge: Edge) => void;
  edgeTools?: Parameters<Edge['addTools']>[0];
}

/**
 * Binds the common X6 interaction contract used by UML-like editors.
 * Diagram-specific behaviors remain callbacks in the owning editor.
 */
export function attachCanvasEventAdapter(options: CanvasEventAdapterOptions): () => void {
  const {
    graph,
    isInternalUpdate,
    onNodeClick,
    onBlankClick,
    onNodeMoved,
    onNodeResized,
    onEdgeClick,
    onNewEdge,
    onEdgeRemoved,
    edgeTools,
  } = options;

  const handleNodeClick = ({ node }: { node: Node }) => onNodeClick?.(node);
  const handleBlankClick = () => onBlankClick?.();
  const handleNodeMoved = ({ node }: { node: Node }) => {
    if (!isInternalUpdate.current) onNodeMoved?.(node);
  };
  const handleNodeResized = ({ node }: { node: Node }) => {
    if (!isInternalUpdate.current) onNodeResized?.(node);
  };
  const handleEdgeClick = ({ edge }: { edge: Edge }) => onEdgeClick?.(edge);
  const handleEdgeConnected = ({ edge, isNew }: { edge: Edge; isNew?: boolean }) => {
    if (isInternalUpdate.current || !isNew) return;
    const sourceId = edge.getSourceCellId();
    const targetId = edge.getTargetCellId();
    if (sourceId && targetId) onNewEdge?.(edge, sourceId, targetId);
  };
  const handleEdgeMouseEnter = ({ edge }: { edge: Edge }) => {
    if (!edgeTools) return;
    try { edge.addTools(edgeTools); } catch { /* ignore disposed cells */ }
  };
  const handleEdgeMouseLeave = ({ edge }: { edge: Edge }) => {
    if (!edgeTools) return;
    try { edge.removeTools(); } catch { /* ignore disposed cells */ }
  };
  const handleEdgeRemoved = ({ edge }: { edge: Edge }) => {
    if (!isInternalUpdate.current) onEdgeRemoved?.(edge);
  };

  graph.on('node:click', handleNodeClick);
  graph.on('blank:click', handleBlankClick);
  graph.on('node:moved', handleNodeMoved);
  graph.on('node:resized', handleNodeResized);
  graph.on('edge:click', handleEdgeClick);
  graph.on('edge:connected', handleEdgeConnected);
  if (edgeTools) {
    graph.on('edge:mouseenter', handleEdgeMouseEnter);
    graph.on('edge:mouseleave', handleEdgeMouseLeave);
  }
  graph.on('edge:removed', handleEdgeRemoved);

  return () => {
    graph.off('node:click', handleNodeClick);
    graph.off('blank:click', handleBlankClick);
    graph.off('node:moved', handleNodeMoved);
    graph.off('node:resized', handleNodeResized);
    graph.off('edge:click', handleEdgeClick);
    graph.off('edge:connected', handleEdgeConnected);
    if (edgeTools) {
      graph.off('edge:mouseenter', handleEdgeMouseEnter);
      graph.off('edge:mouseleave', handleEdgeMouseLeave);
    }
    graph.off('edge:removed', handleEdgeRemoved);
  };
}
