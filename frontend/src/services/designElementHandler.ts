/**
 * 共享的 design_element 处理逻辑
 *
 * 从 AgentChat.tsx 中提取，供 Toolbar (v2) 和 AgentChat (v1) 共用。
 * 处理流式 UML 元素并在画布上实时渲染。
 */

import { useDiagramStore } from '../stores/diagramStore';

export function parseDesignElement(data: string): any | null {
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

/**
 * 处理单个 design_element 事件，将元素渲染到画布。
 *
 * 重要: ``store`` 快照不能用于读取 add* 之后的新数据 —
 * 所有"先写后读"路径统一用 ``useDiagramStore.getState()`` 实时获取最新状态。
 *
 * @param store — diagramStore 的 getState() 快照（用于调用方法）
 * @param event — { type: string, data: string }
 * @param idMap — 跨事件共享的 LLM ID → 真实 ID 映射（流式模式必须持久化）
 */
export function handleDesignElement(
  store: ReturnType<typeof useDiagramStore.getState>,
  event: { type: string; data: string },
  idMap?: Map<string, string>,
): void {
  const obj = parseDesignElement(event.data);
  if (!obj) return;

  if (!idMap) idMap = new Map<string, string>();

  const mapId = (id: string) => idMap!.get(id) || id;

  const lastOf = <T,>(arr: T[]): T | undefined => arr[arr.length - 1];

  /** Live state accessor — use after any write that needs re-reading. */
  const live = (): ReturnType<typeof useDiagramStore.getState> =>
    useDiagramStore.getState();

  // ── currentType 优化: 跳过重复的 setActiveDiagram ──
  let currentType = '';

  /** Find a diagram by type and optional name via live state. */
  const findDiagram = (type: string, name?: string) => {
    const diagrams = live().project.diagrams;
    if (name) {
      const d = diagrams.find(
        d => (d.diagram_type || 'class') === type && d.name === name
      );
      if (d) return d;
    }
    return diagrams.find(
      d => (d.diagram_type || 'class') === type
    );
  };

  /** Switch active diagram. Skips setActiveDiagram if type hasn't changed. */
  const switchTo = (type: string, name?: string) => {
    if (currentType === type) return;
    const diagrams = live().project.diagrams;
    const idx = name
      ? diagrams.findIndex(d =>
          (d.diagram_type || 'class') === type && d.name === name)
      : diagrams.findIndex(d =>
          (d.diagram_type || 'class') === type);
    if (idx < 0) {
      const autoName = name || (type === 'class' ? '类图' : type === 'sequence' ? '时序图' : '组件图');
      store.addDiagram(type, autoName);
      store.setActiveDiagram(live().project.diagrams.length - 1);
    } else {
      store.setActiveDiagram(idx);
    }
    currentType = type;
  };

  const handlers: Record<string, (o: any) => void> = {
    diagram_create: (o) => {
      const dtype = o.type || 'class';
      const dname = o.name || '';
      const existing = live().project.diagrams.findIndex(d =>
        (d.diagram_type || 'class') === dtype && d.name === dname);
      if (existing < 0) {
        store.addDiagram(dtype, dname, o.component_id || '');
      }
    },
    diagram_meta: () => { /* no-op */ },
    class: (o) => {
      const diagramName = o.diagram_name;
      switchTo('class', diagramName);
      store.addClass({ x: o.x ?? o.position?.x ?? 100, y: o.y ?? o.position?.y ?? 100 });
      const c = lastOf(findDiagram('class', diagramName)?.classes || []);
      if (c) {
        idMap.set(o.id, c.id);
        store.updateClass(c.id, {
          name: o.name || 'Class', stereotype: o.stereotype || 'class',
          attributes: o.attributes || [], methods: o.methods || [],
          note: o.note || '',
          provided_interfaces: o.provided_interfaces || [],
          required_interfaces: o.required_interfaces || [],
        });
      }
    },
    relation: (o) => {
      const diagramName = o.diagram_name;
      switchTo('class', diagramName);
      store.addRelation(mapId(o.source), mapId(o.target));
      const r = lastOf(findDiagram('class', diagramName)?.relations || []);
      if (r) {
        idMap.set(o.id, r.id);
        store.updateRelation(r.id, {
          type: o.type || 'association',
          multiplicity_source: o.multiplicity_source || '',
          multiplicity_target: o.multiplicity_target || '',
          role_name: o.role_name || '', note: o.note || '',
        });
      }
    },
    lifeline: (o) => {
      const diagramName = o.diagram_name;
      switchTo('sequence', diagramName);
      store.addLifeline(o.x ?? 200);
      const ll = lastOf(findDiagram('sequence', diagramName)?.lifelines || []);
      if (ll) {
        idMap.set(o.id, ll.id);
        store.updateLifeline(ll.id, {
          name: o.name || 'Participant', class_ref: o.class_ref || '',
          activations: o.activations || [],
        });
      }
    },
    message: (o) => {
      const diagramName = o.diagram_name;
      switchTo('sequence', diagramName);
      store.addMessage(mapId(o.from_lifeline), mapId(o.to_lifeline));
      const m = lastOf(findDiagram('sequence', diagramName)?.messages || []);
      if (m) {
        idMap.set(o.id, m.id);
        store.updateMessage(m.id, {
          label: o.label || 'message()', type: o.type || 'sync',
          order: o.order ?? 1, note: o.note || '',
        });
      }
    },
    fragment: (o) => {
      const diagramName = o.diagram_name;
      switchTo('sequence', diagramName);
      store.addFragment(o.y_start ?? 200);
      const f = lastOf(findDiagram('sequence', diagramName)?.fragments || []);
      if (f) {
        idMap.set(o.id, f.id);
        store.updateFragment(f.id, {
          type: o.type || 'loop', label: o.label || '',
          x: o.x ?? 80, width: o.width ?? 280,
          y_start: o.y_start ?? 200, y_end: o.y_end ?? 320,
        } as any);
      }
    },
    component: (o) => {
      const diagramName = o.diagram_name;
      switchTo('component', diagramName);
      store.addComponent({ x: o.x ?? 150, y: o.y ?? 100 }, o.parent_id || '');
      const comp = lastOf(findDiagram('component', diagramName)?.components || []);
      if (comp) {
        idMap.set(o.id, comp.id);
        store.updateComponent(comp.id, {
          name: o.name || 'Component', width: o.width ?? 200, height: o.height ?? 160,
          provided_interfaces: o.provided_interfaces || [],
          required_interfaces: o.required_interfaces || [],
        });
      }
    },
    comp_rel: (o) => {
      const diagramName = o.diagram_name;
      switchTo('component', diagramName);
      store.addCompRelation(mapId(o.source), mapId(o.target));
      const cr = lastOf(findDiagram('component', diagramName)?.comp_relations || []);
      if (cr) {
        idMap.set(o.id, cr.id);
        store.updateCompRelation(cr.id, { type: o.type || 'dependency' } as any);
      }
    },
    diagram_update: (o) => {
      const specs = [{ type: o.type, name: o.name, component_id: o.component_id, data: o.data }];
      store.addDiagramsFromSpec(specs);
    },
  };

  const h = handlers[event.type];
  if (h) {
    h(obj);
  }
}

/**
 * 处理 design_updated 事件，将结果推送到 DiffViewer。
 * 从 AgentChat.tsx 的设计更新逻辑提取。
 */
export function processDesignUpdated(
  diagrams: Array<{ type: string; name: string; component_id: string; data: any }>,
  consistencyReport: any[],
  uiStore: any,
  diagramStore: any,
): void {
  const originals: Record<string, any> = {};
  const optimizeds: Record<string, any> = {};
  const diffs: Record<string, string> = {};

  for (const spec of diagrams) {
    const dtype = spec.type || 'class';
    const existing = diagramStore.project.diagrams.find(
      d => (d.diagram_type || 'class') === dtype && d.name === spec.name
    );
    const dkey = `${dtype}:${spec.name || ''}`;
    const opt = spec.data ? { ...spec.data } : {};
    const orig = existing && Object.keys(existing).length > 1  // >1 排除仅含 name/type 的默认空图
      ? { ...existing }
      : null;

    // 空工程时原始版也指向优化版，diff 文案标注为新建设计
    originals[dkey] = orig || opt;
    optimizeds[dkey] = opt;
    if (orig) {
      diffs[dkey] = JSON.stringify({ before: orig, after: opt }, null, 2);
    } else {
      diffs[dkey] = `// 从需求描述全新生成此设计 ("${spec.name || dtype}")\n`
        + JSON.stringify(opt, null, 2);
    }
  }

  // 将优化结果写入画布（diagramStore），否则画布不会更新
  const specs = diagrams.map(d => ({
    type: d.type || 'class',
    name: d.name || '',
    component_id: d.component_id || '',
    data: d.data || {},
  }));
  diagramStore.addDiagramsFromSpec(specs);

  uiStore.setGlobalOptimizationResult(
    originals, optimizeds, diffs, consistencyReport || [], ''
  );
  uiStore.setRightPanelTab('diff');
  uiStore.setRightPanelVisible(true);
}
