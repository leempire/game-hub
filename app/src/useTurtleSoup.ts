/** Socket state with stale-match rejection and no automatic paid-request retries. */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Room } from './api';
import { RoomSocket, type SocketMessage } from './socket';
import type { SoupResult, SoupView } from './turtleSoup';

export default function useTurtleSoup(code: string, token: string | null, uid?: number) {
  const [game, setGame] = useState<SoupView | null>(null);
  const [result, setResult] = useState<SoupResult | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [draft, updateDraft] = useState('');
  const [mode, setMode] = useState<'ask' | 'guess'>('ask');
  const socket = useRef<RoomSocket | null>(null);
  const current = useRef<SoupView | null>(null);
  const final = useRef<SoupResult | null>(null);
  const synced = useRef(false);
  const draftRef = useRef('');
  const editVersion = useRef(0);
  const pending = useRef<{ id: string; text: string; clearedVersion: number } | null>(null);
  const setDraft = useCallback((value: string) => {
    editVersion.current += 1;
    draftRef.current = value;
    updateDraft(value);
    setError('');
  }, []);
  const restoreDraft = useCallback(() => {
    const submission = pending.current;
    if (submission && draftRef.current === '' && editVersion.current === submission.clearedVersion) {
      setDraft(submission.text);
    }
    pending.current = null;
    setSending(false);
  }, [setDraft]);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    const retired = new Set<string>();
    const acceptResult = (value: SoupResult, authoritative = false) => {
      if (value.game !== 'turtle_soup' || !value.summary || retired.has(value.match_id)
        || (!authoritative && value.match_id !== current.current?.match_id)) return;
      final.current = value;
      setResult(value);
      pending.current = null;
      setSending(false);
    };
    const accept = (message: SocketMessage) => {
      const nextRoom = message.room as Room | undefined;
      const value = message.game as SoupView | undefined;
      if (message.type === 'sync' && !value && nextRoom?.status === 'waiting') {
        const previousResult = message.soup_result as unknown as SoupResult | undefined;
        if (previousResult && retired.has(previousResult.match_id)) return;
        setRoom(nextRoom);
        synced.current = true;
        setConnected(true);
        setWaiting(true);
        setGame(null);
        if (message.soup_result) acceptResult(message.soup_result as unknown as SoupResult, true);
        return;
      }
      if (!value?.match_id || retired.has(value.match_id) || final.current?.match_id === value.match_id) return;
      const before = current.current;
      const same = before?.match_id === value.match_id;
      const authoritative = message.type === 'sync' || message.type === 'game_start';
      if (!authoritative && (!synced.current || !same)) return;
      if (same && value.revision < before.revision) return;
      if (!same) {
        if (before) retired.add(before.match_id);
        if (final.current) retired.add(final.current.match_id);
        final.current = null;
        setResult(null);
        pending.current = null;
        setDraft('');
        setSending(false);
      }
      const resolved = value.requests.find(item => item.id === pending.current?.id && item.user_id === uid);
      if (resolved && resolved.status !== 'pending') {
        if (resolved.status === 'failed') {
          restoreDraft();
          setError(resolved.error || '主持暂未回答，请重新提交');
        } else {
          pending.current = null;
          setSending(false);
        }
      }
      if (authoritative && pending.current && !resolved) {
        restoreDraft();
        setError('上次提交未确认，请重新提交');
      }
      if (nextRoom) setRoom(nextRoom);
      current.current = value;
      setGame(value);
      synced.current = true;
      setConnected(true);
      setWaiting(false);
    };
    ws.on('sync', accept);
    ws.on('game_start', accept);
    ws.on('soup_state', accept);
    ws.on('game_over', message => acceptResult(message as unknown as SoupResult));
    ws.on('lobby', message => {
      const value = message.room as Room;
      if (value) setRoom(value);
      if (value?.status === 'waiting') { setWaiting(true); setGame(null); }
    });
    ws.on('error', message => {
      if (pending.current && typeof message.request_id === 'string' && message.request_id !== pending.current.id) return;
      restoreDraft();
      setError(typeof message.message === 'string' ? message.message : '提交失败，请重试');
    });
    ws.on('room_closed', () => { setWaiting(true); setGame(null); setError('房间已解散，请返回房间列表'); });
    const disconnected = () => { synced.current = false; setConnected(false); setSending(false); };
    ws.connect(disconnected, disconnected);
    return () => { synced.current = false; ws.close(); };
  }, [code, token, uid, setDraft, restoreDraft]);

  const send = () => {
    const value = current.current;
    if (!value || !synced.current || !socket.current?.isConnected || !value.can_submit
      || final.current || pending.current || !draftRef.current.trim()) return;
    const id = `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
    const text = draftRef.current;
    pending.current = { id, text, clearedVersion: editVersion.current + 1 };
    setDraft('');
    setSending(true);
    setError('');
    socket.current.send({ type: 'soup_submit', match_id: value.match_id, revision: value.revision,
      request_id: id, kind: mode, text });
  };
  const reveal = () => {
    const value = current.current;
    if (value?.can_reveal && synced.current && socket.current?.isConnected && !final.current) {
      socket.current.send({ type: 'soup_reveal', match_id: value.match_id, revision: value.revision });
    }
  };
  const close = useCallback(() => { synced.current = false; socket.current?.close(); }, []);
  return { game, result, room, connected, waiting, sending, error, draft, setDraft, mode, setMode, send, reveal, close };
}
