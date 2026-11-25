/** Focus-scoped foreground polling. Responses from a previous session never update UI. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

type IsCurrent = () => boolean;
type Task = (isCurrent: IsCurrent) => Promise<void>;
export function startForegroundPolling(task: Task, onError: (error: unknown) => void, interval: number) {
  let alive = true, epoch = 0, busy = false;
  let state = AppState.currentState;
  const run = async () => {
    if (!alive || busy || state !== 'active') return;
    busy = true;
    const generation = epoch;
    const isCurrent = () => alive && state === 'active' && generation === epoch;
    try { await task(isCurrent); }
    catch (error) { if (isCurrent()) onError(error); }
    finally { busy = false; }
  };
  void run();
  const timer = setInterval(() => void run(), interval);
  const subscription = AppState.addEventListener('change', next => {
    if (state === next) return;
    state = next; epoch++;
    if (next === 'active') void run();
  });
  return () => { alive = false; epoch++; clearInterval(timer); subscription.remove(); };
}

export default function useForegroundPolling(key: string, task: Task,
  onError: (error: unknown) => void, interval = 3000) {
  const callbacks = useRef({ task, onError });
  useEffect(() => { callbacks.current = { task, onError }; }, [task, onError]);
  const [reload, setReload] = useState(0);
  const activeSession = useRef('');
  useFocusEffect(useCallback(() => {
    const session = `${key}:${reload}`;
    activeSession.current = session;
    return startForegroundPolling(
      valid => callbacks.current.task(() => valid() && activeSession.current === session),
      error => callbacks.current.onError(error), interval,
    );
  }, [key, reload, interval]));
  return useCallback(() => setReload(value => value + 1), []);
}
