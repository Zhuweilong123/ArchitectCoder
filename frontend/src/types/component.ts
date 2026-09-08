/** Component diagram types */

export interface CompNode {
  id: string;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  parent_id: string;
  provided_interfaces: string[];
  required_interfaces: string[];
}

export interface CompRelation {
  id: string;
  source: string;
  target: string;
  type: 'dependency' | 'delegation';
  /** User-adjusted orthogonal routing points. Undefined means use auto-routing. */
  vertices?: Array<{ x: number; y: number }>;
}

export function createDefaultComponent(x?: number, y?: number, parentId = ''): CompNode {
  return {
    id: `comp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    name: parentId ? 'SubComponent' : 'NewComponent',
    x: x || 150 + Math.random() * 400,
    y: y || 100 + Math.random() * 200,
    width: parentId ? 150 : 200,
    height: parentId ? 100 : 160,
    parent_id: parentId,
    provided_interfaces: [],
    required_interfaces: [],
  };
}

export function createDefaultCompRelation(source: string, target: string): CompRelation {
  return {
    id: `crel_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    source,
    target,
    type: 'dependency',
  };
}
