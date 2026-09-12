import type { CanvasTheme } from '../../stores/uiStore';
import type { InterfaceLanguage } from '../../i18n';
import type { SeqLifeline, MessageType } from '../../types/sequence';
import { getCanvasLabels } from './canvasLabels';
import { escapeHtml } from '../../utils/safeHtml';
import { SEQUENCE_LIFELINE_WIDTH } from '../../utils/sequenceLayout';

export const LIFELINE_WIDTH = SEQUENCE_LIFELINE_WIDTH;
export const LIFELINE_HEIGHT = 400;
export const LIFELINE_Y = 120;

export function buildLifelineHTML(
  lifeline: SeqLifeline,
  selected: boolean,
  endpointHighlighted: boolean,
  theme: CanvasTheme,
  language: InterfaceLanguage,
): string {
  const selClass = [
    selected ? 'selected' : '',
    endpointHighlighted ? 'message-endpoint' : '',
  ].filter(Boolean).join(' ');
  const hint = selected
    ? '<div class="seq-click-hint">' + getCanvasLabels(language).sequenceDiagram.selectedLifelineHint + '</div>'
    : '';
  return '<div class="seq-lifeline-node theme-' + theme + ' ' + selClass + '">' +
    '<div class="seq-lifeline-name">' + escapeHtml(lifeline.name) + '</div>' +
    '<div class="seq-lifeline-body">' + hint + '</div>' +
    '</div>';
}

export function getMessageVisual(type: MessageType, theme: CanvasTheme) {
  const palette = theme === 'dark'
    ? {
        sync: '#60a5fa', async: '#4ade80', return: '#cbd5e1', simple: '#e2e8f0', self: '#a78bfa',
      }
    : theme === 'eye-care'
      ? {
          sync: '#547a5d', async: '#4f805d', return: '#718078', simple: '#52675a', self: '#89745d',
        }
      : {
        sync: '#2563eb', async: '#16a34a', return: '#64748b', simple: '#475569', self: '#7c3aed',
        };
  const color = palette[type];
  return {
    color,
    dash: type === 'return' ? '6,3' : '',
    marker: type === 'simple' ? null : {
      name: type === 'async' ? 'classic' : 'block',
      width: 10,
      height: 6,
      fill: color,
      stroke: color,
    },
  };
}
