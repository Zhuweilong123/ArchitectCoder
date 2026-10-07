import { useCallback, useMemo, useRef, useState } from 'react';
import type { ChatMessage } from './agentChatUtils';

function loadDraft(sessionId: string): string {
  try { return localStorage.getItem(`agentChatDraft:${sessionId}`) || ''; }
  catch { return ''; }
}

function saveDraft(sessionId: string, value: string) {
  try {
    if (value) localStorage.setItem(`agentChatDraft:${sessionId}`, value);
    else localStorage.removeItem(`agentChatDraft:${sessionId}`);
  } catch { /* Keep editing usable if browser storage is unavailable. */ }
}

/** Recall user inputs and save an independent unsent draft for each session. */
export function useChatInputHistory(messages: ChatMessage[], initialSessionId: string) {
  const sessionRef = useRef(initialSessionId);
  const [inputValue, updateInputValue] = useState(() => loadDraft(initialSessionId));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const draftRef = useRef(inputValue);
  const editsRef = useRef(new Map<string, string>());
  const history = useMemo(
    () => messages.filter((item) => item.role === 'user' && item.content.trim()),
    [messages],
  );
  const selectedIndex = history.findIndex((item) => item.id === selectedId);
  const currentIndex = selectedIndex < 0 ? history.length : selectedIndex;

  const setInputValue = useCallback((value: string) => {
    updateInputValue(value);
    // Save immediately so a refresh right after typing cannot lose the edit.
    saveDraft(sessionRef.current, value);
  }, []);

  const resetInputHistory = useCallback(() => {
    updateInputValue('');
    saveDraft(sessionRef.current, '');
    setSelectedId(null);
    draftRef.current = '';
    editsRef.current.clear();
  }, []);

  const activateInputSession = useCallback((sessionId: string) => {
    // Old-session edits are already saved; loading never clears either draft.
    sessionRef.current = sessionId;
    const draft = loadDraft(sessionId);
    updateInputValue(draft);
    setSelectedId(null);
    draftRef.current = draft;
    editsRef.current.clear();
  }, []);

  const fillHistoryInput = useCallback((next: ChatMessage | undefined, original = false) => {
    if (selectedIndex < 0) {
      draftRef.current = inputValue;
    } else {
      editsRef.current.set(history[selectedIndex].id, inputValue);
    }
    if (next && original) editsRef.current.delete(next.id);
    setSelectedId(next?.id ?? null);
    updateInputValue(next
      ? editsRef.current.get(next.id) ?? next.content
      : draftRef.current);
    // Browsing historical messages must not overwrite the unsent draft.
    if (!next) saveDraft(sessionRef.current, draftRef.current);
  }, [history, inputValue, selectedIndex]);

  const recallInput = useCallback((direction: -1 | 1) => {
    const nextIndex = currentIndex + direction;
    if (nextIndex < 0 || nextIndex > history.length) return;
    fillHistoryInput(history[nextIndex]);
  }, [currentIndex, history, fillHistoryInput]);

  const recallMessage = useCallback((messageId: string) => {
    const target = history.find((item) => item.id === messageId);
    // Clicking a bubble always fills its recorded original, without sending it.
    if (target) fillHistoryInput(target, true);
  }, [history, fillHistoryInput]);

  const restoreDraft = useCallback(() => {
    if (selectedIndex >= 0) fillHistoryInput(undefined);
  }, [selectedIndex, fillHistoryInput]);

  return {
    inputValue, setInputValue, resetInputHistory, activateInputSession, recallInput,
    recallMessage, restoreDraft,
    canRecallOlder: currentIndex > 0,
    canRecallNewer: selectedIndex >= 0,
    historyPosition: selectedIndex >= 0 ? selectedIndex + 1 : null,
    historyCount: history.length,
  };
}
