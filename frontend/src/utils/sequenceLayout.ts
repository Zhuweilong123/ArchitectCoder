/** Shared sequence-diagram layout rules used by state and rendering. */

import type { SeqMessage } from '../types/sequence';

export const SEQUENCE_MESSAGE_START_Y = 190;
export const SEQUENCE_MESSAGE_GAP = 48;

export function sequenceMessageY(message: SeqMessage): number {
  return message.y || SEQUENCE_MESSAGE_START_Y + (message.order - 1) * SEQUENCE_MESSAGE_GAP;
}
