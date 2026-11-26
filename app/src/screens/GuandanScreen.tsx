import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { getRoom, leaveRoom } from '../api';
import type { Room } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave } from '../backGuard';
import GameShell, { GameResult } from '../GameShell';
import PokerPlayHistory from '../PokerPlayHistory';
import { describeGuandanCombo, describeGuandanPlay, formatGuandanResult, guandanRanks, isGuandanWild } from '../guandan';
import type {
  GuandanCard, GuandanCombo, GuandanCommand, GuandanQuery, GuandanResultMessage, GuandanVersion, GuandanView,
} from '../guandan';
import GuandanTable from '../GuandanTable';
import type { RootStackParamList } from '../navigation';
import { RoomSocket } from '../socket';
import type { SocketMessage } from '../socket';
import { colors, fontSize, radius, spacing } from '../theme';
import { AppModal } from '../ui';
import { notify } from '../utils';

type Pending = GuandanCommand | 'surrender' | null;
type Selection = { ids: number[]; combo: GuandanCombo | null };

function versionOf({ match_id, revision, deal }: GuandanView): GuandanVersion {
  return { match_id, revision, deal };
}

function matchesVersion(version: GuandanVersion, message: GuandanVersion | Record<string, unknown>) {
  return message.match_id === version.match_id && message.revision === version.revision && message.deal === version.deal;
}

function selectionHasWild(game: GuandanView, ids: number[]) {
  return game.hand.some(card => ids.includes(card.id) && isGuandanWild(card, game.level));
}

