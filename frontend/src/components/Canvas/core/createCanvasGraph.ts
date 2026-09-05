import { Graph, Edge } from '@antv/x6';
import { History } from '@antv/x6-plugin-history';
import { Transform } from '@antv/x6-plugin-transform';
import { Selection } from '@antv/x6-plugin-selection';
import { Snapline } from '@antv/x6-plugin-snapline';
import { Export } from '@antv/x6-plugin-export';

export type CanvasTheme = 'light' | 'dark' | 'blueprint';

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
    router?: Record<string, unknown>;
    connector?: Record<string, unknown>;
  };
}

const canvasThemeVisuals: Record<CanvasTheme, { background: string; grid: string }> = {
  light: { background: '#fafafa', grid: '#e0e0e0' },
  dark: { background: '#111827', grid: '#334155' },
  blueprint: { background: '#eaf5ff', grid: '#bae6fd' },
};

/** Keep X6's generated background/grid in sync with the HTML node theme. */
export function applyCanvasThemeToGraph(graph: Graph, theme: CanvasTheme): void {
  const visuals = canvasThemeVisuals[theme];
  graph.drawBackground({ color: visuals.background });
  // `drawGrid` replaces the grid definition, while `update` refreshes the
  // existing pattern and preserves the diagram's configured size/visibility.
  graph.grid.update({ color: visuals.grid, thickness: 1 });
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
        connector: connection.connector ?? { name: 'smooth' },
        connectionPoint: 'boundary',
        router: connection.router ?? { name: 'normal' },
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
  graph.use(new Selection({
    enabled: true,
    rubberband: true,
    multipleSelectionModifiers: 'shift',
    showNodeSelectionBox: true,
  }));
  graph.use(new Snapline({ enabled: true, sharp: true }));
  graph.use(new Export());
  return graph;
}
