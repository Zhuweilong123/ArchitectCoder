/**
 * Component Diagram Editor — powered by AntV X6.
 * Reuses the same X6 patterns as UMLEditor.
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Graph, Node } from '@antv/x6';
import { useShallow } from 'zustand/react/shallow';
import { getActiveDiagram, selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useUiStore, type CanvasTheme } from '../../stores/uiStore';
import { attachGraphViewport } from './graphViewport';
import { createCanvasGraph } from './core/createCanvasGraph';
import { attachCanvasEventAdapter } from './core/canvasEventAdapter';
import { snapCanvasPosition } from './core/snapToGrid';
import type { CompNode, CompRelation } from '../../types/component';
import './CompEditor.css';
import { escapeHtml } from '../../utils/safeHtml';

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
      body: { stroke: '#d48806', strokeWidth: 2, fill: '#fffbe6', rx: 6, ry: 6 },
      fo: { refWidth: '100%', refHeight: '100%' },
      content: { html: '' },
    },
    ports: {
      groups: {
        top: {
          position: { name: 'top' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#d48806', strokeWidth: 2, fill: '#fff' } },
        },
        right: {
          position: { name: 'right' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#d48806', strokeWidth: 2, fill: '#fff' } },
        },
        bottom: {
          position: { name: 'bottom' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#d48806', strokeWidth: 2, fill: '#fff' } },
        },
        left: {
          position: { name: 'left' },
          markup: [{ tagName: 'circle', selector: 'circle' }],
          attrs: { circle: { r: 5, magnet: true, stroke: '#d48806', strokeWidth: 2, fill: '#fff' } },
        },
      },
      items: [{ id: 'pt', group: 'top' }, { id: 'pr', group: 'right' }, { id: 'pb', group: 'bottom' }, { id: 'pl', group: 'left' }],
    },
  });

  console.log('[CompEditor] X6 component shapes registered');
}

// ── HTML builder ─────────────────────────────────────

function buildCompHTML(comp: CompNode, selected: boolean, theme: CanvasTheme): string {
  const isChild = !!comp.parent_id;
  const selClass = selected ? 'selected' : '';
  const childClass = isChild ? 'child' : '';

  // UML 2.5.1 lollipop (provided) and socket (required) notation
  const provided = (comp.provided_interfaces || []).map((i) =>
    `<div class="comp-iface provided"><span class="comp-lollipop">⊃</span> ${escapeHtml(i)}</div>`
  ).join('');
  const required = (comp.required_interfaces || []).map((i) =>
    `<div class="comp-iface required"><span class="comp-socket">⊂</span> ${escapeHtml(i)}</div>`
  ).join('');

  return `<div class="comp-node theme-${theme} ${childClass} ${selClass}">
    <div class="comp-stereotype">${isChild ? '' : '«component»'}</div>
    <div class="comp-name">${escapeHtml(comp.name)}</div>
    ${provided ? `<div class="comp-block"><div class="comp-block-label">provided interfaces</div>${provided}</div>` : ''}
    ${required ? `<div class="comp-block"><div class="comp-block-label">required interfaces</div>${required}</div>` : ''}
  </div>`;
}

// ── Component ────────────────────────────────────────

const COMP_WIDTH = 200;
const COMP_HEIGHT = 160;
const CHILD_WIDTH = 150;
const CHILD_HEIGHT = 100;

interface ComponentClipboard {
  components: CompNode[];
  relations: CompRelation[];
}

function getCompNodeSize(comp: CompNode): { width: number; height: number } {
  const isChild = !!comp.parent_id;
  const interfaceCount = (comp.provided_interfaces || []).length
    + (comp.required_interfaces || []).length;
  const minHeight = 76 + (interfaceCount > 0 ? 24 + interfaceCount * 18 : 0);
  return {
    width: comp.width || (isChild ? CHILD_WIDTH : COMP_WIDTH),
    height: Math.max(comp.height || (isChild ? CHILD_HEIGHT : COMP_HEIGHT), minHeight),
  };
}

const CompEditor: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const isInternalUpdate = useRef(false);
  const clipboard = useRef<ComponentClipboard | null>(null);

  // ── Context menu state ──────────────────────────────
  const [ctxMenu, setCtxMenu] = useState<{
    visible: boolean; x: number; y: number; compId: string; compName: string;
  }>({ visible: false, x: 0, y: 0, compId: '', compName: '' });

  const {
    diagram, selectedComponentId, selectedCompRelationId,
    addComponent, removeComponent, moveComponent,
    addCompRelation, removeCompRelation,
    selectComponent, selectCompRelation,
    undo, redo, project, setActiveDiagram, addDiagram,
  } = useDiagramStore(useShallow((s) => ({
    diagram: selectActiveDiagram(s),
    selectedComponentId: s.selectedComponentId,
    addComponent: s.addComponent,
    removeComponent: s.removeComponent,
    moveComponent: s.moveComponent,
    addCompRelation: s.addCompRelation,
    removeCompRelation: s.removeCompRelation,
    selectComponent: s.selectComponent,
    selectCompRelation: s.selectCompRelation,
    undo: s.undo,
    redo: s.redo,
    project: s.project,
    setActiveDiagram: s.setActiveDiagram,
    addDiagram: s.addDiagram,
    selectedCompRelationId: s.selectedCompRelationId,
  })));
  const viewport = useDiagramStore((s) => s.viewport);

  const { setRightPanelTab, canvasTheme } = useUiStore();

  // ── Init graph ──────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;
    ensureShapesRegistered();

    const d = getActiveDiagram();
    const graph = createCanvasGraph({
      container: containerRef.current,
      grid: {
        size: d.grid_size || 20,
        visible: d.grid_visible !== false,
        color: d.grid_color || '#e0e0e0',
        thickness: d.grid_thickness || 1,
      },
      connection: {
        allowMulti: true,
        line: {
          stroke: '#d48806', strokeWidth: 2, strokeDasharray: '6,4',
          targetMarker: { name: 'block', width: 10, height: 6 },
        },
        router: { name: 'orth' },
        connector: { name: 'rounded' },
      },
    });

    const detachViewport = attachGraphViewport(graph, {
      container: containerRef.current,
      zoom: viewport.zoom,
      panX: viewport.panX,
      panY: viewport.panY,
      onZoom: (zoom) => useDiagramStore.getState().setZoom(zoom),
      onPan: (x, y) => useDiagramStore.getState().setPan(x, y),
    });

    const detachCanvasEvents = attachCanvasEventAdapter({
      graph,
      isInternalUpdate,
      onNodeClick: (node) => {
        selectComponent(node.id);
        setRightPanelTab('properties');
      },
      onBlankClick: () => {
        selectComponent(null);
        selectCompRelation(null);
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
        moveComponent(node.id, nextPosition.x, nextPosition.y);
      },
      onNodeResized: (node) => {
        useDiagramStore.getState().updateComponent(node.id, {
          width: node.size().width,
          height: node.size().height,
        });
      },
      onEdgeClick: (edge) => {
        selectCompRelation(edge.id);
        setRightPanelTab('properties');
      },
      onNewEdge: (edge, sourceId, targetId) => {
        isInternalUpdate.current = true;
        edge.remove();
        isInternalUpdate.current = false;
        addCompRelation(sourceId, targetId);
      },
      onEdgeRemoved: (edge) => removeCompRelation(edge.id),
      edgeTools: [
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
                useDiagramStore.getState().updateCompRelation(pastedRelation.id, { type: relation.type });
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
            relations: copied.relations.map((relation) => ({ ...relation })),
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
    graphRef.current = graph;
    console.log('[CompEditor] Graph initialized');

    return () => {
      _didFirstSync.current = false;
      document.removeEventListener('keydown', handleKeyDown);
      detachCanvasEvents();
      detachViewport();
      try { graph.dispose(); } catch { /* ignore */ }
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync diagram → graph ───────────────────────────
  const prevCompIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const renderCache = useRef<Map<string, { entity: CompNode; selected: boolean; theme: CanvasTheme; html: string }>>(new Map());
  const nodeSignatureCache = useRef<Map<string, string>>(new Map());
  const edgeSignatureCache = useRef<Map<string, string>>(new Map());
  const _didFirstSync = useRef(false);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;

    try {
      isInternalUpdate.current = true;
      const comps = diagram.components || [];
      const rels = diagram.comp_relations || [];
      const currentIds = new Set(comps.map((c) => c.id));

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
        const cachedRender = renderCache.current.get(c.id);
        const htmlContent = cachedRender?.entity === c && cachedRender.selected === selected
          && cachedRender.theme === canvasTheme
          ? cachedRender.html
          : buildCompHTML(c, selected, canvasTheme);
        const cached = htmlCache.current.get(c.id);
        const signature = JSON.stringify([
          htmlContent, c.x, c.y, w, h, c.parent_id || '',
        ]);
        renderCache.current.set(c.id, { entity: c, selected, theme: canvasTheme, html: htmlContent });
        try {
          const existing = graph.getCellById(c.id);
          if (existing && existing.isNode()) {
            if (nodeSignatureCache.current.get(c.id) === signature) return;
            const node = existing as Node;
            node.setPosition(c.x, c.y);
            node.setSize({ width: w, height: h });
            node.setAttrs({
              body: {
                stroke: selected ? '#2563eb' : '#d48806',
                strokeWidth: selected ? 2.5 : 1.5,
              },
            });
            if (cached !== htmlContent) {
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
                  stroke: selected ? '#2563eb' : '#d48806',
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

      rels.forEach((r) => {
        const selected = r.id === selectedCompRelationId;
        const stroke = selected ? '#2563eb' : r.type === 'delegation' ? '#389e0d' : '#d48806';
        const dash = r.type === 'delegation' ? '' : '6,4';
        const signature = JSON.stringify([r.source, r.target, r.type, selected, canvasTheme]);
        const lineAttrs = {
          stroke, strokeWidth: selected ? 2.5 : 2, strokeDasharray: dash,
          targetMarker: { name: 'block', width: 10, height: 6 },
        };
        const labels = [{
          attrs: {
            text: { text: r.type, fontSize: 10, fill: stroke },
            rect: { fill: canvasTheme === 'dark' ? '#172033' : '#fff', stroke: 'none', rx: 3 },
          },
          position: { distance: 0.5, offset: -10 },
        }];
        try {
          if (existingEdges.has(r.id)) {
            if (edgeSignatureCache.current.get(r.id) === signature) return;
            const edge = graph.getCellById(r.id) as any;
            if (edge) {
              edge.setSource({ cell: r.source });
              edge.setTarget({ cell: r.target });
              edge.setRouter({ name: 'orth' });
              edge.setConnector({ name: 'rounded' });
              edge.setLabels(labels);
              edge.setAttrByPath('line/stroke', stroke);
              edge.setAttrByPath('line/strokeWidth', selected ? 2.5 : 2);
              edge.setAttrByPath('line/strokeDasharray', dash);
              edgeSignatureCache.current.set(r.id, signature);
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
              source: { cell: r.source },
              target: { cell: r.target },
              attrs: {
                line: lineAttrs,
              },
              labels,
              router: { name: 'orth' },
              connector: { name: 'rounded' },
            });
            if (edge) edgeSignatureCache.current.set(r.id, signature);
          }
        } catch (e) { console.warn('[CompEditor] Edge error:', r.id, e); }
      });

      prevCompIds.current = currentIds;
      isInternalUpdate.current = false;

      if (
        !_didFirstSync.current
        && graph.getNodes().length > 0
        && !(viewport.panX || viewport.panY)
        && viewport.zoom === 1
      ) {
        _didFirstSync.current = true;
        console.log('[CompEditor] First sync with elements, scheduling centerContent. Nodes:', graph.getNodes().length);
        setTimeout(() => {
          const g = graphRef.current;
          if (!g) return;
          g.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
          const sidebarW = useUiStore.getState().rightPanelWidth;
          const bbox = g.getAllCellsBBox?.() || g.getContentBBox?.() || { x: 0, y: 0, width: 0, height: 0 };
          const visibleW = g.options.width - sidebarW;
          if (bbox.width < visibleW - 40) {
            g.translate(g.translate().tx - sidebarW / 2, g.translate().ty);
          }
        }, 200);
      }
    } catch (err) {
      console.error('[CompEditor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram.components, diagram.comp_relations, selectedComponentId, selectedCompRelationId, canvasTheme]);

  // ── Apply store zoom to the graph (toolbar zoom buttons) ──
  // Epsilon guard breaks the zoomTo → scale event → setZoom → effect loop.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    if (Math.abs(graph.zoom() - viewport.zoom) > 0.001) {
      graph.zoomTo(viewport.zoom);
    }
  }, [viewport.zoom]);

  // Restore persisted translation when switching diagrams or loading a project.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const translation = graph.translate();
    if (
      Math.abs(translation.tx - viewport.panX) > 0.5
      || Math.abs(translation.ty - viewport.panY) > 0.5
    ) {
      graph.translate(viewport.panX, viewport.panY);
    }
  }, [viewport.panX, viewport.panY]);

  // ── Sync grid settings ─────────────────────────────
  useEffect(() => {
    const graph = graphRef.current as any;
    if (!graph) return;
    try {
      if (diagram.grid_visible !== false) {
        graph.showGrid();
        graph.setGridSize(diagram.grid_size || 20);
        graph.drawGrid({ size: diagram.grid_size || 20,
          args: { color: diagram.grid_color || '#e0e0e0', thickness: diagram.grid_thickness || 1 } });
      } else {
        graph.hideGrid();
      }
    } catch (e) { /* ignore */ }
  }, [diagram.grid_visible, diagram.grid_size, diagram.grid_color, diagram.grid_thickness]);

  // ── Auto-center on recenter trigger ───────────────
  const recenterCounter = useDiagramStore((s) => s.recenterCounter);
  useEffect(() => {
    if (recenterCounter <= 0) return;
    setTimeout(() => {
      const g = graphRef.current;
      if (!g) return;
      g.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
      const sidebarW = useUiStore.getState().rightPanelWidth;
      const bbox = g.getAllCellsBBox?.() || g.getContentBBox?.() || { x: 0, y: 0, width: 0, height: 0 };
      const visibleW = g.options.width - sidebarW;
      if (bbox.width < visibleW - 40) {
        g.translate(g.translate().tx - sidebarW / 2, g.translate().ty);
      }
    }, 100);
  }, [recenterCounter]);

  const [showToolbar, setShowToolbar] = useState(true);

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
          <Tooltip title="选中组件时创建子组件，未选中时创建顶层组件">
            <Button size="small" icon={<PlusOutlined />} onClick={handleAddComponent}>组件</Button>
          </Tooltip>
          <Button size="small" type="text" onClick={() => setShowToolbar(false)}
            style={{ fontSize: 10, marginLeft: 4 }}>✕</Button>
        </div>
      )}
      {!showToolbar && (
        <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 100 }}>
          <Button size="small" type="dashed" onClick={() => setShowToolbar(true)}>🔧</Button>
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
                color: '#d48806', borderBottom: '1px solid #f0f0f0', marginBottom: 4,
              }}>
                📦 {ctxMenu.compName}
              </div>

              {/* Linked class diagrams */}
              <div style={{ padding: '2px 12px 6px', fontSize: 11, color: '#999', fontWeight: 500 }}>
                关联的类图 ({linkedClassDiagrams.length})
              </div>
              {linkedClassDiagrams.length === 0 ? (
                <div style={{ padding: '2px 12px 6px', fontSize: 12, color: '#bbb' }}>
                  暂无关联类图
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
                关联的时序图 ({linkedSeqDiagrams.length})
              </div>
              {linkedSeqDiagrams.length === 0 ? (
                <div style={{ padding: '2px 12px 6px', fontSize: 12, color: '#bbb' }}>
                  暂无关联时序图
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
                  <span>➕</span> <span>为此组件新建类图</span>
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
                  <span>➕</span> <span>为此组件新建时序图</span>
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
