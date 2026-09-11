import { Stereotype, type UmlClass } from '../../types/uml';

export interface ClassRenderLayout {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Estimate a readable minimum size without overwriting the user's manual resize. */
export function getClassNodeSize(cls: UmlClass): { width: number; height: number } {
  const width = cls.size.width || 200;
  const maxChars = Math.max(18, Math.floor((width - 20) / 7));
  const wrappedRows = (rows: string[]) => rows.reduce((sum, row) => (
    sum + Math.max(1, Math.ceil(row.replace(/\s+/g, ' ').trim().length / maxChars))
  ), 0);
  const attributeRows = cls.attributes.map((attribute) => (
    `${attribute.visibility} ${attribute.name}: ${attribute.type}${attribute.default_value ? ` = ${attribute.default_value}` : ''}`
  ));
  const operationRows = cls.methods.map((method) => (
    `${method.visibility} ${method.name}(${method.params}): ${method.return_type}`
  ));
  const interfaceRows = (cls.provided_interfaces?.length ? 1 : 0)
    + (cls.required_interfaces?.length ? 1 : 0);
  const headerHeight = cls.stereotype !== Stereotype.CLASS ? 58 : 42;
  const interfaceHeight = interfaceRows ? 28 + interfaceRows * 16 : 0;
  const attributesHeight = 28 + Math.max(1, wrappedRows(attributeRows)) * 19;
  const operationsHeight = 28 + Math.max(1, wrappedRows(operationRows)) * 19;
  const noteLines = cls.note
    ? Math.max(1, Math.ceil(cls.note.replace(/\s+/g, ' ').trim().length / maxChars))
    : 0;
  const noteHeight = noteLines ? 12 + noteLines * 14 : 0;

  return {
    width,
    height: Math.max(
      cls.size.height || 150,
      headerHeight + interfaceHeight + attributesHeight + operationsHeight + noteHeight + 8,
    ),
  };
}

function rectanglesOverlap(
  first: ClassRenderLayout,
  second: ClassRenderLayout,
  gap: number,
): boolean {
  return first.x < second.x + second.width + gap
    && first.x + first.width + gap > second.x
    && first.y < second.y + second.height + gap
    && first.y + first.height + gap > second.y;
}

/** Resolve overlaps introduced when a class grows to fit long members/notes. */
export function resolveClassLayouts(classes: UmlClass[]): Map<string, ClassRenderLayout> {
  const gap = 36;
  const placed: ClassRenderLayout[] = [];
  const layouts = new Map<string, ClassRenderLayout>();
  const ordered = [...classes].sort((a, b) => (
    a.position.y - b.position.y
    || a.position.x - b.position.x
    || a.id.localeCompare(b.id)
  ));

  ordered.forEach((cls) => {
    const size = getClassNodeSize(cls);
    const desired = { x: cls.position.x, y: cls.position.y, ...size };
    let position = desired;
    const candidates = [
      desired,
      ...placed.flatMap((other) => [
        { ...desired, x: other.x - size.width - gap },
        { ...desired, x: other.x + other.width + gap },
        { ...desired, y: other.y + other.height + gap },
      ]),
    ];
    const valid = candidates
      .filter((candidate) => placed.every((other) => !rectanglesOverlap(candidate, other, 0)))
      .sort((a, b) => {
        const distance = (candidate: ClassRenderLayout) =>
          Math.abs(candidate.x - desired.x) + Math.abs(candidate.y - desired.y);
        return distance(a) - distance(b) || a.y - b.y || a.x - b.x;
      });
    if (valid.length > 0) position = valid[0];
    else if (placed.length > 0) {
      const bottom = Math.max(...placed.map((item) => item.y + item.height));
      position = { ...desired, y: bottom + gap };
    }
    layouts.set(cls.id, position);
    placed.push(position);
  });
  return layouts;
}
