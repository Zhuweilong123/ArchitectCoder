/**
 * Component Diagram Editor — powered by AntV X6.
 * Reuses the same X6 patterns as UMLEditor.
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Edge, Graph, Node } from '@antv/x6';
import { useShallow } from 'zustand/react/shallow';
import { getActiveDiagram, selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useUiStore, type CanvasTheme } from '../../stores/uiStore';
import { getCanvasLabels } from './canvasLabels';
import {
  buildCompHTML, CHILD_HEIGHT, CHILD_WIDTH,
  componentThemeVisuals, getCompNodeSize,
} from './compRenderUtils';
import { centerCanvasAfterFirstSync, useCanvasGraphViewport } from './core/useCanvasGraphViewport';
import { applyCanvasThemeToGraph, createCanvasGraph } from './core/createCanvasGraph';
import { disposeCanvasGraphInstance, registerCanvasGraphInstance } from './core/canvasLifecycle';
import { attachCanvasEventAdapter } from './core/canvasEventAdapter';
import { snapCanvasPosition } from './core/snapToGrid';
import {
  edgeVerticesEqual, getObstacleAvoidingEdgeVertices, getObstacleAvoidingManhattanRouter, materializeEdgeRouteVertices,
  resolveEdgeSelection, syncCanvasGrid,
} from './core/canvasCommon';
import type { CompNode, CompRelation } from '../../types/component';
import { getComponentDividerTop } from '../../utils/componentLayout';
import { componentRoutingObstacles, getComponentDelegationPorts, routeComponentOuterEdges } from './core/componentRouting';

// Child rows have a 32 px gap, leaving room for a selectable inner route.
const COMPONENT_EDGE_CLEARANCE = 12;
const OUTER_EDGE_CLEARANCE = 24;
import './CompEditor.css';

// ── Register X6 shapes (once) ────────────────────────

let shapesRegistered = false;
function ensureShapesRegistered() {
  if (shapesRegistered) return;
  shapesRegistered = true;

  Graph.registerNode('comp-component', {
    inherit: 'rect',
    markup: [
      { tagName: 'rect', selector: 'body' },
      {
        tagName: 'foreignObject', selector: 'fo',
        children: [{
          tagName: 'div', ns: 'http://www.w3.org/1999/xhtml', selector: 'content',
          style: {
            width: '100%', height: '100%',
            fontFamily: 'Consolas, Monaco, monospace',
            fontSize: '12px', lineHeight: '1.5', overflow: 'hidden',
          },
        }],
      },
    ],
    attrs: {
      body: { stroke: '#b7791f', strokeWidth: 1.5, fill: '#fffaf1', rx: 8, ry: 8 },
      fo: { refWidth: '100%', refHeight: '100%' },
      content: { html: '' },
    },
    ports: {
      groups: {
        top: {
          position: { name: 'top' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#b7791f', strokeWidth: 1.5, fill: '#fffdf8' } },
        },
        right: {
          position: { name: 'right' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#b7791f', strokeWidth: 1.5, fill: '#fffdf8' } },
        },
        bottom: {
          position: { name: 'bottom' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#b7791f', strokeWidth: 1.5, fill: '#fffdf8' } },
        },
        left: {
          position: { name: 'left' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#b7791f', strokeWidth: 1.5, fill: '#fffdf8' } },
        },
      },
      items: [{ id: 'pt', group: 'top' }, { id: 'pr', group: 'right' }, { id: 'pb', group: 'bottom' }, { id: 'pl', group: 'left' }],
    },
  });

  console.log('[CompEditor] X6 component shapes registered');
}

// ── Component ────────────────────────────────────────

interface ComponentClipboard {
  components: CompNode[];
  relations: CompRelation[];
}

const CompEditor: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const isInternalUpdate = useRef(false);
  const edgeSelectionCycle = useRef({
    point: null as { x: number; y: number } | null,
    ids: [] as string[],
    index: 0,
    timestamp: 0,
  });
  const clipboard = useRef<ComponentClipboard | null>(null);

  // ── Context menu state ──────────────────────────────
  const [ctxMenu, setCtxMenu] = useState<{
    visible: boolean; x: number; y: number; compId: string; compName: string;
  }>({ visible: false, x: 0, y: 0, compId: '', compName: '' });

  const {
    diagram, selectedComponentId, selectedCompRelationId,
    addComponent, removeComponent, moveComponent,
    addCompRelation, updateCompRelation, removeCompRelation,
    selectComponent, selectCompRelation,
    undo, redo, project, setActiveDiagram, addDiagram, autoLayoutComponents,
  } = useDiagramStore(useShallow((s) => ({
    diagram: selectActiveDiagram(s),
    selectedComponentId: s.selectedComponentId,
    addComponent: s.addComponent,
    removeComponent: s.removeComponent,
    moveComponent: s.moveComponent,
    addCompRelation: s.addCompRelation,
    updateCompRelation: s.updateCompRelation,
    removeCompRelation: s.removeCompRelation,
    selectComponent: s.selectComponent,
    selectCompRelation: s.selectCompRelation,
    undo: s.undo,
    redo: s.redo,
    project: s.project,
    setActiveDiagram: s.setActiveDiagram,
    addDiagram: s.addDiagram,
    autoLayoutComponents: s.autoLayoutComponents,
    selectedCompRelationId: s.selectedCompRelationId,
  })));
  const viewport = useDiagramStore((s) => s.viewport);
  const gridSettings = useDiagramStore((s) => s.project.grid_settings);

  const { setRightPanelTab, setRightPanelVisible, canvasTheme, interfaceLanguage } = useUiStore();

  // ── Init graph ──────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;
    ensureShapesRegistered();

    const graph = createCanvasGraph({
      container: containerRef.current,
      grid: {
        size: gridSettings.grid_size,
        visible: gridSettings.grid_visible,
        color: gridSettings.grid_color,
        thickness: gridSettings.grid_thickness,
      },
      connection: {
        allowMulti: true,
        line: {
          stroke: '#b7791f', strokeWidth: 2, strokeDasharray: '6,4',
          targetMarker: { name: 'block', width: 10, height: 6 },
        },
        router: getObstacleAvoidingManhattanRouter(COMPONENT_EDGE_CLEARANCE),
        connector: { name: 'normal' },
      },
    });

    const detachCanvasEvents = attachCanvasEventAdapter({
      graph,
      isInternalUpdate,
      onNodeClick: (node) => {
        selectComponent(node.id);
        setRightPanelTab('properties');
        setRightPanelVisible(true);
      },
      onBlankClick: () => {
        selectComponent(null);
        selectCompRelation(null);
        setRightPanelVisible(false);
      },
      onNodeMoved: (node) => {
        const position = node.position();
        const store = useDiagramStore.getState();
        const nextPosition = snapCanvasPosition(
          { x: position.x, y: position.y },
          store.project.grid_settings.snap_to_grid,
          store.project.grid_settings.grid_size,
        );
        if (position.x !== nextPosition.x || position.y !== nextPosition.y) {
          isInternalUpdate.current = true;
          node.setPosition(nextPosition.x, nextPosition.y);
          isInternalUpdate.current = false;
        }
        graph.getConnectedEdges(node).forEach((edge) => {
          const relation = (getActiveDiagram().comp_relations || []).find((item) => item.id === edge.id);
          if (!Array.isArray(relation?.vertices)) edge.setVertices([]);
        });
        moveComponent(node.id, nextPosition.x, nextPosition.y);
      },
      onNodeResized: (node) => {
        useDiagramStore.getState().updateComponent(node.id, {
          width: node.size().width,
          height: node.size().height,
        });
      },
      onEdgeClick: (edge, point) => {
        const selectedEdge = point
          ? resolveEdgeSelection(graph, edge, point, edgeSelectionCycle.current)
          : edge;
        selectCompRelation(selectedEdge.id);
        setRightPanelTab('properties');
        setRightPanelVisible(true);
      },
      onEdgeMouseEnter: (edge) => {
        const relation = (getActiveDiagram().comp_relations || []).find((item) => item.id === edge.id);
        if (!relation || Array.isArray(relation.vertices)) return;
        isInternalUpdate.current = true;
        materializeEdgeRouteVertices(graph, edge);
        isInternalUpdate.current = false;
      },
      onEdgeEndpointChanged: (edge) => {
        const relation = (getActiveDiagram().comp_relations || []).find((item) => item.id === edge.id);
        if (!relation) return;
        const source = edge.getSourceCellId();
        const target = edge.getTargetCellId();
        if (!source || !target || source === target) return;
        if (!Array.isArray(relation.vertices)) edge.setVertices([]);
        updateCompRelation(edge.id, { source, target });
      },
      onEdgeVerticesChanged: (edge) => {
        const relation = (getActiveDiagram().comp_relations || []).find((item) => item.id === edge.id);
        if (!relation) return;
        const vertices = edge.getVertices().map(({ x, y }) => ({ x, y }));
        if (JSON.stringify(relation.vertices) === JSON.stringify(vertices)) return;
        updateCompRelation(edge.id, { vertices });
      },
      onNewEdge: (edge, sourceId, targetId) => {
        isInternalUpdate.current = true;
        edge.remove();
        isInternalUpdate.current = false;
        addCompRelation(sourceId, targetId);
      },
      onEdgeRemoved: (edge) => removeCompRelation(edge.id),
      edgeTools: [
        // Segment handles move orthogonal runs and therefore control the
        // length of a 90-degree turn without introducing port re-layout.
        { name: 'segments', args: { threshold: 20, snapRadius: 12 } },
        { name: 'vertices', args: { addable: false, removable: true, snapRadius: 12 } },
        { name: 'source-arrowhead' },
        { name: 'target-arrowhead' },
        { name: 'button-remove', args: { distance: -30 } },
      ],
    });

    // Right-click context menu on component nodes
    graph.on('node:contextmenu', ({ node, e }: any) => {
      const evt = e.evt || e;
      evt?.preventDefault?.();
      const store = useDiagramStore.getState();
      const comp = (getActiveDiagram().components || []).find((c) => c.id === node.id);
      setCtxMenu({
        visible: true,
        x: evt?.clientX || evt?.pageX || 0,
        y: evt?.clientY || evt?.pageY || 0,
        compId: node.id,
        compName: comp?.name || '',
      });
    });

    // Keyboard
    const handleKeyDown = (e: KeyboardEvent) => {
      const store = useDiagramStore.getState();
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const key = e.key.toLowerCase();
      const modifier = e.ctrlKey || e.metaKey;
      if (modifier && key === 'c') {
        if (store.selectedComponentId) {
          const activeDiagram = getActiveDiagram();
          const components = activeDiagram.components || [];
          const copiedIds = new Set([store.selectedComponentId]);
          let changed = true;
          while (changed) {
            changed = false;
            components.forEach((component) => {
              if (component.parent_id && copiedIds.has(component.parent_id) && !copiedIds.has(component.id)) {
                copiedIds.add(component.id);
                changed = true;
              }
            });
          }
          clipboard.current = {
            components: JSON.parse(JSON.stringify(components.filter((component) => copiedIds.has(component.id)))),
            relations: JSON.parse(JSON.stringify(
              (activeDiagram.comp_relations || []).filter(
                (relation) => copiedIds.has(relation.source) && copiedIds.has(relation.target)
              )
            )),
          };
          console.log('[CompEditor] Copied component subtree:', copiedIds.size);
        }
      } else if (modifier && key === 'v') {
        e.preventDefault();
        if (clipboard.current?.components.length) {
          const copied = clipboard.current;
          store.beginBatch();
          try {
            const idMap = new Map<string, string>();
            const pending = [...copied.components];
            const copiedIds = new Set(copied.components.map((component) => component.id));
            while (pending.length > 0) {
              const index = pending.findIndex((component) => (
                !component.parent_id
                || idMap.has(component.parent_id)
                || !copiedIds.has(component.parent_id)
              ));
              const component = pending.splice(index >= 0 ? index : 0, 1)[0];
              const newParentId = component.parent_id ? idMap.get(component.parent_id) || '' : '';
              store.addComponent({ x: component.x + 30, y: component.y + 30 }, newParentId);

              const activeComponents = getActiveDiagram().components || [];
              const pasted = activeComponents[activeComponents.length - 1];
              if (!pasted) continue;
              idMap.set(component.id, pasted.id);
              const store2 = useDiagramStore.getState();
              store2.updateComponent(pasted.id, {
                name: component.name,
                width: component.width,
                height: component.height,
                provided_interfaces: [...(component.provided_interfaces || [])],
                required_interfaces: [...(component.required_interfaces || [])],
              });
            }

            copied.relations.forEach((relation) => {
              const source = idMap.get(relation.source);
              const target = idMap.get(relation.target);
              if (!source || !target) return;
              store.addCompRelation(source, target);
              const relations = getActiveDiagram().comp_relations || [];
              const pastedRelation = relations[relations.length - 1];
              if (pastedRelation && pastedRelation.type !== relation.type) {
                useDiagramStore.getState().updateCompRelation(pastedRelation.id, {
                  type: relation.type,
                  vertices: relation.vertices?.map(({ x, y }) => ({ x: x + 30, y: y + 30 })),
                });
              } else if (pastedRelation && relation.vertices) {
                useDiagramStore.getState().updateCompRelation(pastedRelation.id, {
                  vertices: relation.vertices.map(({ x, y }) => ({ x: x + 30, y: y + 30 })),
                });
              }
            });
          } finally {
            store.endBatch();
          }
          clipboard.current = {
            components: copied.components.map((component) => ({
              ...component,
              x: component.x + 30,
              y: component.y + 30,
            })),
            relations: copied.relations.map((relation) => ({
              ...relation,
              vertices: relation.vertices?.map(({ x, y }) => ({ x: x + 30, y: y + 30 })),
            })),
          };
        }
      } else if (modifier && key === 'z' && !e.shiftKey) { e.preventDefault(); store.undo(); }
      else if (modifier && (key === 'y' || (key === 'z' && e.shiftKey))) { e.preventDefault(); store.redo(); }
      else if (key === 'escape') {
        e.preventDefault();
        graph.cleanSelection();
        selectComponent(null);
        selectCompRelation(null);
      }
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        const cells = graph.getSelectedCells();
        const nodeIds = cells.filter((cell) => cell.isNode()).map((cell) => cell.id);
        const edgeIds = cells.filter((cell) => cell.isEdge()).map((cell) => cell.id);
        if (nodeIds.length === 0 && store.selectedComponentId) nodeIds.push(store.selectedComponentId);
        if (edgeIds.length === 0 && store.selectedCompRelationId) edgeIds.push(store.selectedCompRelationId);
        if (nodeIds.length > 0 || edgeIds.length > 0) {
          e.preventDefault();
          isInternalUpdate.current = true;
          store.beginBatch();
          try {
            nodeIds.forEach((id) => store.removeComponent(id));
            edgeIds.forEach((id) => store.removeCompRelation(id));
          } finally {
            store.endBatch();
          }
          graph.cleanSelection();
          isInternalUpdate.current = false;
          selectComponent(null);
          selectCompRelation(null);
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    if (!(viewport.panX || viewport.panY) && viewport.zoom === 1) {
      graph.centerContent();
    }
    registerCanvasGraphInstance(graph, graphRef);
    console.log('[CompEditor] Graph initialized');

    return () => {
      _didFirstSync.current = false;
      disposeCanvasGraphInstance(graph, graphRef, () => {
        document.removeEventListener('keydown', handleKeyDown);
        detachCanvasEvents();
      });
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useCanvasGraphViewport(graphRef, containerRef, viewport);

  // ── Sync diagram → graph ───────────────────────────
  const prevCompIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const renderCache = useRef<Map<string, { entity: CompNode; selected: boolean; theme: CanvasTheme; html: string }>>(new Map());
  const nodeSignatureCache = useRef<Map<string, string>>(new Map());
  const edgeSignatureCache = useRef<Map<string, string>>(new Map());
  const autoRouteCache = useRef<Map<string, {
    key: string;
    vertices: Array<{ x: number; y: number }>;
  }>>(new Map());
  const _didFirstSync = useRef(false);
  const outerRouteCache = useRef<{ key: string; routes: ReturnType<typeof routeComponentOuterEdges> } | null>(null);
  const renderedTheme = useRef<CanvasTheme | null>(null);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    applyCanvasThemeToGraph(graph, canvasTheme);

    try {
      isInternalUpdate.current = true;
      const comps = diagram.components || [];
      const rels = diagram.comp_relations || [];
      const currentIds = new Set(comps.map((c) => c.id));
      const themeChanged = renderedTheme.current !== canvasTheme;
      const themeVisuals = componentThemeVisuals[canvasTheme];

      // Remove deleted
      prevCompIds.current.forEach((id) => {
        if (!currentIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
          htmlCache.current.delete(id);
          renderCache.current.delete(id);
          nodeSignatureCache.current.delete(id);
        }
      });

      // Add/update components + handle embedding
      comps.forEach((c) => {
        const isChild = !!c.parent_id;
        const { width: w, height: h } = getCompNodeSize(c);
        const selected = c.id === selectedComponentId;
        // Theme is part of the rendered HTML. Always rebuild this small HTML
        // fragment so a theme change can never reuse a stale node fragment.
        const htmlContent = buildCompHTML(c, selected, canvasTheme, interfaceLanguage,
          comps.some((child) => child.parent_id === c.id));
        const cached = htmlCache.current.get(c.id);
        const signature = JSON.stringify([
          htmlContent, c.x, c.y, w, h, c.parent_id || '', canvasTheme,
        ]);
        renderCache.current.set(c.id, { entity: c, selected, theme: canvasTheme, html: htmlContent });
        try {
          const existing = graph.getCellById(c.id);
          if (existing && existing.isNode()) {
            // A theme change must refresh every node, even if an older cache
            // entry accidentally reports the same layout signature.
            if (!themeChanged && nodeSignatureCache.current.get(c.id) === signature) return;
            const node = existing as Node;
            node.setPosition(c.x, c.y);
            node.setSize({ width: w, height: h });
            node.setAttrs({
              body: {
                fill: themeVisuals.surface,
                stroke: selected ? '#2563eb' : themeVisuals.accent,
                strokeWidth: selected ? 2.5 : 1.5,
              },
            });
            if (themeChanged || cached !== htmlContent) {
              node.setAttrByPath('content/html', htmlContent);
              htmlCache.current.set(c.id, htmlContent);
            }
            // Re-embed child in parent
            if (isChild) {
              const parent = graph.getCellById(c.parent_id);
              if (parent) parent.addChild(node);
            }
            nodeSignatureCache.current.set(c.id, signature);
          } else {
            const node = graph.addNode({
              id: c.id, shape: 'comp-component',
              x: c.x, y: c.y,
              width: w, height: h,
              attrs: {
                content: { html: htmlContent },
                body: {
                  fill: themeVisuals.surface,
                  stroke: selected ? '#2563eb' : themeVisuals.accent,
                  strokeWidth: selected ? 2.5 : 1.5,
                },
              },
            });
            htmlCache.current.set(c.id, htmlContent);
            if (node) nodeSignatureCache.current.set(c.id, signature);
            if (isChild && node) {
              const parent = graph.getCellById(c.parent_id) as Node;
              if (parent) parent.addChild(node as Node);
            }
          }
        } catch (e) { console.warn('[CompEditor] Sync error:', c.name, e); }
      });

      // Sync edges
      const existingEdges = new Set(graph.getEdges().map((e) => e.id));
      const dataEdgeIds = new Set(rels.map((r) => r.id));
      existingEdges.forEach((id) => {
        if (!dataEdgeIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
          edgeSignatureCache.current.delete(id);
        }
      });

      const componentRects = comps.map((component) => {
        const { width, height } = getCompNodeSize(component);
        return { id: component.id, x: component.x, y: component.y, width, height };
      });
      const topLevelRects = componentRects.filter((rect) => (
        !comps.find((component) => component.id === rect.id)?.parent_id
        || !comps.some((component) => component.id === comps.find((item) => item.id === rect.id)?.parent_id)
      ));
      const autoRouteCacheKey = JSON.stringify([
        componentRects,
        comps.map(({ id, parent_id }) => [id, parent_id]),
        rels.map(({ id, source, target, type, vertices }) => [id, source, target, type, vertices]),
      ]);
      if (outerRouteCache.current?.key !== autoRouteCacheKey) {
        outerRouteCache.current = {
          key: autoRouteCacheKey,
          routes: routeComponentOuterEdges(rels, topLevelRects, OUTER_EDGE_CLEARANCE),
        };
      }
      rels.forEach((r) => {
        const sourceComponent = comps.find((component) => component.id === r.source);
        const targetComponent = comps.find((component) => component.id === r.target);
        const localDelegation = r.type === 'delegation'
          && !!sourceComponent && targetComponent?.parent_id === sourceComponent.id;
        const delegationPort = localDelegation
          ? getComponentDelegationPorts(sourceComponent, targetComponent, comps, COMPONENT_EDGE_CLEARANCE)
          : undefined;
        const outerRoute = !localDelegation ? outerRouteCache.current?.routes.get(r.id) : undefined;
        const outerPorts = outerRoute?.ports;
        const sourceAnchorDx = localDelegation
          ? delegationPort!.source.x - (sourceComponent.x + sourceComponent.width / 2)
          : 0;
        const sourceAnchorDy = localDelegation
          ? getComponentDividerTop(sourceComponent) - sourceComponent.height / 2
          : 0;
        const sourceCenter = sourceComponent ? {
          x: sourceComponent.x + sourceComponent.width / 2,
          y: sourceComponent.y + sourceComponent.height / 2,
        } : undefined;
        const targetCenter = targetComponent ? {
          x: targetComponent.x + targetComponent.width / 2,
          y: targetComponent.y + targetComponent.height / 2,
        } : undefined;
        const horizontal = sourceCenter && targetCenter
          ? Math.abs(targetCenter.x - sourceCenter.x) >= Math.abs(targetCenter.y - sourceCenter.y)
          : true;
        const forward = sourceCenter && targetCenter
          ? (horizontal ? targetCenter.x >= sourceCenter.x : targetCenter.y >= sourceCenter.y)
          : true;
        const sourcePoint = delegationPort?.source || outerPorts?.sourcePoint || (sourceComponent && sourceCenter ? horizontal
          ? { x: sourceComponent.x + (forward ? sourceComponent.width + 1 : -1), y: sourceCenter.y }
          : { x: sourceCenter.x, y: sourceComponent.y + (forward ? sourceComponent.height + 1 : -1) }
          : undefined);
        const targetPoint = delegationPort?.target || outerPorts?.targetPoint || (targetComponent && targetCenter
          ? horizontal
            ? { x: targetComponent.x + (forward ? -1 : targetComponent.width + 1), y: targetCenter.y }
            : { x: targetCenter.x, y: targetComponent.y + (forward ? -1 : targetComponent.height + 1) }
          : undefined);
        const sourceTerminal = localDelegation
          ? { cell: r.source, anchor: { name: 'center', args: {
                dx: sourceAnchorDx, dy: sourceAnchorDy,
              } },
              connectionPoint: { name: 'anchor' } }
          : outerPorts && sourceCenter
            ? { cell: r.source, anchor: { name: 'center', args: {
                  dx: outerPorts.sourcePoint.x - sourceCenter.x,
                  dy: outerPorts.sourcePoint.y - sourceCenter.y,
                } }, connectionPoint: { name: 'anchor' } }
          : sourcePoint && sourceCenter ? { cell: r.source, anchor: { name: 'center', args: {
              dx: sourcePoint.x - sourceCenter.x, dy: sourcePoint.y - sourceCenter.y,
            } }, connectionPoint: { name: 'anchor' } } : { cell: r.source };
        const targetTerminal = localDelegation
          ? { cell: r.target, anchor: { name: delegationPort!.targetAnchor }, connectionPoint: { name: 'anchor' } }
          : outerPorts && targetCenter
            ? { cell: r.target, anchor: { name: 'center', args: {
                  dx: outerPorts.targetPoint.x - targetCenter.x,
                  dy: outerPorts.targetPoint.y - targetCenter.y,
                } }, connectionPoint: { name: 'anchor' } }
          : targetPoint && targetCenter ? { cell: r.target, anchor: { name: 'center', args: {
              dx: targetPoint.x - targetCenter.x, dy: targetPoint.y - targetCenter.y,
            } }, connectionPoint: { name: 'anchor' } } : { cell: r.target };
        const selected = r.id === selectedCompRelationId;
        const stroke = selected
          ? (canvasTheme === 'dark' ? '#93c5fd' : canvasTheme === 'eye-care' ? '#6e9677' : '#2563eb')
          : r.type === 'delegation'
            ? (canvasTheme === 'dark' ? '#4ade80' : canvasTheme === 'eye-care' ? '#4f805d' : '#389e0d')
            : (canvasTheme === 'dark' ? '#fbbf24' : canvasTheme === 'eye-care' ? '#a9782c' : '#b7791f');
        const dash = r.type === 'delegation' ? '' : '6,4';
        const labelColor = canvasTheme === 'dark' ? '#f8fafc' : stroke;
        const labelBackground = canvasTheme === 'dark'
          ? '#111827' : canvasTheme === 'eye-care' ? '#f8f7ee' : '#ffffff';
        const labelBorder = canvasTheme === 'dark'
          ? '#475569' : canvasTheme === 'eye-care' ? '#cbd7c9' : '#e2e8f0';
        const lineAttrs = {
          stroke, strokeWidth: selected ? 2.5 : 2, strokeDasharray: dash,
          targetMarker: { name: 'block', width: 10, height: 6, fill: stroke, stroke },
        };
        // A dashed orange arrow already conveys a UML dependency. Repeating
        // “dependency” on every long route obscures the diagram, especially
        // when several dependencies share a corridor. Keep labels for the
        // less self-evident relation kinds.
        const labels = r.type === 'dependency' || localDelegation ? [] : [{
          attrs: {
            text: { text: r.type, fontSize: 10, fontWeight: 600, fill: labelColor },
            rect: { fill: labelBackground, stroke: labelBorder, strokeWidth: 0.8, rx: 4, ry: 4 },
          },
          position: { distance: 0.5, offset: -10 },
        }];
        // Imported diagrams use `null` for an untouched route; only a real
        // vertices array is user-owned. Automatic routes must be recalculated
        // whenever the component layout changes.
        const cachedAutoRoute = autoRouteCache.current.get(r.id);
        const vertices = Array.isArray(r.vertices)
          ? r.vertices
          : cachedAutoRoute?.key === autoRouteCacheKey
            ? cachedAutoRoute.vertices
            : outerRoute
              ? outerRoute.vertices
              : getObstacleAvoidingEdgeVertices(r, rels,
                componentRoutingObstacles(r.source, r.target, comps, componentRects), COMPONENT_EDGE_CLEARANCE,
                { source: sourcePoint, target: targetPoint });
        if (!Array.isArray(r.vertices) && cachedAutoRoute?.key !== autoRouteCacheKey) {
          autoRouteCache.current.set(r.id, { key: autoRouteCacheKey, vertices });
        }
        const interactionAttrs = {
          stroke: 'transparent',
          strokeWidth: 10,
          fill: 'none',
          pointerEvents: 'stroke',
        };
        const edgeSignature = JSON.stringify([
          r.source, r.target, r.type, selected, canvasTheme, vertices,
          sourceAnchorDx, sourceAnchorDy, delegationPort?.targetAnchor,
          sourcePoint, targetPoint,
        ]);
        try {
          if (existingEdges.has(r.id)) {
            if (edgeSignatureCache.current.get(r.id) === edgeSignature) return;
            const edge = graph.getCellById(r.id) as any;
            if (edge) {
              edge.setSource(sourceTerminal);
              edge.setTarget(targetTerminal);
              if (!edgeVerticesEqual(edge.getVertices(), vertices)) edge.setVertices(vertices);
              edge.setRouter({ name: 'normal' });
              edge.setConnector({ name: 'normal' });
              edge.setLabels(labels);
              edge.setAttrByPath('line/stroke', stroke);
              edge.setAttrByPath('line/strokeWidth', selected ? 2.5 : 2);
              edge.setAttrByPath('line/strokeDasharray', dash);
              edge.setAttrByPath('line/targetMarker/fill', stroke);
              edge.setAttrByPath('line/targetMarker/stroke', stroke);
              edge.setAttrByPath('wrap/stroke', interactionAttrs.stroke);
              edge.setAttrByPath('wrap/strokeWidth', interactionAttrs.strokeWidth);
              edge.setAttrByPath('wrap/pointerEvents', interactionAttrs.pointerEvents);
              edgeSignatureCache.current.set(r.id, edgeSignature);
            }
          } else {
            if (!graph.getCellById(r.source)) {
              console.warn('[CompEditor] Sync edge skipped — source node missing:', r.id, r.source);
              return;
            }
            if (!graph.getCellById(r.target)) {
              console.warn('[CompEditor] Sync edge skipped — target node missing:', r.id, r.target);
              return;
            }
            const edge = graph.addEdge({
              id: r.id,
              source: sourceTerminal,
              target: targetTerminal,
              vertices,
              attrs: {
                line: lineAttrs,
                wrap: interactionAttrs,
              },
              labels,
              router: { name: 'normal' },
              connector: { name: 'normal' },
            });
            if (edge) edgeSignatureCache.current.set(r.id, edgeSignature);
          }
        } catch (e) { console.warn('[CompEditor] Edge error:', r.id, e); }
      });

      renderedTheme.current = canvasTheme;
      prevCompIds.current = currentIds;
      isInternalUpdate.current = false;

      centerCanvasAfterFirstSync(graph, graphRef, _didFirstSync, viewport);
    } catch (err) {
      console.error('[CompEditor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram.components, diagram.comp_relations, selectedComponentId, selectedCompRelationId, canvasTheme, interfaceLanguage]);

  // ── Apply store zoom to the graph (toolbar zoom buttons) ──
  // Epsilon guard breaks the zoomTo → scale event → setZoom → effect loop.
  // ── Sync grid settings ─────────────────────────────
  useEffect(() => {
    const graph = graphRef.current as any;
    if (!graph) return;
    syncCanvasGrid(graph, {
      visible: gridSettings.grid_visible,
      size: gridSettings.grid_size,
      color: gridSettings.grid_color,
      thickness: gridSettings.grid_thickness,
    });
  }, [gridSettings]);

  const [showToolbar, setShowToolbar] = useState(true);
  const labels = getCanvasLabels(interfaceLanguage).componentDiagram;

  const handleAddComponent = useCallback(() => {
    const store = useDiagramStore.getState();
    const parent = store.selectedComponentId;
    if (parent) {
      // Create child inside selected parent
      const parentComp = getActiveDiagram().components?.find((c) => c.id === parent);
      const relX = 20 + Math.random() * 80;
      const relY = 40 + Math.random() * 60;
      store.addComponent({ x: relX, y: relY }, parent);
    } else {
      const x = 150 + Math.random() * 400;
      const y = 100 + Math.random() * 200;
      store.addComponent({ x, y });
    }
  }, []);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {showToolbar && (
        <div style={{
          position: 'absolute', top: 8, left: 8, zIndex: 100,
          background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
          padding: '4px 6px', boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
        }}>
          <Tooltip title={labels.addTitle}>
            <Button size="small" icon={<PlusOutlined />} onClick={handleAddComponent}>{labels.add}</Button>
          </Tooltip>
          {(diagram.components || []).length >= 2 && (
            <Tooltip title={labels.autoLayoutTitle}>
              <Button size="small" onClick={autoLayoutComponents}>{labels.autoLayout}</Button>
            </Tooltip>
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
      <div ref={containerRef} className={`comp-canvas-container theme-${canvasTheme}`} />

      {/* Component right-click context menu */}
      {ctxMenu.visible && (() => {
        const linkedClassDiagrams = project.diagrams.filter(
          (d) => d.component_id === ctxMenu.compId && (d.diagram_type || 'class') === 'class'
        );
        const linkedSeqDiagrams = project.diagrams.filter(
          (d) => d.component_id === ctxMenu.compId && d.diagram_type === 'sequence'
        );
        const closeMenu = () => setCtxMenu((prev) => ({ ...prev, visible: false }));

        return (
          <>
            {/* Backdrop to close on click-away */}
            <div style={{ position: 'fixed', inset: 0, zIndex: 999 }} onClick={closeMenu} />
            <div style={{
              position: 'fixed', left: ctxMenu.x, top: ctxMenu.y, zIndex: 1000,
              background: '#fff', border: '1px solid #d9d9d9', borderRadius: 8,
              boxShadow: '0 4px 16px rgba(0,0,0,0.15)', padding: 4, minWidth: 200,
              maxHeight: 360, overflowY: 'auto',
            }}>
              {/* Header — component name */}
              <div style={{
                padding: '6px 12px', fontSize: 13, fontWeight: 600,
                color: '#9a6b2f', borderBottom: '1px solid #f0f0f0', marginBottom: 4,
              }}>
                📦 {ctxMenu.compName}
              </div>

              {/* Linked class diagrams */}
              <div style={{ padding: '2px 12px 6px', fontSize: 11, color: '#999', fontWeight: 500 }}>
                {labels.linkedClassDiagrams(linkedClassDiagrams.length)}
              </div>
              {linkedClassDiagrams.length === 0 ? (
                <div style={{ padding: '2px 12px 6px', fontSize: 12, color: '#bbb' }}>
                  {labels.noLinkedClassDiagrams}
                </div>
              ) : (
                linkedClassDiagrams.map((d, i) => (
                  <div key={d.name || i} style={{
                    padding: '5px 12px 5px 20px', cursor: 'pointer', fontSize: 12,
                    borderRadius: 4, display: 'flex', alignItems: 'center', gap: 6,
                  }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f5ff')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    onClick={() => {
                      const idx = project.diagrams.indexOf(d);
                      if (idx >= 0) { setActiveDiagram(idx); closeMenu(); }
                    }}
                  >
                    <span>📋</span> <span>{d.name}</span>
                  </div>
                ))
              )}

              {/* Linked sequence diagrams */}
              <div style={{
                padding: '2px 12px 6px', fontSize: 11, color: '#999', fontWeight: 500,
                borderTop: '1px solid #f0f0f0', marginTop: 4, paddingTop: 6,
              }}>
                {labels.linkedSequenceDiagrams(linkedSeqDiagrams.length)}
              </div>
              {linkedSeqDiagrams.length === 0 ? (
                <div style={{ padding: '2px 12px 6px', fontSize: 12, color: '#bbb' }}>
                  {labels.noLinkedSequenceDiagrams}
                </div>
              ) : (
                linkedSeqDiagrams.map((d, i) => (
                  <div key={d.name || i} style={{
                    padding: '5px 12px 5px 20px', cursor: 'pointer', fontSize: 12,
                    borderRadius: 4, display: 'flex', alignItems: 'center', gap: 6,
                  }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f5ff')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    onClick={() => {
                      const idx = project.diagrams.indexOf(d);
                      if (idx >= 0) { setActiveDiagram(idx); closeMenu(); }
                    }}
                  >
                    <span>⏱️</span> <span>{d.name}</span>
                  </div>
                ))
              )}

              {/* Create actions */}
              <div style={{ borderTop: '1px solid #f0f0f0', marginTop: 4, paddingTop: 4 }}>
                <div style={{
                  padding: '5px 12px', cursor: 'pointer', fontSize: 12, borderRadius: 4,
                  display: 'flex', alignItems: 'center', gap: 6, color: '#1890ff',
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#e6f7ff')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  onClick={() => {
                    const compName = ctxMenu.compName || 'Component';
                    addDiagram('class', `${compName}_class`, ctxMenu.compId);
                    closeMenu();
                  }}
                >
                  <span>➕</span> <span>{labels.createClassDiagram}</span>
                </div>
                <div style={{
                  padding: '5px 12px', cursor: 'pointer', fontSize: 12, borderRadius: 4,
                  display: 'flex', alignItems: 'center', gap: 6, color: '#1890ff',
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#e6f7ff')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  onClick={() => {
                    const compName = ctxMenu.compName || 'Component';
                    addDiagram('sequence', `${compName}_seq`, ctxMenu.compId);
                    closeMenu();
                  }}
                >
                  <span>➕</span> <span>{labels.createSequenceDiagram}</span>
                </div>
              </div>
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default CompEditor;