export default function GuandanScreen({ code }: { code: string }) {
  const { token, user } = useAuth();
  const uid = user?.id;
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { top = 0, left = 0, right = 0, bottom = 0 } = useSafeAreaInsets();
  const [game, setGame] = useState<GuandanView | null>(null);
  const [result, setResult] = useState<GuandanResultMessage | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [online, setOnline] = useState<Record<number, boolean>>({});
  const [bots, setBots] = useState<number[]>([]);
  const [hostId, setHostId] = useState<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [selection, setSelection] = useState<Selection>({ ids: [], combo: null });
  const [pending, setPending] = useState<Pending>(null);
  const [reading, setReading] = useState<GuandanQuery | null>(null);
  const [feedback, setFeedback] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [confirm, setConfirm] = useState<'leave' | 'surrender' | null>(null);
  const [leaving, setLeaving] = useState(false);

  const socket = useRef<RoomSocket | null>(null);
  const current = useRef<GuandanView | null>(null);
  const terminal = useRef<GuandanResultMessage | null>(null);
  const chosen = useRef<Selection>({ ids: [], combo: null });
  const submitting = useRef<Pending>(null);
  const request = useRef<{ action: GuandanQuery; id: number } | null>(null);
  const sequence = useRef(0);
  const synced = useRef(false);
  const inLobby = useRef(false);
  const skipRemove = useRef(false);

  const choose = useCallback((ids: number[], combo: GuandanCombo | null = null) => {
    const value = { ids, combo };
    chosen.current = value;
    setSelection(value);
  }, []);
  const updatePending = useCallback((action: Pending) => {
    submitting.current = action;
    setPending(action);
  }, []);
  const clearQuery = useCallback(() => {
    request.current = null;
    setReading(null);
  }, []);
  const canSend = useCallback((game: GuandanView) =>
    current.current !== null && matchesVersion(game, current.current)
    && game.phase !== 'finished' && synced.current && !inLobby.current && !terminal.current
    && !submitting.current && socket.current?.isConnected, []);

  const query = useCallback((action: GuandanQuery, game: GuandanView, ids: number[] = []) => {
    if (!canSend(game) || game.phase !== 'playing' || game.current_id !== uid) return;
    const id = ++sequence.current;
    request.current = { action, id };
    setReading(action);
    socket.current?.send({ type: 'guandan_action', ...versionOf(game), action, request_id: id,
      ...(action === 'preview' ? { cards: ids } : {}) });
  }, [canSend, uid]);

  const backToRoom = useCallback(() => {
    skipRemove.current = true;
    socket.current?.close();
    navigation.popTo('Room', { code });
  }, [navigation, code]);
  const exit = useCallback(() => {
    skipRemove.current = true;
    socket.current?.close();
    navigation.popTo('MainTabs');
  }, [navigation]);
  const requestBack = useCallback(() => {
    if (pickerOpen) { setPickerOpen(false); return; }
    if (inLobby.current || terminal.current || !current.current) backToRoom();
    else setConfirm('leave');
  }, [backToRoom, pickerOpen]);
  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (Platform.OS === 'web' || skipRemove.current) return;
    event.preventDefault();
    requestBack();
  }), [navigation, requestBack]);
  useEffect(() => onConfirmLeave(() => {
    if (navigation.isFocused?.() !== false) requestBack();
  }), [navigation, requestBack]);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    const retired = new Set<string>();
    const rememberRoom = (room?: Room) => {
      if (!room) return;
      setHostId(room.host_id);
      setNames(previous => ({ ...previous, ...Object.fromEntries(
        room.players.map(player => [player.id, player.nickname || player.username]),
      ) }));
      setOnline(Object.fromEntries(room.players.map(player => [player.id, player.connected])));
      setBots(room.players.filter(player => player.is_bot).map(player => player.id));
    };
    const acceptResult = (value: GuandanResultMessage, fromSync = false) => {
      const knownMatch = current.current?.match_id ?? terminal.current?.match_id;
      if (value.game !== 'guandan' || value.summary?.match_id !== value.match_id || retired.has(value.match_id)
        || (knownMatch ? knownMatch !== value.match_id : !fromSync)
        || (uid !== undefined && !value.summary.players.includes(uid))) return;
      terminal.current = value;
      setResult(value);
      setConfirm(null);
      setPickerOpen(false);
      choose([]);
      clearQuery();
      setFeedback('');
      updatePending(null);
    };
    const showWaiting = () => {
      inLobby.current = true;
      setWaiting(true);
      setGame(null);
      setConfirm(null);
      setPickerOpen(false);
      choose([]);
      clearQuery();
      setFeedback('');
      updatePending(null);
    };
    const acceptGame = (message: SocketMessage) => {
      const room = message.room as Room | undefined;
      const value = message.game as GuandanView | undefined;
      const previous = current.current;
      if (message.type === 'sync' && !value && room?.status === 'waiting') {
        const result = message.guandan_result as GuandanResultMessage | undefined;
        const knownMatch = previous?.match_id ?? terminal.current?.match_id;
        if (result && (retired.has(result.match_id) || (knownMatch && result.match_id !== knownMatch))) return;
        if (!result && synced.current && !inLobby.current && previous?.phase !== 'finished') return;
        rememberRoom(room);
        showWaiting();
        synced.current = true;
        setConnected(true);
        if (result) acceptResult(result, true);
        return;
      }
      if (value?.game !== 'guandan' || !value.match_id || !value.players.includes(uid ?? -1)
        || retired.has(value.match_id) || terminal.current?.match_id === value.match_id) return;
      const sameMatch = previous?.match_id === value.match_id;
      const authoritative = message.type === 'sync' || message.type === 'game_start';
      if (!authoritative && (!synced.current || inLobby.current || !sameMatch)) return;
      if (sameMatch && (value.revision < previous.revision || (!authoritative && value.revision === previous.revision))) return;
      const changedPhase = !sameMatch || previous.deal !== value.deal || previous.phase !== value.phase;
      const refresh = changedPhase || previous.revision !== value.revision || !synced.current;
      if (!sameMatch) {
        if (previous) retired.add(previous.match_id);
        if (terminal.current) retired.add(terminal.current.match_id);
        terminal.current = null;
        setResult(null);
        setConfirm(null);
      }
      if (changedPhase) setPickerOpen(false);
      current.current = value;
      rememberRoom(room);
      inLobby.current = false;
      synced.current = true;
      setWaiting(false);
      setConnected(true);
      setGame(value);
      if (refresh) {
        const ids = changedPhase || submitting.current === 'pass' ? [] : chosen.current.ids.filter(id =>
          value.hand.some(card => card.id === id) && (value.phase !== 'returning' || value.return_choices.includes(id)));
        choose(ids);
        clearQuery();
        setFeedback('');
        updatePending(null);
        if (selectionHasWild(value, ids)) query('preview', value, ids);
      }
    };
    const reply = (action: GuandanQuery, message: SocketMessage) => {
      const value = current.current;
      if (!value || !canSend(value) || request.current?.action !== action || request.current.id !== message.request_id
        || !matchesVersion(value, message)) return;
      clearQuery();
      const combo = message.combo as GuandanCombo | null;
      if (combo && combo.cards.every(card => value.hand.some(entry => entry.id === card.id))) {
        choose(action === 'hint' ? combo.cards.map(card => card.id) : chosen.current.ids, combo);
        setFeedback(describeGuandanCombo(combo, value.level));
      } else {
        setFeedback(typeof message.message === 'string' ? message.message
          : action === 'hint' ? '没有可用提示' : '这组牌无法出牌');
      }
    };
    ws.on('sync', acceptGame);
    ws.on('game_start', acceptGame);
    ws.on('guandan_state', acceptGame);
    ws.on('guandan_hint', message => reply('hint', message));
    ws.on('guandan_preview', message => reply('preview', message));
    ws.on('game_over', message => acceptResult(message as unknown as GuandanResultMessage));
    const updateOnline = (message: SocketMessage, active: boolean) => {
      const id = message.user_id;
      if (typeof id === 'number') setOnline(previous => ({ ...previous, [id]: active }));
    };
    ws.on('player_disconnected', message => updateOnline(message, false));
    ws.on('player_reconnected', message => updateOnline(message, true));
    ws.on('lobby', message => {
      const room = message.room as Room | undefined;
      rememberRoom(room);
      if (room?.status === 'waiting' && (!current.current || terminal.current || current.current.phase === 'finished')) showWaiting();
    });
    ws.on('error', message => {
      const value = current.current;
      if (message.game === 'guandan' && (!value || !matchesVersion(value, message) || !submitting.current)) return;
      const detail = typeof message.message === 'string' ? message.message : '请稍后重试';
      if (submitting.current) {
        setFeedback(detail);
        updatePending(null);
      } else notify('提示', detail);
    });
    ws.on('room_closed', () => { notify('房间已解散'); exit(); });
    const waitForSync = () => {
      synced.current = false;
      setConnected(false);
      updatePending(null);
      clearQuery();
      setFeedback('');
    };
    const blur = navigation.addListener('blur', () => { ws.pause(); setPickerOpen(false); });
    const focus = navigation.addListener('focus', () => ws.resume());
    ws.connect(waitForSync, waitForSync);
    return () => { blur(); focus(); synced.current = false; socket.current = null; ws.close(); };
  }, [token, code, uid, navigation, exit, canSend, query, choose, clearQuery, updatePending]);

  const name = (id: number) => names[id] ?? `玩家 ${id}`;
  const toggle = (card: GuandanCard) => {
    if (!game || !canSend(game) || !game.hand.some(entry => entry.id === card.id)) return;
    const ids = chosen.current.ids;
    if (game.phase === 'returning' && !game.return_choices.includes(card.id)) return;
    if (game.phase !== 'returning' && game.phase !== 'playing') return;
    const next = ids.includes(card.id) ? ids.filter(id => id !== card.id) : game.phase === 'returning' ? [card.id] : [...ids, card.id];
    choose(next);
    clearQuery();
    setFeedback('');
    if (selectionHasWild(game, next)) query('preview', game, next);
  };
  const sendAction = (action: GuandanCommand | 'hint') => {
    if (!game || !canSend(game)) return;
    if (action === 'hint') { query(action, game); return; }
    const { ids, combo } = chosen.current;
    if ((action === 'play' || action === 'pass') && (game.phase !== 'playing' || game.current_id !== uid)) return;
    if (action === 'pass' && !game.last_play) return;
    if (action === 'play' && (!ids.length || ids.some(id => !game.hand.some(card => card.id === id))
      || (selectionHasWild(game, ids) && !combo))) return;
    if (action === 'return' && (game.phase !== 'returning' || ids.length !== 1 || !game.return_choices.includes(ids[0]))) return;
    if (action === 'next' && (game.phase !== 'between' || hostId !== uid)) return;
    const payload = action === 'play' ? { cards: ids } : action === 'return' ? { card: ids[0] } : {};
    clearQuery();
    setFeedback('');
    updatePending(action);
    socket.current?.send({ type: 'guandan_action', ...versionOf(game), action, ...payload });
  };
  const surrender = () => {
    if (!game || !canSend(game)) return;
    setConfirm(null);
    clearQuery();
    updatePending('surrender');
    socket.current?.surrender();
  };
  const leave = async () => {
    if (!token || leaving) return;
    setLeaving(true);
    try {
      await leaveRoom(token, code);
      exit();
    } catch (cause) {
      const room = await getRoom(token, code).catch(() => null);
      if (room && uid !== undefined && !room.players.some(player => player.id === uid)) exit();
      else notify('退出失败', cause instanceof Error ? cause.message : '请稍后重试');
    } finally {
      setLeaving(false);
    }
  };

  const summary = result?.summary;
  const formatted = summary && formatGuandanResult(summary, uid);
  const finalRanks = summary?.deal_result?.deal === summary?.deal && summary?.deal_result
    ? guandanRanks(summary.players, summary.deal_result) : [];
  return <View style={[styles.page, { paddingTop: top, paddingLeft: left, paddingRight: right, paddingBottom: bottom }]}>
    <GameShell game="guandan" code={code} connected={connected} compact>
      {game && uid !== undefined && !result
        ? <GuandanTable key={game.match_id} game={game} uid={uid} host={hostId === uid} name={name} online={online} bots={bots}
          connected={connected} pending={!!pending} selected={selection.ids} pickerOpen={pickerOpen}
          previewReady={!!selection.combo || !selectionHasWild(game, selection.ids)}
          feedback={reading === 'preview' ? '正在判定牌型…' : reading === 'hint' ? '正在查找提示…' : feedback}
          onPicker={setPickerOpen} onToggle={toggle} onAction={sendAction}
          onSurrender={() => setConfirm('surrender')} onLeave={() => setConfirm('leave')} />
        : !result && <View style={styles.empty}>
          <Text accessibilityLiveRegion="polite" style={styles.note}>{waiting ? '房间正在等待开局' : '正在同步对局…'}</Text>
          <Pressable accessibilityRole="button" onPress={backToRoom} style={styles.secondary}>
            <Text style={styles.note}>返回房间</Text>
          </Pressable>
        </View>}
      <AppModal visible={confirm !== null} onClose={() => { if (!leaving) setConfirm(null); }} testID="guandan-confirm">
        <Text accessibilityRole="header" style={styles.title}>{confirm === 'surrender' ? '确认认输？' : '确认离开房间？'}</Text>
        <Text style={styles.note}>继续操作会判你的队伍负。</Text>
        <Pressable testID="guandan-confirm-submit" accessibilityRole="button"
          disabled={leaving || (confirm === 'surrender' && (!connected || !!pending))}
          style={styles.danger} onPress={() => { if (confirm === 'surrender') surrender(); else void leave(); }}>
          <Text style={styles.dangerLabel}>{leaving ? '正在离开…' : confirm === 'surrender' ? '确认认输' : '确认离开'}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" disabled={leaving} onPress={() => setConfirm(null)} style={styles.secondary}>
          <Text style={styles.note}>取消</Text>
        </Pressable>
      </AppModal>
      {formatted && summary && <GameResult visible result={formatted.headline} reason={formatted.detail}
        roomButtonTestID="guandan-back-to-room" onBackToRoom={backToRoom}
        onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>
        <View testID="guandan-result" style={styles.results}>
          {summary.players.map((id, index) => <View key={id} style={styles.resultRow}>
            <Text style={styles.note}>{name(id)}{id === uid ? '（我）' : ''}
              {summary.remaining?.[index] !== undefined ? ` · 剩 ${summary.remaining[index]} 张` : ''}</Text>
            <Text style={styles.note}>{finalRanks.find(entry => entry.id === id)?.place ?? `第 ${index % 2 + 1} 队`}</Text>
          </View>)}
        </View>
        {!!summary.play_history?.length && <PokerPlayHistory game="guandan" records={summary.play_history}
          name={name} describe={describeGuandanPlay} result />}
      </GameResult>}
    </GameShell>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minHeight: 0, backgroundColor: colors.card },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, padding: spacing.md },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '800' },
  note: { color: colors.textSecondary, fontSize: fontSize.sm },
  danger: { minHeight: 48, backgroundColor: colors.danger, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  dangerLabel: { color: colors.white, fontSize: fontSize.sm, fontWeight: '700' },
  secondary: { minHeight: 48, paddingHorizontal: spacing.md, alignItems: 'center', justifyContent: 'center' },
  results: { gap: spacing.xs, paddingVertical: spacing.xs },
  resultRow: { backgroundColor: colors.surfaceMuted, borderRadius: radius.sm, padding: spacing.sm,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm },
});
