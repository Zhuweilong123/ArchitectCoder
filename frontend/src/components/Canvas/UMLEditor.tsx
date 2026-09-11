/**
 * UML Editor Canvas – powered by AntV X6.
 * Uses proper X6 foreignObject pattern for HTML rendering.
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Graph, Edge, Node } from '@antv/x6';
import { useShallow } from 'zustand/react/shallow';
import { getActiveDiagram, selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useUiStore, type CanvasTheme } from '../../stores/uiStore';
import { getCanvasLabels } from './canvasLabels';
import { useCanvasGraphViewport } from './core/useCanvasGraphViewport';
import { applyCanvasThemeToGraph, createCanvasGraph } from './core/createCanvasGraph';
import { registerCanvasGraph, unregisterCanvasGraph } from './core/canvasRegistry';
import { attachCanvasEventAdapter } from './core/canvasEventAdapter';
import { snapCanvasPosition } from './core/snapToGrid';
import {
  centerCanvasContent, getParallelEdgeVertices, materializeEdgeRouteVertices,
  resolveEdgeSelection, syncCanvasGrid,
} from './core/canvasCommon';
import { getClassNodeSize, resolveClassLayouts } from './umlClassLayout';
import {
  type UmlClass,
  Stereotype, RelationType,
} from '../../types/uml';
import './UMLEditor.css';
import { escapeHtml } from '../../utils/safeHtml';

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
function buildClassHTML(cls: UmlClass, selected: boolean, theme: CanvasTheme, language: Parameters<typeof getCanvasLabels>[0]): string {
  const labels = getCanvasLabels(language).classDiagram;
  const visibilityClass = (visibility: string) => ({
    '+': 'public',
    '-': 'private',
    '#': 'protected',
  }[visibility] || 'package');
  const stereotypeLabel = cls.stereotype !== Stereotype.CLASS
    ? `<div class="uml-stereotype">«${escapeHtml(cls.stereotype)}»</div>` : '';
  const isAbstract = cls.stereotype === Stereotype.ABSTRACT;
  const nameStyle = isAbstract ? 'font-style: italic; text-decoration: underline;' : '';
  const selClass = [
    selected ? 'selected' : '',
    `stereotype-${escapeHtml(cls.stereotype || Stereotype.CLASS)}`,
  ].filter(Boolean).join(' ');

  const attrLines = cls.attributes.map((a) => {
    const staticClass = a.is_static ? ' static' : '';
    const defaultValue = a.default_value ? ` = ${escapeHtml(a.default_value)}` : '';
    return `<div class="uml-member uml-attr${staticClass}">
      <span class="uml-visibility visibility-${visibilityClass(a.visibility)}">${escapeHtml(a.visibility)}</span>
      <span class="uml-member-name">${escapeHtml(a.name)}</span><span class="uml-member-type">: ${escapeHtml(a.type)}${defaultValue}</span>
    </div>`;
  }).join('');

  const methodLines = cls.methods.map((m) => {
    const modifiers = [m.is_static ? 'static' : '', m.is_abstract ? 'abstract' : '']
      .filter(Boolean).join(' ');
    return `<div class="uml-member uml-method ${modifiers}">
      <span class="uml-visibility visibility-${visibilityClass(m.visibility)}">${escapeHtml(m.visibility)}</span>
      <span class="uml-member-name">${escapeHtml(m.name)}</span><span class="uml-member-type">(${escapeHtml(m.params)}): ${escapeHtml(m.return_type)}</span>
    </div>`;
  }).join('');

  const providedLines = (cls.provided_interfaces || []).map((i) =>
    `<span class="uml-iface provided">◉ ${escapeHtml(i)}</span>`
  ).join(' ');
  const requiredLines = (cls.required_interfaces || []).map((i) =>
    `<span class="uml-iface required">◡ ${escapeHtml(i)}</span>`
  ).join(' ');
  const ifaceHTML = (providedLines || requiredLines) ? `
    <div class="uml-class-ifaces">
      ${providedLines ? `<div class="uml-iface-row">${providedLines}</div>` : ''}
      ${requiredLines ? `<div class="uml-iface-row">${requiredLines}</div>` : ''}
    </div>
    <div class="uml-class-divider"></div>` : '';

  return `
    <div class="uml-class-node theme-${theme} ${selClass}">
      <div class="uml-class-header" style="${nameStyle}">
        ${stereotypeLabel}
        <div class="uml-class-name">${escapeHtml(cls.name)}</div>
      </div>
      ${ifaceHTML}
      <div class="uml-class-attrs">
        <div class="uml-section-label">${labels.attributes}</div>
        ${attrLines || '<div class="uml-empty">—</div>'}
      </div>
      <div class="uml-class-divider"></div>
      <div class="uml-class-methods">
        <div class="uml-section-label">${labels.operations}</div>
        ${methodLines || '<div class="uml-empty">—</div>'}
      </div>
      ${cls.note ? `<div class="uml-class-note">${escapeHtml(cls.note)}</div>` : ''}
    </div>
  `;
}

const UMLEditor: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const isInternalUpdate = useRef(false);
  const edgeSelectionCycle = useRef({
    point: null as { x: number; y: number } | null,
    ids: [] as string[],
    index: 0,
    timestamp: 0,
  });
  const clipboard = useRef<{ classes: any[]; relations: any[] }>({ classes: [], relations: [] });

  const {
    diagram, selectedClassId, selectedClassIds, selectedRelationId,
    moveClass, resizeClass, selectClass, selectRelation,
    addRelation, updateRelation, removeClass, removeRelation, addClass,
    undo, redo, selectClasses, alignClasses, distributeClasses,
    autoLayoutClasses,
  } = useDiagramStore(useShallow((s) => ({
    diagram: selectActiveDiagram(s),
    selectedClassId: s.selectedClassId,
    selectedClassIds: s.selectedClassIds,
    selectedRelationId: s.selectedRelationId,
    moveClass: s.moveClass,
    resizeClass: s.resizeClass,
    selectClass: s.selectClass,
    selectRelation: s.selectRelation,
    addRelation: s.addRelation,
    updateRelation: s.updateRelation,
    removeClass: s.removeClass,
    removeRelation: s.removeRelation,
    addClass: s.addClass,
    undo: s.undo,
    redo: s.redo,
    selectClasses: s.selectClasses,
    alignClasses: s.alignClasses,
    distributeClasses: s.distributeClasses,
    autoLayoutClasses: s.autoLayoutClasses,
  })));
  const viewport = useDiagramStore((s) => s.viewport);

  const setRightPanelTab = useUiStore((s) => s.setRightPanelTab);
  const canvasTheme = useUiStore((s) => s.canvasTheme);
  const interfaceLanguage = useUiStore((s) => s.interfaceLanguage);

  // ── Initialize graph (once) ──────────────────────────
  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;

    ensureShapeRegistered();

    const graph = createCanvasGraph({
      container: containerRef.current,
      grid: {
        size: diagram.grid_size || 20,
        visible: true,
        color: diagram.grid_color || '#aaaaaa',
        thickness: diagram.grid_thickness || 1,
      },
      connection: {
        line: {
          stroke: '#1890ff',
          strokeWidth: 2,
          targetMarker: { name: 'block', width: 12, height: 8 },
        },
        router: { name: 'manhattan', args: { padding: 24, step: 20 } },
        connector: { name: 'rounded' },
      },
    });

    // ── Events ───────────────────────────────────────
    const detachCanvasEvents = attachCanvasEventAdapter({
      graph,
      isInternalUpdate,
      onNodeClick: (node) => {
        selectClass(node.id);
        setRightPanelTab('properties');
      },
      onSelectionChanged: (cells) => {
        const classIds = cells
          .filter((cell) => cell.isNode() && cell.shape === 'uml-class')
          .map((cell) => cell.id);
        selectClasses(classIds);
      },
      onBlankClick: () => {
        selectClass(null);
        selectRelation(null);
      },
      onNodeMoved: (node) => {
        const position = node.position();
        const store = useDiagramStore.getState();
        const nextPosition = snapCanvasPosition(
          { x: position.x, y: position.y },
          getActiveDiagram().snap_to_grid,
          getActiveDiagram().grid_size,
        );
        if (position.x !== nextPosition.x || position.y !== nextPosition.y) {
          isInternalUpdate.current = true;
          node.setPosition(nextPosition.x, nextPosition.y);
          isInternalUpdate.current = false;
        }
        graph.getConnectedEdges(node).forEach((edge) => {
          const relation = (getActiveDiagram().relations || []).find((item) => item.id === edge.id);
          if (relation?.vertices === undefined) edge.setVertices([]);
        });
        moveClass(node.id, nextPosition);
      },
      onNodeResized: (node) => {
        resizeClass(node.id, {
          width: node.size().width,
          height: node.size().height,
        });
      },
      onEdgeClick: (edge, point) => {
        const selectedEdge = point
          ? resolveEdgeSelection(graph, edge, point, edgeSelectionCycle.current)
          : edge;
        selectRelation(selectedEdge.id);
        setRightPanelTab('properties');
      },
      onEdgeMouseEnter: (edge) => {
        const relation = (getActiveDiagram().relations || []).find((item) => item.id === edge.id);
        if (!relation || relation.vertices !== undefined) return;
        isInternalUpdate.current = true;
        materializeEdgeRouteVertices(graph, edge);
        isInternalUpdate.current = false;
      },
      onEdgeEndpointChanged: (edge) => {
        const relation = (getActiveDiagram().relations || []).find((item) => item.id === edge.id);
        if (!relation) return;
        const source = edge.getSourceCellId();
        const target = edge.getTargetCellId();
        if (!source || !target || source === target) return;
        if (relation.vertices === undefined) edge.setVertices([]);
        updateRelation(edge.id, { source, target });
      },
      onEdgeVerticesChanged: (edge) => {
        const relation = (getActiveDiagram().relations || []).find((item) => item.id === edge.id);
        if (!relation) return;
        const vertices = edge.getVertices().map(({ x, y }) => ({ x, y }));
        if (JSON.stringify(relation.vertices) === JSON.stringify(vertices)) return;
        updateRelation(edge.id, { vertices });
      },
      onNewEdge: (edge, sourceId, targetId) => {
        isInternalUpdate.current = true;
        edge.remove();
        isInternalUpdate.current = false;
        addRelation(sourceId, targetId);
      },
      onEdgeRemoved: (edge) => removeRelation(edge.id),
      edgeTools: [
        // Segment handles let users move a 90-degree turn without changing
        // the connected ports. Vertex handles provide fine-grained cleanup.
        { name: 'segments', args: { threshold: 20, snapRadius: 12 } },
        { name: 'vertices', args: { addable: false, removable: true, snapRadius: 12 } },
        { name: 'source-arrowhead' },
        { name: 'target-arrowhead' },
        { name: 'button-remove', args: { distance: -40 } },
      ],
    });

    // Keyboard shortcuts
    const handleKeyDown = (e: KeyboardEvent) => {
      const store = useDiagramStore.getState();
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const key = e.key.toLowerCase();
      const modifier = e.ctrlKey || e.metaKey;

      if (modifier && key === 'c') {
        // Copy all selected classes so rubber-band and shift selection behave consistently.
        const graphSelectedIds = graph.getSelectedCells()
          .filter((cell) => cell.isNode() && cell.shape === 'uml-class')
          .map((cell) => cell.id);
        const selectedIds = graphSelectedIds.length > 0
          ? graphSelectedIds
          : (store.selectedClassIds.length > 0
            ? store.selectedClassIds
            : store.selectedClassId ? [store.selectedClassId] : []);
        const selectedIdSet = new Set(selectedIds);
        const classes = getActiveDiagram().classes
          .filter((cls) => selectedIdSet.has(cls.id))
          .map((cls) => JSON.parse(JSON.stringify(cls)));
        if (classes.length > 0) {
          clipboard.current = { classes, relations: [] };
          console.log('[UMLEditor] Copied classes:', classes.length);
        }
      } else if (modifier && key === 'v') {
        e.preventDefault();
        if (clipboard.current.classes.length === 0) return;
        // Paste copied classes at offset position with same size
        store.beginBatch();
        try {
          clipboard.current.classes.forEach((cls: any) => {
            store.addClass({ x: cls.position.x + 30, y: cls.position.y + 30 });
            // Apply copied size, attributes, methods
            const store2 = useDiagramStore.getState();
            const activeDiagram = getActiveDiagram();
            const lastAdded = activeDiagram.classes[activeDiagram.classes.length - 1];
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
        } finally {
          store.endBatch();
        }
        clipboard.current = { classes: clipboard.current.classes.map((c: any) => ({
          ...c, position: { x: c.position.x + 30, y: c.position.y + 30 }
        })), relations: [] };
      } else if (modifier && key === 'z' && !e.shiftKey) {
        e.preventDefault();
        store.undo();
      } else if (modifier && (key === 'y' || (key === 'z' && e.shiftKey))) {
        e.preventDefault();
        store.redo();
      } else if (key === 'escape') {
        e.preventDefault();
        graph.cleanSelection();
        selectClass(null);
        selectRelation(null);
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        const cells = graph.getSelectedCells();
        const selectedNodeIds = cells.filter((cell) => cell.isNode()).map((cell) => cell.id);
        const selectedEdgeIds = cells.filter((cell) => cell.isEdge()).map((cell) => cell.id);
        const nodeIds = selectedNodeIds.length > 0 ? selectedNodeIds : store.selectedClassIds;
        const edgeIds = selectedEdgeIds.length > 0
          ? selectedEdgeIds
          : store.selectedRelationId ? [store.selectedRelationId] : [];
        if (nodeIds.length > 0 || edgeIds.length > 0) {
          e.preventDefault();
          isInternalUpdate.current = true;
          nodeIds.forEach((id) => store.removeClass(id));
          edgeIds.forEach((id) => store.removeRelation(id));
          graph.cleanSelection();
          isInternalUpdate.current = false;
          selectClass(null);
          selectRelation(null);
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    if (!(viewport.panX || viewport.panY) && viewport.zoom === 1) {
      graph.centerContent();
    }
    graphRef.current = graph;
    registerCanvasGraph(graph);
    console.log('[UML Editor] Initialized. Shape registered:', shapeRegistered);

    return () => {
      _didFirstSync.current = false;
      document.removeEventListener('keydown', handleKeyDown);
      detachCanvasEvents();
      unregisterCanvasGraph(graph);
      try { graph.dispose(); } catch { /* ignore */ }
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useCanvasGraphViewport(graphRef, containerRef, viewport);

  // ── Sync diagram → graph ─────────────────────────────
  const prevClassIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const renderCache = useRef<Map<string, {
    entity: UmlClass;
    selected: boolean;
    theme: CanvasTheme;
    html: string;
  }>>(new Map());
  const nodeSignatureCache = useRef<Map<string, string>>(new Map());
  const edgeSignatureCache = useRef<Map<string, string>>(new Map());
  const _didFirstSync = useRef(false);
  const renderedTheme = useRef<CanvasTheme | null>(null);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    applyCanvasThemeToGraph(graph, canvasTheme);

    try {
      isInternalUpdate.current = true;
      const currentIds = new Set(diagram.classes.map((c) => c.id));
      const classLayouts = resolveClassLayouts(diagram.classes);
      const themeChanged = renderedTheme.current !== canvasTheme;

      // Remove deleted nodes
      prevClassIds.current.forEach((id) => {
        if (!currentIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
          htmlCache.current.delete(id);
          renderCache.current.delete(id);
          nodeSignatureCache.current.delete(id);
        }
      });

      // Add or update nodes
      diagram.classes.forEach((cls) => {
        const isSelected = cls.id === selectedClassId;
        // Theme is part of the rendered HTML. Always rebuild this small HTML
        // fragment so a theme change can never reuse a stale node fragment.
        const htmlContent = buildClassHTML(cls, isSelected, canvasTheme, interfaceLanguage);
        const cached = htmlCache.current.get(cls.id);
        const layout = classLayouts.get(cls.id) || {
          x: cls.position.x,
          y: cls.position.y,
          ...getClassNodeSize(cls),
        };
        const { width, height } = layout;
        const signature = JSON.stringify([
          htmlContent, layout.x, layout.y, width, height, canvasTheme,
        ]);
        renderCache.current.set(cls.id, {
          entity: cls,
          selected: isSelected,
          theme: canvasTheme,
          html: htmlContent,
        });

        try {
          const existing = graph.getCellById(cls.id);
          if (existing && existing.isNode()) {
            // A theme change must refresh every node, even if an older cache
            // entry accidentally reports the same layout signature.
            if (!themeChanged && nodeSignatureCache.current.get(cls.id) === signature) return;
            // Update existing node
            const node = existing as Node;
            node.setPosition(layout.x, layout.y);
            node.setSize({ width, height });
            node.setAttrs({
              body: {
                stroke: isSelected ? '#2563eb' : '#cbd5e1',
                strokeWidth: isSelected ? 2.5 : 1.5,
              },
            });
            if (themeChanged || cached !== htmlContent) {
              // X6 attr: set the 'html' attr on the 'content' selector
              node.setAttrByPath('content/html', htmlContent);
              htmlCache.current.set(cls.id, htmlContent);
            }
            nodeSignatureCache.current.set(cls.id, signature);
          } else {
            // Add new node
            const node = graph.addNode({
              id: cls.id,
              shape: 'uml-class',
              x: layout.x,
              y: layout.y,
              width,
              height,
              attrs: {
                content: { html: htmlContent },
                body: {
                  stroke: isSelected ? '#2563eb' : '#cbd5e1',
                  strokeWidth: isSelected ? 2.5 : 1.5,
                },
              },
            });
            if (node) {
              htmlCache.current.set(cls.id, htmlContent);
              nodeSignatureCache.current.set(cls.id, signature);
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
          edgeSignatureCache.current.delete(id);
        }
      });

      const classRects = diagram.classes.map((cls) => ({
        id: cls.id,
        ...(classLayouts.get(cls.id) || {
          x: cls.position.x,
          y: cls.position.y,
          ...getClassNodeSize(cls),
        }),
      }));
      diagram.relations.forEach((rel) => {
        const isSelected = rel.id === selectedRelationId;
        const isComposition = rel.type === RelationType.COMPOSITION;
        const isAggregation = rel.type === RelationType.AGGREGATION;
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
          stroke: isSelected
            ? (canvasTheme === 'dark' ? '#93c5fd' : canvasTheme === 'eye-care' ? '#6e9677' : '#2563eb')
            : (canvasTheme === 'dark' ? '#94a3b8' : canvasTheme === 'eye-care' ? '#52675a' : '#64748b'),
          strokeWidth: isSelected ? 2.5 : 1.5,
          strokeDasharray: isDashed ? '5,5' : '',
          sourceMarker: isComposition || isAggregation
            ? {
                name: 'diamond',
              width: 16,
              height: 12,
                fill: isComposition
                  ? (canvasTheme === 'dark' ? '#94a3b8' : canvasTheme === 'eye-care' ? '#8ea594' : '#64748b')
                  : canvasTheme === 'eye-care' ? '#f8f7ee' : '#ffffff',
              }
            : undefined,
          targetMarker: {
            name: arrowStyle,
            width: 12,
            height: 8,
            fill: rel.type === RelationType.INHERITANCE || rel.type === RelationType.REALIZATION
              ? canvasTheme === 'eye-care' ? '#f8f7ee' : '#ffffff'
              : isSelected
                ? (canvasTheme === 'dark' ? '#93c5fd' : canvasTheme === 'eye-care' ? '#6e9677' : '#2563eb')
                : (canvasTheme === 'dark' ? '#94a3b8' : canvasTheme === 'eye-care' ? '#52675a' : '#64748b'),
          },
        };
        const labelColor = canvasTheme === 'dark'
          ? '#f8fafc'
          : canvasTheme === 'eye-care' ? (isSelected ? '#547a5d' : '#3f5145')
            : isSelected ? '#1d4ed8' : '#475569';
        const labelBackground = canvasTheme === 'dark'
          ? '#111827' : canvasTheme === 'eye-care' ? '#f8f7ee' : '#ffffff';
        const labelBorder = canvasTheme === 'dark'
          ? (isSelected ? '#60a5fa' : '#475569')
          : canvasTheme === 'eye-care' ? (isSelected ? '#6e9677' : '#cbd7c9')
            : isSelected ? '#93c5fd' : '#cbd5e1';
        const edgeLabels = labelText ? [{
          attrs: {
            text: {
              text: labelText,
              fontSize: 10,
              fontWeight: 600,
              fill: labelColor,
            },
            rect: {
              fill: labelBackground,
              stroke: labelBorder,
              strokeWidth: 1,
              rx: 4,
              ry: 4,
            },
          },
          position: { distance: 0.5, offset: -10 },
        }] : [];
        // An explicit (including empty) vertices array is a user override.
        // Only untouched relations fall back to the automatic parallel-edge lane.
        const existingVertices = (graph.getCellById(rel.id) as Edge | null)?.getVertices() || [];
        const vertices = rel.vertices !== undefined
          ? rel.vertices
          : existingVertices.length > 0
            ? existingVertices
            : getParallelEdgeVertices(rel, diagram.relations, classRects);
        const interactionAttrs = {
          stroke: 'transparent',
          strokeWidth: 10,
          fill: 'none',
          pointerEvents: 'stroke',
        };
        const signature = JSON.stringify([
          rel.source, rel.target, labelText, isDashed, arrowStyle,
          isSelected, isComposition, isAggregation, vertices, canvasTheme,
        ]);

        try {
          if (existingEdgeIds.has(rel.id)) {
            if (edgeSignatureCache.current.get(rel.id) === signature) return;
            // Update existing edge
            const edge = graph.getCellById(rel.id) as Edge;
            if (edge) {
              edge.setSource({ cell: rel.source });
              edge.setTarget({ cell: rel.target });
              edge.setLabels(edgeLabels);
              edge.setVertices(vertices);
              edge.setRouter({ name: 'manhattan', args: { padding: 24, step: 20 } });
              edge.setConnector({ name: 'rounded' });
              edge.setAttrByPath('line/stroke', lineAttrs.stroke);
              edge.setAttrByPath('line/strokeWidth', lineAttrs.strokeWidth);
              edge.setAttrByPath('line/strokeDasharray', isDashed ? '5,5' : '');
              // X6 has no built-in marker named "none".  Writing that name
              // leaves an invalid marker definition on ordinary associations
              // and crashes the edge-tool render on the next click/hover.
              if (lineAttrs.sourceMarker) {
                edge.setAttrByPath('line/sourceMarker', lineAttrs.sourceMarker);
              } else {
                edge.removeAttrByPath('line/sourceMarker');
              }
              edge.setAttrByPath('line/targetMarker/name', arrowStyle);
              edge.setAttrByPath('line/targetMarker/fill', lineAttrs.targetMarker.fill);
              edge.setAttrByPath('wrap/stroke', interactionAttrs.stroke);
              edge.setAttrByPath('wrap/strokeWidth', interactionAttrs.strokeWidth);
              edge.setAttrByPath('wrap/pointerEvents', interactionAttrs.pointerEvents);
              edgeSignatureCache.current.set(rel.id, signature);
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
            const edge = graph.addEdge({
              id: rel.id,
              source: { cell: rel.source },
              target: { cell: rel.target },
              labels: edgeLabels,
              vertices,
              router: { name: 'manhattan', args: { padding: 24, step: 20 } },
              connector: { name: 'rounded' },
              attrs: { line: lineAttrs, wrap: interactionAttrs },
            });
            if (edge) edgeSignatureCache.current.set(rel.id, signature);
          }
        } catch (e) {
          console.warn('[UML Editor] Sync edge error:', rel.id, e);
        }
      });

      renderedTheme.current = canvasTheme;
      prevClassIds.current = currentIds;
      isInternalUpdate.current = false;

      if (
        !_didFirstSync.current
        && graph.getNodes().length > 0
        && !(viewport.panX || viewport.panY)
        && viewport.zoom === 1
      ) {
        _didFirstSync.current = true;
        console.log('[UMLEditor] First sync with elements, scheduling centerContent. Nodes:', graph.getNodes().length);
        setTimeout(() => {
          const g = graphRef.current;
          if (!g) return;
          centerCanvasContent(g, useUiStore.getState().rightPanelWidth);
        }, 200);
      }
    } catch (err) {
      console.error('[UML Editor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram.classes, diagram.relations, selectedClassId, selectedRelationId, canvasTheme, interfaceLanguage]);

  // ── Apply store zoom to the graph (toolbar zoom buttons) ──
  // Epsilon guard breaks the zoomTo → scale event → setZoom → effect loop.
  // ── Sync grid settings ───────────────────────────────
  useEffect(() => {
    const graph = graphRef.current as any;
    if (!graph) return;
    syncCanvasGrid(graph, {
      visible: diagram.grid_visible,
      size: diagram.grid_size,
      color: diagram.grid_color || '#aaaaaa',
      thickness: diagram.grid_thickness || 1,
    });
  }, [diagram.grid_visible, diagram.grid_size, diagram.grid_color, diagram.grid_thickness]);

  // ── Auto-center on recenter trigger ───────────────
  const recenterCounter = useDiagramStore((s) => s.recenterCounter);
  useEffect(() => {
    if (recenterCounter <= 0) return;
    setTimeout(() => {
      const g = graphRef.current;
      if (!g) return;
      centerCanvasContent(g, useUiStore.getState().rightPanelWidth);
    }, 100);
  }, [recenterCounter]);

  // ── Helpers ──────────────────────────────────────────
  const handleAddClass = useCallback(() => {
    const x = 150 + Math.random() * 400;
    const y = 100 + Math.random() * 300;
    useDiagramStore.getState().addClass({ x, y });
  }, []);

  const [showToolbar, setShowToolbar] = useState(true);
  const labels = getCanvasLabels(interfaceLanguage).classDiagram;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {selectedClassIds.length >= 2 && (
        <div style={{
          position: 'absolute', top: showToolbar ? 44 : 8, left: 8, zIndex: 100,
          display: 'flex', alignItems: 'center', gap: 3,
          background: 'var(--canvas-toolbar-bg, #ffffff)',
          border: '1px solid var(--canvas-toolbar-border, #d9e1ec)',
          borderRadius: 6, padding: '4px 6px',
          boxShadow: '0 2px 8px rgba(15,23,42,0.12)',
        }}>
          <span style={{ fontSize: 11, color: '#64748b', marginRight: 3 }}>
            {labels.selected(selectedClassIds.length)}
          </span>
          <Button size="small" type="text" title={labels.alignLeft} onClick={() => alignClasses('left')}>L</Button>
          <Button size="small" type="text" title={labels.alignCenter} onClick={() => alignClasses('center')}>C</Button>
          <Button size="small" type="text" title={labels.alignRight} onClick={() => alignClasses('right')}>R</Button>
          <Button size="small" type="text" title={labels.alignTop} onClick={() => alignClasses('top')}>T</Button>
          <Button size="small" type="text" title={labels.alignMiddle} onClick={() => alignClasses('middle')}>M</Button>
          <Button size="small" type="text" title={labels.alignBottom} onClick={() => alignClasses('bottom')}>B</Button>
          <Button size="small" type="text" title={labels.distributeHorizontal} onClick={() => distributeClasses('horizontal')}>↔</Button>
          <Button size="small" type="text" title={labels.distributeVertical} onClick={() => distributeClasses('vertical')}>↕</Button>
        </div>
      )}
      {showToolbar && (
        <div style={{
          position: 'absolute', top: 8, left: 8, zIndex: 100,
          background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
          padding: '4px 6px', boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
        }}>
          <Button size="small" icon={<PlusOutlined />} title={labels.add} onClick={handleAddClass}>
            {labels.add}
          </Button>
          {diagram.classes.length >= 2 && (
            <Button size="small" type="text" title={labels.autoLayoutTitle} onClick={autoLayoutClasses}>
              {labels.autoLayout}
            </Button>
          )}
          <Button size="small" type="text" title={labels.hideToolbar} onClick={() => setShowToolbar(false)}
            style={{ fontSize: 10, marginLeft: 4 }}>✕</Button>
        </div>
      )}
      {!showToolbar && (
        <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 100 }}>
          <Button size="small" type="dashed" title={labels.showToolbar} onClick={() => setShowToolbar(true)}>🔧</Button>
        </div>
      )}
      <div ref={containerRef} className={`uml-canvas-container theme-${canvasTheme}`} />
    </div>
  );
};

export default UMLEditor;
