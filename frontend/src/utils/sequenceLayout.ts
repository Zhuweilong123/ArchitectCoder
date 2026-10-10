/** Shared sequence-diagram layout rules used by state and rendering. */

import type { SeqFragment, SeqLifeline, SeqMessage } from '../types/sequence';
import { fitStructuredFragments, fragmentMessageIds, operandMessageIds } from './sequenceFragments';

export const SEQUENCE_MESSAGE_START_Y = 190;
export const SEQUENCE_MESSAGE_GAP = 48;
export const SEQUENCE_LIFELINE_WIDTH = 140;
export const SEQUENCE_LIFELINE_Y = 120;

/** Match the header's 82px text area, padding and 15px line height. */
export function sequenceLifelineHeaderHeight(name: string): number {
  const rows = (name || '').split('\n').reduce((sum, line) => {
    let lineCount = 1;
    let usedWidth = 0;
    // CSS wraps at spaces first, then breaks an oversized word. Counting
    // only total characters misses rows such as "ctrl: ControllerNode".
    line.trim().split(/\s+/).forEach((word) => {
      const widths = [...word].map((char) => /[^\x00-\xff]/.test(char) ? 11 : 7);
      const wordWidth = widths.reduce((total, width) => total + width, 0);
      if (usedWidth > 0) {
        if (usedWidth + 7 + wordWidth <= 82) usedWidth += 7;
        else {
          lineCount += 1;
          usedWidth = 0;
        }
      }
      widths.forEach((width) => {
        if (usedWidth + width > 82) {
          lineCount += 1;
          usedWidth = 0;
        }
        usedWidth += width;
      });
    });
    return sum + lineCount;
  }, 0);
  return Math.max(30, rows * 15 + 14);
}

export function sequenceContentStartY(lifelines: SeqLifeline[]): number {
  const headerHeight = Math.max(30, ...lifelines.map((ll) => sequenceLifelineHeaderHeight(ll.name)));
  // Four pixels of node padding, then a clear band before fragment titles.
  return SEQUENCE_LIFELINE_Y + 4 + headerHeight + 20;
}

export function sequenceMessageY(message: SeqMessage): number {
  return message.y || SEQUENCE_MESSAGE_START_Y + (message.order - 1) * SEQUENCE_MESSAGE_GAP;
}

function estimatedLabelHeight(label: string): number {
  // X6 labels do not wrap automatically. The extra spacing keeps long labels
  // readable when a browser or font renders them on more than one visual line.
  const rows = Math.max(1, Math.ceil((label || '').trim().length / 46));
  return 24 + rows * 16;
}

function estimatedLabelWidth(label: string): number {
  // Sequence labels use a 10px monospace-like visual. A six-pixel average
  // matches the rendered width more closely than a conservative seven-pixel
  // estimate, keeping a five-participant request flow compact at 100% zoom.
  return Math.min(320, Math.max(0, (label || '').trim().length * 6 + 24));
}

export interface SequenceLayoutResult {
  lifelines: SeqLifeline[];
  messages: SeqMessage[];
  fragments: SeqFragment[];
}

/**
 * Arrange a sequence diagram around its interaction flow rather than blindly
 * preserving a historical X order. The earliest caller stays on the left,
 * then the next participant is chosen by its strongest interaction with the
 * already placed chain. This produces the natural actor → boundary → service
 * → infrastructure order for common request flows while remaining stable for
 * disconnected participants.
 */
