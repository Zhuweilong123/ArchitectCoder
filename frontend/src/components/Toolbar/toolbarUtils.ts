export interface ProjectDiagramSummary {
  total: number;
  typeCount: number;
  text: string;
}

export function summarizeProjectDiagrams(
  diagrams: Array<{ diagram_type?: string }>,
): ProjectDiagramSummary {
  const labels: Record<string, string> = {
    class: '类图',
    sequence: '时序图',
    component: '组件图',
  };
  const order = ['class', 'sequence', 'component', 'other'];
  const counts = diagrams.reduce<Record<string, number>>((result, diagram) => {
    const type = diagram.diagram_type === 'class'
      ? 'class'
      : diagram.diagram_type === 'sequence'
        ? 'sequence'
        : diagram.diagram_type === 'component'
          ? 'component'
          : !diagram.diagram_type
            ? 'class'
            : 'other';
    result[type] = (result[type] || 0) + 1;
    return result;
  }, {});
  const parts = order
    .filter((type) => counts[type])
    .map((type) => `${labels[type] || '其他图表'} ${counts[type]} 张`);
  return {
    total: diagrams.length,
    typeCount: parts.length,
    text: parts.join('、'),
  };
}

export function normalizePath(value: string): string {
  return value.replace(/\\/g, '/').replace(/\/+$/, '');
}

export function pathBaseName(value: string): string {
  return normalizePath(value).split('/').pop() || value;
}

export function fileStem(value: string): string {
  return pathBaseName(value).replace(/[^\w.-]/g, '_').replace(/^\.+|\.+$/g, '') || 'Untitled';
}

export function pathDirName(value: string): string {
  const normalized = normalizePath(value);
  const index = normalized.lastIndexOf('/');
  return index > 0 ? normalized.slice(0, index) : normalized;
}

export function relativePath(value: string, root: string): string {
  const target = normalizePath(value);
  const base = normalizePath(root);
  if (target === base) return '.';
  const prefix = `${base}/`;
  return target.startsWith(prefix) ? target.slice(prefix.length) : pathBaseName(target);
}
