import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, ScrollView, Modal, StyleSheet, Platform, useWindowDimensions } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import { onConfirmLeave, useModalBackGuard } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import GameShell, { GameResult } from '../GameShell';
import { shellPlayers } from '../gamePlayers';
import { colors, playerColors, withAlpha, spacing, radius, fontSize, lastMoveMark, lastMoveMarkEdge } from '../theme';
import { notify } from '../utils';
import UtttBoard from '../UtttBoard';
import { UtttLegalMove, UtttState, UTTT_BOARD_NAMES, utttSymbol } from '../uttt';

interface Props { code: string; onLeave: () => void }
type Preview = UtttLegalMove & { revision: number };
const resultLabel = (winner: UtttState['winner']) => winner === 'draw' ? '和棋' : `${utttSymbol(winner || 0)} 胜`;
const reasons: Record<string, string> = { three_boards: '大盘三连', draw: '所有小盘已结束', surrender: '认输结束', disconnect: '断线超时', leave: '退出结束' };

export default function UltimateTicTacToeScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const { width, height } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<UtttState | null>(null);
  const [connected, setConnected] = useState(false);
  const [inLobby, setInLobby] = useState(false);
  const [focus, setFocus] = useState(4);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const [showResult, setShowResult] = useState(false);
  const [dcSeconds, setDcSeconds] = useState<number | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  const socketRef = useRef<RoomSocket | null>(null);
  const gameRef = useRef<UtttState | null>(null);
  const previewRef = useRef<Preview | null>(null);
  const submittingRef = useRef(false);
  const connectedRef = useRef(false);
  const inLobbyRef = useRef(false);
  const skipRemoveRef = useRef(false);
  const leaveRef = useRef(onLeave);
  useEffect(() => { leaveRef.current = onLeave; }, [onLeave]);
  const opponent = room?.players.find(p => p.id !== user?.id);
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcSeconds ?? opponent?.reconnect_left);
  const clearPreview = useCallback(() => { previewRef.current = null; setPreview(null); }, []);

  const backToRoom = useCallback(() => {
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.popTo('Room', { code });
  }, [navigation, code]);
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  useEffect(() => navigation.addListener('beforeRemove', (event: any) => {
    if (Platform.OS === 'web' || skipRemoveRef.current || gameRef.current?.winner || inLobbyRef.current) return;
    event.preventDefault();
    clearPreview();
    setConfirm('leave');
  }), [navigation, clearPreview]);
  useEffect(() => onConfirmLeave(() => {
    if (gameRef.current?.winner || inLobbyRef.current) backToRoom();
    else { clearPreview(); setConfirm('leave'); }
  }), [backToRoom, clearPreview]);

  useEffect(() => {
    if (!token) return;
    let disposed = false;
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;
    const resetInput = () => { clearPreview(); submittingRef.current = false; setSubmitting(false); };
    const acceptRoom = (value: Room) => {
      setRoom(value);
      const opp = value.players.find(p => p.id !== user?.id);
      setOpponentDisconnected(value.status === 'playing' && !!opp && !opp.connected);
      setDcSeconds(opp?.reconnect_left ?? null);
    };
    const acceptGame = (msg: SocketMessage) => {
      const value = msg.game as UtttState | undefined;
      if (msg.room) acceptRoom(msg.room as Room);
      if (!value) {
        if ((msg.room as Room | undefined)?.status === 'waiting') {
          resetInput();
          inLobbyRef.current = true;
          setInLobby(true);
          connectedRef.current = false;
          setConnected(ws.isConnected);
        }
        return;
      }
      resetInput();
      inLobbyRef.current = false;
      setInLobby(false);
      gameRef.current = value;
      setGame(value);
      connectedRef.current = ws.isConnected;
      setConnected(ws.isConnected);
      if (value.active_board !== null) setFocus(value.active_board);
      else setFocus(current => value.local_results[current] === 0 ? current : value.legal[0]?.board ?? 4);
      setConfirm(null);
      setShowResult(!!value.winner);
    };
    ws.on('sync', acceptGame);
    ws.on('game_start', acceptGame);
    ws.on('uttt_state', acceptGame);
    ws.on('lobby', msg => { if (msg.room) acceptRoom(msg.room as Room); });
    ws.on('game_over', msg => {
      resetInput();
      const previous = gameRef.current;
      if (previous) {
        const value = { ...previous, winner: msg.winner as UtttState['winner'], reason: msg.reason as string, legal: [] };
        gameRef.current = value;
        setGame(value);
      }
      setConfirm(null);
      setShowResult(true);
    });
    ws.on('player_disconnected', msg => {
      if (msg.user_id !== user?.id) { setOpponentDisconnected(true); setDcSeconds(msg.timeout as number ?? null); }
    });
    ws.on('player_reconnected', msg => {
      if (msg.user_id !== user?.id) { setOpponentDisconnected(false); setDcSeconds(null); }
    });
    ws.on('error', msg => { resetInput(); notify('提示', msg.message as string); });
    ws.on('room_closed', () => { ws.close(); skipRemoveRef.current = true; notify('房间已解散'); leaveRef.current(); });
    getRoom(token, code).then(value => { if (!disposed) acceptRoom(value); }).catch(() => {});
    // 打开连接后仍等 sync，不能用断线前的盘面确认。
    const waitForSync = () => {
      if (disposed) return;
      connectedRef.current = false;
      setConnected(false);
      resetInput();
    };
    ws.connect(waitForSync, waitForSync);
    return () => { disposed = true; connectedRef.current = false; ws.close(); };
  }, [token, code, user?.id, clearPreview]);

  const selectCell = (cell: number) => {
    const current = gameRef.current;
    if (!current || !connectedRef.current || !socketRef.current?.isConnected || submittingRef.current || current.winner || current.next !== current.your_color) return;
    const move = current.legal.find(item => item.board === focus && item.cell === cell);
    if (!move) return;
    const value = { ...move, revision: current.revision };
    previewRef.current = value;
    setPreview(value);
  };
  const confirmMove = () => {
    const current = gameRef.current;
    const value = previewRef.current;
    const ws = socketRef.current;
    if (!current || !value || !ws?.isConnected || !connectedRef.current || submittingRef.current || current.winner || current.next !== current.your_color || value.revision !== current.revision) return;
    if (!current.legal.some(item => item.board === value.board && item.cell === value.cell)) { clearPreview(); return; }
    // ref 先锁住同一帧的重复点击，等权威响应再解锁。
    submittingRef.current = true;
    setSubmitting(true);
    ws.utttMove(value.board, value.cell, value.revision);
  };
  const leave = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); }
    catch (error) { notify('退出失败', error instanceof Error ? error.message : '请稍后重试'); return; }
    socketRef.current?.close();
    skipRemoveRef.current = true;
    leaveRef.current();
  };

  const myTurn = !!game && !game.winner && !inLobby && game.next === game.your_color;
  const canPlay = myTurn && connected && !submitting;
  const legalHere = game?.legal.filter(move => move.board === focus) ?? [];
  const turn = !connected ? '正在重连，等待同步…' : inLobby ? '本局已结束或尚未开始' : game?.winner ? '对局结束' : myTurn ? '轮到你落子' : opponent?.is_bot ? `${opponent.nickname || opponent.username} 思考中…` : '等待对方落子';
  const localResult = game?.local_results[focus] ?? 0;
  const focusNote = localResult ? `${localResult === 3 ? '和棋' : utttSymbol(localResult) + ' 已赢下'} · 已封盘，仅查看`
    : !legalHere.length ? '本回合不可落子 · 仅查看' : !myTurn ? '对手回合 · 仅查看' : '点格预览，再确认';
  const previewText = preview ? preview.winner ? `本手将结束对局：${resultLabel(preview.winner)}`
    : `下一步对手：${preview.next_board === null ? '任意未结束的小盘' : UTTT_BOARD_NAMES[preview.next_board] + '小盘'}`
    : game?.winner ? resultLabel(game.winner) : '先选一个空格，预览对手下一步去哪里';
  const compact = height < 700;
  const overviewSize = Math.min(width - spacing.md * 2, compact ? 174 : 222);
  const cellSize = compact ? 46 : 56;
  const lastMove = game?.moves[game.moves.length - 1];

  return (
    <GameShell game="uttt" code={code} connected={connected} players={shellPlayers(room, user?.id, game)} style={styles.page} actions={<View testID="uttt-actions" style={styles.actions}>
        <Text testID="uttt-destination" accessibilityLiveRegion="polite" style={styles.destination}>{!connected ? '连接断开，预览已清空；同步后可继续' : submitting ? '正在提交，等待服务器确认…' : previewText}</Text>
        <View style={styles.row}>
          <TouchableOpacity testID="uttt-cancel" accessibilityRole="button" disabled={!preview || submitting} onPress={clearPreview} style={[styles.button, styles.secondary, (!preview || submitting) && styles.disabled]}><Text style={styles.label}>取消选择</Text></TouchableOpacity>
          <TouchableOpacity testID="uttt-confirm" accessibilityRole="button" disabled={!preview || !canPlay} onPress={confirmMove} style={[styles.button, styles.primary, styles.grow, (!preview || !canPlay) && styles.disabled]}><Text style={styles.white}>{submitting ? '提交中…' : '确认落子'}</Text></TouchableOpacity>
        </View>
        <View style={styles.row}>
          <TouchableOpacity testID="uttt-surrender" accessibilityRole="button" disabled={!game || !!game.winner || inLobby || !connected} onPress={() => { clearPreview(); setConfirm('surrender'); }} style={[styles.dangerButton, (!game || !!game.winner || inLobby || !connected) && styles.disabled]}><Text style={styles.dangerText}>认输</Text></TouchableOpacity>
          <TouchableOpacity testID="uttt-leave" accessibilityRole="button" onPress={() => { clearPreview(); setConfirm('leave'); }} style={styles.dangerButton}><Text style={styles.muted}>离开房间</Text></TouchableOpacity>
        </View>
      </View>}>
      <View style={styles.header}>
        <View style={styles.grow}>
          <Text style={styles.identity}>你是 <Text style={{ color: game?.your_color === 'white' ? playerColors.red : playerColors.blue }}>{utttSymbol(game?.your_color || 0)}</Text> · X 先手</Text>
          <Text accessibilityLiveRegion="polite" style={styles.turn}>{turn}</Text>
        </View>

      </View>
      {opponentDisconnected && !game?.winner && <Text style={styles.disconnect}>对方已断线，等待重连（{dcLeft} 秒），超时判负</Text>}
      <ScrollView testID="uttt-scroll" style={styles.grow} contentContainerStyle={styles.content}>
        {inLobby && <TouchableOpacity testID="uttt-back-from-lobby" accessibilityRole="button" onPress={backToRoom} style={[styles.button, styles.primary]}><Text style={styles.white}>返回房间</Text></TouchableOpacity>}
        {!game ? <Text style={styles.muted}>正在同步棋盘…</Text> : <>
          <View style={styles.sectionLabel}><Text style={styles.label}>全盘概览</Text><Text style={styles.muted}>{game.winner ? '最终盘面' : game.active_board === null ? '本回合：自由选盘' : `本回合：${UTTT_BOARD_NAMES[game.active_board]}小盘`}</Text></View>
          <UtttBoard boards={game.boards} local_results={game.local_results} size={overviewSize}
            activeBoard={game.active_board} focus={focus} lastMove={lastMove}
            pending={preview ? { ...preview, color: game.your_color } : null}
            nextBoard={preview && !preview.winner ? preview.next_board : undefined}
            eligibleBoards={Array.from(new Set(game.legal.map(move => move.board)))}
            onSelect={board => { if (!submittingRef.current) { clearPreview(); setFocus(board); } }} />
          <View style={styles.sectionLabel}><Text style={styles.label}>{UTTT_BOARD_NAMES[focus]}小盘</Text><Text style={styles.muted}>{focusNote}</Text></View>
          <View testID="uttt-focus" style={styles.focusBoard}>
            {[0, 1, 2].map(row => <View key={row} style={styles.gridRow}>
              {[0, 1, 2].map(col => {
                const cell = row * 3 + col;
                const pending = preview?.board === focus && preview.cell === cell;
                const value = game.boards[focus][cell];
                const symbol = utttSymbol(value || (pending ? game.your_color : 0));
                const disabled = !canPlay || !legalHere.some(move => move.cell === cell);
                const isLast = lastMove?.board === focus && lastMove.cell === cell;
                return <TouchableOpacity key={cell} testID={`uttt-cell-${focus}-${cell}`} activeOpacity={0.7}
                  accessibilityRole="button" accessibilityLabel={`${UTTT_BOARD_NAMES[focus]}小盘，第${row + 1}行第${col + 1}列，${value ? symbol : pending ? '待确认 ' + symbol : disabled ? '不可落子' : '空格，可选'}`}
                  accessibilityState={{ disabled, selected: pending }} disabled={disabled} onPress={() => selectCell(cell)}
                  style={[styles.cell, { width: cellSize, height: cellSize }, pending && styles.pending,
                    col < 2 && styles.rightLine, row < 2 && styles.bottomLine]}>
                  <Text style={[styles.symbol, { color: value === 1 || (pending && game.your_color === 'black') ? playerColors.blue : playerColors.red }, pending && { opacity: 0.5 }]}>{symbol}</Text>
                  {isLast && <View style={styles.lastMove} />}
                </TouchableOpacity>;
              })}
            </View>)}
          </View>
          <Text style={styles.muted}>X {game.local_results.filter(value => value === 1).length} 盘 · O {game.local_results.filter(value => value === 2).length} 盘 · 已落 {game.moves.length} 手 · 不限思考时间</Text>
        </>}
      </ScrollView>

      <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.overlay}><View style={styles.modal}>
          <Text style={styles.modalTitle}>{confirm === 'surrender' ? '确认认输？' : '确认离开房间？'}</Text>
          <Text style={styles.explanation}>{confirm === 'surrender' ? '认输后本局判负，无法撤销' : game?.winner || inLobby ? '离开后可从房间列表重新加入' : '对局中离开将判负'}</Text>
          <TouchableOpacity testID="uttt-confirm-danger" accessibilityRole="button" style={[styles.button, styles.dangerFill]} onPress={() => {
            const action = confirm; setConfirm(null);
            if (action === 'leave') void leave();
            else if (socketRef.current?.isConnected) socketRef.current.surrender();
            else notify('连接已断开', '重连后可认输');
          }}><Text style={styles.white}>{confirm === 'surrender' ? '确认认输' : '确认离开'}</Text></TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" style={[styles.button, styles.secondary]} onPress={() => setConfirm(null)}><Text style={styles.label}>再想想</Text></TouchableOpacity>
        </View></View>
      </Modal>
      <GameResult visible={showResult} result={resultLabel(game?.winner || null)} players={shellPlayers(room, user?.id, game)} roomButtonTestID="uttt-back-to-room" onBackToRoom={backToRoom}
        onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>


          <Text style={styles.explanation}>{game?.reason ? reasons[game.reason] || game.reason : ''} · 共 {game?.moves.length ?? 0} 手</Text>


      </GameResult>
    </GameShell>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  grow: { flex: 1 },
  row: { flexDirection: 'row', gap: spacing.sm },
  gridRow: { flexDirection: 'row' },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border },
  identity: { fontSize: fontSize.sm, color: colors.textSecondary },
  turn: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  muted: { fontSize: fontSize.xs, color: colors.textMuted },
  label: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text },
  content: { alignItems: 'center', paddingHorizontal: spacing.md, paddingVertical: spacing.sm, gap: spacing.sm },
  sectionLabel: { width: '100%', maxWidth: 480, flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', gap: spacing.xs },
  focusBoard: { borderWidth: 2, borderColor: colors.borderStrong, borderRadius: radius.sm, overflow: 'hidden', backgroundColor: colors.card },
  cell: { justifyContent: 'center', alignItems: 'center' },
  rightLine: { borderRightWidth: 1, borderRightColor: colors.borderStrong },
  bottomLine: { borderBottomWidth: 1, borderBottomColor: colors.borderStrong },
  pending: { backgroundColor: withAlpha(colors.primary, 0.1) },
  symbol: { fontSize: 30, fontWeight: '700' },
  lastMove: { position: 'absolute', right: 3, top: 3, width: 8, height: 8, borderRadius: 4, borderWidth: 1, borderColor: lastMoveMarkEdge, backgroundColor: lastMoveMark },
  disconnect: { color: colors.warning, fontSize: fontSize.xs, textAlign: 'center', padding: spacing.xs },
  actions: { paddingHorizontal: spacing.md, paddingTop: spacing.sm, paddingBottom: spacing.sm, gap: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  destination: { color: colors.primary, backgroundColor: withAlpha(colors.primary, 0.07), textAlign: 'center', fontSize: fontSize.sm, padding: spacing.sm, borderRadius: radius.sm, minHeight: 36 },
  button: { minHeight: 46, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm },
  primary: { backgroundColor: colors.primary },
  secondary: { borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.card },
  white: { color: colors.white, fontSize: fontSize.sm, fontWeight: '700' },
  disabled: { opacity: 0.45 },
  dangerButton: { flex: 1, minHeight: 44, justifyContent: 'center', alignItems: 'center' },
  dangerText: { color: colors.danger, fontSize: fontSize.sm },
  dangerFill: { backgroundColor: colors.danger },
  overlay: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.lg, backgroundColor: colors.overlay },
  modal: { width: '100%', maxWidth: 360, padding: spacing.lg, borderRadius: radius.lg, gap: spacing.md, backgroundColor: colors.card },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center' },
  explanation: { fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center' },
  result: { fontSize: fontSize.xxl, fontWeight: '700', color: colors.primary, textAlign: 'center' },
});
