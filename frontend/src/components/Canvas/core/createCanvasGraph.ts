import { Graph, Edge } from '@antv/x6';
import { History } from '@antv/x6-plugin-history';
import { Transform } from '@antv/x6-plugin-transform';
import { Selection } from '@antv/x6-plugin-selection';
import { Snapline } from '@antv/x6-plugin-snapline';

export interface CanvasGraphOptions {
  container: HTMLElement;
  grid: {
    size: number;
    visible: boolean;
    color: string;
    thickness: number;
  };
  /** Omit for diagram types whose edges are rendered from persisted points. */
  connection?: {
    line: Record<string, unknown>;
    allowMulti?: boolean;
  };
}

/** Create the shared X6 graph shell used by all diagram editors. */
export function createCanvasGraph(options: CanvasGraphOptions): Graph {
  const { container, grid, connection } = options;
  const graph = new Graph({
    container,
    width: container.clientWidth,
    height: container.clientHeight,
    background: { color: '#fafafa' },
    grid: {
      size: grid.size,
      visible: grid.visible,
      args: { color: grid.color, thickness: grid.thickness },
    },
    ...(connection ? {
      connecting: {
        connector: { name: 'smooth' },
        connectionPoint: 'boundary',
        router: { name: 'normal' },
        allowBlank: false,
        allowMulti: connection.allowMulti ?? false,
        highlight: true,
        snap: { radius: 20 },
        createEdge() {
          return new Edge({ attrs: { line: connection.line } });
        },
        validateConnection({ sourceCell, targetCell }: any) {
          return !!(
            sourceCell
            && targetCell
            && sourceCell.id !== targetCell.id
          );
        },
      },
    } : {}),
    mousewheel: {
      enabled: true,
      modifiers: ['ctrl', 'meta'],
      minScale: 0.1,
      maxScale: 5,
    },
    panning: { enabled: true },
  } as any);

  graph.use(new History({ enabled: true }));
  graph.use(new Transform({ resizing: true, rotating: false }));
  graph.use(new Selection({ enabled: true, rubberband: true, showNodeSelectionBox: true }));
  graph.use(new Snapline({ enabled: true, sharp: true }));
  return graph;
}
