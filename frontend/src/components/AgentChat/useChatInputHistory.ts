import { useCallback, useMemo, useRef, useState } from 'react';
import type { ChatMessage } from './agentChatUtils';

/** Recall current-session user inputs without changing the recorded messages. */
export function useChatInputHistory(messages: ChatMessage[]) {
  const [inputValue, setInputValue] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const draftRef = useRef('');
  const editsRef = useRef(new Map<string, string>());
  const history = useMemo(
    () => messages.filter((item) => item.role === 'user' && item.content.trim()),
    [messages],
  );
  const selectedIndex = history.findIndex((item) => item.id === selectedId);
  const currentIndex = selectedIndex < 0 ? history.length : selectedIndex;

  const resetInputHistory = useCallback(() => {
    setInputValue('');
    setSelectedId(null);
    draftRef.current = '';
    editsRef.current.clear();
  }, []);

  const recallInput = useCallback((direction: -1 | 1) => {
    const nextIndex = currentIndex + direction;
    if (nextIndex < 0 || nextIndex > history.length) return;
    if (selectedIndex < 0) {
      draftRef.current = inputValue;
    } else {
      editsRef.current.set(history[selectedIndex].id, inputValue);
    }
    const next = history[nextIndex];
    setSelectedId(next?.id ?? null);
    setInputValue(next
      ? editsRef.current.get(next.id) ?? next.content
      : draftRef.current);
  }, [currentIndex, history, inputValue, selectedIndex]);

  return {
    inputValue, setInputValue, resetInputHistory, recallInput,
    canRecallOlder: currentIndex > 0,
    canRecallNewer: selectedIndex >= 0,
    historyPosition: selectedIndex >= 0 ? selectedIndex + 1 : null,
    historyCount: history.length,
  };
}
