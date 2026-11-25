import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AppState, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { HeaderBackButton, useHeaderHeight } from '@react-navigation/elements';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { getRoom, leaveRoom, type Room } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave } from '../backGuard';
import GameShell, { GameResult } from '../GameShell';
import type { RootStackParamList } from '../navigation';
import { RoomSocket, type SocketMessage } from '../socket';
import { colors, fontSize, gameSurfaces, radius, spacing } from '../theme';
import { AppModal, Card } from '../ui';
import UndercoverSummary, { UndercoverEliminations, UndercoverTallies } from '../UndercoverSummary';
import { undercoverReasonLabels, undercoverWinnerLabels, type UndercoverResultMessage, type UndercoverView } from '../undercover';
import { notify } from '../utils';

const phaseLabels = { clue: '轮流发言', vote: '投票', runoff: '平票重投', finished: '结算' };

export default function UndercoverScreen({ code }: { code: string }) {
  const { token, user } = useAuth();
  const uid = user?.id;
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const headerHeight = useHeaderHeight();
  const { bottom } = useSafeAreaInsets();
  const [game, setGame] = useState<UndercoverView | null>(null);
  const [result, setResult] = useState<UndercoverResultMessage | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [wordOpen, setWordOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [deadline, setDeadline] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const socketRef = useRef<RoomSocket | null>(null);
  const gameRef = useRef<UndercoverView | null>(null);
  const selectedRef = useRef<number | null>(null);
  const pendingRef = useRef(false);
  const synced = useRef(false);
  const inLobby = useRef(false);
  const resultRef = useRef<UndercoverResultMessage | null>(null);
  const skipRemove = useRef(false);

  const clearSelection = useCallback(() => { selectedRef.current = null; setSelected(null); }, []);
  const setSubmitting = useCallback((value: boolean) => { pendingRef.current = value; setPending(value); }, []);
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
    setWordOpen(false);
    const current = gameRef.current;
    if (inLobby.current || resultRef.current || !current || (uid != null && !current.alive.includes(uid))) backToRoom();
    else setConfirmLeave(true);
  }, [backToRoom, uid]);
  useLayoutEffect(() => {
    if (Platform.OS === 'web') {
      navigation.setOptions({ headerLeft: ({ tintColor }) => (
        <HeaderBackButton tintColor={tintColor} onPress={requestBack} accessibilityLabel="返回" />
      ) });
    }
  }, [navigation, requestBack]);
  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (Platform.OS === 'web' || skipRemove.current) return;
    event.preventDefault();
    requestBack();
  }), [navigation, requestBack]);
  useEffect(() => onConfirmLeave(requestBack), [requestBack]);
  useEffect(() => {
    const hideWord = () => setWordOpen(false);
    const change = AppState.addEventListener('change', state => { if (state !== 'active') hideWord(); });
    const blur = Platform.OS === 'android' ? AppState.addEventListener('blur', hideWord) : undefined;
    const unsubscribe = navigation.addListener('blur', hideWord);
    return () => { change.remove(); blur?.remove(); unsubscribe(); };
  }, [navigation]);
  useEffect(() => {
    if (!deadline || !connected || !game || game.paused || game.phase === 'finished') return;
    const timer = setInterval(() => setSeconds(Math.max(0, Math.ceil((deadline - Date.now()) / 1000))), 250);
    return () => clearInterval(timer);
  }, [connected, deadline, game]);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;
    const retired = new Set<string>();
    const rememberNames = (room?: Room) => {
      if (room) setNames(previous => ({ ...previous, ...Object.fromEntries(
        room.players.map(player => [player.id, player.nickname || player.username]),
      ) }));
    };
    const acceptResult = (value: UndercoverResultMessage, fromSync = false) => {
      const currentMatch = gameRef.current?.match_id;
      if (value.game !== 'undercover' || !value.summary || retired.has(value.match_id)
        || (!fromSync && value.match_id !== currentMatch)) return;
      resultRef.current = value;
      setResult(value);
      setWordOpen(false);
      clearSelection();
      setSubmitting(false);
      setError('');
      setConfirmLeave(false);
    };
    const acceptGame = (msg: SocketMessage) => {
      const room = msg.room as Room | undefined;
      const value = msg.game as UndercoverView | undefined;
      if (msg.type === 'sync' && !value && room?.status === 'waiting') {
        const previousResult = msg.undercover_result as UndercoverResultMessage | undefined;
        if (previousResult && retired.has(previousResult.match_id)) return;
        rememberNames(room);
        synced.current = true;
        inLobby.current = true;
        setConnected(true);
        setWaiting(true);
        setGame(null);
        setWordOpen(false);
        if (previousResult) acceptResult(previousResult, true);
        return;
      }
      if (!value?.match_id || retired.has(value.match_id) || resultRef.current?.match_id === value.match_id) return;
      const previous = gameRef.current;
      const sameMatch = previous?.match_id === value.match_id;
      const authoritative = msg.type === 'sync' || msg.type === 'game_start';
      if (!authoritative && (!synced.current || inLobby.current || !sameMatch)) return;
      if (sameMatch && value.revision < previous.revision) return;
      if (sameMatch && !authoritative && value.revision === previous.revision
        && !!value.paused === !!previous.paused) return;
      rememberNames(room);
      if (!sameMatch) {
        if (previous) retired.add(previous.match_id);
        if (resultRef.current) retired.add(resultRef.current.match_id);
        resultRef.current = null;
        setResult(null);
        setWordOpen(false);
        setConfirmLeave(false);
      }
      if (!sameMatch || previous.phase_id !== value.phase_id) {
        setDraft('');
        clearSelection();
        setSubmitting(false);
        setError('');
      } else if (value.your_vote != null || value.phase === 'finished'
        || (selectedRef.current != null && !value.candidate_ids.includes(selectedRef.current))) {
        clearSelection();
        setSubmitting(false);
      }
      inLobby.current = false;
      synced.current = true;
      gameRef.current = value;
      setWaiting(false);
      setConnected(true);
      setGame(value);
      setDeadline(Date.now() + value.remaining_ms);
      setSeconds(Math.ceil(value.remaining_ms / 1000));
    };
    ws.on('sync', acceptGame);
    ws.on('game_start', acceptGame);
    ws.on('undercover_state', acceptGame);
    ws.on('game_over', msg => acceptResult(msg as unknown as UndercoverResultMessage));
    ws.on('lobby', msg => {
      const room = msg.room as Room | undefined;
      if (room?.status !== 'waiting') return;
      rememberNames(room);
      inLobby.current = true;
      setWaiting(true);
      setGame(null);
      setWordOpen(false);
    });
    ws.on('error', msg => {
      const message = typeof msg.message === 'string' ? msg.message : '请稍后重试';
      if (pendingRef.current) setError(message);
      else notify('提示', message);
      setSubmitting(false);
    });
    ws.on('room_closed', () => { notify('房间已解散'); exit(); });
    const waitForSync = () => {
      synced.current = false;
      setConnected(false);
      clearSelection();
      setSubmitting(false);
      setError('');
      setWordOpen(false);
    };
    ws.connect(waitForSync, waitForSync);
    return () => { synced.current = false; ws.close(); };
  }, [token, code, exit, clearSelection, setSubmitting]);

  const name = (id: number) => names[id] ?? `玩家 ${id}`;
  const ownName = (id: number) => id === uid ? `${name(id)}（我）` : name(id);
  const canSend = () => {
    const current = gameRef.current;
    return !!game && !!current && current.match_id === game.match_id && current.phase_id === game.phase_id
      && synced.current && socketRef.current?.isConnected && !inLobby.current && !resultRef.current && !pendingRef.current;
  };
  const sendClue = () => {
    if (!canSend() || !gameRef.current?.can_clue || !draft.trim()) return;
    setError('');
    setSubmitting(true);
    socketRef.current?.send({ type: 'undercover_clue', match_id: game?.match_id, phase_id: game?.phase_id, text: draft });
  };
  const sendVote = () => {
    if (!canSend() || selected === null || selected !== selectedRef.current
      || gameRef.current?.your_vote != null || !gameRef.current?.candidate_ids.includes(selected)) return;
    setError('');
    setSubmitting(true);
    socketRef.current?.send({ type: 'undercover_vote', match_id: game?.match_id, phase_id: game?.phase_id, target_id: selected });
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

  const eliminated = !!game && uid != null && !game.alive.includes(uid);
  const voting = game?.phase === 'vote' || game?.phase === 'runoff';
  const canClue = !!game?.can_clue && !result;
  const canVote = voting && !eliminated && game.your_vote == null && !!game.candidate_ids.length && !result;
  const clueDisabled = !connected || pending || !draft.trim();
  const voteDisabled = !connected || pending || selected === null;
  const currentClues = game?.clues.filter(clue => clue.round === game.round) ?? [];
  const previousClues = game?.clues.filter(clue => clue.round < game.round) ?? [];
  const stageNote = !game ? null
    : game.phase === 'clue'
      ? game.can_clue ? '轮到你发言' : game.speaker_id != null ? `${name(game.speaker_id)} 正在发言` : '等待下一位发言'
      : voting ? `已投票 ${game.votes_cast} / ${game.alive.length}` : '正在结算';
  const showError = !!error && (canClue || canVote);

  return <KeyboardAvoidingView style={styles.page} behavior="padding" keyboardVerticalOffset={headerHeight}>
    <GameShell game="undercover" code={code} connected={connected}
      status={game && <View style={styles.status}>
        <View style={styles.statusMain}>
          <Text accessibilityRole="header" style={styles.title}>第 {game.round} 轮 · {phaseLabels[game.phase]}</Text>
          <Text accessibilityLiveRegion="polite" style={styles.note}>{game.paused ? '存活玩家均已离线，计时暂停' : stageNote}</Text>
        </View>
        {game.phase !== 'finished' && <View style={styles.clock} accessibilityLabel={connected ? `剩余 ${seconds} 秒` : '计时待同步'}>
          <Ionicons name="time-outline" size={18} color={colors.primary} />
          <Text style={styles.clockText} testID="undercover-clock">{connected ? `${seconds} 秒` : '—'}</Text>
        </View>}
      </View>}
      actions={<View style={[styles.actions, { paddingBottom: bottom }]}>
        {showError && <Text style={styles.error} accessibilityRole="alert">{error}</Text>}
        {canClue && <View style={styles.inputRow}>
          <TextInput testID="undercover-clue-input" accessibilityLabel="你的线索" value={draft}
            editable={connected && !pending} placeholder="写一句线索（1–60 字）" placeholderTextColor={colors.textMuted}
            onChangeText={value => { setDraft(value); setError(''); }}
            autoCapitalize="none" autoCorrect={false} style={styles.input} />
          <TouchableOpacity testID="undercover-clue-submit" accessibilityRole="button" disabled={clueDisabled}
            onPress={sendClue} style={[styles.button, styles.primary, clueDisabled && styles.disabled]}>
            <Text style={clueDisabled ? styles.note : styles.buttonText}>{pending ? '提交中…' : '发送'}</Text>
          </TouchableOpacity>
        </View>}
        {canVote && <TouchableOpacity testID="undercover-vote-confirm" accessibilityRole="button" disabled={voteDisabled}
          onPress={sendVote} style={[styles.button, styles.primary, voteDisabled && styles.disabled]}>
          <Text style={voteDisabled ? styles.note : styles.buttonText}>
            {pending ? '提交中…' : selected === null ? '选择玩家后确认' : `确认投给 ${name(selected)}`}
          </Text>
        </TouchableOpacity>}
        {(!game || waiting || eliminated) && !result
          ? <TouchableOpacity testID="undercover-back-to-room" accessibilityRole="button" onPress={backToRoom}
            style={[styles.button, styles.primary]}><Text style={styles.buttonText}>返回房间</Text></TouchableOpacity>
          : !result && <TouchableOpacity accessibilityRole="button" onPress={() => { setWordOpen(false); setConfirmLeave(true); }}
            style={styles.leave}><Text style={styles.note}>离开房间</Text></TouchableOpacity>}
      </View>}>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {!game && <Text style={styles.note}>{waiting ? '房间正在等待开局' : '正在同步对局…'}</Text>}
        {game?.your_word && !result && <Card style={styles.wordCard}>
          <TouchableOpacity testID="undercover-word-toggle" accessibilityRole="button" accessibilityState={{ expanded: wordOpen }}
            disabled={!connected} onPress={() => setWordOpen(value => !value)} style={styles.wordToggle}>
            <Ionicons name={wordOpen ? 'eye-off-outline' : 'eye-outline'} size={22} color={colors.primary} />
            <Text style={styles.title}>{wordOpen ? '收起我的词' : '查看我的词'}</Text>
          </TouchableOpacity>
          {wordOpen && <Text testID="undercover-word" style={styles.word}>{game.your_word}</Text>}
        </Card>}
        {eliminated && <Text testID="undercover-eliminated" style={styles.banner}>你已出局，可以继续查看线索和票数</Text>}
        {voting && game.your_vote != null && <Text style={styles.banner}>已锁票 · 投给 {name(game.your_vote)}</Text>}
        {voting && !eliminated && game.your_vote == null && game.candidate_ids.length === 0
          && <Text style={styles.banner}>本轮没有可投的玩家</Text>}
        {canVote && <Card style={styles.section}>
          <Text accessibilityRole="header" style={styles.title}>选择你怀疑的玩家</Text>
          <View style={styles.candidates}>
            {game.candidate_ids.map(id =>
              <TouchableOpacity key={id} testID={`undercover-vote-${id}`} accessibilityRole="button"
                accessibilityState={{ selected: selected === id }} disabled={!connected || pending}
                onPress={() => {
                  if (!canSend() || !gameRef.current?.candidate_ids.includes(id)) return;
                  selectedRef.current = id;
                  setSelected(id);
                  setError('');
                }}
                style={[styles.candidate, selected === id && styles.selected]}>
                <Text style={styles.candidateName}>{name(id)}</Text>
                {selected === id && <Ionicons name="checkmark" size={18} color={colors.primary} />}
              </TouchableOpacity>)}
          </View>
        </Card>}
        {game && <Card style={styles.section}>
          <Text accessibilityRole="header" style={styles.title}>本轮线索</Text>
          {game.turn_order.map(id => {
            const clue = currentClues.find(item => item.user_id === id);
            const active = game.speaker_id === id;
            return <View key={id} style={styles.clueRow}>
              <Text style={[styles.playerName, active && styles.active]}>{ownName(id)}</Text>
              <Text style={clue?.text != null ? styles.clue : styles.note}>
                {clue ? clue.text ?? '超时跳过' : active ? '正在发言' : '等待发言'}
              </Text>
            </View>;
          })}
        </Card>}
        {!!game?.eliminations.length && <Card style={styles.section}>
          <Text accessibilityRole="header" style={styles.title}>出局记录</Text>
          <UndercoverEliminations eliminations={game.eliminations} name={ownName} />
        </Card>}
        {!!game?.tallies.length && <Card style={styles.section}>
          <Text accessibilityRole="header" style={styles.title}>已公布票数</Text>
          <UndercoverTallies tallies={game.tallies} name={name} />
        </Card>}
        {!!previousClues.length && <Card style={styles.section}>
          <Text accessibilityRole="header" style={styles.title}>往轮线索</Text>
          {[...previousClues].reverse().map(clue =>
            <View key={`${clue.round}-${clue.user_id}`} style={styles.clueRow}>
              <Text style={styles.playerName}>第 {clue.round} 轮 · {ownName(clue.user_id)}</Text>
              <Text style={clue.text != null ? styles.clue : styles.note}>{clue.text ?? '超时跳过'}</Text>
            </View>)}
        </Card>}
      </ScrollView>
    </GameShell>
    <AppModal visible={confirmLeave} onClose={() => { if (!leaving) setConfirmLeave(false); }}>
      <Text style={styles.title}>确认离开房间？</Text>
      <Text style={styles.note}>对局中离开将记为负局。</Text>
      <TouchableOpacity accessibilityRole="button" disabled={leaving} onPress={() => void leave()}
        style={[styles.button, styles.danger]}>
        <Text style={styles.buttonText}>{leaving ? '正在离开…' : '确认离开'}</Text>
      </TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" disabled={leaving} onPress={() => setConfirmLeave(false)}
        style={styles.leave}><Text style={styles.note}>取消</Text></TouchableOpacity>
    </AppModal>
    {result && <GameResult visible result={undercoverWinnerLabels[result.winner]}
      reason={uid != null && result.forfeited.includes(uid) ? '你已离场，本局记负' : undercoverReasonLabels[result.reason]}
      roomButtonTestID="undercover-back-to-room" onBackToRoom={backToRoom}
      onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>
      <UndercoverSummary summary={result.summary} name={ownName} testID="undercover-result" />
    </GameResult>}
  </KeyboardAvoidingView>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minHeight: 0 },
  scroll: { flex: 1, minHeight: 0 },
  content: { alignSelf: 'center', width: '100%', maxWidth: 680, padding: spacing.sm, gap: spacing.sm },
  status: { width: '100%', maxWidth: 680, alignSelf: 'center', flexDirection: 'row', alignItems: 'center',
    gap: spacing.sm, paddingHorizontal: spacing.xs },
  statusMain: { flex: 1, minWidth: 0, gap: spacing.xs },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  note: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 20 },
  clock: { flexDirection: 'row', gap: spacing.xs, alignItems: 'center', padding: spacing.sm,
    backgroundColor: gameSurfaces.selection, borderRadius: radius.md },
  clockText: { color: colors.primaryDark, fontSize: fontSize.sm, fontVariant: ['tabular-nums'], fontWeight: '800' },
  wordCard: { backgroundColor: gameSurfaces.selection, padding: spacing.xs },
  wordToggle: { paddingHorizontal: spacing.sm, minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  word: { color: colors.primaryDark, fontSize: fontSize.xxl, fontWeight: '800', paddingHorizontal: spacing.sm,
    paddingBottom: spacing.sm },
  banner: { color: colors.textSecondary, backgroundColor: colors.surfaceMuted, padding: spacing.sm,
    borderRadius: radius.md, fontSize: fontSize.sm, fontWeight: '700' },
  section: { gap: spacing.sm },
  candidates: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  candidate: { width: '47%', flexGrow: 1, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 48,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, borderWidth: 1, borderColor: colors.borderStrong,
    borderRadius: radius.md, backgroundColor: colors.card },
  candidateName: { flex: 1, minWidth: 0, color: colors.text, fontSize: fontSize.sm, fontWeight: '700' },
  selected: { borderColor: colors.primary, borderWidth: 2, backgroundColor: gameSurfaces.selection },
  clueRow: { gap: 2, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.xs },
  playerName: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '700' },
  active: { color: colors.primaryDark },
  clue: { color: colors.text, fontSize: fontSize.md, lineHeight: 22 },
  actions: { width: '100%', maxWidth: 680, alignSelf: 'center', paddingHorizontal: spacing.sm, paddingTop: spacing.xs },
  inputRow: { flexDirection: 'row', gap: spacing.sm },
  input: { flex: 1, minWidth: 0, minHeight: 48, paddingHorizontal: spacing.sm, borderWidth: 1,
    borderColor: colors.borderStrong, backgroundColor: colors.card, borderRadius: radius.md,
    color: colors.text, fontSize: fontSize.md },
  button: { minHeight: 48, paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
    alignItems: 'center', justifyContent: 'center', borderRadius: radius.md },
  primary: { backgroundColor: colors.primary },
  disabled: { backgroundColor: colors.disabled },
  danger: { backgroundColor: colors.danger },
  buttonText: { color: colors.white, fontWeight: '800', fontSize: fontSize.md, textAlign: 'center' },
  leave: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  error: { color: colors.danger, fontSize: fontSize.sm, paddingBottom: spacing.xs },
});
