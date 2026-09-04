/**
 * Sequence Diagram Editor — lifelines + messages powered by AntV X6.
 * Reuses the same X6 patterns as UMLEditor (isInternalUpdate, sync effect, etc.)
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import { Graph, Node, Edge } from '@antv/x6';
import { useShallow } from 'zustand/react/shallow';
import { Button, Select, Tooltip } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { getActiveDiagram, selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useUiStore, type CanvasTheme } from '../../stores/uiStore';
import { attachGraphViewport } from './graphViewport';
import { createCanvasGraph } from './core/createCanvasGraph';
import { snapCanvasPosition } from './core/snapToGrid';
import { centerCanvasContent, syncCanvasGrid, syncCanvasViewport } from './core/canvasCommon';
import type { SeqLifeline, SeqMessage, MessageType } from '../../types/sequence';
import { MESSAGE_TYPE_LABELS, FRAGMENT_LABELS, type FragmentType } from '../../types/sequence';
import './SeqEditor.css';
import { escapeHtml } from '../../utils/safeHtml';

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
        stroke: 'transparent', strokeWidth: 0, fill: 'transparent',
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

function buildLifelineHTML(lifeline: SeqLifeline, selected: boolean, theme: CanvasTheme): string {
  const selClass = selected ? 'selected' : '';
  const bars = (lifeline.activations || []).map((y, i) =>
    `<div class="seq-activation" style="top:${y - 6}px" title="激活条 #${i + 1}"></div>`
  ).join('');
  const hint = selected
    ? '<div class="seq-click-hint">▼ 已选中，点击另一生命线创建消息 ▼</div>'
    : '';
  return `<div class="seq-lifeline-node theme-${theme} ${selClass}">
    <div class="seq-lifeline-name">${escapeHtml(lifeline.name)}</div>
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
  const [messageMode, setMessageMode] = useState<MessageType>('sync');
  const messageModeRef = useRef<MessageType>('sync');

  const {
    diagram, selectedLifelineId, selectedMessageId,
    addLifeline, removeLifeline, moveLifeline,
    selectLifeline, selectMessage,
    undo, redo, arrangeSequence,
  } = useDiagramStore(useShallow((s) => ({
    diagram: selectActiveDiagram(s),
    selectedLifelineId: s.selectedLifelineId,
    selectedMessageId: s.selectedMessageId,
    addLifeline: s.addLifeline,
    removeLifeline: s.removeLifeline,
    moveLifeline: s.moveLifeline,
    selectLifeline: s.selectLifeline,
    selectMessage: s.selectMessage,
    undo: s.undo,
    redo: s.redo,
    arrangeSequence: s.arrangeSequence,
  })));
  const viewport = useDiagramStore((s) => s.viewport);

  const { setRightPanelTab, canvasTheme } = useUiStore();

  // ── Canvas context menu ────────────────────────────
  const [ctxMenu, setCtxMenu] = useState<{
    visible: boolean;
    x: number;
    y: number;
    nodeId: string;
    kind: 'fragment' | 'lifeline';
    messageY: number;
  }>({ visible: false, x: 0, y: 0, nodeId: '', kind: 'fragment', messageY: 0 });

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
    });

    const detachViewport = attachGraphViewport(graph, {
      container: containerRef.current,
      zoom: viewport.zoom,
      panX: viewport.panX,
      panY: viewport.panY,
      onZoom: (zoom) => useDiagramStore.getState().setZoom(zoom),
      onPan: (x, y) => useDiagramStore.getState().setPan(x, y),
    });

    // Click-to-click message creation (lifelines only)
    graph.on('node:click', ({ node }) => {
      if (node.shape === 'seq-fragment') {
        (graph as any).__selectedFragment = node.id;
        return;
      }
      const store = useDiagramStore.getState();
      if (store.selectedLifelineId && store.selectedLifelineId !== node.id) {
        store.addMessage(store.selectedLifelineId, node.id, messageModeRef.current);
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

    // Right-click on a fragment or lifeline → context menu
    graph.on('node:contextmenu', ({ node, e }: any) => {
      const evt = e.evt || e;
      const clientX = evt?.clientX || evt?.pageX || 0;
      const clientY = evt?.clientY || evt?.pageY || 0;
      if (node.shape === 'seq-fragment' || node.shape === 'seq-lifeline') {
        evt?.preventDefault?.();
        const bbox = node.getBBox();
        const localPoint = graph.clientToLocal(clientX, clientY);
        setCtxMenu({
          visible: true,
          x: clientX,
          y: clientY,
          nodeId: node.id,
          kind: node.shape === 'seq-lifeline' ? 'lifeline' : 'fragment',
          messageY: node.shape === 'seq-lifeline'
            ? Math.max(bbox.y + 30, Math.min(localPoint.y, bbox.y + bbox.height - 30))
            : 0,
        });
      }
    });

    // Fragment move: keep height constant by shifting both y_start and y_end
    let fragMoved = false;
    graph.on('node:moved', ({ node }) => {
      if (node.shape === 'seq-fragment' && !isInternalUpdate.current) {
        fragMoved = true;
        const h = node.size().height;
        const store = useDiagramStore.getState();
        const position = node.position();
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
        useDiagramStore.getState().updateFragment(node.id, {
          x: nextPosition.x,
          y_start: nextPosition.y,
          y_end: nextPosition.y + h,
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
      if (isInternalUpdate.current || node.shape !== 'seq-lifeline') return;
      const position = node.position();
      const nextPosition = snapCanvasPosition(
        { x: position.x, y: position.y },
        getActiveDiagram().snap_to_grid,
        getActiveDiagram().grid_size,
      );
      // Lifelines share one horizontal baseline. Only persist X; any
      // accidental vertical drag is immediately snapped back to it.
      if (position.x !== nextPosition.x || position.y !== LIFELINE_Y) {
        isInternalUpdate.current = true;
        node.setPosition(nextPosition.x, LIFELINE_Y);
        isInternalUpdate.current = false;
      }
      moveLifeline(node.id, nextPosition.x);
    });

    graph.on('edge:click', ({ edge }) => {
      selectMessage(edge.id);
      setRightPanelTab('properties');
    });

    // Save edge Y position when dragged
    graph.on('edge:change:source', ({ edge, current }) => {
      if (isInternalUpdate.current) return;
      if (typeof (current as any)?.y === 'number') {
        const store = useDiagramStore.getState();
        store.updateMessage(edge.id, { y: (current as any).y } as any);
      }
    });
    graph.on('edge:change:target', ({ edge, current }) => {
      if (isInternalUpdate.current) return;
      if (typeof (current as any)?.y === 'number') {
        const store = useDiagramStore.getState();
        store.updateMessage(edge.id, { y: (current as any).y } as any);
      }
    });
    // Also capture vertex moves for self-messages
    graph.on('edge:change:vertices', ({ edge, current }) => {
      if (isInternalUpdate.current) return;
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
      const key = e.key.toLowerCase();
      const modifier = e.ctrlKey || e.metaKey;

      if (modifier && key === 'c') {
        if (store.selectedLifelineId) {
          const ll = (getActiveDiagram().lifelines || []).find((l) => l.id === store.selectedLifelineId);
          if (ll) clipboard.current = JSON.parse(JSON.stringify(ll));
        }
      } else if (modifier && key === 'v') {
        e.preventDefault();
        if (clipboard.current) {
          const c = clipboard.current;
          store.beginBatch();
          try {
            store.addLifeline(c.x + 30);
            // Apply copied name
            const store2 = useDiagramStore.getState();
            const lls = getActiveDiagram().lifelines || [];
            const pasted = lls[lls.length - 1];
            if (pasted) {
              store2.updateLifeline(pasted.id, {
                name: c.name, class_ref: c.class_ref,
                activations: [...(c.activations || [])],
              });
            }
          } finally {
            store.endBatch();
          }
        }
      } else if (modifier && key === 'z' && !e.shiftKey) {
        e.preventDefault(); store.undo();
      } else if (modifier && (key === 'y' || (key === 'z' && e.shiftKey))) {
        e.preventDefault(); store.redo();
      } else if (key === 'escape') {
        e.preventDefault();
        graph.cleanSelection();
        selectLifeline(null);
        selectMessage(null);
        (graph as any).__selectedFragment = null;
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        // Delete selected element
        const fragId = (graph as any).__selectedFragment;
        const targetIds = {
          fragment: fragId || '',
          message: store.selectedMessageId || '',
          lifeline: store.selectedLifelineId || '',
        };
        if (targetIds.fragment || targetIds.message || targetIds.lifeline) {
          e.preventDefault();
          store.beginBatch();
          try {
            if (targetIds.fragment) store.removeFragment(targetIds.fragment);
            else if (targetIds.message) store.removeMessage(targetIds.message);
            else if (targetIds.lifeline) store.removeLifeline(targetIds.lifeline);
          } finally {
            store.endBatch();
          }
          selectLifeline(null);
          selectMessage(null);
          (graph as any).__selectedFragment = null;
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    graphRef.current = graph;
    if (!(viewport.panX || viewport.panY) && viewport.zoom === 1) {
      graph.centerContent();
    }
    console.log('[SeqEditor] Graph initialized');

    return () => {
      _didFirstSync.current = false;  // reset for StrictMode remount
      document.removeEventListener('keydown', handleKeyDown);
      detachViewport();
      try { graph.dispose(); } catch { /* ignore */ }
      graphRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync diagram → graph ───────────────────────────
  const prevLifelineIds = useRef<Set<string>>(new Set());
  const htmlCache = useRef<Map<string, string>>(new Map());
  const renderCache = useRef<Map<string, { entity: SeqLifeline; selected: boolean; theme: CanvasTheme; html: string }>>(new Map());
  const lifelineSignatureCache = useRef<Map<string, string>>(new Map());
  const messageSignatureCache = useRef<Map<string, string>>(new Map());
  const fragmentSignatureCache = useRef<Map<string, string>>(new Map());
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
          renderCache.current.delete(id);
          lifelineSignatureCache.current.delete(id);
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
        const selected = ll.id === selectedLifelineId;
        const cachedRender = renderCache.current.get(ll.id);
        const htmlContent = cachedRender?.entity === ll && cachedRender.selected === selected
          && cachedRender.theme === canvasTheme
          ? cachedRender.html
          : buildLifelineHTML(ll, selected, canvasTheme);
        const cached = htmlCache.current.get(ll.id);
        const signature = JSON.stringify([
          htmlContent, ll.x, LIFELINE_Y, LIFELINE_WIDTH, neededHeight,
        ]);
        renderCache.current.set(ll.id, { entity: ll, selected, theme: canvasTheme, html: htmlContent });
        try {
          const existing = graph.getCellById(ll.id);
          if (existing && existing.isNode()) {
            if (lifelineSignatureCache.current.get(ll.id) === signature) return;
            const node = existing as Node;
            node.setPosition(ll.x, LIFELINE_Y);
            node.setSize({ width: LIFELINE_WIDTH, height: neededHeight });
            node.setAttrs({
              body: {
                stroke: 'transparent',
                strokeWidth: 0,
                fill: 'transparent',
              },
            });
            if (cached !== htmlContent) {
              node.setAttrByPath('content/html', htmlContent);
              htmlCache.current.set(ll.id, htmlContent);
            }
            lifelineSignatureCache.current.set(ll.id, signature);
          } else {
            const node = graph.addNode({
              id: ll.id,
              shape: 'seq-lifeline',
              x: ll.x, y: LIFELINE_Y,
              width: LIFELINE_WIDTH, height: neededHeight,
              attrs: {
                content: { html: htmlContent },
                body: {
                  stroke: 'transparent',
                  strokeWidth: 0,
                  fill: 'transparent',
                },
              },
            });
            htmlCache.current.set(ll.id, htmlContent);
            if (node) lifelineSignatureCache.current.set(ll.id, signature);
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
          messageSignatureCache.current.delete(id);
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

        // Connect to the visual axis of each lifeline instead of the outer
        // node boundary. This makes messages feel anchored to the dashed line.
        const sourceAxisX = srcLL.x + LIFELINE_WIDTH / 2;
        const targetAxisX = tgtLL.x + LIFELINE_WIDTH / 2;
        let fromX: number, toX: number;
        if (isSelf) {
          fromX = sourceAxisX;
          toX = sourceAxisX;
        } else {
          fromX = sourceAxisX;
          toX = targetAxisX;
        }

        const isSelected = msg.id === selectedMessageId;
        let strokeColor = '#1890ff';
        let strokeDash = '';
        if (msg.type === 'return') { strokeColor = '#888'; strokeDash = '6,3'; }
        else if (msg.type === 'simple') { strokeColor = '#333'; }
        else if (msg.type === 'async') { strokeColor = '#52c41a'; }
        if (isSelected) strokeColor = '#2563eb';

        const lineAttrs: Record<string, unknown> = {
          stroke: strokeColor,
          strokeWidth: isSelected ? 2.5 : 2,
          strokeDasharray: strokeDash,
          targetMarker: { name: 'block', width: 10, height: 6 },
        };
        const signature = JSON.stringify([
          srcLL.x, tgtLL.x, msgY, isSelf, msg.label, msg.type, strokeColor, strokeDash,
          isSelected, canvasTheme,
        ]);

        try {
          const existing = graph.getCellById(msg.id);
          if (existing && existing.isEdge()) {
            if (messageSignatureCache.current.get(msg.id) === signature) return;
            // Update existing edge: positions, vertices, style, label
            const edge = existing as Edge;
            edge.setSource({ x: fromX, y: msgY });
            edge.setTarget({ x: toX, y: isSelf ? msgY + 24 : msgY });
            if (isSelf) {
              edge.setVertices([
                { x: sourceAxisX + 40, y: msgY },
                { x: sourceAxisX + 40, y: msgY + 24 },
              ]);
            } else {
              edge.setVertices([]);
            }
            edge.setLabels(msg.label ? [{
              attrs: {
                text: { text: msg.label, fontSize: 10, fill: strokeColor },
                rect: { fill: canvasTheme === 'dark' ? '#172033' : '#fff', stroke: 'none', rx: 3 },
              },
              position: { distance: 0.5, offset: -12 },
            }] : []);
            edge.setAttrByPath('line/stroke', strokeColor);
            edge.setAttrByPath('line/strokeWidth', isSelected ? 2.5 : 2);
            edge.setAttrByPath('line/strokeDasharray', strokeDash);
            messageSignatureCache.current.set(msg.id, signature);
            return;
          }

          // New edge
          const edgeLabel = msg.label
            ? [{
                attrs: {
                  text: { text: msg.label, fontSize: 10, fill: strokeColor },
                  rect: { fill: canvasTheme === 'dark' ? '#172033' : '#fff', stroke: 'none', rx: 3 },
                },
                position: { distance: 0.5, offset: -12 },
              }]
            : undefined;

          if (isSelf) {
            const edge = graph.addEdge({
              id: msg.id,
              source: { x: sourceAxisX, y: msgY },
              target: { x: sourceAxisX, y: msgY + 24 },
              vertices: [
                { x: sourceAxisX + 40, y: msgY },
                { x: sourceAxisX + 40, y: msgY + 24 },
              ],
              labels: edgeLabel,
              connector: { name: 'rounded' },
              attrs: { line: lineAttrs },
            });
            if (edge) messageSignatureCache.current.set(msg.id, signature);
          } else {
            const edge = graph.addEdge({
              id: msg.id,
              source: { x: fromX, y: msgY },
              target: { x: toX, y: msgY },
              labels: edgeLabel,
              attrs: { line: lineAttrs },
            });
            if (edge) messageSignatureCache.current.set(msg.id, signature);
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
          fragmentSignatureCache.current.delete(n.id);
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
        const stroke = f.type === 'alt' ? '#722ed1' : f.type === 'loop' ? '#1890ff' : '#555';
        const dash = f.type === 'opt' ? '4,2' : '';
        const signature = JSON.stringify([label, f.x || 80, yStart, w, h, stroke, dash]);
        try {
          const existing = graph.getCellById(f.id);
          if (existing && existing.isNode()
            && fragmentSignatureCache.current.get(f.id) === signature) return;
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
            fn.setAttrByPath('labelText/html', `<span>${escapeHtml(label)}</span>`);
            fn.setAttrByPath('body/stroke', stroke);
            fn.setAttrByPath('body/strokeDasharray', dash);
            // Fragments are visual containers. Keep them behind lifelines and
            // message edges so elements inside a loop remain selectable.
            fn.toBack();
            fragmentSignatureCache.current.set(f.id, signature);
          }
        } catch (e) { /* ignore */ }
      });

      prevLifelineIds.current = currentLIds;
      isInternalUpdate.current = false;

      // Center viewport as soon as the first elements appear
      if (
        !_didFirstSync.current
        && graph.getNodes().length > 0
        && !(viewport.panX || viewport.panY)
        && viewport.zoom === 1
      ) {
        _didFirstSync.current = true;
        console.log('[SeqEditor] First sync with elements, scheduling centerContent. Nodes:', graph.getNodes().length);
        setTimeout(() => {
          const g = graphRef.current;
          if (!g) return;
          centerCanvasContent(g, useUiStore.getState().rightPanelWidth);
        }, 200);
      }

    } catch (err) {
      console.error('[SeqEditor] Sync error:', err);
      isInternalUpdate.current = false;
    }
  }, [diagram.lifelines, diagram.messages, diagram.fragments, selectedLifelineId, selectedMessageId, canvasTheme]);

  // ── Apply store zoom to the graph (toolbar zoom buttons) ──
  // Epsilon guard breaks the zoomTo → scale event → setZoom → effect loop.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    syncCanvasViewport(graph, viewport);
  }, [viewport.zoom]);

  // Restore persisted translation when switching diagrams or loading a project.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    syncCanvasViewport(graph, viewport);
  }, [viewport.panX, viewport.panY]);

  // ── Sync grid settings ─────────────────────────────
  useEffect(() => {
    const graph = graphRef.current as any;
    if (!graph) return;
    syncCanvasGrid(graph, {
      visible: diagram.grid_visible !== false,
      size: diagram.grid_size || 20,
      color: diagram.grid_color || '#e0e0e0',
      thickness: diagram.grid_thickness || 1,
    });
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
      centerCanvasContent(g2, useUiStore.getState().rightPanelWidth);
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
    const msgs = getActiveDiagram().messages || [];
    const y = msgs.length > 0
      ? Math.max(...msgs.map((m) => (m.y || 100))) + 60
      : 200;
    store.addFragment(y);
    // Set the fragment type
    const frags = getActiveDiagram().fragments || [];
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
          <Tooltip title="先选择消息类型，再依次点击发送方和接收方生命线">
            <Select
              size="small"
              value={messageMode}
              onChange={(value: MessageType) => {
                messageModeRef.current = value;
                setMessageMode(value);
              }}
              options={(Object.keys(MESSAGE_TYPE_LABELS) as MessageType[]).map((type) => ({
                value: type,
                label: MESSAGE_TYPE_LABELS[type],
              }))}
              style={{ width: 104 }}
            />
          </Tooltip>
          {(diagram.lifelines || []).length > 0 && (
            <Tooltip title="均匀排列生命线并整理消息时间轴">
              <Button size="small" onClick={arrangeSequence}>整理</Button>
            </Tooltip>
          )}
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

      <div ref={containerRef} className={`seq-canvas-container theme-${canvasTheme}`} />

      {/* Canvas right-click menu */}
      {ctxMenu.visible && (
        <div
          style={{
            position: 'fixed', left: ctxMenu.x, top: ctxMenu.y, zIndex: 1000,
            background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6,
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)', padding: 4, minWidth: 100,
          }}
          onClick={() => setCtxMenu({ ...ctxMenu, visible: false })}
        >
          {ctxMenu.kind === 'lifeline' && (
            <div
              style={{ padding: '4px 12px', cursor: 'pointer', fontSize: 12, borderRadius: 4 }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#f0f0f0')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              onClick={() => {
                useDiagramStore.getState().addMessage(
                  ctxMenu.nodeId,
                  ctxMenu.nodeId,
                  'self',
                  ctxMenu.messageY,
                );
              }}
            >添加自反消息</div>
          )}
          {ctxMenu.kind === 'fragment' && (
            <>
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
            </>
          )}
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
