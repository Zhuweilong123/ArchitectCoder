import { useEffect, useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useUiStore } from '../../stores/uiStore';
import { selectActiveDiagram, useDiagramStore } from '../../stores/diagramStore';
import { useReviewStore } from '../../stores/reviewStore';
import { findDiagramComparison, getDiagramChanges, type DesignChange, type MemberChange } from '../../utils/designChanges';
import { getActiveCanvasGraph } from './core/canvasRegistry';
import './DesignChangeOverlay.css';

const SVG_NS = 'http://www.w3.org/2000/svg';
const colors = { added: '#16803d', modified: '#b45309', removed: '#c62828' };
const labels = { added: '新增', modified: '修改', removed: '删除' };
const english = { added: 'Added', modified: 'Changed', removed: 'Removed' };

function clearHighlights(container: Element) {
  container.querySelectorAll('[data-design-change]').forEach((element) => {
    element.removeAttribute('data-design-change');
    element.removeAttribute('data-design-change-kind');
    (element as HTMLElement).style.removeProperty('--design-change-color');
  });
  container.querySelectorAll('[data-design-change-line]').forEach((element) => element.removeAttribute('data-design-change-line'));
  container.querySelectorAll('[data-design-review-decoration]').forEach((element) => element.remove());
}

function decorate(container: Element, changes: DesignChange[], optimized: boolean, zh: boolean) {
  const graph = getActiveCanvasGraph();
  if (!graph) return;
  const cells = new Map(Array.from(container.querySelectorAll<SVGGElement>('[data-cell-id]')).map((e) => [e.getAttribute('data-cell-id'), e]));
  for (const change of changes) {
    if ((optimized && change.status === 'removed') || (!optimized && change.status === 'added')) continue;
    const cell = cells.get(change.id);
    if (!cell) continue;
    cell.setAttribute('data-design-change', change.status);
    cell.setAttribute('data-design-change-kind', change.kind);
    cell.style.setProperty('--design-change-color', colors[change.status]);
    const model = graph.getCellById(change.id);
    if (model?.isEdge()) {
      cell.querySelectorAll('path[stroke]').forEach((path) => {
        if (path.getAttribute('stroke') !== 'transparent') path.setAttribute('data-design-change-line', change.status);
      });
    } else if (model?.isNode()) {
      // Put the badge above the box so it never obscures a title or member.
      const badge = document.createElementNS(SVG_NS, 'g');
      badge.setAttribute('data-design-review-decoration', 'badge');
      badge.setAttribute('pointer-events', 'none');
      const width = zh ? 38 : 62;
      const x = Math.max(0, model.getSize().width - width);
      const rect = document.createElementNS(SVG_NS, 'rect');
      Object.entries({ x, y: -19, width, height: 16, rx: 4, fill: colors[change.status] }).forEach(([key, val]) => rect.setAttribute(key, String(val)));
      const text = document.createElementNS(SVG_NS, 'text');
      Object.entries({ x: x + width / 2, y: -7, fill: '#fff', 'font-size': 10, 'font-family': 'sans-serif', 'text-anchor': 'middle' }).forEach(([key, val]) => text.setAttribute(key, String(val)));
      text.textContent = (zh ? labels : english)[change.status];
      badge.append(rect, text);
      cell.append(badge);
    }
    for (const section of ['attributes', 'methods', 'provided', 'required'] as const) {
      const members = change.members.filter((member) => member.section === section);
      const selector = section === 'attributes' ? '.uml-attr' : section === 'methods' ? '.uml-method'
        : `.comp-iface.${section}, .uml-iface.${section}`;
      const rows = Array.from(cell.querySelectorAll<HTMLElement>(selector));
      const hidden: MemberChange[] = [];
      for (const member of members) {
        const text = optimized ? member.afterText : member.beforeText;
        if (!text) continue;
        const row = rows.find((element) => section === 'attributes' || section === 'methods'
          ? element.getAttribute('data-member-text') === text
          : element.textContent?.includes(text));
        if (row) {
          row.setAttribute('data-design-change', member.status);
          row.style.setProperty('--design-change-color', colors[member.status]);
        } else hidden.push(member);
      }
      const toggle = cell.querySelector(`.uml-member-toggle[data-member-section="${section}"]`);
      if (toggle && hidden.length) {
        const hint = document.createElement('span');
        hint.setAttribute('data-design-review-decoration', 'hidden-count');
        hint.textContent = zh ? ` · ${hidden.length} 项变更` : ` · ${hidden.length} changes`;
        toggle.append(hint);
      }
    }
  }
}

