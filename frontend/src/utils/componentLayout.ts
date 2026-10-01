import type { UmlDiagram } from '../types/uml';
import type { CompNode } from '../types/component';
import type { ArchitectureEdge } from './architectureGraph';
import {
  componentArchitectureBlock, componentGrid, componentGroups, componentLayers, packComponentBlocks,
  type ComponentBlock, type ComponentSize,
} from './componentArchitecture';

/** Space occupied by a component's title and interface groups. */
export function getComponentHeaderHeight(component: CompNode): number {
  const groups = [component.provided_interfaces || [], component.required_interfaces || []];
  return 76 + groups.reduce((height, interfaces) => (
    height + (interfaces.length ? 24 + interfaces.length * 22 : 0)
  ), 0);
}

export function getComponentChildTop(component: CompNode): number {
  return getComponentHeaderHeight(component) + 36;
}

export function getComponentDividerTop(component: CompNode): number {
  return getComponentHeaderHeight(component) + 12;
}

/** Calculate component positions and dimensions without mutating store state. */
export function layoutComponents(diagram: UmlDiagram): UmlDiagram | null {
  const components = diagram.components || [];
  if (components.length < 2) return null;
  const byId = new Map(components.map((component) => [component.id, component]));
  const children = new Map<string, CompNode[]>();
  components.forEach((component) => {
    if (!component.parent_id || !byId.has(component.parent_id)) return;
    if (!children.has(component.parent_id)) children.set(component.parent_id, []);
    children.get(component.parent_id)!.push(component);
  });
  const roots = components.filter((component) => !component.parent_id || !byId.has(component.parent_id));
  const sizes = new Map<string, ComponentSize>(components.map((component) => [component.id, {
    width: component.width || (component.parent_id ? 150 : 200),
    height: Math.max(component.height || (component.parent_id ? 100 : 160), getComponentHeaderHeight(component)),
  }]));
  const childBlocks = new Map<string, ComponentBlock>();
  const relations = diagram.comp_relations || [];
  const projectEdges = (ids: string[]): ArchitectureEdge[] => {
    const members = new Set(ids);
    const owner = (id: string) => {
      const seen = new Set<string>();
      while (byId.has(id) && !seen.has(id)) {
        if (members.has(id)) return id;
        seen.add(id);
        id = byId.get(id)!.parent_id;
      }
      return '';
    };
    return relations.filter((relation) => relation.type !== 'delegation').map((relation) => ({
      source: owner(relation.source), target: owner(relation.target),
    })).filter((edge) => edge.source && edge.target && edge.source !== edge.target);
  };
  const prepare = (id: string, visiting = new Set<string>()) => {
    if (visiting.has(id)) return;
    const members = children.get(id) || [];
    members.forEach((child) => prepare(child.id, new Set(visiting).add(id)));
    if (!members.length) return;
    // Delegated children get the first available positions, shortening the
    // links from the parent's interface area without inventing dependencies.
    const delegated = new Set(relations.filter((relation) => relation.type === 'delegation' && relation.source === id)
      .map((relation) => relation.target));
    const ids = [...members].sort((a, b) => Number(delegated.has(b.id)) - Number(delegated.has(a.id))).map(({ id }) => id);
    const edges = projectEdges(ids);
    const groups = componentGroups(ids, edges);
    const linked = groups.filter((group) => group.length > 1);
    const isolated = groups.filter((group) => group.length === 1).flat();
    const blocks = linked.map((group) => componentLayers(group, edges, sizes, 48, 40));
    if (isolated.length) blocks.push(componentGrid(isolated, sizes));
    const block = packComponentBlocks(blocks, 40);
    childBlocks.set(id, block);
    // Automatic layout recomputes container size. Retaining an old oversized
    // parent makes subsequent compact layouts keep the old empty space.
    sizes.set(id, {
      width: Math.max(200, block.width + 40),
      height: block.height + getComponentChildTop(byId.get(id)!) + 20,
    });
  };
  roots.forEach(({ id }) => prepare(id));
  const rootIds = roots.map(({ id }) => id);
  const edges = projectEdges(rootIds);
  const groups = componentGroups(rootIds, edges);
  const linked = groups.filter((group) => group.length > 1);
  const isolated = groups.filter((group) => group.length === 1).flat();
  const blocks = linked.map((group) => componentArchitectureBlock(group.map((id) => byId.get(id)!), edges, sizes));
  if (isolated.length) blocks.push(componentGrid(isolated, sizes, 3, 100));
  const layout = packComponentBlocks(blocks);
  const positions = new Map<string, { x: number; y: number }>();
  layout.positions.forEach((point, id) => positions.set(id, { x: point.x + 100, y: point.y + 80 }));
  const placeChildren = (id: string, visiting = new Set<string>()) => {
    if (visiting.has(id)) return;
    const origin = positions.get(id);
    const block = childBlocks.get(id);
    if (!origin || !block) return;
    block.positions.forEach((point, childId) => {
      positions.set(childId, {
        x: origin.x + 20 + point.x,
        y: origin.y + getComponentChildTop(byId.get(id)!) + point.y,
      });
      placeChildren(childId, new Set(visiting).add(id));
    });
  };
  roots.forEach(({ id }) => placeChildren(id));
  return {
    ...diagram,
    comp_relations: relations.map((relation) => ({ ...relation, vertices: undefined })),
    components: components.map((component) => ({
      ...component, ...(positions.get(component.id) || {}), ...(sizes.get(component.id) || {}),
    })),
  };
}
