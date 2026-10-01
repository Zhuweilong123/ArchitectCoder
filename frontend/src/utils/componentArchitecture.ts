import type { CompNode } from '../types/component';
import { orderArchitectureGraph, type ArchitectureEdge } from './architectureGraph';

export interface ComponentSize { width: number; height: number }
export interface ComponentBlock extends ComponentSize {
  positions: Map<string, { x: number; y: number }>;
}

/** Containment is handled by the caller; unrelated dependency graphs stay separate. */
export function componentGroups(ids: string[], edges: ArchitectureEdge[]): string[][] {
  const owner = new Map(ids.map((id) => [id, id]));
  const root = (id: string): string => {
    const parent = owner.get(id)!;
    if (parent !== id) owner.set(id, root(parent));
    return owner.get(id)!;
  };
  edges.forEach(({ source, target }) => {
    if (owner.has(source) && owner.has(target)) owner.set(root(target), root(source));
  });
  const groups = new Map<string, string[]>();
  ids.forEach((id) => {
    const key = root(id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(id);
  });
  return [...groups.values()];
}

function place(block: ComponentBlock, other: ComponentBlock, x: number, y: number): void {
  other.positions.forEach((point, id) => block.positions.set(id, { x: x + point.x, y: y + point.y }));
  block.width = Math.max(block.width, x + other.width);
  block.height = Math.max(block.height, y + other.height);
}

export function componentGrid(
  ids: string[], sizes: Map<string, ComponentSize>, columns = 3, gap = 32,
): ComponentBlock {
  const count = Math.min(columns, ids.length);
  const widths = Array.from({ length: count }, () => 0);
  const heights: number[] = [];
  ids.forEach((id, index) => {
    const size = sizes.get(id)!;
    widths[index % count] = Math.max(widths[index % count], size.width);
    const row = Math.floor(index / count);
    heights[row] = Math.max(heights[row] || 0, size.height);
  });
  const positions = new Map<string, { x: number; y: number }>();
  ids.forEach((id, index) => positions.set(id, {
    x: widths.slice(0, index % count).reduce((sum, width) => sum + width + gap, 0),
    y: heights.slice(0, Math.floor(index / count)).reduce((sum, height) => sum + height + gap, 0),
  }));
  return {
    positions,
    width: widths.reduce((sum, width) => sum + width, 0) + Math.max(0, count - 1) * gap,
    height: heights.reduce((sum, height) => sum + height, 0) + Math.max(0, heights.length - 1) * gap,
  };
}

/** Preserve dependency levels inside a cluster, rather than flattening them into a grid. */
export function componentLayers(
  ids: string[], edges: ArchitectureEdge[], sizes: Map<string, ComponentSize>,
  gapX = 140, gapY = 100,
): ComponentBlock {
  const order = new Map(ids.map((id, index) => [id, index]));
  const { rows } = orderArchitectureGraph(ids, edges, order);
  const levels = [...rows.keys()].sort((a, b) => a - b);
  const block: ComponentBlock = { width: 0, height: 0, positions: new Map() };
  // Long chains use consecutive bands, with at most three columns in each.
  for (let band = 0; band < levels.length; band += 3) {
    const columns = levels.slice(band, band + 3).map((level) => (
      componentGrid(rows.get(level)!, sizes, 1, gapY)
    ));
    const height = Math.max(...columns.map((column) => column.height));
    const top = block.height ? block.height + gapY : 0;
    let x = 0;
    columns.forEach((column) => {
      place(block, column, x, top + (height - column.height) / 2);
      x += column.width + gapX;
    });
  }
  return block;
}

export function packComponentBlocks(blocks: ComponentBlock[], gap = 100): ComponentBlock {
  const block: ComponentBlock = { width: 0, height: 0, positions: new Map() };
  const area = blocks.reduce((sum, item) => sum + item.width * item.height, 0);
  const targetWidth = Math.max(0, ...blocks.map((item) => item.width), Math.sqrt(area * 1.6));
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  blocks.forEach((item) => {
    if (x && x + item.width > targetWidth) {
      x = 0;
      y += rowHeight + gap;
      rowHeight = 0;
    }
    place(block, item, x, y);
    x += item.width + gap;
    rowHeight = Math.max(rowHeight, item.height);
  });
  return block;
}

type ComponentRole = 'entry' | 'service' | 'observer' | 'foundation';

/** Roles are layout hints, not new UML relationships or inferred software layers. */
export function componentRoles(components: CompNode[], edges: ArchitectureEdge[]): Map<string, ComponentRole> {
  const incoming = new Map(components.map(({ id }) => [id, new Set<string>()]));
  const outgoing = new Map(components.map(({ id }) => [id, new Set<string>()]));
  edges.forEach(({ source, target }) => {
    if (source !== target && incoming.has(target) && outgoing.has(source)) {
      incoming.get(target)!.add(source);
      outgoing.get(source)!.add(target);
    }
  });
  const cyclic = (id: string) => {
    const seen = new Set<string>();
    const visit = (next: string): boolean => {
      if (seen.has(next)) return false;
      seen.add(next);
      return [...outgoing.get(next) || []].some((target) => target === id || visit(target));
    };
    return visit(id);
  };
  return new Map(components.map((component) => {
    const inputs = incoming.get(component.id)!.size;
    const outputs = outgoing.get(component.id)!.size;
    const name = component.name.toLowerCase();
    const interfaces = (component.provided_interfaces || []).join(' ').toLowerCase();
    let role: ComponentRole = 'service';
    if (!cyclic(component.id)) {
      if (!inputs && outputs >= 3) role = 'entry';
      else if (!outputs && inputs >= 2 && !(component.required_interfaces || []).length) role = 'foundation';
      else if (/\b(analysis|monitoring|monitor|evaluation|telemetry|observability)\b|分析|监测|评估/.test(name)
        || /metrics|telemetry|统计|指标/.test(interfaces)) role = 'observer';
      else if (!inputs && outputs && /\b(adapter|gateway|frontend|entry)\b|适配|网关|入口/.test(name)) role = 'entry';
    }
    return [component.id, role];
  }));
}

/** Put observers beside their consumers and shared providers below the service graph. */
export function componentArchitectureBlock(
  components: CompNode[], edges: ArchitectureEdge[], sizes: Map<string, ComponentSize>,
): ComponentBlock {
  const roles = componentRoles(components, edges);
  const ids = (role: ComponentRole) => components.filter((component) => roles.get(component.id) === role).map(({ id }) => id);
  const services = ids('service');
  if (!services.length) return componentLayers(components.map(({ id }) => id), edges, sizes);
  const groups = componentGroups(services, edges);
  const business = packComponentBlocks(groups.map((group) => componentLayers(group, edges, sizes)));
  const sideIds = [...ids('entry'), ...ids('observer')];
  const side = componentGrid(sideIds, sizes, 1, 100);
  const block: ComponentBlock = { width: 0, height: 0, positions: new Map() };
  const businessX = side.width ? side.width + 140 : 0;
  place(block, business, businessX, 0);
  if (sideIds.length) place(block, side, 0, Math.max(0, (business.height - side.height) / 2));
  const foundations = componentGrid(ids('foundation'), sizes, 2, 100);
  if (foundations.positions.size) {
    const x = Math.max(businessX, block.width - foundations.width);
    const bottom = Math.max(0, ...services.filter((id) => {
      const point = block.positions.get(id)!;
      return point.x < x + foundations.width && point.x + sizes.get(id)!.width > x;
    }).map((id) => block.positions.get(id)!.y + sizes.get(id)!.height));
    place(block, foundations, x, bottom + 100);
  }
  return block;
}
