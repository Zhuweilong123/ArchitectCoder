/**
 * UML Editor Canvas – powered by AntV X6.
 * Uses proper X6 foreignObject pattern for HTML rendering.
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Graph, Edge, Node } from '@antv/x6';
import { History } from '@antv/x6-plugin-history';
import { Transform } from '@antv/x6-plugin-transform';
import { Selection } from '@antv/x6-plugin-selection';
import { Snapline } from '@antv/x6-plugin-snapline';
import { useDiagramStore } from '../../stores/diagramStore';
import { useUiStore } from '../../stores/uiStore';
import {
  type UmlClass,
  Stereotype, RelationType,
} from '../../types/uml';
import './UMLEditor.css';

// ── Register UML class shape ─────────────────────────
// Pattern follows X6's own text-block shape implementation

let shapeRegistered = false;
function ensureShapeRegistered() {
  if (shapeRegistered) return;
  shapeRegistered = true;

  Graph.registerNode('uml-class', {
    inherit: 'rect',
    markup: [
      {
        tagName: 'rect',
        selector: 'body',
      },
      {
        tagName: 'foreignObject',
        selector: 'fo',
        children: [
          {
            tagName: 'div',
            ns: 'http://www.w3.org/1999/xhtml',
            selector: 'content',
            style: {
              width: '100%',
              height: '100%',
              position: 'static',
              backgroundColor: 'transparent',
              margin: 0,
              padding: 0,
              boxSizing: 'border-box',
              display: 'flex',
              flexDirection: 'column',
              fontFamily: 'Consolas, Monaco, Menlo, monospace',
              fontSize: '12px',
              lineHeight: '1.5',
              overflow: 'hidden',
            },
          },
        ],
      },
    ],
    attrs: {
      body: {
        stroke: '#333333',
        strokeWidth: 2,
        fill: '#ffffff',
        rx: 6,
        ry: 6,
        magnet: true,
      },
      fo: {
        refWidth: '100%',
        refHeight: '100%',
      },
      content: {
        html: '',
      },
    },
    ports: {
      groups: {
        top: {
          position: { name: 'top' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: {
            circle: {
              r: 6,
              magnet: true,
              stroke: '#1890ff',
              strokeWidth: 2,
              fill: '#ffffff',
            },
          },
        },
        right: {
          position: { name: 'right' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: {
            circle: {
              r: 6,
              magnet: true,
              stroke: '#1890ff',
              strokeWidth: 2,
              fill: '#ffffff',
            },
          },
        },
        bottom: {
          position: { name: 'bottom' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: {
            circle: {
              r: 6,
              magnet: true,
              stroke: '#1890ff',
              strokeWidth: 2,
              fill: '#ffffff',
            },
          },
        },
        left: {
          position: { name: 'left' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: {
            circle: {
              r: 6,
              magnet: true,
              stroke: '#1890ff',
              strokeWidth: 2,
              fill: '#ffffff',
            },
          },
        },
      },
      items: [
        { id: 'pt', group: 'top' },
        { id: 'pr', group: 'right' },
        { id: 'pb', group: 'bottom' },
        { id: 'pl', group: 'left' },
      ],
    },
  });
}

// ── Helper: Generate HTML for a UML class ──────────────
function buildClassHTML(cls: UmlClass, selected: boolean): string {
  const stereotypeLabel = cls.stereotype !== Stereotype.CLASS
    ? `<div class="uml-stereotype">«${cls.stereotype}»</div>` : '';
  const isAbstract = cls.stereotype === Stereotype.ABSTRACT;
  const nameStyle = isAbstract ? 'font-style: italic; text-decoration: underline;' : '';
  const selClass = selected ? 'selected' : '';

  const attrLines = cls.attributes.map((a) => {
    const stat = a.is_static ? ' style="text-decoration: underline;"' : '';
    return `<div class="uml-attr"${stat}>${a.visibility} ${a.name}: ${a.type}</div>`;
  }).join('');

  const methodLines = cls.methods.map((m) => {
    const abs = m.is_abstract ? ' font-style: italic;' : '';
    const stat = m.is_static ? ' text-decoration: underline;' : '';
    return `<div class="uml-method" style="${abs}${stat}">${m.visibility} ${m.name}(${m.params}): ${m.return_type}</div>`;
  }).join('');

  const providedLines = (cls.provided_interfaces || []).map((i) =>
    `<span class="uml-iface provided">◉ ${i}</span>`
  ).join(' ');
  const requiredLines = (cls.required_interfaces || []).map((i) =>
    `<span class="uml-iface required">◡ ${i}</span>`
  ).join(' ');
  const ifaceHTML = (providedLines || requiredLines) ? `
    <div class="uml-class-ifaces">
      ${providedLines ? `<div class="uml-iface-row">${providedLines}</div>` : ''}
      ${requiredLines ? `<div class="uml-iface-row">${requiredLines}</div>` : ''}
    </div>
    <div class="uml-class-divider"></div>` : '';

  return `
    <div class="uml-class-node ${selClass}">
      <div class="uml-class-header" style="${nameStyle}">
        ${stereotypeLabel}
        <div class="uml-class-name">${cls.name}</div>
      </div>
      ${ifaceHTML}
      <div class="uml-class-attrs">${attrLines || '<div class="uml-empty">(no attributes)</div>'}</div>
      <div class="uml-class-divider"></div>
      <div class="uml-class-methods">${methodLines || '<div class="uml-empty">(no methods)</div>'}</div>
    </div>
  `;
}

// ── Component ──────────────────────────────────────────
const UMLEditor: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const isInternalUpdate = useRef(false);
  const clipboard = useRef<{ classes: any[]; relations: any[] }>({ classes: [], relations: [] });

  const {
    diagram, selectedClassId,
    moveClass, resizeClass, selectClass, selectRelation,
    addRelation, removeClass, removeRelation, addClass,
    undo, redo,
  } = useDiagramStore();

  const { setRightPanelTab } = useUiStore();

  // ── Initialize graph (once) ──────────────────────────
  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;

    ensureShapeRegistered();

    const graph = new Graph({
      container: containerRef.current,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      background: { color: '#fafafa' },
      grid: {
        size: diagram.grid_size || 20,
        visible: true,
        args: { color: diagram.grid_color || '#aaaaaa', thickness: diagram.grid_thickness || 1 },
      },
      connecting: {
        connector: { name: 'smooth' },
        connectionPoint: 'boundary',
        router: { name: 'normal' },
        allowBlank: false,
        highlight: true,
        snap: { radius: 20 },
        createEdge() {
          return new Edge({
            attrs: {
              line: {
                stroke: '#1890ff',
                strokeWidth: 2,
                targetMarker: { name: 'block', width: 12, height: 8 },
              },
            },
          });
        },
        validateConnection({ sourceCell, targetCell }) {
          if (!sourceCell || !targetCell) return false;
          if (sourceCell.id === targetCell.id) return false;
          return true;
        },
      },
      mousewheel: {
        enabled: true,
        modifiers: ['ctrl', 'meta'],
        minScale: 0.1,
        maxScale: 5,
      },
      panning: { enabled: true },
    });

    graph.use(new History({ enabled: true }));
    graph.use(new Transform({ resizing: true, rotating: false }));
    graph.use(new Selection({ enabled: true, rubberband: true, showNodeSelectionBox: true }));
    graph.use(new Snapline({ enabled: true, sharp: true }));

    // Write Ctrl+wheel zoom back to the store so the toolbar % and saved project stay in sync.
    graph.on('scale', ({ sx }) => {
      useDiagramStore.getState().setZoom(sx);
    });

    // ── Events ───────────────────────────────────────
    graph.on('node:click', ({ node }) => {
      selectClass(node.id);
      setRightPanelTab('properties');
    });

    graph.on('blank:click', () => {
      selectClass(null);
      selectRelation(null);
    });

    graph.on('node:moved', ({ node }) => {
      moveClass(node.id, { x: node.position().x, y: node.position().y });
    });

    graph.on('node:resized', ({ node }) => {
      resizeClass(node.id, {
        width: node.size().width,
        height: node.size().height,
      });
    });

    graph.on('edge:click', ({ edge }) => {
      selectRelation(edge.id);
      setRightPanelTab('properties');
    });

    graph.on('edge:connected', ({ edge, isNew }) => {
      if (isNew) {
        const src = edge.getSourceCellId();
        const tgt = edge.getTargetCellId();
        if (src && tgt) {
          isInternalUpdate.current = true;
          edge.remove();
          isInternalUpdate.current = false;
          addRelation(src, tgt);
        }
      }
    });

    graph.on('edge:mouseenter', ({ edge }) => {
      try {
        edge.addTools([
          { name: 'source-arrowhead' },
          { name: 'target-arrowhead' },
          { name: 'button-remove', args: { distance: -40 } },
        ]);
      } catch { /* ignore */ }
    });

    graph.on('edge:mouseleave', ({ edge }) => {
      try { edge.removeTools(); } catch { /* ignore */ }
    });

    graph.on('edge:removed', ({ edge }) => {
      if (!isInternalUpdate.current) {
        removeRelation(edge.id);
      }
    });

    // Keyboard shortcuts
    const handleKeyDown = (e: KeyboardEvent) => {
      const store = useDiagramStore.getState();
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (e.ctrlKey && e.key === 'c') {
        // Copy selected class
        if (store.selectedClassId) {
          const cls = store.diagram.classes.find((c) => c.id === store.selectedClassId);
          if (cls) {
            clipboard.current = { classes: [JSON.parse(JSON.stringify(cls))], relations: [] };
            console.log('[UMLEditor] Copied:', cls.name);
          }
        }
      } else if (e.ctrlKey && e.key === 'v') {
        // Paste copied classes at offset position with same size
        clipboard.current.classes.forEach((cls: any) => {
          const newId = `class_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
          store.addClass({ x: cls.position.x + 30, y: cls.position.y + 30 });
          // Apply copied size, attributes, methods
          const store2 = useDiagramStore.getState();
          const lastAdded = store2.diagram.classes[store2.diagram.classes.length - 1];
          if (lastAdded) {
            store2.updateClass(lastAdded.id, {
              name: cls.name,
              size: { ...cls.size },
              attributes: [...cls.attributes],
              methods: [...cls.methods],
              stereotype: cls.stereotype,
              note: cls.note,
            });
          }
        });
        clipboard.current = { classes: clipboard.current.classes.map((c: any) => ({
          ...c, position: { x: c.position.x + 30, y: c.position.y + 30 }
        })), relations: [] };
      } else if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        store.undo();
      } else if (e.ctrlKey && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault();
        store.redo();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        const cells = graph.getSelectedCells();
        if (cells.length > 0) {
          e.preventDefault();
          isInternalUpdate.current = true;
          cells.forEach((cell) => {
            if (cell.isNode()) store.removeClass(cell.id);
            else if (cell.isEdge()) store.removeRelation(cell.id);
            cell.remove();
          });
          isInternalUpdate.current = false;
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    graph.centerContent();
    graphRef.current = graph;
    console.log('[UML Editor] Initialized. Shape registered:', shapeRegistered);

    return () => {
      _didFirstSync.current = false;
      document.removeEventListener('keydown', handleKeyDown);
      try { graph.dispose(); } catch { /* ignore */ }
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync diagram → graph ─────────────────────────────
  const prevClassIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const _didFirstSync = useRef(false);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;

    try {
      isInternalUpdate.current = true;
      const currentIds = new Set(diagram.classes.map((c) => c.id));

      // Remove deleted nodes
      prevClassIds.current.forEach((id) => {
        if (!currentIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
          htmlCache.current.delete(id);
        }
      });

      // Add or update nodes
      diagram.classes.forEach((cls) => {
        const isSelected = cls.id === selectedClassId;
        const htmlContent = buildClassHTML(cls, isSelected);
        const cached = htmlCache.current.get(cls.id);

        try {
          const existing = graph.getCellById(cls.id);
          if (existing && existing.isNode()) {
            // Update existing node
            const node = existing as Node;
            node.setPosition(cls.position.x, cls.position.y);
            node.setSize(cls.size);
            if (cached !== htmlContent) {
              // X6 attr: set the 'html' attr on the 'content' selector
              node.setAttrByPath('content/html', htmlContent);
              htmlCache.current.set(cls.id, htmlContent);
            }
          } else {
            // Add new node
            const node = graph.addNode({
              id: cls.id,
              shape: 'uml-class',
              x: cls.position.x,
              y: cls.position.y,
              width: cls.size.width || 200,
              height: cls.size.height || 150,
              attrs: {
                content: { html: htmlContent },
              },
            });
            if (node) {
              htmlCache.current.set(cls.id, htmlContent);
            }
          }
        } catch (e) {
          console.warn('[UML Editor] Sync node error:', cls.name, e);
        }
      });

      // Sync edges
      const existingEdgeIds = new Set(graph.getEdges().map((e) => e.id));
      const diagramEdgeIds = new Set(diagram.relations.map((r) => r.id));

      existingEdgeIds.forEach((id) => {
        if (!diagramEdgeIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
        }
      });

      diagram.relations.forEach((rel) => {
        const labelText = [
          rel.type,
          rel.multiplicity_source ? `[${rel.multiplicity_source}]` : '',
          rel.multiplicity_target ? `→[${rel.multiplicity_target}]` : '',
          rel.role_name,
        ].filter(Boolean).join(' ');

        const isDashed = rel.type === RelationType.REALIZATION
          || rel.type === RelationType.DEPENDENCY;
        const arrowStyle = rel.type === RelationType.INHERITANCE
          || rel.type === RelationType.REALIZATION
          ? 'block' : 'classic';

        const lineAttrs = {
          stroke: '#555555',
          strokeWidth: 2,
          strokeDasharray: isDashed ? '5,5' : '',
          targetMarker: { name: arrowStyle, width: 12, height: 8 },
        };

        try {
          if (existingEdgeIds.has(rel.id)) {
            // Update existing edge
            const edge = graph.getCellById(rel.id) as Edge;
            if (edge) {
              edge.setLabels(labelText ? [labelText] : []);
              edge.setAttrByPath('line/strokeDasharray', isDashed ? '5,5' : '');
              edge.setAttrByPath('line/targetMarker/name', arrowStyle);
            }
          } else {
            // Add new edge — skip if source or target class doesn't exist on canvas
            if (!graph.getCellById(rel.source)) {
              console.warn('[UML Editor] Sync edge skipped — source node missing:', rel.id, rel.source);
              return;
            }
            if (!graph.getCellById(rel.target)) {
              console.warn('[UML Editor] Sync edge skipped — target node missing:', rel.id, rel.target);
              return;
            }
            graph.addEdge({
              id: rel.id,
              source: { cell: rel.source },
              target: { cell: rel.target },
              labels: labelText ? [labelText] : undefined,
              attrs: { line: lineAttrs },
            });
          }
        } catch (e) {
          console.warn('[UML Editor] Sync edge error:', rel.id, e);
        }
      });

      prevClassIds.current = currentIds;
      isInternalUpdate.current = false;

      if (!_didFirstSync.current && graph.getNodes().length > 0) {
        _didFirstSync.current = true;
        console.log('[UMLEditor] First sync with elements, scheduling centerContent. Nodes:', graph.getNodes().length);
        setTimeout(() => {
          const g = graphRef.current;
          if (!g) return;
          const bbox = g.getAllCellsBBox?.() || g.getContentBBox?.() || { x: 0, y: 0, width: 0, height: 0 };
          const sidebarW = useUiStore.getState().rightPanelWidth;
          g.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
          // Shift left to account for sidebar, but only if content fits visible area
          const visibleW = g.options.width - sidebarW;
          if (bbox.width < visibleW - 40) {
            g.translate(g.translate().tx - sidebarW / 2, g.translate().ty);
          }
        }, 200);
      }
    } catch (err) {
      console.error('[UML Editor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram, selectedClassId]);

  // ── Apply store zoom to the graph (toolbar zoom buttons) ──
  // Epsilon guard breaks the zoomTo → scale event → setZoom → effect loop.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    if (Math.abs(graph.zoom() - diagram.zoom) > 0.001) {
      graph.zoomTo(diagram.zoom);
    }
  }, [diagram.zoom]);

  // ── Sync grid settings ───────────────────────────────
  useEffect(() => {
    const graph = graphRef.current as any;
    if (!graph) return;
    try {
      if (diagram.grid_visible) {
        graph.showGrid();
        graph.setGridSize(diagram.grid_size);
        // Redraw with color/thickness (omit type to use default dot preset)
        graph.drawGrid({
          size: diagram.grid_size,
          args: {
            color: diagram.grid_color || '#aaaaaa',
            thickness: diagram.grid_thickness || 1,
          },
        });
      } else {
        graph.hideGrid();
      }
    } catch (e) {
      console.warn('[UML Editor] Grid sync error:', e);
    }
  }, [diagram.grid_visible, diagram.grid_size, diagram.grid_color, diagram.grid_thickness]);

  // ── Auto-center on recenter trigger ───────────────
  const recenterCounter = useDiagramStore((s) => s.recenterCounter);
  useEffect(() => {
    if (recenterCounter <= 0) return;
    setTimeout(() => {
      const g = graphRef.current;
      if (!g) return;
      const bbox = g.getAllCellsBBox?.() || g.getContentBBox?.() || { x: 0, y: 0, width: 0, height: 0 };
      const sidebarW = useUiStore.getState().rightPanelWidth;
      g.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
      const visibleW = g.options.width - sidebarW;
      if (bbox.width < visibleW - 40) {
        g.translate(g.translate().tx - sidebarW / 2, g.translate().ty);
      }
    }, 100);
  }, [recenterCounter]);

  // ── Helpers ──────────────────────────────────────────
  const handleAddClass = useCallback(() => {
    const x = 150 + Math.random() * 400;
    const y = 100 + Math.random() * 300;
    useDiagramStore.getState().addClass({ x, y });
  }, []);

  const [showToolbar, setShowToolbar] = useState(true);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {showToolbar && (
        <div style={{
          position: 'absolute', top: 8, left: 8, zIndex: 100,
          background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
          padding: '4px 6px', boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
        }}>
          <Button size="small" icon={<PlusOutlined />} onClick={handleAddClass}>类</Button>
          <Button size="small" type="text" onClick={() => setShowToolbar(false)}
            style={{ fontSize: 10, marginLeft: 4 }}>✕</Button>
        </div>
      )}
      {!showToolbar && (
        <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 100 }}>
          <Button size="small" type="dashed" onClick={() => setShowToolbar(true)}>🔧</Button>
        </div>
      )}
      <div ref={containerRef} className="uml-canvas-container" />
    </div>
  );
};

export default UMLEditor;
