/** Explicit membership survives layout. Coordinates are presentation only. */
import type { SeqFragment, SeqMessage, SeqOperand } from '../types/sequence';

export function fragmentMessageIds(fragment: SeqFragment, fragments: SeqFragment[], visited = new Set<string>()): Set<string> {
  if (visited.has(fragment.id)) return new Set();
  const nextVisited = new Set(visited).add(fragment.id);
  const ids = new Set((fragment.operands || []).flatMap((operand) => operand.message_ids));
  fragments.filter((child) => child.parent_fragment_id === fragment.id).forEach((child) => {
    fragmentMessageIds(child, fragments, nextVisited).forEach((id) => ids.add(id));
  });
  return ids;
}

export function operandMessageIds(operand: SeqOperand, fragment: SeqFragment, fragments: SeqFragment[]): Set<string> {
  const ids = new Set(operand.message_ids);
  fragments.filter((child) => child.parent_fragment_id === fragment.id && child.parent_operand_id === operand.id)
    .forEach((child) => fragmentMessageIds(child, fragments).forEach((id) => ids.add(id)));
  return ids;
}

export function fitStructuredFragments(fragments: SeqFragment[], messages: SeqMessage[]): SeqFragment[] {
  const byMessage = new Map(messages.map((message) => [message.id, message]));
  const fitted = new Map<string, SeqFragment>();
  const visiting = new Set<string>();
  const fit = (fragment: SeqFragment): SeqFragment => {
    if (fitted.has(fragment.id)) return fitted.get(fragment.id)!;
    if (visiting.has(fragment.id) || !fragment.operands?.length) return fragment;
    visiting.add(fragment.id);
    const operands = fragment.operands.map((operand) => {
      const memberYs = operand.message_ids.map((id) => byMessage.get(id)?.y)
        .filter((y): y is number => typeof y === 'number' && Number.isFinite(y));
      const children = fragments.filter((child) => child.parent_fragment_id === fragment.id && child.parent_operand_id === operand.id).map(fit);
      const starts = [...memberYs.map((y) => y - 28), ...children.map((child) => child.y_start - 24)];
      const ends = [...memberYs.map((y) => y + 36), ...children.map((child) => child.y_end + 12)];
      return starts.length ? { ...operand, y_start: Math.min(...starts), y_end: Math.max(...ends) } : operand;
    });
    const next = {
      ...fragment, operands,
      y_start: Math.min(...operands.map((o) => o.y_start)) - 24,
      y_end: Math.max(...operands.map((o) => o.y_end)) + 8,
    };
    visiting.delete(fragment.id);
    fitted.set(fragment.id, next);
    return next;
  };
  return fragments.map(fit);
}

/** New fields must not become dangling when users delete existing elements. */
export function pruneFragmentReferences(fragments: SeqFragment[], messageIds: Set<string>, lifelineIds?: Set<string>): SeqFragment[] {
  return fragments.map((fragment) => ({
    ...fragment,
    ...(lifelineIds ? { lifeline_ids: (fragment.lifeline_ids || []).filter((id) => lifelineIds.has(id)) } : {}),
    ...(fragment.operands ? { operands: fragment.operands.map((operand) => ({
      ...operand, message_ids: operand.message_ids.filter((id) => messageIds.has(id)),
    })) } : {}),
  }));
}
