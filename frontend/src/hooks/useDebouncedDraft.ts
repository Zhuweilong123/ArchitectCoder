import { useCallback, useEffect, useRef, useState } from 'react';

type DraftCommit = () => void;

interface DebouncedDraft {
  scheduleDraft: (key: string, value: unknown, commit: DraftCommit) => void;
  flushDraft: (key: string) => unknown;
  draftValue: <T>(key: string, fallback: T) => T;
}

/**
 * Keeps text input responsive while coalescing high-frequency store writes.
 * Pending values are flushed by the caller on blur and committed on unmount.
 */
export function useDebouncedDraft(delayMs = 300): DebouncedDraft {
  const [, forceRender] = useState(0);
  const draftsRef = useRef<Map<string, unknown>>(new Map());
  const commitsRef = useRef<Map<string, DraftCommit>>(new Map());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const scheduleDraft = useCallback((key: string, value: unknown, commit: DraftCommit) => {
    draftsRef.current.set(key, value);
    commitsRef.current.set(key, commit);
    forceRender((version) => version + 1);

    const previousTimer = timersRef.current.get(key);
    if (previousTimer) clearTimeout(previousTimer);
    const timer = setTimeout(() => {
      commit();
      draftsRef.current.delete(key);
      commitsRef.current.delete(key);
      timersRef.current.delete(key);
      forceRender((version) => version + 1);
    }, delayMs);
    timersRef.current.set(key, timer);
  }, [delayMs]);

  const flushDraft = useCallback((key: string) => {
    const timer = timersRef.current.get(key);
    if (timer) clearTimeout(timer);
    timersRef.current.delete(key);
    if (!draftsRef.current.has(key)) return undefined;
    const value = draftsRef.current.get(key);
    draftsRef.current.delete(key);
    commitsRef.current.delete(key);
    forceRender((version) => version + 1);
    return value;
  }, []);

  const draftValue = useCallback(<T,>(key: string, fallback: T): T => {
    return (draftsRef.current.has(key) ? draftsRef.current.get(key) : fallback) as T;
  }, []);

  useEffect(() => () => {
    timersRef.current.forEach((timer) => clearTimeout(timer));
    commitsRef.current.forEach((commit) => commit());
    timersRef.current.clear();
    commitsRef.current.clear();
    draftsRef.current.clear();
  }, []);

  return { scheduleDraft, flushDraft, draftValue };
}
