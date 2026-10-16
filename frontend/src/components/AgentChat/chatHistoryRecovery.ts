import type { TraceHistoryEntry } from '../../services/api';
import type { ChatMessage } from './agentChatUtils';

/** Restore durable text while retaining cached steps and live messages. */
export function mergeChatHistory(
  sessionId: string, history: TraceHistoryEntry[], local: ChatMessage[],
): ChatMessage[] {
  const restored: ChatMessage[] = history
    .filter(entry => entry.role === 'user' || entry.role === 'assistant')
    .map((entry, index) => ({
      id: `trace_${entry.metadata?.message_id || `${sessionId}_${index}`}`,
      role: entry.role === 'user' ? 'user' : 'agent',
      content: entry.content,
      timestamp: Number(entry.metadata?.timestamp) || 0,
    }));
  if (!restored.length) return local;

  // Match occurrences from newest to oldest, including a cached suffix.
  // Identical requests in distinct turns must remain distinct.
  const matches = new Map<number, number>();
  const newestTimestamp = restored[restored.length - 1].timestamp;
  let before = restored.length;
  for (let index = local.length - 1; index >= 0; index--) {
    const message = local[index];
    if (message.role === 'system') continue;
    if (newestTimestamp && message.timestamp > newestTimestamp + 1000
      && !message.id.startsWith('resume_') && !message.id.startsWith('trace_')) continue;
    for (let target = before - 1; target >= 0; target--) {
      const persisted = restored[target];
      const completedStream = message.id.startsWith('stream_') && message.content.length > 0
        && persisted.content.startsWith(message.content);
      if (message.role === persisted.role
        && (message.id === persisted.id || message.content === persisted.content || completedStream)) {
        matches.set(index, target);
        restored[target] = {
          ...message, ...persisted,
          timestamp: persisted.timestamp || message.timestamp,
          // Retain websocket identity so terminal replay stays idempotent.
          id: message.id.startsWith('stream_') ? persisted.id : message.id,
        };
        before = target;
        break;
      }
    }
  }

  // Preserve notices and messages arriving during the HTTP request, placing
  // them before their next matched conversation message.
  const extras = new Map<number, ChatMessage[]>();
  let next = restored.length;
  for (let index = local.length - 1; index >= 0; index--) {
    const matched = matches.get(index);
    if (matched !== undefined) next = matched;
    else extras.set(next, [local[index], ...(extras.get(next) || [])]);
  }
  return restored.flatMap((message, index) => [...(extras.get(index) || []), message])
    .concat(extras.get(restored.length) || []);
}
