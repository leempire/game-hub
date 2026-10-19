// 军棋对局屏 —— 12 行 × 5 列、2 人在线对战，**明棋 / 暗棋**两种玩法。
// 照 XiangqiScreen 的骨架：WS sync、应用内 Modal、返回双路径、Vibration。
//
// 三条与其它棋类不同的设计：
// 1. **两个阶段**：布阵（`phase === "setup"`）→ 对局。布阵是「服务端发一套合法
//    默认阵型 + 点两子交换 + 随机 + 确认」，**交换是否合法只在服务端判定**
//    （地雷只能在后两排、军旗必须在大本营、炸弹不能在第一排）—— 客户端一行
//    约束都不写。双方都确认才开打。
// 2. **暗棋下对手的棋子是 `p === null`**：服务端压根没下发身份（不是渲染时藏），
//    棋盘上画成牌背。战报也只说「谁出局」，除非房间开了「公开阵亡身份」。
// 3. **两步落子 + 合法落点由服务端下发**（同象棋）：12 行棋盘在手机上每格只有
//    30~40px，达不到 44pt，两步式让误触可以在提交前撤销。
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Modal, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, Vibration, View,
  useWindowDimensions,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';

import { getRoom, leaveRoom, Room } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave, useModalBackGuard } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';
import { RoomSocket, SocketMessage } from '../socket';
import {
  LzqBattle, LzqView, PIECE_ORDER, battleText, pieceName, reasonText, sideName,
} from '../luzhanqi';
import { LzqLayout, makeLayout } from '../luzhanqiBoard';
import { notify } from '../utils';
import { colors, fontSize, radius, spacing, luzhanqiAccent } from '../theme';
import LuzhanqiBoardView from './LuzhanqiBoardView';

interface Props {
  code: string;
  onLeave: () => void;
}

/** 棋盘高度上限（平板/桌面端别铺满整屏）。 */
const BOARD_MAX_H = 560;

const WINNER_LABELS: Record<string, string> = {
  red: '红方胜',
  black: '黑方胜',
  draw: '和棋',
};