export default function DesignChangeOverlay() {
  const diagram = useDiagramStore(selectActiveDiagram);
  const ui = useUiStore(useShallow((s) => ({
    originals: s.originalDiagrams, optimizeds: s.optimizedDiagrams,
    original: s.originalDiagram, optimized: s.optimizedDiagram,
    showingOptimized: s.showingOptimized, comparison: s.designComparisonVisible,
    language: s.interfaceLanguage,
  })));
  const pending = useReviewStore((s) => s.status === 'pending' && s.reviewType === 'uml_diff');
  const [enabled, setEnabled] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const pair = useMemo(() => findDiagramComparison(diagram, ui.originals, ui.optimizeds, ui.original, ui.optimized),
    [diagram, ui.originals, ui.optimizeds, ui.original, ui.optimized]);
  const changes = useMemo(() => pair ? getDiagramChanges(pair.original, pair.optimized) : [], [pair]);
  const active = !!pair && (ui.comparison || pending);
  const zh = ui.language === 'zh';
  // The toolbar may change diagrams independently of the diff tab. Infer the
  // displayed version from its semantic content when the snapshots differ.
  const optimized = useMemo(() => pair && getDiagramChanges(pair.original, diagram, false).length === 0 ? false
    : pair && getDiagramChanges(pair.optimized, diagram, false).length === 0 ? true : ui.showingOptimized,
  [pair, diagram, ui.showingOptimized]);

  useEffect(() => {
    const container = panelRef.current?.closest('.app-content');
    if (!container) return;
    clearHighlights(container);
    if (!active || !enabled) return;
    let frame = 0;
    const apply = () => {
      frame = 0;
      clearHighlights(container);
      decorate(container, changes, !!optimized, zh);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(apply); };
    const observer = new MutationObserver((mutations) => {
      const meaningful = mutations.some((mutation) => {
        const target = mutation.target instanceof Element ? mutation.target : mutation.target.parentElement;
        if (target?.closest('[data-design-review-decoration], .design-change-panel')) return false;
        return [...mutation.addedNodes, ...mutation.removedNodes].some((node) => !(node instanceof Element && node.hasAttribute('data-design-review-decoration')));
      });
      if (meaningful) schedule();
    });
    observer.observe(container, { childList: true, subtree: true });
    schedule();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); clearHighlights(container); };
  }, [active, enabled, changes, optimized, zh]);

  const showVersion = (after: boolean) => {
    if (!pair) return;
    useDiagramStore.getState().setDiagram(after ? pair.optimized : pair.original);
    useUiStore.getState().setShowingOptimized(after);
    useDiagramStore.getState().triggerRecenter('fit');
  };
  const locate = (change: DesignChange, member?: MemberChange) => {
    const after = member ? member.status !== 'removed' : change.status !== 'removed';
    if (after !== !!optimized) showVersion(after);
    if (member && (member.section === 'attributes' || member.section === 'methods')) {
      const store = useDiagramStore.getState();
      const cls = selectActiveDiagram(store).classes.find((item) => item.id === change.id);
      const open = member.section === 'attributes' ? cls?.expanded_attributes : cls?.expanded_methods;
      if (cls && !open) store.toggleClassMembers(cls.id, member.section);
    }
    let tries = 0;
    const focus = () => {
      const graph = getActiveCanvasGraph();
      const cell = graph?.getCellById(change.id);
      if (cell && graph?.findViewByCell(cell)) {
        graph.centerCell(cell);
        graph.select(cell);
      } else if (++tries < 20) requestAnimationFrame(focus);
    };
    requestAnimationFrame(focus);
  };
  if (!active) return <div ref={panelRef} hidden />;
  return <div ref={panelRef} className="design-change-panel" aria-label={zh ? '设计变更对比' : 'Design changes'}>
    <div className="design-change-controls">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{zh ? `变更 ${changes.length}` : `Changes ${changes.length}`} {expanded ? '▴' : '▾'}</button>
      <label><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{zh ? '高亮' : 'Highlight'}</label>
    </div>
    <div className="design-change-legend" title={zh ? '元素数量，成员变化列在对应元素下方' : 'Element counts. Member changes are listed below each element.'}>{(['added', 'modified', 'removed'] as const).map((status) => <span key={status} style={{ color: colors[status] }}>
      {(zh ? labels : english)[status]} {changes.filter((c) => c.status === status).length}
    </span>)}</div>
    {expanded && <>
      <div className="design-change-versions">
        <button type="button" aria-pressed={!optimized} onClick={() => showVersion(false)}>{zh ? '优化前' : 'Before'}</button>
        <button type="button" aria-pressed={!!optimized} onClick={() => showVersion(true)}>{zh ? '优化后' : 'After'}</button>
      </div>
      <div className="design-change-list">
        {changes.length === 0 && <p>{zh ? '没有设计内容变更，布局调整不计入。' : 'No design changes. Layout adjustments are excluded.'}</p>}
        {changes.map((change) => <div key={`${change.kind}:${change.id}`}>
          <button type="button" className="design-change-item" onClick={() => locate(change)}><span style={{ color: colors[change.status] }}>{(zh ? labels : english)[change.status]}</span> {change.label}</button>
          {change.members.map((member, i) => <button type="button" className="design-change-member" key={i} onClick={() => locate(change, member)}>
            <span style={{ color: colors[member.status] }}>{(zh ? labels : english)[member.status]}</span> {member.label}
          </button>)}
        </div>)}
      </div>
      <p className="design-change-help">{zh ? '点击变更定位；删除项在优化前版本中查看。' : 'Click to locate. Removed items appear in the before version.'}</p>
    </>}
  </div>;
}