export function arrangeSequenceLayout(
  lifelines: SeqLifeline[],
  messages: SeqMessage[],
  fragments: SeqFragment[],
): SequenceLayoutResult {
  const originalLifelines = [...lifelines].sort((a, b) => a.x - b.x || a.id.localeCompare(b.id));
  const orderedMessages = [...messages].sort((a, b) => (
    sequenceMessageY(a) - sequenceMessageY(b) || a.order - b.order || a.id.localeCompare(b.id)
  ));
  if (originalLifelines.length === 0 && orderedMessages.length === 0) {
    return { lifelines, messages, fragments };
  }

  const originalIndex = new Map(originalLifelines.map((lifeline, index) => [lifeline.id, index]));
  const weights = new Map<string, Map<string, number>>();
  const addWeight = (first: string, second: string, amount: number) => {
    if (first === second) return;
    const firstWeights = weights.get(first) || new Map<string, number>();
    firstWeights.set(second, (firstWeights.get(second) || 0) + amount);
    weights.set(first, firstWeights);
  };
  orderedMessages.forEach((message, index) => {
    // Early calls establish the main scenario spine, while later calls still
    // influence ordering enough to keep reporting and callback paths nearby.
    const amount = Math.max(1, 4 - Math.floor(index / 4));
    addWeight(message.from_lifeline, message.to_lifeline, amount);
    addWeight(message.to_lifeline, message.from_lifeline, amount);
  });

  const byId = new Map(originalLifelines.map((lifeline) => [lifeline.id, lifeline]));
  const firstMessage = orderedMessages.find((message) => byId.has(message.from_lifeline));
  const remaining = new Set(originalLifelines.map((lifeline) => lifeline.id));
  const orderedIds: string[] = [];
  const firstId = firstMessage?.from_lifeline || originalLifelines[0]?.id;
  if (firstId && remaining.delete(firstId)) orderedIds.push(firstId);
  while (remaining.size > 0) {
    const candidates = [...remaining];
    candidates.sort((first, second) => {
      const score = (candidate: string) => orderedIds.reduce((sum, placed) => (
        sum + (weights.get(candidate)?.get(placed) || 0)
      ), 0);
      return score(second) - score(first)
        || (originalIndex.get(first) || 0) - (originalIndex.get(second) || 0)
        || first.localeCompare(second);
    });
    const next = candidates[0];
    remaining.delete(next);
    orderedIds.push(next);
  }

  const orderedLifelines = orderedIds.map((id) => byId.get(id)!).filter(Boolean);
  const positionById = new Map<string, number>();
  let nextX = 100;
  orderedLifelines.forEach((lifeline, index) => {
    positionById.set(lifeline.id, nextX);
    if (index === orderedLifelines.length - 1) return;
    const nextId = orderedLifelines[index + 1].id;
    const requiredLabelWidth = orderedMessages.reduce((largest, message) => {
      const isDirectPair = (message.from_lifeline === lifeline.id && message.to_lifeline === nextId)
        || (message.from_lifeline === nextId && message.to_lifeline === lifeline.id);
      return isDirectPair ? Math.max(largest, estimatedLabelWidth(message.label)) : largest;
    }, 0);
    const headerWidth = Math.max(lifeline.name.length, orderedLifelines[index + 1].name.length) * 8 + 32;
    nextX += Math.max(220, requiredLabelWidth + 36, headerWidth + 54);
  });

  const oldMessageY = new Map(orderedMessages.map((message) => [message.id, sequenceMessageY(message)]));
  const operandOwner = new Map<string, string>();
  fragments.forEach((f) => (f.operands || []).forEach((o) => o.message_ids.forEach((id) => operandOwner.set(id, o.id))));
  const contentStartY = sequenceContentStartY(lifelines);
  const fragmentById = new Map(fragments.map((fragment) => [fragment.id, fragment]));
  const nestingDepth = (fragment: SeqFragment) => {
    const seen = new Set([fragment.id]);
    let depth = 0;
    let parent = fragmentById.get(fragment.parent_fragment_id || '');
    while (parent && !seen.has(parent.id)) {
      seen.add(parent.id); depth += 1;
      parent = fragmentById.get(parent.parent_fragment_id || '');
    }
    return depth;
  };
  const structured = fragments.filter((fragment) => fragment.operands?.length);
  const maxDepth = structured.length ? Math.max(...structured.map(nestingDepth)) : 0;
  // Reserve operand/fragment titles, including nested frames, above the first message.
  const titleSpace = structured.length ? 24 + maxDepth * 48 : 0;
  let nextY = Math.max(SEQUENCE_MESSAGE_START_Y, contentStartY + 28 + titleSpace);
  const emptyOperands = structured.flatMap((fragment) => (fragment.operands || [])
    .filter((operand) => operandMessageIds(operand, fragment, fragments).size === 0
      && !fragments.some((child) => child.parent_fragment_id === fragment.id && child.parent_operand_id === operand.id)));
  // Empty alternatives still occupy a region. Keep common continuation out.
  const timeline = [
    ...orderedMessages.map((message) => ({ message, emptyId: '', oldY: sequenceMessageY(message) })),
    ...emptyOperands.map((operand) => ({ message: null, emptyId: operand.id, oldY: (operand.y_start + operand.y_end) / 2 })),
  ].sort((a, b) => a.oldY - b.oldY);
  const emptyY = new Map<string, number>();
  const arrangedMessages: SeqMessage[] = [];
  timeline.forEach((entry, index) => {
    const message = entry.message;
    if (message) arrangedMessages.push({ ...message, y: nextY, order: arrangedMessages.length + 1 });
    else emptyY.set(entry.emptyId, nextY);
    const following = timeline[index + 1];
    if (following) {
      const nextMessage = following.message;
      const selfInvolved = message?.type === 'self' || nextMessage?.type === 'self'
        || (message && message.from_lifeline === message.to_lifeline)
        || (nextMessage && nextMessage.from_lifeline === nextMessage.to_lifeline);
      nextY += Math.max(
        SEQUENCE_MESSAGE_GAP,
        estimatedLabelHeight(message?.label || ''),
        selfInvolved ? 62 : 0,
        (!message || !nextMessage || operandOwner.get(message.id) !== operandOwner.get(nextMessage.id)) ? 80 + maxDepth * 48 : 0,
      );
    }
  });
  const newMessageY = new Map(arrangedMessages.map((message) => [message.id, message.y]));

  const arrangedFragments = fragments.map((originalFragment) => {
    const fragment = { ...originalFragment,
      ...(originalFragment.operands ? { operands: originalFragment.operands.map((operand) => {
        const center = emptyY.get(operand.id);
        return center === undefined ? operand : { ...operand, y_start: center - 28, y_end: center + 36 };
      }) } : {}),
    };
    const explicitIds = fragmentMessageIds(fragment, fragments);
    const contained = orderedMessages.filter((message) => {
      if (fragment.operands?.length) return explicitIds.has(message.id);
      const y = oldMessageY.get(message.id) || 0;
      return y >= fragment.y_start && y <= fragment.y_end;
    });
    if (contained.length === 0) {
      const shift = Math.max(0, contentStartY - fragment.y_start);
      return { ...fragment, y_start: fragment.y_start + shift, y_end: fragment.y_end + shift };
    }
    const involved = new Set(contained.flatMap((message) => [message.from_lifeline, message.to_lifeline]));
    (fragment.lifeline_ids || []).forEach((id) => involved.add(id));
    // A break spans the enclosing interaction, including idle participants.
    if (fragment.type === 'break') {
      const parent = fragments.find((f) => f.id === fragment.parent_fragment_id);
      const coverage = parent?.lifeline_ids?.length ? parent.lifeline_ids : lifelines.map((l) => l.id);
      coverage.forEach((id) => involved.add(id));
    }
    const positions = [...involved]
      .map((id) => positionById.get(id))
      .filter((x): x is number => typeof x === 'number');
    const minMessageY = Math.min(...contained.map((message) => newMessageY.get(message.id) || SEQUENCE_MESSAGE_START_Y));
    const maxMessageY = Math.max(...contained.map((message) => newMessageY.get(message.id) || SEQUENCE_MESSAGE_START_Y));
    const padding = 24 + (fragment.operands?.length ? (maxDepth - nestingDepth(fragment)) * 24 : 0);
    const x = positions.length > 0 ? Math.max(40, Math.min(...positions) - padding) : fragment.x;
    const right = positions.length > 0 ? Math.max(...positions) + SEQUENCE_LIFELINE_WIDTH + padding : fragment.x + fragment.width;
    return {
      ...fragment,
      x,
      width: Math.max(220, right - x),
      y_start: Math.max(contentStartY, minMessageY - 28),
      y_end: Math.max(minMessageY + 72, maxMessageY + 36),
    };
  });

  return {
    lifelines: lifelines.map((lifeline) => ({ ...lifeline, x: positionById.get(lifeline.id) ?? lifeline.x })),
    messages: arrangedMessages,
    fragments: fitStructuredFragments(arrangedFragments, arrangedMessages),
  };
}