export default function LuzhanqiScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth, height: winHeight } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<LzqView | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  /** 选中的棋子 / 布阵时待交换的第一枚（**引擎坐标**）。 */
  const [selected, setSelected] = useState<[number, number] | null>(null);
  const [swapSel, setSwapSel] = useState<[number, number] | null>(null);
  const [battleAt, setBattleAt] = useState<[number, number] | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // --- 棋盘尺寸 ---
  // 5 列 × 12 行，两半之间还要留一条「前线/山界」带（0.5 格高）。
  // 总高 = 12.5 格；格宽可以比格高宽（棋子本来就是长方形小方块）。
  const rows = game?.rows ?? 12;
  const cols = game?.cols ?? 5;
  const availW = winWidth - spacing.md * 2;
  const availH = Math.max(200, winHeight - 330);
  const cellH = Math.min(availH / (rows + 0.5), BOARD_MAX_H / (rows + 0.5));
  const cellW = Math.min(availW / cols, cellH * 1.5);
  const layout: LzqLayout | null = useMemo(
    () => (game ? makeLayout(game.topology, cellW, cellH, cellH * 0.5) : null),
    [game, cellW, cellH],
  );

  const myColor = game?.your_color ?? '';
  const flipped = myColor === 'black';
  const inSetup = game?.phase === 'setup';
  const isMyTurn = !!game && !game.winner && game.phase === 'playing' && game.next === myColor;

  const toDisp = useCallback(
    (r: number, c: number): [number, number] => [flipped ? rows - 1 - r : r, c],
    [flipped, rows],
  );
  const toEngine = useCallback(
    (dr: number, c: number): [number, number] => [flipped ? rows - 1 - dr : dr, c],
    [flipped, rows],
  );

  // --- 返回双路径 ---
  useEffect(() => navigation.addListener('beforeRemove', (e: any) => {
    if (Platform.OS === 'web') return;
    if (skipRemoveRef.current) return;
    if (gameOverRef.current) {
      skipRemoveRef.current = true;
      navigation.dispatch(e.data.action);
      return;
    }
    e.preventDefault();
    setConfirm('leave');
  }), [navigation]);

  useEffect(() => onConfirmLeave(() => {
    if (gameOverRef.current) { navigation.goBack(); return; }
    setConfirm('leave');
  }), [navigation]);

  // --- WebSocket ---
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const adopt = (g: LzqView) => {
      setGame(g);
      setSelected(null);
      setSwapSel(null);
      setBattleAt(null);
      if (g.winner) setShowGameOver(true);
    };

    const syncOpponent = (roomData: Room | undefined) => {
      if (!roomData) return;
      const opp = roomData.players.find((p) => p.id !== user?.id);
      setOpponentDisconnected(opp ? !opp.connected : false);
    };

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) setRoom(roomData);
      if (msg.game) adopt(msg.game as LzqView);
      syncOpponent(roomData);
    };
    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) { setRoom(roomData); syncOpponent(roomData); }
    };
    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as LzqView;
      if (g) { setShowGameOver(false); gameOverShownRef.current = false; adopt(g); }
    };
    const handleSetup = (msg: SocketMessage) => {
      const g = msg.game as LzqView;
      if (g) { setGame(g); setSwapSel(null); }
    };
    const handleReady = (msg: SocketMessage) => {
      setGame((prev) => (prev ? { ...prev, ready: (msg.ready as Record<string, boolean>) ?? prev.ready } : prev));
    };
    const handleBegin = (msg: SocketMessage) => {
      const g = msg.game as LzqView;
      if (g) adopt(g);
    };
    const handleMove = (msg: SocketMessage) => {
      const from = msg.from as number[];
      const to = msg.to as number[];
      const battle = (msg.battle as LzqBattle | null) ?? null;
      setGame((prev) => {
        if (!prev) return prev;
        const cells = prev.cells.map((row) => [...row]);
        const mover = cells[from[0]][from[1]];
        // 只按**碰子结果**改盘面，不需要知道被吃的是谁 —— 暗棋下本来也不知道。
        const outcome = battle?.outcome;
        if (!battle) {
          cells[to[0]][to[1]] = mover;
          cells[from[0]][from[1]] = null;
        } else if (outcome === 'attacker_dies') {
          cells[from[0]][from[1]] = null;
        } else if (outcome === 'defender_dies') {
          cells[to[0]][to[1]] = mover;
          cells[from[0]][from[1]] = null;
        } else {
          cells[to[0]][to[1]] = null;
          cells[from[0]][from[1]] = null;
        }
        // 司令阵亡 → 对手那面军旗当场翻过来（位置由服务端给）
        for (const rev of (msg.newly_revealed as Array<{ color: string; at: number[] }>) ?? []) {
          cells[rev.at[0]][rev.at[1]] = { c: rev.color, p: 'junqi' };
        }
        const battles = battle ? [...prev.battles, battle] : prev.battles;
        return {
          ...prev,
          cells,
          battles,
          next: msg.next as string,
          legal: (msg.legal as number[][]) ?? [],
          flag_revealed: (msg.flag_revealed as Record<string, boolean>) ?? prev.flag_revealed,
          last_move: { from, to, by: msg.by as string },
        };
      });
      setSelected(null);
      setSwapSel(null);
      if (battle) {
        setBattleAt([to[0], to[1]]);
        if (Platform.OS !== 'web') Vibration.vibrate(20);
      }
    };
    const handleGameOver = (msg: SocketMessage) => {
      setGame((prev) => (prev ? {
        ...prev, winner: msg.winner as string, reason: msg.reason as string, legal: [],
      } : prev));
      setSelected(null);
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (token) getRoom(token, code).then(setRoom).catch(() => {});
    };
    const handlePlayerDisconnected = (msg: SocketMessage) => {
      if ((msg.user_id as number) !== user?.id) {
        setOpponentDisconnected(true);
        setDcTimeout((msg.timeout as number) ?? null);
      }
    };
    const handlePlayerReconnected = (msg: SocketMessage) => {
      if ((msg.user_id as number) !== user?.id) setOpponentDisconnected(false);
    };
    const handleRoomClosed = () => { notify('房间已解散'); ws.close(); onLeave(); };
    const handleError = (msg: SocketMessage) => {
      notify('提示', msg.message as string);
      setSwapSel(null);
    };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleGameStart);
    ws.on('luzhanqi_setup', handleSetup);
    ws.on('luzhanqi_ready', handleReady);
    ws.on('luzhanqi_begin', handleBegin);
    ws.on('move', handleMove);
    ws.on('game_over', handleGameOver);
    ws.on('player_disconnected', handlePlayerDisconnected);
    ws.on('player_reconnected', handlePlayerReconnected);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);
    ws.connect();
    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave]);

  // --- 派生：可落点 / 可交换 ---
  /** 选中棋子的合法落点（引擎坐标）。 */
  const dests = useMemo(() => {
    const set = new Set<string>();
    if (!game || !selected || game.phase !== 'playing') return set;
    for (const m of game.legal) {
      if (m[0] === selected[0] && m[1] === selected[1]) set.add(`${m[2]},${m[3]}`);
    }
    return set;
  }, [game, selected]);

  /** 棋盘层要的是**屏幕坐标**。 */
  const targetsDisp = useMemo(() => {
    const set = new Set<string>();
    for (const k of dests) {
      const [r, c] = k.split(',').map(Number);
      const [dr] = toDisp(r, c);
      set.add(`${dr},${c}`);
    }
    return set;
  }, [dests, toDisp]);

  /** 自己有哪些子是可动的（用来提示「这枚走不了」）。 */
  const movable = useMemo(() => {
    const set = new Set<string>();
    if (!game) return set;
    for (const m of game.legal) set.add(`${m[0]},${m[1]}`);
    return set;
  }, [game]);

  // --- 交互 ---
  const handleCellPress = useCallback((dr: number, c: number) => {
    if (!game) return;
    const [er, ec] = toEngine(dr, c);
    const cell = game.cells[er][ec];

    if (game.phase === 'setup') {
      if (!cell || cell.c !== myColor) { setSwapSel(null); return; }
      if (!swapSel) { setSwapSel([er, ec]); return; }
      if (swapSel[0] === er && swapSel[1] === ec) { setSwapSel(null); return; }
      socketRef.current?.luzhanqiSwap(swapSel, [er, ec]);
      setSwapSel([er, ec]);
      if (Platform.OS !== 'web') Vibration.vibrate(10);
      return;
    }

    if (!isMyTurn) return;
    const key = `${er},${ec}`;
    if (selected && dests.has(key)) {
      if (Platform.OS !== 'web') Vibration.vibrate(10);
      socketRef.current?.luzhanqiMove(selected[1], selected[0], ec, er);
      setSelected(null);
      return;
    }
    if (cell && cell.c === myColor) {
      if (movable.has(key)) {
        setSelected([er, ec]);
      } else {
        setSelected(null);
        notify('走不了', `${pieceName(cell.p)} 现在没有合法落点`);
      }
      return;
    }
    setSelected(null);
  }, [game, myColor, swapSel, isMyTurn, selected, dests, movable, toEngine]);

  const handleLeaveRoom = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); } catch { /* 继续走本地退出 */ }
    socketRef.current?.leave();
    socketRef.current?.close();
    skipRemoveRef.current = true;
    onLeave();
  };

  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);

  // --- 派生显示 ---
  const opponent = room?.players.find((p) => p.id !== user?.id);
  const oppReady = game?.ready?.[myColor === 'red' ? 'black' : 'red'] ?? false;
  const iReady = game?.ready?.[myColor] ?? false;
  const lastBattle = game?.battles?.length ? game.battles[game.battles.length - 1] : null;
  const turnText = (() => {
    if (!game) return '加载中…';
    if (game.winner) return '对局结束';
    if (game.phase === 'setup') return '布阵阶段';
    if (game.next === myColor) return '轮到你走';
    if (opponent?.is_bot) return `${opponent.nickname || opponent.username} 思考中…`;
    return '等待对方…';
  })();
  const myAlive = PIECE_ORDER
    .map((p) => ({ p, n: game?.alive?.[p] ?? 0 }))
    .filter((x) => x.n > 0);

  return (
    <View style={styles.root}>
      {/* 顶栏：身份 + 回合提示（左）／帮助 + 房号（右） */}
      <View style={styles.topBar}>
        <View style={{ flex: 1 }}>
          <Text style={styles.turnText}>{turnText}</Text>
          <Text style={styles.sideText}>
            {sideName(myColor)}
            {game?.mode === 'hidden'
              ? (game.reveal ? ' · 暗棋（公开阵亡身份）' : ' · 暗棋')
              : ' · 明棋'}
            {opponent ? ` · 对 ${opponent.nickname || opponent.username}` : ''}
          </Text>
        </View>
        <GameHelpButton game="luzhanqi" size={24} />
        <Text style={styles.codeText}>#{code}</Text>
      </View>

      {opponentDisconnected && dcLeft !== null && (
        <View style={styles.dcBar}>
          <Ionicons name="cloud-offline-outline" size={14} color={colors.white} />
          <Text style={styles.dcText}>对方掉线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      {/* 战报条：**留死高度**，出现/消失不推挤棋盘（俄罗斯方块的教训） */}
      <View style={styles.battleBar}>
        {lastBattle ? (
          <Text style={styles.battleText} numberOfLines={1}>
            {battleText(lastBattle, myColor)}
          </Text>
        ) : (
          <Text style={styles.battleHint} numberOfLines={1}>
            {inSetup
              ? '点两枚棋子交换位置，摆好后点「确认布阵」'
              : '点自己的棋子 → 再点高亮落点'}
          </Text>
        )}
      </View>

      {/* 布阵阶段的操作条 */}
      {inSetup && (
        <View style={styles.setupBar}>
          <Text style={styles.setupHint}>
            {iReady ? '已确认，等待对方…' : (oppReady ? '对方已确认布阵' : '双方布阵中')}
          </Text>
          {/* 经典阵型：点一下就整套摆好（名单由服务端下发）。
              布阵要摆 25 枚子，逐枚交换在手机上很费时 —— 这是「随机」之外
              另一条一次到位的路，而且是**有讲究**的阵型，不是掷骰子。 */}
          {!iReady && (game.presets ?? []).length > 0 && (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              style={styles.presetScroll}
              contentContainerStyle={styles.presetRow}
            >
              {(game.presets ?? []).map((p) => (
                <TouchableOpacity
                  key={p.key}
                  testID={`lz-preset-${p.key}`}
                  style={styles.presetChip}
                  onPress={() => socketRef.current?.luzhanqiPreset(p.key)}
                  activeOpacity={0.8}
                >
                  <Text style={styles.presetChipText}>{p.label}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          )}
          <View style={styles.setupBtns}>
            <TouchableOpacity
              testID="lz-random"
              style={[styles.ghostBtn, iReady && styles.btnDisabled]}
              disabled={iReady}
              onPress={() => socketRef.current?.luzhanqiRandom()}
            >
              <Text style={styles.ghostBtnText}>随机</Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="lz-confirm"
              style={[styles.primaryBtn, iReady && styles.btnDisabled]}
              disabled={iReady}
              onPress={() => socketRef.current?.luzhanqiConfirm()}
            >
              <Text style={styles.primaryBtnText}>{iReady ? '已确认' : '确认布阵'}</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* 棋盘 */}
      <ScrollView
        style={styles.boardScroll}
        contentContainerStyle={styles.boardScrollContent}
        horizontal={false}
      >
        {game && layout ? (
          <LuzhanqiBoardView
            view={game}
            layout={layout}
            flipped={flipped}
            selected={selected ? toDisp(selected[0], selected[1]) : null}
            swapSel={swapSel ? toDisp(swapSel[0], swapSel[1]) : null}
            targets={targetsDisp}
            battleAt={battleAt ? toDisp(battleAt[0], battleAt[1]) : null}
            onCellPress={handleCellPress}
            interactive={inSetup ? !iReady : isMyTurn}
          />
        ) : (
          <Text style={styles.loading}>加载中…</Text>
        )}
      </ScrollView>

      {/* 我方存活棋子（军棋 25 枚，记不住自己还剩什么是常事） */}
      <View style={styles.tray}>
        {myAlive.length === 0 ? (
          <Text style={styles.trayEmpty}>—</Text>
        ) : (
          myAlive.map(({ p, n }) => (
            <View key={p} style={styles.trayChip}>
              <Text style={styles.trayText}>{pieceName(p)} {n}</Text>
            </View>
          ))
        )}
      </View>

      {/* 底部：认输 / 退出房间（危险操作，都要二次确认） */}
      <View style={styles.bottomBar}>
        <TouchableOpacity
          testID="lz-surrender"
          style={[styles.ghostBtn, styles.flex1]}
          onPress={() => setConfirm('surrender')}
        >
          <Text style={styles.ghostBtnText}>认输</Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="lz-leave"
          style={[styles.ghostBtn, styles.flex1]}
          onPress={() => setConfirm('leave')}
        >
          <Text style={styles.ghostBtnText}>退出房间</Text>
        </TouchableOpacity>
      </View>

      {/* 认输确认 */}
      <Modal visible={confirm === 'surrender'} transparent animationType="fade"
        onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalMask}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>确认认输？</Text>
            <Text style={styles.modalBody}>认输会立刻判负并结束本局。</Text>
            <View style={styles.modalBtns}>
              <TouchableOpacity style={[styles.ghostBtn, styles.flex1]} onPress={() => setConfirm(null)}>
                <Text style={styles.ghostBtnText}>再想想</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="lz-confirm-surrender"
                style={[styles.dangerBtn, styles.flex1]}
                onPress={() => { setConfirm(null); socketRef.current?.surrender(); }}
              >
                <Text style={styles.primaryBtnText}>确认认输</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* 退出确认 */}
      <Modal visible={confirm === 'leave'} transparent animationType="fade"
        onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalMask}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>退出房间？</Text>
            <Text style={styles.modalBody}>对局还没结束，退出等于认输。</Text>
            <View style={styles.modalBtns}>
              <TouchableOpacity style={[styles.ghostBtn, styles.flex1]} onPress={() => setConfirm(null)}>
                <Text style={styles.ghostBtnText}>再想想</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="lz-confirm-leave"
                style={[styles.dangerBtn, styles.flex1]}
                onPress={() => { setConfirm(null); handleLeaveRoom(); }}
              >
                <Text style={styles.primaryBtnText}>确认退出</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* 结算 */}
      <Modal visible={showGameOver} transparent animationType="fade"
        onRequestClose={handleBackToRoom}>
        <View style={styles.modalMask}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>
              {game?.winner ? WINNER_LABELS[game.winner] ?? '对局结束' : '对局结束'}
            </Text>
            <Text style={styles.modalBody}>
              {reasonText(game?.reason)}
              {game?.winner && game.winner !== 'draw' && game.winner !== myColor ? '（我方告负）' : ''}
            </Text>
            <View style={styles.modalBtns}>
              <TouchableOpacity
                testID="lz-back-room"
                style={[styles.primaryBtn, styles.flex1]}
                onPress={handleBackToRoom}
              >
                <Text style={styles.primaryBtnText}>返回房间</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  topBar: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  turnText: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sideText: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  codeText: { fontSize: fontSize.xs, color: colors.textMuted },
  dcBar: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: colors.warning, paddingHorizontal: spacing.md, paddingVertical: 6,
  },
  dcText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  battleBar: {
    height: 30, justifyContent: 'center',
    paddingHorizontal: spacing.md,
    backgroundColor: colors.card,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  battleText: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text },
  battleHint: { fontSize: fontSize.xs, color: colors.textMuted },
  setupBar: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    backgroundColor: colors.card, gap: spacing.sm,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  setupHint: { fontSize: fontSize.xs, color: colors.textSecondary },
  setupBtns: { flexDirection: 'row', gap: spacing.sm },
  // 经典阵型芯片：横向滚动（5 个名字一行放不下），44pt 高。
  // `flexGrow: 0` 是必须的：横向 ScrollView 在列容器里默认会去抢剩余高度，
  // 把棋盘挤扁。
  presetScroll: { flexGrow: 0 },
  presetRow: { flexDirection: 'row', gap: spacing.sm, paddingVertical: 2 },
  presetChip: {
    minHeight: 44, justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radius.full,
    borderWidth: 1, borderColor: luzhanqiAccent,
    backgroundColor: colors.card,
  },
  presetChipText: { fontSize: fontSize.sm, color: luzhanqiAccent, fontWeight: '600' },
  boardScroll: { flex: 1 },
  boardScrollContent: { alignItems: 'center', paddingVertical: spacing.sm },
  loading: { color: colors.textMuted, padding: spacing.lg },
  tray: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 6,
    paddingHorizontal: spacing.md, paddingVertical: 6,
    borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card,
  },
  trayChip: {
    paddingHorizontal: 7, paddingVertical: 2, borderRadius: radius.sm,
    backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border,
  },
  trayText: { fontSize: fontSize.xs, color: colors.textSecondary },
  trayEmpty: { fontSize: fontSize.xs, color: colors.textMuted },
  bottomBar: {
    flexDirection: 'row', gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingBottom: spacing.md, paddingTop: spacing.sm,
  },
  flex1: { flex: 1 },
  primaryBtn: {
    paddingVertical: spacing.sm + 6, borderRadius: radius.md,
    backgroundColor: colors.primary, alignItems: 'center',
  },
  primaryBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  dangerBtn: {
    paddingVertical: spacing.sm + 6, borderRadius: radius.md,
    backgroundColor: colors.danger, alignItems: 'center',
  },
  ghostBtn: {
    paddingVertical: spacing.sm + 6, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong, alignItems: 'center',
    backgroundColor: colors.card,
  },
  ghostBtnText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  btnDisabled: { opacity: 0.45 },
  modalMask: {
    flex: 1, backgroundColor: colors.overlay,
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  modalCard: {
    width: '100%', maxWidth: 340, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg, gap: spacing.sm,
  },
  modalTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  modalBody: { fontSize: fontSize.sm, color: colors.textSecondary },
  modalBtns: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
});
