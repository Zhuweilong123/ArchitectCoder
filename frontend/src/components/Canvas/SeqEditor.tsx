/**
 * Sequence Diagram Editor — lifelines + messages powered by AntV X6.
 * Reuses the same X6 patterns as UMLEditor (isInternalUpdate, sync effect, etc.)
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Graph, Node, Edge } from '@antv/x6';
import { History } from '@antv/x6-plugin-history';
import { Transform } from '@antv/x6-plugin-transform';
import { Selection } from '@antv/x6-plugin-selection';
import { Snapline } from '@antv/x6-plugin-snapline';
import { Button, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useDiagramStore } from '../../stores/diagramStore';
import { useUiStore } from '../../stores/uiStore';
import type { SeqLifeline, SeqMessage, MessageType } from '../../types/sequence';
import { MESSAGE_TYPE_LABELS, FRAGMENT_LABELS, type FragmentType } from '../../types/sequence';
import './SeqEditor.css';

// ── Register X6 shapes (once) ────────────────────────

let shapesRegistered = false;
function ensureShapesRegistered() {
  if (shapesRegistered) return;
  shapesRegistered = true;

  // Lifeline: header rect + dashed body line
  Graph.registerNode('seq-lifeline', {
    inherit: 'rect',
    resizable: false,
    markup: [
      { tagName: 'rect', selector: 'body' },
      {
        tagName: 'foreignObject',
        selector: 'fo',
        children: [
          {
            tagName: 'div',
            ns: 'http://www.w3.org/1999/xhtml',
            selector: 'content',
            style: {
              width: '100%', height: '100%',
              fontFamily: 'Consolas, Monaco, monospace',
              fontSize: '12px', lineHeight: '1.5',
              overflow: 'hidden',
            },
          },
        ],
      },
    ],
    attrs: {
      body: {
        stroke: '#333', strokeWidth: 2, fill: '#ffe6cc',
        rx: 4, ry: 4,
      },
      fo: { refWidth: '100%', refHeight: '100%' },
      content: { html: '' },
    },
    ports: {},
  });

  // Fragment: overlay with label tab
  Graph.registerNode('seq-fragment', {
    inherit: 'rect',
    markup: [
      { tagName: 'rect', selector: 'body' },
      {
        tagName: 'foreignObject', selector: 'label',
        children: [{
          tagName: 'div', ns: 'http://www.w3.org/1999/xhtml', selector: 'labelText',
          style: {
            position: 'absolute', top: 0, left: 0,
            fontSize: '11px', fontWeight: 600, fontFamily: 'Consolas, monospace',
            color: '#333', background: '#fff9e6', padding: '1px 6px',
            border: '1px solid #d9d9d9', borderRadius: '0 0 4px 0',
            whiteSpace: 'nowrap',
          },
        }],
      },
    ],
    attrs: {
      body: {
        stroke: '#555', strokeWidth: 1.5, fill: 'rgba(230,247,255,0.15)',
        rx: 2, ry: 2, magnet: true,
      },
      label: { refWidth: '100%', refHeight: '22', refX: 0, refY: 0 },
      labelText: { html: '' },
    },
    ports: {},
  });

  console.log('[SeqEditor] X6 sequence shapes registered');
}

// ── HTML builders ────────────────────────────────────

function buildLifelineHTML(lifeline: SeqLifeline, selected: boolean): string {
  const selClass = selected ? 'selected' : '';
  const bars = (lifeline.activations || []).map((y, i) =>
    `<div class="seq-activation" style="top:${y - 6}px" title="激活条 #${i + 1}"></div>`
  ).join('');
  const hint = selected
    ? '<div class="seq-click-hint">▼ 已选中，点击另一生命线创建消息 ▼</div>'
    : '';
  return `<div class="seq-lifeline-node ${selClass}">
    <div class="seq-lifeline-name">${lifeline.name}</div>
    <div class="seq-lifeline-body">
      <div class="seq-lifeline-dash"></div>
      ${bars}
      ${hint}
    </div>
  </div>`;
}

// ── Component ────────────────────────────────────────

const LIFELINE_WIDTH = 140;
const LIFELINE_HEIGHT = 400;
const LIFELINE_Y = 120;  // give top padding so lifelines aren't cut off

const SeqEditor: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const isInternalUpdate = useRef(false);
  const clipboard = useRef<any>(null);

  const {
    diagram, project, selectedLifelineId, selectedMessageId,
    addLifeline, removeLifeline, moveLifeline,
    selectLifeline, selectMessage,
    undo, redo,
  } = useDiagramStore();

  const { setRightPanelTab } = useUiStore();

  // ── Fragment context menu ───────────────────────────
  const [ctxMenu, setCtxMenu] = useState<{ visible: boolean; x: number; y: number; nodeId: string }>({
    visible: false, x: 0, y: 0, nodeId: '',
  });

  // ── Init graph ──────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;
    ensureShapesRegistered();

    const d = useDiagramStore.getState().diagram;
    const graph = new Graph({
      container: containerRef.current,
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      background: { color: '#fafafa' },
      grid: {
        size: d.grid_size || 20, visible: d.grid_visible !== false,
        args: { color: d.grid_color || '#e0e0e0', thickness: d.grid_thickness || 1 },
      },
      mousewheel: { enabled: true, modifiers: ['ctrl', 'meta'], minScale: 0.1, maxScale: 5 },
      panning: { enabled: true },
    });

    graph.use(new History({ enabled: true }));
    graph.use(new Transform({ resizing: true, rotating: false }));
    graph.use(new Selection({ enabled: true, rubberband: true, showNodeSelectionBox: true }));
    graph.use(new Snapline({ enabled: true, sharp: true }));

    // Click-to-click message creation (lifelines only)
    graph.on('node:click', ({ node, e }) => {
      if (node.shape === 'seq-fragment') {
        (graph as any).__selectedFragment = node.id;
        return;
      }
      const store = useDiagramStore.getState();
      if (store.selectedLifelineId === node.id) {
        store.addMessage(node.id, node.id);
        return;
      }
      if (store.selectedLifelineId) {
        store.addMessage(store.selectedLifelineId, node.id);
        return;
      }
      selectLifeline(node.id);
      setRightPanelTab('properties');
    });

    graph.on('blank:click', () => {
      selectLifeline(null);
      selectMessage(null);
      (graph as any).__selectedFragment = null;
    });

    // Right-click on fragment → context menu
    graph.on('node:contextmenu', ({ node, e }: any) => {
      if (node.shape === 'seq-fragment') {
        const evt = e.evt || e;
        evt?.preventDefault?.();
        setCtxMenu({
          visible: true,
          x: evt?.clientX || evt?.pageX || e.clientX || e.pageX || 0,
          y: evt?.clientY || evt?.pageY || e.clientY || e.pageY || 0,
          nodeId: node.id,
        });
      }
    });

    // Fragment move: keep height constant by shifting both y_start and y_end
    let fragMoved = false;
    graph.on('node:moved', ({ node }) => {
      if (node.shape === 'seq-fragment' && !isInternalUpdate.current) {
        fragMoved = true;
        const h = node.size().height;
        useDiagramStore.getState().updateFragment(node.id, {
          x: node.position().x,
          y_start: node.position().y,
          y_end: node.position().y + h,
        } as any);
      }
    });
    graph.on('node:resized', ({ node }) => {
      if (node.shape === 'seq-fragment' && !isInternalUpdate.current) {
        fragMoved = true;
        useDiagramStore.getState().updateFragment(node.id, {
          x: node.position().x, width: node.size().width,
          y_start: node.position().y, y_end: node.position().y + node.size().height,
        } as any);
      }
    });
    // One snapshot per drag/resize operation (on mouseup)
    graph.on('cell:mouseup', ({ cell }: any) => {
      if (fragMoved && cell?.shape === 'seq-fragment') {
        useDiagramStore.getState().pushSnapshot('move_fragment');
        fragMoved = false;
      }
    });

    graph.on('node:click', ({ node }) => {
      if (node.shape === 'seq-fragment') {
        // Select fragment for deletion
        const store = useDiagramStore.getState();
        // Store the fragment ID temporarily for delete key
        (graph as any).__selectedFragment = node.id;
        return;
      }
    });

    graph.on('node:moved', ({ node }) => {
      moveLifeline(node.id, node.position().x);
    });

    graph.on('edge:click', ({ edge }) => {
      selectMessage(edge.id);
      setRightPanelTab('properties');
    });

    // Save edge Y position when dragged
    graph.on('edge:change:source', ({ edge, current }) => {
      if (typeof (current as any)?.y === 'number') {
        const store = useDiagramStore.getState();
        store.updateMessage(edge.id, { y: (current as any).y } as any);
      }
    });
    graph.on('edge:change:target', ({ edge, current }) => {
      if (typeof (current as any)?.y === 'number') {
        const store = useDiagramStore.getState();
        store.updateMessage(edge.id, { y: (current as any).y } as any);
      }
    });
    // Also capture vertex moves for self-messages
    graph.on('edge:change:vertices', ({ edge, current }) => {
      const store = useDiagramStore.getState();
      const src = edge.getSource();
      const srcY = typeof (src as any)?.y === 'number' ? (src as any).y : edge.getSourcePoint()?.y;
      if (typeof srcY === 'number') {
        store.updateMessage(edge.id, { y: srcY } as any);
      }
    });

    // Keyboard
    const handleKeyDown = (e: KeyboardEvent) => {
      const store = useDiagramStore.getState();
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (e.ctrlKey && e.key === 'c') {
        if (store.selectedLifelineId) {
          const ll = (store.diagram.lifelines || []).find((l) => l.id === store.selectedLifelineId);
          if (ll) clipboard.current = JSON.parse(JSON.stringify(ll));
        }
      } else if (e.ctrlKey && e.key === 'v') {
        if (clipboard.current) {
          const c = clipboard.current;
          store.addLifeline(c.x + 30);
          // Apply copied name
          const store2 = useDiagramStore.getState();
          const lls = store2.diagram.lifelines || [];
          const pasted = lls[lls.length - 1];
          if (pasted) {
            store2.updateLifeline(pasted.id, {
              name: c.name, class_ref: c.class_ref,
              activations: [...(c.activations || [])],
            });
          }
        }
      } else if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
        e.preventDefault(); store.undo();
      } else if (e.ctrlKey && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault(); store.redo();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        // Delete selected element
        const fragId = (graph as any).__selectedFragment;
        if (fragId) {
          e.preventDefault();
          store.removeFragment(fragId);
          (graph as any).__selectedFragment = null;
        } else if (store.selectedMessageId) {
          e.preventDefault();
          store.removeMessage(store.selectedMessageId);
        } else if (store.selectedLifelineId) {
          e.preventDefault();
          store.removeLifeline(store.selectedLifelineId);
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    graphRef.current = graph;
    graph.centerContent();
    console.log('[SeqEditor] Graph initialized');

    return () => {
      _didFirstSync.current = false;  // reset for StrictMode remount
      document.removeEventListener('keydown', handleKeyDown);
      try { graph.dispose(); } catch { /* ignore */ }
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync diagram → graph ───────────────────────────
  const prevLifelineIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const _didFirstSync = useRef(false);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;

    try {
      isInternalUpdate.current = true;
      const lifelines = diagram.lifelines || [];
      const messages = diagram.messages || [];
      const currentLIds = new Set(lifelines.map((l) => l.id));

      // Remove deleted lifelines
      prevLifelineIds.current.forEach((id) => {
        if (!currentLIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
          htmlCache.current.delete(id);
        }
      });

      // Calculate needed height from actual message positions, not a heuristic.
      // Backend layout engine spaces messages at 45px intervals starting from
      // _SEQ_START_Y=190. Using msg.y directly avoids drift from formula mismatches.
      let maxMsgY = 0;
      for (const m of messages) {
        const y = m.y || (LIFELINE_Y + 30 + m.order * 45);
        if (y > maxMsgY) maxMsgY = y;
      }
      const neededHeight = Math.max(
        LIFELINE_HEIGHT,
        maxMsgY > 0 ? (maxMsgY - LIFELINE_Y + 60) : 0  // 60px padding below last message
      );

      // Add/update lifelines (coordinate validation handled by store)
      lifelines.forEach((ll) => {
        const htmlContent = buildLifelineHTML(ll, ll.id === selectedLifelineId);
        const cached = htmlCache.current.get(ll.id);
        try {
          const existing = graph.getCellById(ll.id);
          if (existing && existing.isNode()) {
            const node = existing as Node;
            node.setPosition(ll.x, LIFELINE_Y);
            node.setSize({ width: LIFELINE_WIDTH, height: neededHeight });
            if (cached !== htmlContent) {
              node.setAttrByPath('content/html', htmlContent);
              htmlCache.current.set(ll.id, htmlContent);
            }
          } else {
            graph.addNode({
              id: ll.id,
              shape: 'seq-lifeline',
              x: ll.x, y: LIFELINE_Y,
              width: LIFELINE_WIDTH, height: neededHeight,
              attrs: { content: { html: htmlContent } },
            });
            htmlCache.current.set(ll.id, htmlContent);
          }
        } catch (e) {
          console.warn('[SeqEditor] Sync lifeline error:', ll.name, e);
        }
      });

      // Remove deleted messages (those in graph but not in data)
      const graphEdgeIds = new Set(graph.getEdges().map((e) => e.id));
      const dataMsgIds = new Set(messages.map((m) => m.id));
      graphEdgeIds.forEach((id) => {
        if (!dataMsgIds.has(id)) {
          try { graph.removeCell(id); } catch { /* ignore */ }
        }
      });

      // Add/update messages — use persisted msg.y, fall back to order-based calculation
      const lifelineMap = new Map(lifelines.map((l) => [l.id, l]));
      const MSG_Y_BASE = LIFELINE_Y + 30;
      messages.forEach((msg) => {
        const srcLL = lifelineMap.get(msg.from_lifeline);
        const tgtLL = lifelineMap.get(msg.to_lifeline);
        if (!srcLL || !tgtLL) return;

        const isSelf = msg.from_lifeline === msg.to_lifeline;
        const msgY = msg.y || MSG_Y_BASE + msg.order * 45;  // persisted Y takes priority; fallback matches backend 45px gap

        // Connect from source lifeline edge → target lifeline edge
        // Self-messages stay on right side; normal messages connect edges
        let fromX: number, toX: number;
        if (isSelf) {
          fromX = srcLL.x + LIFELINE_WIDTH;
          toX   = srcLL.x + LIFELINE_WIDTH;
        } else {
          const srcIsLeft = srcLL.x <= tgtLL.x;
          fromX = srcIsLeft ? srcLL.x + LIFELINE_WIDTH : srcLL.x;
          toX   = srcIsLeft ? tgtLL.x : tgtLL.x + LIFELINE_WIDTH;
        }

        let strokeColor = '#1890ff';
        let strokeDash = '';
        if (msg.type === 'return') { strokeColor = '#888'; strokeDash = '6,3'; }
        else if (msg.type === 'simple') { strokeColor = '#333'; }
        else if (msg.type === 'async') { strokeColor = '#52c41a'; }

        const lineAttrs: Record<string, unknown> = {
          stroke: strokeColor,
          strokeWidth: 2,
          strokeDasharray: strokeDash,
          targetMarker: { name: 'block', width: 10, height: 6 },
        };

        try {
          const existing = graph.getCellById(msg.id);
          if (existing && existing.isEdge()) {
            // Update existing edge: positions, vertices, style, label
            const edge = existing as Edge;
            edge.setSource({ x: fromX, y: msgY });
            edge.setTarget({ x: toX, y: isSelf ? msgY + 24 : msgY });
            if (isSelf) {
              edge.setVertices([
                { x: srcLL.x + LIFELINE_WIDTH + 40, y: msgY },
                { x: srcLL.x + LIFELINE_WIDTH + 40, y: msgY + 24 },
              ]);
            } else {
              edge.setVertices([]);
            }
            edge.setLabels(msg.label ? [{
              attrs: {
                text: { text: msg.label, fontSize: 10, fill: strokeColor },
                rect: { fill: '#fff', stroke: 'none', rx: 2 },
              },
              position: { distance: 0.5, offset: -12 },
            }] : []);
            edge.setAttrByPath('line/stroke', strokeColor);
            edge.setAttrByPath('line/strokeDasharray', strokeDash);
            return;
          }

          // New edge
          const edgeLabel = msg.label
            ? [{
                attrs: {
                  text: { text: msg.label, fontSize: 10, fill: strokeColor },
                  rect: { fill: '#fff', stroke: 'none', rx: 2 },
                },
                position: { distance: 0.5, offset: -12 },
              }]
            : undefined;

          if (isSelf) {
            graph.addEdge({
              id: msg.id,
              source: { x: srcLL.x + LIFELINE_WIDTH, y: msgY },
              target: { x: srcLL.x + LIFELINE_WIDTH, y: msgY + 24 },
              vertices: [
                { x: srcLL.x + LIFELINE_WIDTH + 40, y: msgY },
                { x: srcLL.x + LIFELINE_WIDTH + 40, y: msgY + 24 },
              ],
              labels: edgeLabel,
              connector: { name: 'rounded' },
              attrs: { line: lineAttrs },
            });
          } else {
            graph.addEdge({
              id: msg.id,
              source: { x: fromX, y: msgY },
              target: { x: toX, y: msgY },
              labels: edgeLabel,
              attrs: { line: lineAttrs },
            });
          }
        } catch (e) {
          console.warn('[SeqEditor] Sync message error:', msg.id, e);
        }
      });

      // Sync fragments (UML 2.5.1 combined fragments)
      const fragments = diagram.fragments || [];
      const fragIds = new Set(fragments.map((f) => f.id));
      // Remove deleted fragments
      graph.getNodes().forEach((n) => {
        if (n.shape === 'seq-fragment' && !fragIds.has(n.id)) {
          try { graph.removeCell(n.id); } catch { /* ignore */ }
        }
      });
      // Add/update fragments
      const existingFragIds = new Set(
        graph.getNodes().filter((n) => n.shape === 'seq-fragment').map((n) => n.id)
      );
      fragments.forEach((f) => {
        const label = `${f.type}${f.label ? ` ${f.label}` : ''}`;
        const w = f.width || 280;
        const yStart = Math.max(f.y_start || 80, 80);  // ensure fragment clears toolbar
        const h = Math.max(60, (f.y_end || (yStart + 120)) - yStart);
        try {
          const existing = graph.getCellById(f.id);
          if (existing && existing.isNode()) {
            (existing as Node).setPosition(f.x || 80, yStart);
            existing.setSize({ width: w, height: h });
          } else {
            graph.addNode({
              id: f.id, shape: 'seq-fragment',
              x: f.x || 80, y: yStart,
              width: w, height: h,
            });
          }
          // Always update label + style
          const fn = graph.getCellById(f.id) as Node;
          if (fn) {
            fn.setAttrByPath('labelText/html', `<span>${label}</span>`);
            fn.setAttrByPath('body/stroke', f.type === 'alt' ? '#722ed1' :
              f.type === 'loop' ? '#1890ff' : '#555');
            fn.setAttrByPath('body/strokeDasharray', f.type === 'opt' ? '4,2' : '');
          }
        } catch (e) { /* ignore */ }
      });

      prevLifelineIds.current = currentLIds;
      isInternalUpdate.current = false;

      // Center viewport as soon as the first elements appear
      if (!_didFirstSync.current && graph.getNodes().length > 0) {
        _didFirstSync.current = true;
        console.log('[SeqEditor] First sync with elements, scheduling centerContent. Nodes:', graph.getNodes().length);
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
      console.error('[SeqEditor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram, selectedLifelineId]);

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
    const g = graphRef.current;
    if (!g) return;
    console.log('[SeqEditor] recenterCounter watcher, counter:', recenterCounter, 'nodes:', g.getNodes().length);
    setTimeout(() => {
      const g2 = graphRef.current;
      if (!g2) return;
      g2.centerContent({ padding: { top: 20, right: 20, bottom: 20, left: 20 } });
      const sidebarW = useUiStore.getState().rightPanelWidth;
      const bbox = g2.getAllCellsBBox?.() || g2.getContentBBox?.() || { x: 0, y: 0, width: 0, height: 0 };
      const visibleW = g2.options.width - sidebarW;
      if (bbox.width < visibleW - 40) {
        g2.translate(g2.translate().tx - sidebarW / 2, g2.translate().ty);
      }
    }, 100);
  }, [recenterCounter]);

  // ── Floating toolbar ────────────────────────────────
  const [showToolbar, setShowToolbar] = useState(true);

  const handleAddLifeline = useCallback(() => {
    const x = 150 + Math.random() * 300;
    addLifeline(x);
  }, [addLifeline]);

  const handleAddFragment = useCallback((type: FragmentType) => {
    const store = useDiagramStore.getState();
    const msgs = store.diagram.messages || [];
    const y = msgs.length > 0
      ? Math.max(...msgs.map((m) => (m.y || 100))) + 60
      : 200;
    store.addFragment(y);
    // Set the fragment type
    const frags = store.diagram.fragments || [];
    if (frags.length > 0) {
      store.updateFragment(frags[frags.length - 1].id, {
        type,
        y_start: y,
        y_end: y + 120,
      } as any);
    }
  }, []);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* Floating toolbar */}
      {showToolbar && (
        <div style={{
          position: 'absolute', top: 8, left: 8, zIndex: 100,
          background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
          padding: '4px 6px', display: 'flex', gap: 4, alignItems: 'center',
          boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
          flexWrap: 'wrap', maxWidth: 360,
        }}>
          <Tooltip title="添加生命线">
            <Button size="small" icon={<PlusOutlined />} onClick={handleAddLifeline}>生命线</Button>
          </Tooltip>
          <span style={{ fontSize: 11, color: '#999', margin: '0 2px' }}>片段:</span>
          {(Object.keys(FRAGMENT_LABELS) as FragmentType[]).map((t) => (
            <Tooltip key={t} title={`添加 ${FRAGMENT_LABELS[t]} 片段`}>
              <Button size="small" onClick={() => handleAddFragment(t)}
                style={{ fontSize: 11, padding: '0 6px' }}>{FRAGMENT_LABELS[t]}</Button>
            </Tooltip>
          ))}
          <Button size="small" type="text"
            onClick={() => setShowToolbar(false)}
            style={{ fontSize: 10, marginLeft: 4 }}>✕</Button>
        </div>
      )}

      {!showToolbar && (
        <div style={{
          position: 'absolute', top: 8, left: 8, zIndex: 100,
        }}>
          <Button size="small" type="dashed" onClick={() => setShowToolbar(true)}>🔧</Button>
        </div>
      )}

      <div ref={containerRef} className="seq-canvas-container" />

      {/* Fragment right-click menu */}
      {ctxMenu.visible && (
        <div
          style={{
            position: 'fixed', left: ctxMenu.x, top: ctxMenu.y, zIndex: 1000,
            background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)', padding: 4, minWidth: 100,
          }}
          onClick={() => setCtxMenu({ ...ctxMenu, visible: false })}
        >
          <div
            style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, borderRadius: 4 }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f0f0')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            onClick={() => {
              const fn = graphRef.current?.getCellById(ctxMenu.nodeId);
              if (fn) (fn as Node).toBack();
            }}
          >置于底层</div>
          <div
            style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, borderRadius: 4 }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f0f0')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            onClick={() => {
              const fn = graphRef.current?.getCellById(ctxMenu.nodeId);
              if (fn) (fn as Node).toFront();
            }}
          >置于上层</div>
        </div>
      )}
      {/* Click anywhere to close menu */}
      {ctxMenu.visible && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 999 }}
          onClick={() => setCtxMenu({ ...ctxMenu, visible: false })} />
      )}
    </div>
  );
};

export default SeqEditor;
