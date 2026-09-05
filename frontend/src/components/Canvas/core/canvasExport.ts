import type { Graph } from '@antv/x6';

export type CanvasExportFormat = 'png' | 'svg';

const SVG_NS = 'http://www.w3.org/2000/svg';

interface ExportPalette {
  body: string;
  header: string;
  surface: string;
  text: string;
  secondary: string;
  divider: string;
  accent: string;
}

function getExportPalette(root: Element | null): ExportPalette {
  const isDark = root?.classList.contains('theme-dark');
  const isBlueprint = root?.classList.contains('theme-blueprint');
  if (isDark) {
    return {
      body: '#172033', header: '#1d3557', surface: '#1e293b',
      text: '#e2e8f0', secondary: '#94a3b8', divider: '#334155', accent: '#60a5fa',
    };
  }
  if (isBlueprint) {
    return {
      body: '#f6fbff', header: '#dbeafe', surface: '#eff6ff',
      text: '#164e63', secondary: '#0369a1', divider: '#bae6fd', accent: '#0284c7',
    };
  }
  return {
    body: '#ffffff', header: '#eef4ff', surface: '#f8fafc',
    text: '#334155', secondary: '#64748b', divider: '#d9e1ec', accent: '#64748b',
  };
}

function createSvgElement<T extends keyof SVGElementTagNameMap>(
  document: Document,
  tagName: T,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[T] {
  const element = document.createElementNS(SVG_NS, tagName);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function wrapExportText(value: string, maxChars: number): string[] {
  const text = value.replace(/\s+/g, ' ').trim();
  if (!text) return [''];
  if (text.length <= maxChars) return [text];
  const lines: string[] = [];
  for (let index = 0; index < text.length; index += maxChars) {
    lines.push(text.slice(index, index + maxChars));
  }
  return lines;
}

function appendExportText(
  parent: SVGElement,
  document: Document,
  value: string,
  x: number,
  y: number,
  options: { fill: string; fontSize: number; fontWeight?: string; anchor?: string; maxChars?: number },
): void {
  const text = createSvgElement(document, 'text', {
    x,
    y,
    fill: options.fill,
    'font-family': 'Consolas, Monaco, monospace',
    'font-size': options.fontSize,
    'font-weight': options.fontWeight || '400',
    'text-anchor': options.anchor || 'start',
  });
  const lines = wrapExportText(value, options.maxChars || 40);
  lines.forEach((line, index) => {
    const tspan = createSvgElement(document, 'tspan', {
      x,
      dy: index === 0 ? 0 : options.fontSize * 1.35,
    });
    tspan.textContent = line;
    text.appendChild(tspan);
  });
  parent.appendChild(text);
}

function getNodeHtmlRoot(node: any): Element | null {
  const html = node.attr?.('content/html');
  if (typeof html !== 'string' || !html) return null;
  return new DOMParser().parseFromString(html, 'text/html').body.firstElementChild;
}

function removeExportOnlyElements(cellElement: Element): void {
  cellElement.querySelectorAll('[class*="x6-port"], [data-port-id]').forEach((element) => {
    element.remove();
  });
}

function flattenUmlClass(
  node: any,
  cellElement: Element,
  document: Document,
): void {
  const root = getNodeHtmlRoot(node);
  if (!root) return;
  const palette = getExportPalette(root);
  const size = node.getSize();
  const width = Number(size.width) || 200;
  const height = Number(size.height) || 150;
  const native = createSvgElement(document, 'g', { 'data-export-render': 'uml-class' });
  const body = cellElement.querySelector('rect');
  body?.setAttribute('fill', palette.body);
  body?.setAttribute('stroke', palette.divider);

  const stereotype = root.querySelector('.uml-stereotype')?.textContent || '';
  const name = root.querySelector('.uml-class-name')?.textContent || '';
  const attributes = Array.from(root.querySelectorAll('.uml-attr'))
    .map((element) => element.textContent || '');
  const methods = Array.from(root.querySelectorAll('.uml-method'))
    .map((element) => element.textContent || '');
  const interfaces = Array.from(root.querySelectorAll('.uml-iface-row'))
    .map((element) => element.textContent || '');
  const note = root.querySelector('.uml-class-note')?.textContent || '';
  const maxChars = Math.max(18, Math.floor((width - 20) / 7));
  const headerHeight = stereotype ? 58 : 42;

  native.appendChild(createSvgElement(document, 'rect', {
    x: 0, y: 0, width, height: headerHeight, fill: palette.header,
  }));
  if (stereotype) {
    appendExportText(native, document, stereotype, width / 2, 17, {
      fill: palette.secondary, fontSize: 10, anchor: 'middle', maxChars,
    });
  }
  appendExportText(native, document, name, width / 2, stereotype ? 42 : 27, {
    fill: palette.text, fontSize: 14, fontWeight: '700', anchor: 'middle', maxChars,
  });

  let y = headerHeight;
  const appendSection = (label: string, rows: string[], fill: string) => {
    const values = rows.length > 0 ? rows : ['—'];
    const rowHeights = values.map((row) => {
      const lineCount = wrapExportText(row, maxChars).length;
      return Math.max(19, lineCount * 11 * 1.35 + 4);
    });
    const sectionHeight = 28 + rowHeights.reduce((sum, rowHeight) => sum + rowHeight, 0);
    native.appendChild(createSvgElement(document, 'rect', {
      x: 0, y, width, height: sectionHeight, fill,
    }));
    appendExportText(native, document, label, 10, y + 16, {
      fill: palette.secondary, fontSize: 9, fontWeight: '700', maxChars,
    });
    let rowY = y + 35;
    values.forEach((row, index) => {
      appendExportText(native, document, row, 10, rowY, {
        fill: palette.text, fontSize: 11, maxChars,
      });
      rowY += rowHeights[index];
    });
    y += sectionHeight;
    native.appendChild(createSvgElement(document, 'line', {
      x1: 0, y1: y, x2: width, y2: y, stroke: palette.divider, 'stroke-width': 1,
    }));
  };

  if (interfaces.length > 0) {
    const interfaceHeight = 28 + interfaces.length * 16;
    native.appendChild(createSvgElement(document, 'rect', {
      x: 0, y, width, height: interfaceHeight, fill: palette.surface,
    }));
    interfaces.forEach((row, index) => appendExportText(
      native, document, row, 10, y + 18 + index * 16,
      { fill: palette.secondary, fontSize: 10, maxChars },
    ));
    y += interfaceHeight;
    native.appendChild(createSvgElement(document, 'line', {
      x1: 0, y1: y, x2: width, y2: y, stroke: palette.divider, 'stroke-width': 1,
    }));
  }
  appendSection('ATTRIBUTES', attributes, palette.surface);
  appendSection('OPERATIONS', methods, palette.body);
  if (note) {
    appendExportText(native, document, note, 10, y + 22, {
      fill: palette.secondary, fontSize: 10, maxChars,
    });
  }

  cellElement.querySelectorAll('foreignObject').forEach((element) => element.remove());
  cellElement.appendChild(native);
  removeExportOnlyElements(cellElement);
}

function flattenComponent(
  node: any,
  cellElement: Element,
  document: Document,
): void {
  const root = getNodeHtmlRoot(node);
  if (!root) return;
  const palette = getExportPalette(root);
  const size = node.getSize();
  const width = Number(size.width) || 200;
  const height = Number(size.height) || 160;
  const native = createSvgElement(document, 'g', { 'data-export-render': 'component' });
  const body = cellElement.querySelector('rect');
  body?.setAttribute('fill', palette.body);
  body?.setAttribute('stroke', palette.accent);
  const maxChars = Math.max(18, Math.floor((width - 20) / 7));
  const stereotype = root.querySelector('.comp-stereotype')?.textContent || '';
  const name = root.querySelector('.comp-name')?.textContent || '';
  const interfaces = Array.from(root.querySelectorAll('.comp-iface'))
    .map((element) => element.textContent || '');
  let y = 22;
  if (stereotype.trim()) {
    appendExportText(native, document, stereotype, width / 2, y, {
      fill: palette.accent, fontSize: 10, anchor: 'middle', maxChars,
    });
    y += 18;
  }
  appendExportText(native, document, name, width / 2, y + 2, {
    fill: palette.text, fontSize: 13, fontWeight: '700', anchor: 'middle', maxChars,
  });
  y += 18;
  native.appendChild(createSvgElement(document, 'line', {
    x1: 10, y1: y, x2: width - 10, y2: y, stroke: palette.divider, 'stroke-width': 1,
  }));
  interfaces.forEach((value) => {
    y += 20;
    appendExportText(native, document, value, 12, y, {
      fill: palette.text, fontSize: 10, maxChars,
    });
  });
  cellElement.querySelectorAll('foreignObject').forEach((element) => element.remove());
  cellElement.appendChild(native);
  removeExportOnlyElements(cellElement);
}

function flattenHtmlDiagramNodes(graph: Graph, svg: SVGSVGElement): void {
  const elements = Array.from(svg.querySelectorAll('[data-cell-id]'));
  graph.getNodes().forEach((node) => {
    if (node.shape !== 'uml-class' && node.shape !== 'comp-component') return;
    const cellElement = elements.find((element) => element.getAttribute('data-cell-id') === node.id);
    if (!cellElement) return;
    if (node.shape === 'uml-class') flattenUmlClass(node, cellElement, svg.ownerDocument);
    else flattenComponent(node, cellElement, svg.ownerDocument);
  });
}

function normalizeExportEdgeLabels(svg: SVGSVGElement, backgroundColor: string): void {
  const isDark = backgroundColor.toLowerCase() === '#111827';
  const textColor = isDark ? '#f8fafc' : '#334155';
  const haloColor = isDark ? '#111827' : backgroundColor;
  const borderColor = isDark ? '#475569' : '#cbd5e1';

  svg.querySelectorAll('.x6-edge-label text').forEach((element) => {
    const text = element as SVGTextElement;
    text.style.setProperty('fill', textColor);
    text.style.setProperty('stroke', haloColor);
    text.style.setProperty('stroke-width', '1.5px');
    text.style.setProperty('paint-order', 'stroke');
    text.style.setProperty('stroke-linejoin', 'round');
  });
  svg.querySelectorAll('.x6-edge-label rect').forEach((element) => {
    const rect = element as SVGRectElement;
    rect.style.setProperty('fill', isDark ? '#172033' : '#ffffff');
    rect.style.setProperty('stroke', borderColor);
    rect.style.setProperty('stroke-width', '0.8px');
  });
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadDataUri(dataUri: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = dataUri;
  anchor.download = filename;
  anchor.click();
}

/** Export the visible graph content with a small margin around its bounds. */
export function exportCanvasGraph(
  graph: Graph,
  format: CanvasExportFormat,
  filename: string,
  backgroundColor = '#fafafa',
): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      // Let X6 calculate the content viewBox. It already converts the graph
      // coordinates to the exported SVG coordinates; supplying a second
      // manually converted viewBox shifts diagrams when the canvas is panned.
      const options = {
        padding: 32,
        preserveDimensions: true,
        copyStyles: true,
        serializeImages: true,
        backgroundColor,
        beforeSerialize(this: Graph, svg: SVGSVGElement) {
          flattenHtmlDiagramNodes(this, svg);
          normalizeExportEdgeLabels(svg, backgroundColor);
          const bounds = svg.viewBox.baseVal;
          const background = svg.ownerDocument.createElementNS('http://www.w3.org/2000/svg', 'rect');
          background.setAttribute('x', String(bounds.x));
          background.setAttribute('y', String(bounds.y));
          background.setAttribute('width', String(bounds.width));
          background.setAttribute('height', String(bounds.height));
          background.setAttribute('fill', backgroundColor);
          background.setAttribute('pointer-events', 'none');
          svg.insertBefore(background, svg.firstChild);
          return svg;
        },
      };

      if (format === 'png') {
        graph.toPNG((dataUri: string) => {
          if (!dataUri) {
            reject(new Error('Canvas export returned no image data'));
            return;
          }
          downloadDataUri(dataUri, filename);
          resolve();
        }, options);
        return;
      }

      graph.toSVG((svg: string) => {
        if (!svg) {
          reject(new Error('Canvas export returned no SVG data'));
          return;
        }
        downloadBlob(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), filename);
        resolve();
      }, options);
    } catch (error) {
      reject(error);
    }
  });
}

/** Download the complete editable project snapshot as a portable design file. */
export function exportProjectSnapshot(snapshot: unknown, filename: string): void {
  const content = JSON.stringify(snapshot, null, 2);
  downloadBlob(new Blob([content], { type: 'application/json;charset=utf-8' }), filename);
}
