/** Shared sequence-diagram layout rules used by state and rendering. */

import type { SeqFragment, SeqLifeline, SeqMessage } from '../types/sequence';

export const SEQUENCE_MESSAGE_START_Y = 190;
export const SEQUENCE_MESSAGE_GAP = 48;
export const SEQUENCE_LIFELINE_WIDTH = 140;

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
  let nextY = SEQUENCE_MESSAGE_START_Y;
  const arrangedMessages = orderedMessages.map((message, index) => {
    const arranged = { ...message, y: nextY, order: index + 1 };
    const following = orderedMessages[index + 1];
    if (following) {
      const selfInvolved = message.type === 'self' || following.type === 'self'
        || message.from_lifeline === message.to_lifeline || following.from_lifeline === following.to_lifeline;
      nextY += Math.max(
        SEQUENCE_MESSAGE_GAP,
        estimatedLabelHeight(message.label),
        selfInvolved ? 62 : 0,
      );
    }
    return arranged;
  });
  const newMessageY = new Map(arrangedMessages.map((message) => [message.id, message.y]));

  const arrangedFragments = fragments.map((fragment) => {
    const contained = orderedMessages.filter((message) => {
      const y = oldMessageY.get(message.id) || 0;
      return y >= fragment.y_start && y <= fragment.y_end;
    });
    if (contained.length === 0) return fragment;
    const involved = new Set(contained.flatMap((message) => [message.from_lifeline, message.to_lifeline]));
    const positions = [...involved]
      .map((id) => positionById.get(id))
      .filter((x): x is number => typeof x === 'number');
    const minMessageY = Math.min(...contained.map((message) => newMessageY.get(message.id) || SEQUENCE_MESSAGE_START_Y));
    const maxMessageY = Math.max(...contained.map((message) => newMessageY.get(message.id) || SEQUENCE_MESSAGE_START_Y));
    const x = positions.length > 0 ? Math.max(60, Math.min(...positions) - 24) : fragment.x;
    const right = positions.length > 0 ? Math.max(...positions) + SEQUENCE_LIFELINE_WIDTH + 24 : fragment.x + fragment.width;
    return {
      ...fragment,
      x,
      width: Math.max(220, right - x),
      y_start: Math.max(80, minMessageY - 28),
      y_end: Math.max(minMessageY + 72, maxMessageY + 36),
    };
  });

  return {
    lifelines: lifelines.map((lifeline) => ({ ...lifeline, x: positionById.get(lifeline.id) ?? lifeline.x })),
    messages: arrangedMessages,
    fragments: arrangedFragments,
  };
}
