/** A deterministic layered order shared by class and component diagrams. */
export interface ArchitectureEdge {
  source: string;
  target: string;
  weight?: number;
}

export interface ArchitectureOrder {
  levels: Map<string, number>;
  rows: Map<number, string[]>;
}

export function orderArchitectureGraph(
  ids: string[], edges: ArchitectureEdge[], originalOrder: Map<string, number>,
): ArchitectureOrder {
  const known = new Set(ids);
  const adjacency = new Map(ids.map((id) => [id, new Set<string>()]));
  const incoming = new Map(ids.map((id) => [id, new Map<string, number>()]));
  const outgoing = new Map(ids.map((id) => [id, new Map<string, number>()]));
  edges.forEach(({ source, target, weight = 1 }) => {
    if (!known.has(source) || !known.has(target) || source === target) return;
    adjacency.get(source)!.add(target);
    outgoing.get(source)!.set(target, (outgoing.get(source)!.get(target) || 0) + weight);
    incoming.get(target)!.set(source, (incoming.get(target)!.get(source) || 0) + weight);
  });

  // Collapse cycles before assigning levels. A cycle is one architectural unit,
  // not an arbitrary tail appended after the acyclic part of the diagram.
  let nextIndex = 0;
  const indices = new Map<string, number>();
  const low = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const components: string[][] = [];
  const visit = (id: string) => {
    indices.set(id, nextIndex);
    low.set(id, nextIndex++);
    stack.push(id);
    onStack.add(id);
    adjacency.get(id)!.forEach((target) => {
      if (!indices.has(target)) {
        visit(target);
        low.set(id, Math.min(low.get(id)!, low.get(target)!));
      } else if (onStack.has(target)) {
        low.set(id, Math.min(low.get(id)!, indices.get(target)!));
      }
    });
    if (low.get(id) !== indices.get(id)) return;
    const members: string[] = [];
    let member: string;
    do {
      member = stack.pop()!;
      onStack.delete(member);
      members.push(member);
    } while (member !== id);
    components.push(members);
  };
  ids.forEach((id) => { if (!indices.has(id)) visit(id); });

  const owner = new Map<string, number>();
  components.forEach((members, index) => members.forEach((id) => owner.set(id, index)));
  const componentEdges = components.map(() => new Set<number>());
  const indegree = components.map(() => 0);
  edges.forEach(({ source, target }) => {
    const from = owner.get(source);
    const to = owner.get(target);
    if (from === undefined || to === undefined || from === to || componentEdges[from].has(to)) return;
    componentEdges[from].add(to);
    indegree[to] += 1;
  });
  const componentLevels = components.map(() => 0);
  const queue = components.map((_, index) => index).filter((index) => indegree[index] === 0);
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const from = queue[cursor];
    componentEdges[from].forEach((to) => {
      componentLevels[to] = Math.max(componentLevels[to], componentLevels[from] + 1);
      if (--indegree[to] === 0) queue.push(to);
    });
  }

  const levels = new Map(ids.map((id) => [id, componentLevels[owner.get(id)!]]));
  const rows = new Map<number, string[]>();
  ids.forEach((id) => {
    const level = levels.get(id)!;
    if (!rows.has(level)) rows.set(level, []);
    rows.get(level)!.push(id);
  });
  const ranks = [...rows.keys()].sort((a, b) => a - b);
  const reorder = (level: number, neighbours: Map<string, Map<string, number>>) => {
    const row = rows.get(level)!;
    const score = (id: string) => {
      let weighted = 0;
      let total = 0;
      neighbours.get(id)!.forEach((weight, neighbour) => {
        const neighbourRow = rows.get(levels.get(neighbour)!);
        if (!neighbourRow || neighbourRow === row) return;
        weighted += weight * neighbourRow.indexOf(neighbour);
        total += weight;
      });
      return total ? weighted / total : originalOrder.get(id) ?? 0;
    };
    row.sort((a, b) => score(a) - score(b)
      || (originalOrder.get(a) ?? 0) - (originalOrder.get(b) ?? 0)
      || a.localeCompare(b));
  };
  ranks.slice(1).forEach((level) => reorder(level, incoming));
  ranks.slice(0, -1).reverse().forEach((level) => reorder(level, outgoing));
  return { levels, rows };
}
