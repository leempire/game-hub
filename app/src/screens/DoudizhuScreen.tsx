import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { getRoom, leaveRoom } from '../api';
import type { Room, RoomSetting } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave } from '../backGuard';
import { describeDoudizhuPlay, doudizhuRole, formatDoudizhuResult } from '../doudizhu';
import type { DoudizhuAction, DoudizhuResultMessage, DoudizhuView } from '../doudizhu';
import DoudizhuTable from '../DoudizhuTable';
import GameShell, { GameResult } from '../GameShell';
import PokerPlayHistory from '../PokerPlayHistory';
import type { RootStackParamList } from '../navigation';
import { RoomSocket } from '../socket';
import type { SocketMessage } from '../socket';
import { colors, fontSize, radius, spacing } from '../theme';
import { AppModal } from '../ui';
import { notify } from '../utils';

type PendingAction = DoudizhuAction['action'] | 'surrender' | null;

export default function DoudizhuScreen({ code }: { code: string }) {
  const { token, user } = useAuth();
  const uid = user?.id;
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { top = 0, left = 0, right = 0, bottom = 0 } = useSafeAreaInsets();
  const [game, setGame] = useState<DoudizhuView | null>(null);
  const [result, setResult] = useState<DoudizhuResultMessage | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [online, setOnline] = useState<Record<number, boolean>>({});
  const [settings, setSettings] = useState<RoomSetting[]>([]);
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState<PendingAction>(null);
  const [feedback, setFeedback] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [confirm, setConfirm] = useState<'leave' | 'surrender' | null>(null);
  const [leaving, setLeaving] = useState(false);

  const socketRef = useRef<RoomSocket | null>(null);
  const gameRef = useRef<DoudizhuView | null>(null);
  const resultRef = useRef<DoudizhuResultMessage | null>(null);
  const pendingRef = useRef<PendingAction>(null);
  const synced = useRef(false);
  const inLobby = useRef(false);
  const skipRemove = useRef(false);
  const changePending = useCallback((action: PendingAction) => {
    pendingRef.current = action;
    setPending(action);
  }, []);

  const backToRoom = useCallback(() => {
    skipRemove.current = true;
    socketRef.current?.close();
    navigation.popTo('Room', { code });
  }, [navigation, code]);
  const exit = useCallback(() => {
    skipRemove.current = true;
    socketRef.current?.close();
    navigation.popTo('MainTabs');
  }, [navigation]);
  const requestBack = useCallback(() => {
    if (pickerOpen) { setPickerOpen(false); return; }
    if (inLobby.current || resultRef.current || !gameRef.current) backToRoom();
    else setConfirm('leave');
  }, [backToRoom, pickerOpen]);
  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (Platform.OS === 'web' || skipRemove.current) return;
    event.preventDefault();
    requestBack();
  }), [navigation, requestBack]);
  useEffect(() => onConfirmLeave(requestBack), [requestBack]);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;
    const retired = new Set<string>();
    const rememberPlayers = (room?: Room) => {
      if (!room) return;
      setNames(previous => ({ ...previous, ...Object.fromEntries(
        room.players.map(player => [player.id, player.nickname || player.username]),
      ) }));
      setOnline(Object.fromEntries(room.players.map(player => [player.id, player.connected])));
    };
    const acceptResult = (value: DoudizhuResultMessage, fromSync = false) => {
      const current = gameRef.current;
      if (value.game !== 'doudizhu' || value.summary?.match_id !== value.match_id || retired.has(value.match_id)
        || (!fromSync && current?.match_id !== value.match_id)
        || (current?.match_id === value.match_id && value.summary.revision < current.revision)) return;
      resultRef.current = value;
      setResult(value);
      setConfirm(null);
      setPickerOpen(false);
      setSelected([]);
      setFeedback('');
      changePending(null);
    };
    const acceptGame = (msg: SocketMessage) => {
      const room = msg.room as Room | undefined;
      const value = msg.game as DoudizhuView | undefined;
      if (msg.type === 'sync' && !value && room?.status === 'waiting') {
        const previousResult = msg.doudizhu_result as DoudizhuResultMessage | undefined;
        if (previousResult && retired.has(previousResult.match_id)) return;
        rememberPlayers(room);
        synced.current = true;
        inLobby.current = true;
        setConnected(true);
        setWaiting(true);
        setGame(null);
        setPickerOpen(false);
        if (previousResult) acceptResult(previousResult, true);
        return;
      }
      if (value?.game !== 'doudizhu' || !value.match_id || retired.has(value.match_id)
        || resultRef.current?.match_id === value.match_id) return;
      const previous = gameRef.current;
      const sameMatch = previous?.match_id === value.match_id;
      const authoritative = msg.type === 'sync' || msg.type === 'game_start';
      if (!authoritative && (!synced.current || inLobby.current || !sameMatch)) return;
      if (sameMatch && (value.revision < previous.revision || (!authoritative && value.revision === previous.revision))) return;
      const advanced = previous?.revision !== value.revision;
      if (!sameMatch) {
        if (previous) retired.add(previous.match_id);
        if (resultRef.current) retired.add(resultRef.current.match_id);
        resultRef.current = null;
        setResult(null);
        setConfirm(null);
        setPickerOpen(false);
      }
      if (!sameMatch || previous.phase !== value.phase || (advanced && pendingRef.current === 'pass')) setSelected([]);
      else setSelected(previousSelection => previousSelection.filter(card => value.hand.includes(card)));
      rememberPlayers(room);
      if (room?.settings) setSettings(room.settings);
      if (!sameMatch || advanced) {
        setFeedback('');
        changePending(null);
      }
      inLobby.current = false;
      synced.current = true;
      gameRef.current = value;
      setWaiting(false);
      setConnected(true);
      setGame(value);
    };
    ws.on('sync', acceptGame);
    ws.on('game_start', acceptGame);
    ws.on('doudizhu_state', acceptGame);
    ws.on('doudizhu_hint', msg => {
      const current = gameRef.current;
      if (!synced.current || inLobby.current || resultRef.current || pendingRef.current !== 'hint' || !current
        || msg.match_id !== current.match_id || msg.revision !== current.revision || !Array.isArray(msg.cards)) return;
      const cards = current.hand.filter(card => (msg.cards as unknown[]).includes(card));
      changePending(null);
      if (cards.length > 0) {
        setSelected(cards);
        setFeedback('');
      } else {
        setFeedback(current.can_pass ? '没有能压过的牌，可以不出' : '暂无可用提示');
      }
    });
    ws.on('game_over', msg => acceptResult(msg as unknown as DoudizhuResultMessage));
    const updateConnection = (msg: SocketMessage, active: boolean) => {
      const id = msg.user_id;
      if (typeof id === 'number') setOnline(previous => ({ ...previous, [id]: active }));
    };
    ws.on('player_disconnected', msg => updateConnection(msg, false));
    ws.on('player_reconnected', msg => updateConnection(msg, true));
    ws.on('lobby', msg => {
      const room = msg.room as Room | undefined;
      rememberPlayers(room);
      if (room?.status !== 'waiting') return;
      inLobby.current = true;
      setWaiting(true);
      setGame(null);
      setPickerOpen(false);
      setConfirm(null);
    });
    ws.on('error', msg => {
      const message = typeof msg.message === 'string' ? msg.message : '请稍后重试';
      if (pendingRef.current) setFeedback(message);
      else notify('提示', message);
      changePending(null);
    });
    ws.on('room_closed', () => { notify('房间已解散'); exit(); });
    const waitForSync = () => {
      synced.current = false;
      setConnected(false);
      changePending(null);
      setFeedback('');
    };
    ws.connect(waitForSync, waitForSync);
    return () => { synced.current = false; ws.close(); };
  }, [token, code, exit, changePending]);

  const name = (id: number) => names[id] ?? (id < 0 ? '人机' : `玩家 ${id}`);
  const canSend = () => {
    const current = gameRef.current;
    return game && current?.match_id === game.match_id && current.revision === game.revision && current.phase !== 'finished'
      && synced.current && !inLobby.current && !resultRef.current && !pendingRef.current && socketRef.current?.isConnected;
  };
  const sendAction = (action: DoudizhuAction) => {
    if (!canSend() || !game || game.current_id !== uid) return;
    if (action.action === 'bid' && (game.phase !== 'bidding' || !game.available_bids.includes(action.bid))) return;
    if (action.action !== 'bid' && game.phase !== 'playing') return;
    if (action.action === 'pass' && !game.can_pass) return;
    if (action.action === 'play' && (!action.cards.length || action.cards.some(card => !game.hand.includes(card)))) return;
    setFeedback('');
    changePending(action.action);
    socketRef.current?.send({ type: 'doudizhu_action', match_id: game.match_id, revision: game.revision, ...action });
  };
  const surrender = () => {
    if (!canSend() || game?.phase !== 'playing') return;
    setConfirm(null);
    setFeedback('');
    changePending('surrender');
    socketRef.current?.surrender();
  };
  const leave = async () => {
    if (!token || leaving) return;
    setLeaving(true);
    try {
      await leaveRoom(token, code);
      exit();
    } catch (cause) {
      const room = await getRoom(token, code).catch(() => null);
      if (room && uid != null && !room.players.some(player => player.id === uid)) exit();
      else notify('退出失败', cause instanceof Error ? cause.message : '请稍后重试');
    } finally {
      setLeaving(false);
    }
  };

  const summary = result?.summary;
  const formatted = summary && formatDoudizhuResult(summary);
  return <View style={[styles.page, { paddingTop: top, paddingLeft: left, paddingRight: right, paddingBottom: bottom }]}>
    <GameShell game="doudizhu" code={code} connected={connected}>
      {game && uid != null && !result
        ? <DoudizhuTable key={game.match_id} game={game} uid={uid} name={name} connected={connected} pending={!!pending} selected={selected}
          online={online} settings={settings}
          feedback={feedback} pickerOpen={pickerOpen} onPicker={setPickerOpen} onAction={sendAction}
          onToggle={card => {
            if (pendingRef.current || !synced.current || !gameRef.current?.hand.includes(card)) return;
            setSelected(previous => previous.includes(card) ? previous.filter(value => value !== card) : [...previous, card]);
            setFeedback('');
          }}
          onSurrender={() => setConfirm('surrender')} onLeave={() => setConfirm('leave')} />
        : !result && <View style={styles.empty}>
          <Text accessibilityLiveRegion="polite" style={styles.note}>{waiting ? '房间正在等待开局' : '正在同步对局…'}</Text>
          <Pressable accessibilityRole="button" onPress={backToRoom} style={styles.secondaryButton}>
            <Text style={styles.note}>返回房间</Text>
          </Pressable>
        </View>}
      <AppModal visible={confirm !== null} onClose={() => { if (!leaving) setConfirm(null); }} testID="doudizhu-confirm">
        <Text accessibilityRole="header" style={styles.title}>{confirm === 'surrender' ? '确认认输？' : '确认离开房间？'}</Text>
        <Text style={styles.note}>{game?.phase === 'bidding' ? '现在离开会取消本局。' : '继续操作会判你的阵营负。'}</Text>
        <Pressable accessibilityRole="button" disabled={leaving || (confirm === 'surrender' && (!connected || !!pending))}
          style={styles.dangerButton} onPress={() => { if (confirm === 'surrender') surrender(); else void leave(); }}>
          <Text style={styles.dangerLabel}>{leaving ? '正在离开…' : confirm === 'surrender' ? '确认认输' : '确认离开'}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" disabled={leaving} onPress={() => setConfirm(null)} style={styles.secondaryButton}>
          <Text style={styles.note}>取消</Text>
        </Pressable>
      </AppModal>
      {formatted && summary && <GameResult visible result={formatted.headline} reason={formatted.detail}
        roomButtonTestID="doudizhu-back-to-room" onBackToRoom={backToRoom}
        onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>
        <View testID="doudizhu-result" style={styles.scores}>
          {summary.seat_uids.map((id, seat) => {
            const score = summary.scores?.[id];
            return <View key={id} style={styles.scoreRow}>
              <Text style={styles.scoreName}>{name(id)}{id === uid ? '（我）' : ''} · {doudizhuRole(id, summary.landlord_id)}
                {summary.remaining?.[seat] !== undefined ? ` · 剩 ${summary.remaining[seat]} 张` : ''}</Text>
              <Text style={styles.score}>{score !== undefined ? `${score > 0 ? '+' : ''}${score} 分` : '—'}</Text>
            </View>;
          })}
        </View>
        {!!summary.play_history?.length && <PokerPlayHistory game="doudizhu" records={summary.play_history}
          name={name} describe={describeDoudizhuPlay} result />}
      </GameResult>}
    </GameShell>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minHeight: 0, backgroundColor: colors.card },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, padding: spacing.md },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '800' },
  note: { color: colors.textSecondary, fontSize: fontSize.sm },
  dangerButton: { minHeight: 48, backgroundColor: colors.danger, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  dangerLabel: { color: colors.white, fontSize: fontSize.sm, fontWeight: '700' },
  secondaryButton: { minHeight: 48, paddingHorizontal: spacing.md, alignItems: 'center', justifyContent: 'center' },
  scores: { gap: spacing.xs, paddingVertical: spacing.xs },
  scoreRow: { backgroundColor: colors.surfaceMuted, borderRadius: radius.sm, padding: spacing.sm,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm },
  scoreName: { flex: 1, color: colors.text, fontSize: fontSize.sm },
  score: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700', fontVariant: ['tabular-nums'] },
});
