/** Sequence diagram types — lifelines, messages, combined fragments. */

export interface SeqLifeline {
  id: string;
  name: string;
  class_ref: string;   // optional: UmlClass.id
  x: number;
  activations: number[];  // y-offsets of activation bars (from top of lifeline body)
}

export type MessageType = 'sync' | 'async' | 'return' | 'simple' | 'self';

export const MESSAGE_TYPE_LABELS: Record<MessageType, string> = {
  sync:   '同步消息',
  async:  '异步消息',
  return: '返回消息',
  simple: '简单消息',
  self:   '自反消息',
};

export interface SeqMessage {
  id: string;
  from_lifeline: string;
  to_lifeline: string;
  label: string;
  type: MessageType;
  order: number;
  y: number;            // persisted Y position — survives diagram switches
  note: string;         // functional comment / 功能备注
}

// UML 2.5.1 Combined Fragment

export type FragmentType = 'loop' | 'alt' | 'opt' | 'break' | 'par' | 'critical' | 'neg';

export const FRAGMENT_LABELS: Record<FragmentType, string> = {
  loop: 'loop', alt: 'alt', opt: 'opt', break: 'break',
  par: 'par', critical: 'critical', neg: 'neg',
};

export interface SeqOperand {
  id: string;
  guard: string;
  message_ids: string[];
  y_start: number;
  y_end: number;
}

export interface SeqFragment {
  id: string;
  type: FragmentType;
  label: string;
  x: number;
  width: number;
  y_start: number;
  y_end: number;
  operands?: SeqOperand[];
  lifeline_ids?: string[];
  parent_fragment_id?: string;
  parent_operand_id?: string;
}

export function createDefaultFragment(y: number): SeqFragment {
  return {
    id: `frag_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    type: 'loop',
    label: '',
    x: 80,
    width: 300,
    y_start: y,
    y_end: y + 120,
  };
}

export function createDefaultLifeline(x?: number): SeqLifeline {
  return {
    id: `life_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    name: 'Participant',
    class_ref: '',
    x: x || 150 + Math.random() * 400,
    activations: [],
  };
}

export function createDefaultMessage(from: string, to: string, order: number, y?: number): SeqMessage {
  return {
    id: `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    from_lifeline: from,
    to_lifeline: to,
    label: 'message()',
    type: 'sync',
    order,
    y: y || (150 + order * 40),  // LIFELINE_Y(120) + 30 + order*40
    note: '',
  };
}
