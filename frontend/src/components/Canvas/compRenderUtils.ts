import type { CanvasTheme } from '../../stores/uiStore';
import type { InterfaceLanguage } from '../../i18n';
import type { CompNode } from '../../types/component';
import { getCanvasLabels } from './canvasLabels';
import { escapeHtml } from '../../utils/safeHtml';
import { getComponentHeaderHeight, getComponentDividerTop } from '../../utils/componentLayout';

export const componentThemeVisuals: Record<CanvasTheme, {
  surface: string;
  accent: string;
}> = {
  light: { surface: '#fffaf1', accent: '#b7791f' },
  dark: { surface: '#172033', accent: '#60a5fa' },
  blueprint: { surface: '#f6fbff', accent: '#0284c7' },
  'eye-care': { surface: '#f8f7ee', accent: '#547a5d' },
};

export const COMP_WIDTH = 200;
export const COMP_HEIGHT = 160;
export const CHILD_WIDTH = 150;
export const CHILD_HEIGHT = 100;

export function buildCompHTML(
  comp: CompNode,
  selected: boolean,
  theme: CanvasTheme,
  language: InterfaceLanguage,
  hasChildren = false,
): string {
  const labels = getCanvasLabels(language).componentDiagram;
  const isChild = !!comp.parent_id;
  const selClass = selected ? 'selected' : '';
  const childClass = isChild ? 'child' : '';
  const provided = (comp.provided_interfaces || []).map((item) =>
    '<div class="comp-iface provided"><svg class="comp-interface-icon" viewBox="0 0 24 18" aria-hidden="true">'
      + '<line x1="1" y1="9" x2="7" y2="9"/><circle cx="14" cy="9" r="6"/></svg>'
      + escapeHtml(item) + '</div>'
  ).join('');
  const required = (comp.required_interfaces || []).map((item) =>
    '<div class="comp-iface required"><svg class="comp-interface-icon" viewBox="0 0 24 18" aria-hidden="true">'
      + '<line x1="1" y1="4" x2="7" y2="4"/><path d="M 7 4 L 7 9 A 7 7 0 0 0 21 9 L 21 4"/>'
      + '</svg>'
      + escapeHtml(item) + '</div>'
  ).join('');

  const divider = hasChildren
    ? '<div class="comp-children-separator" style="top:'
      + getComponentDividerTop(comp) + 'px"></div>'
    : '';
  return '<div class="comp-node theme-' + theme + ' ' + childClass + ' ' + selClass + '">'
    + '<div class="comp-stereotype">' + (isChild ? '' : '«component»') + '</div>'
    + '<div class="comp-name">' + escapeHtml(comp.name) + '</div>'
    + (provided ? '<div class="comp-block"><div class="comp-block-label">' + labels.providedInterfaces + '</div>' + provided + '</div>' : '')
    + (required ? '<div class="comp-block"><div class="comp-block-label">' + labels.requiredInterfaces + '</div>' + required + '</div>' : '')
    + divider
    + '</div>';
}

export function getCompNodeSize(comp: CompNode): { width: number; height: number } {
  const isChild = !!comp.parent_id;
  const minHeight = getComponentHeaderHeight(comp);
  return {
    width: comp.width || (isChild ? CHILD_WIDTH : COMP_WIDTH),
    height: Math.max(comp.height || (isChild ? CHILD_HEIGHT : COMP_HEIGHT), minHeight),
  };
}
