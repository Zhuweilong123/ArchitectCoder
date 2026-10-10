import type { SeqFragment } from '../types/sequence';

/** Native SVG guards/separators survive exports and treat guard text as data. */
export function sequenceOperandView(fragment: SeqFragment, yStart: number, width: number, color: string) {
  const markup: Array<{ tagName: string; selector: string }> = [];
  const attrs: Record<string, Record<string, string | number>> = {};
  (fragment.operands || []).forEach((operand, index) => {
    const guardSelector = `operandGuard${index}`;
    const offset = operand.y_start - yStart;
    markup.push({ tagName: 'text', selector: guardSelector });
    attrs[guardSelector] = {
      x: 12, y: offset + 16, text: operand.guard, fill: color,
      fontSize: 11, fontFamily: 'Consolas, monospace', pointerEvents: 'none',
    };
    if (index > 0) {
      const separator = `operandSeparator${index}`;
      markup.push({ tagName: 'line', selector: separator });
      attrs[separator] = {
        x1: 0, x2: width, y1: offset, y2: offset,
        stroke: color, strokeWidth: 1, strokeDasharray: '6 4', pointerEvents: 'none',
      };
    }
  });
  return { markup, attrs };
}
