import { Stereotype, type UmlClass } from '../types/uml';

export const CLASS_MEMBER_PREVIEW_COUNT = 2;
export const CLASS_MEMBER_TOGGLE_HEIGHT = 22;

export interface ClassTextContent {
  width: number;
  name: string;
  hasStereotype: boolean;
  attributes: string[];
  methods: string[];
  interfaces: string[];
  note: string;
  attributesToggle?: boolean;
  methodsToggle?: boolean;
}

/** Explicit line breaks keep HTML, SVG and layout on the same geometry. */
export function wrapClassText(value: string, width: number, fontSize: number): string[] {
  const text = value.replace(/\s+/g, ' ').trim();
  const lines: string[] = [];
  let line = '';
  let used = 0;
  for (const char of text) {
    // Monospace Latin glyphs are narrower than CJK glyphs. Reserve a little
    // extra width so font fallback cannot push a line out of the class box.
    const advance = /[^\x00-\xff]/.test(char) ? fontSize : fontSize * 0.64;
    if (line && used + advance > Math.max(1, width)) {
      lines.push(line);
      line = '';
      used = 0;
    }
    line += char;
    used += advance;
  }
  lines.push(line);
  return lines;
}

export function getClassTextLayout(content: ClassTextContent) {
  const member = (text: string) => {
    const lines = wrapClassText(text, content.width - 20, 11);
    return { text, lines, height: Math.max(19, lines.length * 14.85 + 4) };
  };
  const attributes = (content.attributes.length ? content.attributes : ['—']).map(member);
  const methods = (content.methods.length ? content.methods : ['—']).map(member);
  const interfaces = content.interfaces.map((text) => wrapClassText(text, content.width - 20, 10));
  const nameLines = wrapClassText(content.name, content.width - 24, 14);
  const noteLines = content.note ? wrapClassText(content.note, content.width - 20, 10) : [];
  const headerHeight = (content.hasStereotype ? 58 : 42) + (nameLines.length - 1) * 18.9;
  const interfaceHeight = interfaces.length ? 12 + interfaces.reduce((sum, lines) => sum + lines.length * 13.5, 0) : 0;
  const attributesHeight = 28 + attributes.reduce((sum, row) => sum + row.height, 0)
    + (content.attributesToggle ? CLASS_MEMBER_TOGGLE_HEIGHT : 0);
  const methodsHeight = 28 + methods.reduce((sum, row) => sum + row.height, 0)
    + (content.methodsToggle ? CLASS_MEMBER_TOGGLE_HEIGHT : 0);
  const noteHeight = noteLines.length ? 13 + noteLines.length * 13.5 : 0;
  return {
    attributes, methods, interfaces, interfaceTexts: content.interfaces, nameLines, noteLines,
    headerHeight, interfaceHeight, attributesHeight, methodsHeight, noteHeight,
    // Divider strokes and a small bottom margin, rather than padding per line.
    height: Math.ceil(headerHeight + interfaceHeight + attributesHeight + methodsHeight + noteHeight + 8),
  };
}

export function getClassContentLayout(cls: UmlClass, width = cls.size.width || 200) {
  return getClassTextLayout({
    width,
    name: cls.name,
    hasStereotype: cls.stereotype !== Stereotype.CLASS,
    attributes: (cls.expanded_attributes ? cls.attributes : cls.attributes.slice(0, CLASS_MEMBER_PREVIEW_COUNT))
      .map((a) => `${a.visibility} ${a.name}: ${a.type}${a.default_value ? ` = ${a.default_value}` : ''}`),
    methods: (cls.expanded_methods ? cls.methods : cls.methods.slice(0, CLASS_MEMBER_PREVIEW_COUNT))
      .map((m) => `${m.visibility} ${m.name}(${m.params}): ${m.return_type}`),
    attributesToggle: cls.attributes.length > CLASS_MEMBER_PREVIEW_COUNT,
    methodsToggle: cls.methods.length > CLASS_MEMBER_PREVIEW_COUNT,
    interfaces: [
      cls.provided_interfaces?.length ? cls.provided_interfaces.map((i) => `● ${i}`).join(' ') : '',
      cls.required_interfaces?.length ? cls.required_interfaces.map((i) => `○ ${i}`).join(' ') : '',
    ].filter(Boolean),
    note: cls.note || '',
  });
}
