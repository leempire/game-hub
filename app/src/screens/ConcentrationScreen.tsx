import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { leaveRoom } from '../api';
import type { Room } from '../api';
import { onConfirmLeave } from '../backGuard';
import ConcentrationBoard from '../ConcentrationBoard';
import { CONCENTRATION_COLUMNS, concentrationPosition, concentrationSuits } from '../concentration';
import type { ConcentrationView } from '../concentration';
import { useAuth } from '../auth';
import GameShell, { GameResult } from '../GameShell';
import type { RootStackParamList } from '../navigation';
import { RoomSocket } from '../socket';
import type { SocketMessage } from '../socket';
import { colors, fontSize, gameSurfaces, radius, spacing } from '../theme';
import { AppModal } from '../ui';
import { notify } from '../utils';

const finishReasons = {
  completed: undefined,
  leave: '有玩家离开房间，提前结算',
  surrender: '有玩家认输，提前结算',
  disconnect: '有玩家断线超时，提前结算',
};

export default function ConcentrationScreen({ code }: { code: string }) {
  const { token, user } = useAuth();
  const uid = user?.id;
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const insets = useSafeAreaInsets();
  const { width, height, fontScale } = useWindowDimensions();
  const [game, setGame] = useState<ConcentrationView | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [online, setOnline] = useState<Record<number, boolean>>({});
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const socketRef = useRef<RoomSocket | null>(null);
  const gameRef = useRef<ConcentrationView | null>(null);
  const synced = useRef(false);
  const inLobby = useRef(false);
  const pendingRef = useRef<number | null>(null);
  const skipRemove = useRef(false);

  const changePending = useCallback((index: number | null) => {
    pendingRef.current = index;
    setPending(index);
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
    if (inLobby.current || gameRef.current?.phase === 'finished' || !gameRef.current) backToRoom();
    else setConfirm(true);
  }, [backToRoom]);

  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (Platform.OS === 'web' || skipRemove.current) return;
    event.preventDefault();
    requestBack();
  }), [navigation, requestBack]);
  useEffect(() => onConfirmLeave(requestBack), [requestBack]);

  useEffect(() => {
    if (!token) return;
    let disposed = false;
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;
    const rememberPlayers = (room?: Room) => {
      if (!room) return;
      setNames(previous => ({ ...previous, ...Object.fromEntries(
        room.players.map(player => [player.id, player.nickname || player.username]),
      ) }));
      setOnline(Object.fromEntries(room.players.map(player => [player.id, player.connected])));
    };
    const acceptGame = (msg: SocketMessage) => {
      const room = msg.room as Room | undefined;
      const sync = msg.type === 'sync';
      const live = msg.type === 'concentration_state';
      const waitingSync = sync && room?.status === 'waiting';
      const value = (waitingSync ? msg.concentration_result : msg.game) as ConcentrationView | undefined;
      const previous = gameRef.current;
      const sameMatch = previous && value && previous.match_id === value.match_id;
      if (live && (!synced.current || !sameMatch)) return;
      if (sameMatch && (value.revision < previous.revision || (live && value.revision === previous.revision))) return;
      if (value?.game === 'concentration') {
        gameRef.current = value;
        setGame(value);
        if (!sameMatch || value.phase === 'finished') setConfirm(false);
      } else if (waitingSync && previous?.phase !== 'finished') {
        gameRef.current = null;
        setGame(null);
        setConfirm(false);
      } else if (!waitingSync) return;
      rememberPlayers(room);
      const waitingNow = waitingSync || (live && inLobby.current);
      inLobby.current = waitingNow;
      setWaiting(waitingNow);
      synced.current = true;
      setConnected(true);
      changePending(null);
    };

    ws.on('sync', acceptGame);
    ws.on('game_start', acceptGame);
    ws.on('concentration_state', acceptGame);
    ws.on('lobby', msg => {
      const room = msg.room as Room | undefined;
      rememberPlayers(room);
      if (room?.status !== 'waiting') return;
      inLobby.current = true;
      setWaiting(true);
      changePending(null);
    });
    const playerConnection = (msg: SocketMessage, isOnline: boolean) => {
      const id = msg.user_id;
      if (typeof id === 'number') setOnline(previous => ({ ...previous, [id]: isOnline }));
    };
    ws.on('player_disconnected', msg => playerConnection(msg, false));
    ws.on('player_reconnected', msg => playerConnection(msg, true));
    ws.on('error', msg => {
      changePending(null);
      notify('提示', typeof msg.message === 'string' ? msg.message : '请稍后重试');
    });
    ws.on('room_closed', () => { notify('房间已解散'); exit(); });
    const waitForSync = () => {
      synced.current = false;
      setConnected(false);
      changePending(null);
    };
    ws.connect(waitForSync, closeCode => {
      if (disposed || skipRemove.current) return;
      waitForSync();
      if (closeCode === 4003 || closeCode === 4004) {
        notify(closeCode === 4003 ? '你已不在房间中' : '房间不存在');
        exit();
      }
    });
    return () => {
      disposed = true;
      socketRef.current = null;
      synced.current = false;
      ws.close();
    };
  }, [token, code, changePending, exit]);

  const flip = (index: number) => {
    const current = gameRef.current;
    const ws = socketRef.current;
    if (!current || !ws?.isConnected || !synced.current || inLobby.current || pendingRef.current !== null
      || (current.phase !== 'first' && current.phase !== 'second') || current.turn_id !== uid
      || current.slots[index]?.state !== 'hidden') return;
    changePending(index);
    ws.send({ type: 'concentration_flip', index, match_id: current.match_id, revision: current.revision });
  };
  const leave = async () => {
    if (!token || leaving) return;
    setLeaving(true);
    try {
      await leaveRoom(token, code);
      exit();
    } catch (error) {
      notify('退出失败', error instanceof Error ? error.message : '请稍后重试');
      setLeaving(false);
    }
  };

  const name = (id: number) => id === uid ? '你' : names[id] ?? `玩家 ${id}`;
  const finished = game?.phase === 'finished';
  const turnId = game?.turn_id;
  const turnName = turnId == null ? '' : `轮到${name(turnId)}`;
  const step = game?.phase === 'second' ? ' · 翻第 2 张' : ' · 翻第 1 张';
  const status = finished ? '对局结束'
    : !connected ? '正在同步牌桌…'
    : waiting || !game ? '房间正在等待开局'
    : game.phase === 'revealing' ? game.outcome === 'match' ? '配对成功 · +1 对' : '未配对'
    : pending !== null ? '正在翻牌…'
    : turnId == null ? '等待下一回合'
    : online[turnId] === false ? `等待 ${name(turnId)} 重连`
    : null;
  const revealed = connected ? game?.slots.flatMap((slot, index) => slot.state === 'revealed' ?
    [`${concentrationPosition(index)} ${concentrationSuits[slot.suit].name} ${slot.rank}`] : []) ?? [] : [];
  const scores = finished ? [...game.scores].sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity)) : [];
  const winners = scores.filter(score => score.rank === 1);
  const result = winners.length > 1 ? '并列第一' : winners.length === 1 ? `${name(winners[0].id)}获胜` : '本局结束';
  const wide = width - insets.left - insets.right >= 680;
  const rows = game ? game.slots.length / CONCENTRATION_COLUMNS : 6;
  const cardHeight = Math.max(48, Math.min(88, Math.floor((height - (wide ? 110 : 260)) / rows)))
    + Math.round(Math.max(0, Math.min(fontScale - 1, 0.6)) * 50);
  const canFlip = connected && !waiting && !confirm && pending === null && turnId === uid
    && (game?.phase === 'first' || game?.phase === 'second');

  const leaveButton = <Pressable testID="concentration-leave" accessibilityRole="button"
    onPress={requestBack} style={styles.leaveButton}><Text style={styles.leaveLabel}>离开房间</Text></Pressable>;

  return <View style={[styles.page, { paddingTop: insets.top, paddingLeft: insets.left,
    paddingRight: insets.right, paddingBottom: insets.bottom }]}>
    <GameShell game="concentration" code={code} connected={connected}>
      {game ? <ScrollView contentContainerStyle={styles.scroll}>
        <View style={[styles.layout, wide && styles.wide]}>
          <View style={[styles.hud, wide && styles.hudWide]}>
            <View testID="concentration-score-list" style={styles.scoreList}>
              {game.scores.map(score => {
                const active = !waiting && !finished && score.id === turnId;
                const label = [name(score.id), score.forfeited ? '弃权' : online[score.id] === false ? '离线' : null]
                  .filter(Boolean).join(' · ');
                return <View key={score.id} testID={`concentration-score-${score.id}`}
                  style={[styles.score, wide && styles.scoreWide, active && styles.scoreActive]}>
                  {active && <View style={styles.turnDot} />}
                  <Text numberOfLines={1} style={styles.name}>{label}</Text>
                  <Text style={styles.pairs}>{score.pairs} 对</Text>
                </View>;
              })}
            </View>
            <View style={styles.turn}>
              <View testID="concentration-status" accessibilityLiveRegion="polite" style={styles.status}>
                {status === null ? <>
                  <Text numberOfLines={1} style={[styles.statusText, styles.turnName]}>{turnName}</Text>
                  <Text numberOfLines={1} style={styles.statusText}>{step}</Text>
                </> : <Text numberOfLines={1} style={[styles.statusText,
                  game.outcome === 'miss' && styles.miss, game.outcome === 'match' && styles.match]}>{status}</Text>}
              </View>
              <Text style={styles.remaining}>剩 {game.remaining_pairs} 对</Text>
            </View>
            <Text numberOfLines={1} accessibilityLiveRegion="polite" style={styles.revealed}>
              {revealed.length ? revealed.join(' · ') : '\u00a0'}
            </Text>
            {wide && !waiting && !finished && leaveButton}
          </View>
          <View style={[styles.board, wide && styles.boardWide]}>
            <ConcentrationBoard key={game.match_id} slots={game.slots} height={cardHeight} connected={connected}
              canFlip={canFlip} pending={pending} keyboardEnabled={!confirm && !finished} onFlip={flip} />
          </View>
        </View>
        {waiting && !finished ? <Pressable testID="concentration-back-to-room" accessibilityRole="button"
          onPress={backToRoom} style={styles.leaveButton}><Text style={styles.leaveLabel}>返回房间</Text></Pressable>
          : !wide && !finished && leaveButton}
      </ScrollView> : <View style={styles.empty}>
        <Text testID="concentration-status" accessibilityLiveRegion="polite" style={styles.remaining}>{status}</Text>
        <Pressable testID="concentration-back-to-room" accessibilityRole="button" onPress={backToRoom} style={styles.leaveButton}>
          <Text style={styles.leaveLabel}>返回房间</Text>
        </Pressable>
      </View>}
      <AppModal visible={confirm && !finished} onClose={() => { if (!leaving) setConfirm(false); }} testID="concentration-confirm">
        <Text accessibilityRole="header" style={styles.title}>确认离开房间？</Text>
        <Text style={styles.remaining}>离开将记为本局弃权。</Text>
        <Pressable testID="concentration-confirm-leave" accessibilityRole="button" disabled={leaving}
          onPress={() => void leave()} style={[styles.leaveButton, styles.dangerButton]}>
          <Text style={styles.white}>{leaving ? '正在离开…' : '确认离开'}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" disabled={leaving} onPress={() => setConfirm(false)} style={styles.leaveButton}>
          <Text style={styles.leaveLabel}>取消</Text>
        </Pressable>
      </AppModal>
      {finished && <GameResult visible result={result} reason={game.reason ? finishReasons[game.reason] : undefined}
        players={scores.map(score => ({ id: score.id, name: names[score.id] ?? name(score.id), isMe: score.id === uid,
          identity: score.forfeited ? '弃权' : score.rank == null ? undefined : `第 ${score.rank} 名`, detail: `${score.pairs} 对` }))}
        roomButtonTestID="concentration-back-to-room" onBackToRoom={backToRoom}
        onViewHistory={() => { backToRoom(); navigation.navigate('History'); }} />}
    </GameShell>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.card },
  scroll: { alignItems: 'center', paddingHorizontal: spacing.sm + spacing.xs, paddingVertical: spacing.sm },
  layout: { alignItems: 'center', width: '100%', maxWidth: 480, gap: spacing.sm },
  wide: { flexDirection: 'row-reverse', alignItems: 'flex-start', maxWidth: 768, gap: spacing.lg },
  board: { width: '100%' },
  boardWide: { width: 'auto', flex: 1, minWidth: 0 },
  hud: { width: '100%', gap: spacing.xs },
  hudWide: { width: 232, padding: spacing.md, borderRadius: radius.lg, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.border },
  scoreList: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: spacing.xs },
  score: { width: '49%', minHeight: 30, flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.sm },
  scoreWide: { width: '100%', minHeight: 40 },
  scoreActive: { backgroundColor: gameSurfaces.selection, borderColor: colors.primary },
  turnDot: { width: 7, height: 7, borderRadius: radius.full, backgroundColor: colors.primary },
  name: { flex: 1, minWidth: 0, color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  pairs: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700', fontVariant: ['tabular-nums'] },
  turn: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  status: { flex: 1, minWidth: 0, flexDirection: 'row' },
  statusText: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  turnName: { minWidth: 0, flexShrink: 1 },
  remaining: { color: colors.textSecondary, fontSize: fontSize.sm },
  revealed: { color: colors.textSecondary, fontSize: fontSize.xs, minHeight: 16 },
  match: { color: colors.success },
  miss: { color: colors.warning },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '800' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.md },
  leaveButton: { alignItems: 'center', justifyContent: 'center', minHeight: 48, paddingHorizontal: spacing.md,
    borderRadius: radius.md },
  leaveLabel: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '700' },
  dangerButton: { backgroundColor: colors.danger },
  white: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
});
