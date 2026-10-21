// Dots and Boxes (点格棋) game screen — 5×5 dots (4×4 boxes), 2-player online.
// Interaction: tap edge zones between dots. Completed boxes get player color fill.
// Follows ReversiScreen pattern: WS sync, Modal confirm, backGuard dual path.
import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, Vibration, Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize, playerColors, withAlpha, dotsInk,
  lastMoveMark,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';

// --- Types ---

interface DotsState {
  dots: number;
  rows: number;
  cols: number;
  h: number[][];
  v: number[][];
  boxes: number[][];
  next: number;
  scores: { 1: number; 2: number };
  moves: Array<{ orient: string; y: number; x: number; player: number }>;
  your_color: 'black' | 'white';
  winner: null | 'black' | 'white' | 'draw';
  reason: null | string;
}

interface Props {
  code: string;
  onLeave: () => void;
}

const P1 = 1;
const P2 = 2;
const DOT_R = 6;
const EDGE_W = 3;
// 边线热区在垂直于边的方向上放大到 2*HIT_PAD（细长目标的标准做法：视觉上只画
// 3px 的线，可点区域是它的十几倍）。20 → 热区 43px，接近 HIG 建议的 44pt；
// 4×4 棋盘 cellSize≈89，相邻边热区不会互相吞掉。
const HIT_PAD = 20;

// 未画边在轮到自己时的「可画」提示：细虚线 + 中性灰。
//
// 为什么不冒充「已画边」（已画边 = 3px 实线 + 饱和玩家色）：
//   1) **虚线是主通道**（形状）：虚线在界面语言里表示「尚未填上 / 待画」，实线
//      才表示「已存在」。这是与颜色无关的第二通道，色觉障碍用户同样能分辨；
//   2) 线宽 2px < 已画的 3px，更远小于封格提示的 6px；
//   3) 中性灰（colors.textMuted）与玩家蓝/红在色相上完全分离。
// 实测对比度：colors.textMuted #6B7280 vs 棋盘底 #FAFAF8 = 4.63:1（≥ 图形 3:1）。
// 原淡紫 rgba(79,70,229,0.15) 只有 1.25:1，边在哪根本看不出来。
const HINT_EDGE_W = 2;
const DASH_COUNT = 3;

// 封格填充动画：小元素的轻微变化，落在方法论 §2 的 200-300ms 区间。
const BOX_FILL_MS = 220;

// 引擎用 'black'/'white' 标识先手/后手（历史命名），但棋盘实际渲染是蓝/红。
// 文案与填充色都从这里取，避免再出现「棋盘画的是蓝红、文案却写黑白」的错位。
const DOTS_COLORS: Record<string, { hex: string; label: string }> = {
  black: { hex: playerColors.blue, label: '蓝方' },
  white: { hex: playerColors.red, label: '红方' },
};

function colorOf(color?: string | null): { hex: string; label: string } {
  return DOTS_COLORS[color || ''] || DOTS_COLORS.black;
}

const WINNER_LABELS: Record<string, string> = {
  black: `${DOTS_COLORS.black.label}胜`,
  white: `${DOTS_COLORS.white.label}胜`,
  draw: '平局',
};

const PLAYER_COLORS: Record<number, string> = {
  [P1]: DOTS_COLORS.black.hex,
  [P2]: DOTS_COLORS.white.hex,
};

const BOX_FILLS: Record<number, string> = {
  [P1]: withAlpha(playerColors.blue, 0.25),
  [P2]: withAlpha(playerColors.red, 0.25),
};

// --- Edge line ---

/**
 * 一条边线的视觉。`dashed` 为真时画成「可画」提示用的细虚线。
 *
 * 虚线是**显式画 DASH_COUNT 段小短线**，不用 `borderStyle: 'dashed'`——RN 原生端
 * 的单边虚线在 Android 上不渲染、iOS 上遇到 borderRadius 会退化成实线，跨端不可靠。
 * 两端的已画边 / 封格提示边仍走实线分支，视觉与原先一致。
 */
function EdgeLine({ orient, thickness, color, dashed, testID }: {
  orient: 'h' | 'v'; thickness: number; color: string; dashed: boolean; testID?: string;
}) {
  const isH = orient === 'h';
  if (!dashed) {
    return (
      <View
        testID={testID}
        style={isH
          ? { width: '100%', height: thickness, backgroundColor: color, borderRadius: thickness / 2 }
          : { width: thickness, height: '100%', backgroundColor: color, borderRadius: thickness / 2 }}
      />
    );
  }
  return (
    <View testID={testID} style={{
      flexDirection: isH ? 'row' : 'column',
      width: isH ? '100%' : thickness,
      height: isH ? thickness : '100%',
      gap: 5,
    }}>
      {Array.from({ length: DASH_COUNT }).map((_, i) => (
        <View key={i} style={{
          flex: 1, backgroundColor: color, borderRadius: thickness / 2,
        }} />
      ))}
    </View>
  );
}

// --- Animated box fill ---

/**
 * 被占格子的填充。`animate` 为真时淡入 + 轻微放大（220ms）；否则直接呈现最终态，
 * 避免重连 sync 时整盘格子一起「跳一下」。
 */
function BoxFill({ animate, style }: { animate: boolean; style: any }) {
  const v = useRef(new Animated.Value(animate ? 0 : 1)).current;
  useEffect(() => {
    if (animate) {
      Animated.timing(v, {
        toValue: 1, duration: BOX_FILL_MS, useNativeDriver: Platform.OS !== 'web',
      }).start();
    }
  }, [animate]);
  return (
    <Animated.View style={[style, {
      opacity: v,
      transform: [{ scale: v.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1] }) }],
    }]} />
  );
}

// --- Main Component ---

export default function DotsScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<DotsState | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  // 断线倒计时的起点：优先后端在事件里给的 timeout，其次房间数据里的剩余值
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  useModalBackGuard(confirm !== null, () => setConfirm(null));

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
    if (gameOverRef.current) {
      navigation.goBack();
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  // 刚被封下的格子（key `by,bx`）：只给这一次走子封的格子播填充动画，
  // 避免重连 sync 时整盘已有格子一起「跳一下」。
  const [animBoxes, setAnimBoxes] = useState<Set<string>>(new Set());

  // Board sizing
  const dots = game?.dots || 5;
  const rows = dots - 1;
  const cols = dots - 1;
  const maxBoardW = Math.min(winWidth - 32, 440);
  const cellSize = Math.floor(maxBoardW / cols);
  const boardW = cellSize * cols;
  const boardH = cellSize * rows;

  // WS connection
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) setRoom(roomData);
      if (msg.game) {
        const g = msg.game as DotsState;
        setGame(g);
        if (g.winner) setShowGameOver(true);
      }
      if (roomData) {
        const opponent = roomData.players.find((p: any) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        const opponent = roomData.players.find((p: any) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as DotsState;
      if (g) {
        setGame(g);
        setShowGameOver(false);
        gameOverShownRef.current = false;
      }
    };

    const handleMove = (msg: SocketMessage) => {
      const completed = (msg.completed as number[][]) || [];
      setGame((prev) => {
        if (!prev) return prev;
        const orient = msg.orient as string;
        const y = msg.y as number;
        const x = msg.x as number;
        const color = msg.color as string;
        const next = msg.next as string;
        const scores = msg.scores as { 1: number; 2: number };
        const playerNum = color === 'black' ? P1 : P2;
        const newH = prev.h.map((r) => [...r]);
        const newV = prev.v.map((r) => [...r]);
        const newBoxes = prev.boxes.map((r) => [...r]);
        if (orient === 'h') newH[y][x] = playerNum;
        else newV[y][x] = playerNum;
        for (const [by, bx] of completed) {
          newBoxes[by][bx] = playerNum;
        }
        const nextNum = next === 'black' ? P1 : P2;
        return {
          ...prev, h: newH, v: newV, boxes: newBoxes,
          next: nextNum, scores: scores || prev.scores,
          // 最近一步高亮要用它。原来 handleMove 不维护 moves，
          // 只更新 h/v/boxes —— moves 永远停在 sync 时的快照。
          moves: [...prev.moves, { orient, y, x, player: playerNum }],
        };
      });
      // 封格填充动画：一次可能封两格（连画），两格一起动。
      if (completed.length > 0) {
        setAnimBoxes(new Set(completed.map(([by, bx]) => `${by},${bx}`)));
        setTimeout(() => setAnimBoxes(new Set()), BOX_FILL_MS + 120);
      }
    };

    const handleGameOver = (msg: SocketMessage) => {
      const winner = msg.winner as string;
      const reason = msg.reason as string;
      const score = msg.score as { black: number; white: number } | undefined;
      setGame((prev) => {
        if (!prev) return prev;
        const newScores = score ? {
          [P1]: score.black, [P2]: score.white,
        } as any : prev.scores;
        return { ...prev, winner: winner as any, reason, scores: newScores };
      });
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

    const handleRoomClosed = () => {
      notify('房间已解散');
      ws.close();
      onLeave();
    };

    const handleError = (msg: SocketMessage) => {
      notify('提示', msg.message as string);
    };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleGameStart);
    ws.on('move', handleMove);
    ws.on('game_over', handleGameOver);
    ws.on('player_disconnected', handlePlayerDisconnected);
    ws.on('player_reconnected', handlePlayerReconnected);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);
    ws.connect();
    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave]);

  const handleEdgePress = useCallback((orient: string, y: number, x: number) => {
    if (!game || !user || game.winner) return;
    const myNum = game.your_color === 'black' ? P1 : P2;
    if (game.next !== myNum) return;
    if (orient === 'h' && game.h[y][x] !== 0) return;
    if (orient === 'v' && game.v[y][x] !== 0) return;
    if (Platform.OS !== 'web') Vibration.vibrate(10);
    socketRef.current?.send({ type: 'move', orient, y, x });
  }, [game, user]);

  const handleSurrender = () => setConfirm('surrender');
  const doSurrender = () => { setConfirm(null); socketRef.current?.surrender(); };

  const handleLeaveRoom = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); } catch { /* ignore */ }
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

  const isMyTurn = game
    ? (game.your_color === 'black' ? P1 : P2) === game.next && !game.winner
    : false;

  const score = game
    ? { black: game.scores[P1] || 0, white: game.scores[P2] || 0 }
    : { black: 0, white: 0 };

  // 最近一步（最后一次画边）。点格棋的边只有 3px 粗、颜色又和对手的边挨着，
  // 不标出来根本看不出对方刚画了哪条（连画时尤其糊）。
  const lastEdge = game && game.moves.length > 0
    ? game.moves[game.moves.length - 1] : null;
  const isLastEdge = (kind: 'h' | 'v', y: number, x: number): boolean =>
    !!lastEdge && lastEdge.orient === kind && lastEdge.y === y && lastEdge.x === x;

  const myColorLabel = game ? colorOf(game.your_color).label : '';
  const turnText = (() => {
    if (!game || game.winner) return '';
    if (isMyTurn) return '轮到你画边';
    // 人机回合有 0.8-1.2 秒的思考延迟（服务端故意留的节奏）。只写「等待对方…」
    // 用户既不知道在等谁、也不知道要等多久，会以为卡住了——方法论 §1 响应时间
    // / §5 状态可见性。真人对手维持原文案。
    const opp = room?.players?.find((p) => p.id !== user?.id);
    if (opp?.is_bot) return `${opp.nickname || opp.username} 思考中…`;
    return '等待对方...';
  })();

  // 封格提示：哪条边画下去能封下格子。
  //
  // 点格棋真正的难点是「连画」——封格后立刻再走一次，新手看 16 个格子的数字
  // 根本读不出哪一步会连锁。把答案直接画在棋盘上，比写一段规则说明有用得多
  // （方法论 §10：能画出来的规则就不要靠文字解释）。
  const claimableEdge = (kind: 'h' | 'v', y: number, x: number): boolean => {
    if (!game) return false;
    const drawn = (by: number, bx: number): number => {
      if (by < 0 || by >= rows || bx < 0 || bx >= cols) return -1;
      return (game.h[by][bx] ? 1 : 0) + (game.h[by + 1][bx] ? 1 : 0)
        + (game.v[by][bx] ? 1 : 0) + (game.v[by][bx + 1] ? 1 : 0);
    };
    // 横边是 (y,x) 格的上边、(y-1,x) 格的下边；竖边是左右两格。
    const boxes: Array<[number, number]> = kind === 'h' ? [[y, x], [y - 1, x]] : [[y, x], [y, x - 1]];
    return boxes.some(([by, bx]) => drawn(by, bx) === 3);
  };

  // Count total boxes
  const totalBoxes = rows * cols;

  return (
    <View style={styles.container}>
      {/* Top bar */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <View style={[styles.colorDot, {
            backgroundColor: game?.your_color === 'black' ? colors.primary : colors.danger,
          }]} />
          <Text style={styles.myColor}>{myColorLabel}</Text>
          <Text style={[styles.turnHint, isMyTurn && styles.turnActive]}>{turnText}</Text>
        </View>
        <View style={styles.scoreRow}>
          <View style={styles.scoreItem}>
            <View style={[styles.scoreDot, { backgroundColor: colors.primary }]} />
            <Text style={styles.scoreNum}>{score.black}</Text>
          </View>
          <Text style={styles.scoreSep}>:</Text>
          <View style={styles.scoreItem}>
            <View style={[styles.scoreDot, { backgroundColor: colors.danger }]} />
            <Text style={styles.scoreNum}>{score.white}</Text>
          </View>
        </View>
        <GameHelpButton game="dots" size={24} />
        <Text style={styles.roomCode}>#{code}</Text>
      </View>

      {opponentDisconnected && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={14} color={colors.white} />
          <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      {/* Board */}
      <View style={[styles.boardWrap, { width: boardW + DOT_R * 2, height: boardH + DOT_R * 2 }]}>
        {/* Box fills */}
        {game && Array.from({ length: rows }).map((_, by) =>
          Array.from({ length: cols }).map((_, bx) => {
            const owner = game.boxes[by][bx];
            if (owner === 0) return null;
            return (
              <BoxFill
                key={`box-${by}-${bx}`}
                animate={animBoxes.has(`${by},${bx}`)}
                style={[styles.boxFill, {
                  left: DOT_R + bx * cellSize + 1,
                  top: DOT_R + by * cellSize + 1,
                  width: cellSize - 2,
                  height: cellSize - 2,
                  backgroundColor: BOX_FILLS[owner],
                }]}
              />
            );
          })
        )}

        {/* Horizontal edges */}
        {game && Array.from({ length: rows + 1 }).map((_, y) =>
          Array.from({ length: cols }).map((_, x) => {
            const val = game.h[y][x];
            const isEmpty = val === 0;
            const claim = isEmpty && isMyTurn && claimableEdge('h', y, x);
            // 「可画」提示（细虚线）：轮到自己、尚未画、也不是封格边
            const drawable = isEmpty && isMyTurn && !claim;
            const edgeColor = isEmpty
              ? (claim ? colors.warning
                : (isMyTurn ? colors.textMuted : 'rgba(0,0,0,0.06)'))
              : PLAYER_COLORS[val];
            // 第二通道是线宽：提示边 6px、可画边 2px、已画的边 3px。颜色不能单独
            // 承载信息（WCAG 1.4.1），而且琥珀与红边的亮度几乎相同（1.04:1），
            // 光靠颜色对色觉障碍者会和「红方已画」混淆。
            const edgeThickness = claim ? EDGE_W * 2 : (drawable ? HINT_EDGE_W : EDGE_W);
            const isLast = isLastEdge('h', y, x);
            return (
              <TouchableOpacity
                key={`h-${y}-${x}`}
                testID={`h-${y}-${x}`}
                style={[styles.edgeTouch, {
                  left: DOT_R + x * cellSize + DOT_R,
                  top: DOT_R + y * cellSize - HIT_PAD,
                  width: cellSize - DOT_R * 2,
                  height: HIT_PAD * 2 + EDGE_W,
                }]}
                onPress={() => handleEdgePress('h', y, x)}
                activeOpacity={isEmpty ? 0.6 : 1}
                disabled={!isEmpty || !isMyTurn}
              >
                {/* 最近一步：先铺一条更粗的黄条（带深墨描边）当光晕，
                    玩家色画在上面 —— 黄色与加粗两条通道，玩家色也照旧可辨 */}
                {isLast && (
                  <View testID={`dots-last-h-${y}-${x}`} style={styles.lastHalo}>
                    <EdgeLine orient="h" thickness={edgeThickness + 6}
                      color={lastMoveMark} dashed={false} />
                  </View>
                )}
                <EdgeLine
                  orient="h"
                  thickness={edgeThickness}
                  color={edgeColor}
                  dashed={drawable}
                  testID={claim ? `dots-hint-h-${y}-${x}` : undefined}
                />
              </TouchableOpacity>
            );
          })
        )}

        {/* Vertical edges */}
        {game && Array.from({ length: rows }).map((_, y) =>
          Array.from({ length: cols + 1 }).map((_, x) => {
            const val = game.v[y][x];
            const isEmpty = val === 0;
            const claim = isEmpty && isMyTurn && claimableEdge('v', y, x);
            const drawable = isEmpty && isMyTurn && !claim;
            const edgeColor = isEmpty
              ? (claim ? colors.warning
                : (isMyTurn ? colors.textMuted : 'rgba(0,0,0,0.06)'))
              : PLAYER_COLORS[val];
            const edgeThickness = claim ? EDGE_W * 2 : (drawable ? HINT_EDGE_W : EDGE_W);
            const isLast = isLastEdge('v', y, x);
            return (
              <TouchableOpacity
                key={`v-${y}-${x}`}
                testID={`v-${y}-${x}`}
                style={[styles.edgeTouch, {
                  left: DOT_R + x * cellSize - HIT_PAD,
                  top: DOT_R + y * cellSize + DOT_R,
                  width: HIT_PAD * 2 + EDGE_W,
                  height: cellSize - DOT_R * 2,
                }]}
                onPress={() => handleEdgePress('v', y, x)}
                activeOpacity={isEmpty ? 0.6 : 1}
                disabled={!isEmpty || !isMyTurn}
              >
                {isLast && (
                  <View testID={`dots-last-v-${y}-${x}`} style={styles.lastHaloV}>
                    <EdgeLine orient="v" thickness={edgeThickness + 6}
                      color={lastMoveMark} dashed={false} />
                  </View>
                )}
                <EdgeLine
                  orient="v"
                  thickness={edgeThickness}
                  color={edgeColor}
                  dashed={drawable}
                  testID={claim ? `dots-hint-v-${y}-${x}` : undefined}
                />
              </TouchableOpacity>
            );
          })
        )}

        {/* Dots */}
        {Array.from({ length: dots }).map((_, y) =>
          Array.from({ length: dots }).map((_, x) => (
            <View key={`dot-${y}-${x}`} style={[styles.dot, {
              left: DOT_R + x * cellSize - DOT_R,
              top: DOT_R + y * cellSize - DOT_R,
              width: DOT_R * 2,
              height: DOT_R * 2,
              borderRadius: DOT_R,
            }]} />
          ))
        )}
      </View>

      {/* Box count */}
      {game && (
        <Text style={styles.boxCount}>
          {score.black + score.white} / {totalBoxes} 格
        </Text>
      )}

      {/* Action buttons */}
      <View style={styles.bottomBar}>
        {game && !game.winner && (
          <TouchableOpacity style={styles.surrenderBtn} onPress={handleSurrender}>
            <Ionicons name="flag-outline" size={16} color={colors.white} />
            <Text style={styles.surrenderBtnText}> 认输</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirm('leave')}>
          <Ionicons name="exit-outline" size={16} color={colors.textSecondary} />
          <Text style={styles.leaveBtnText}> 退出</Text>
        </TouchableOpacity>
      </View>

      {/* Confirm modal */}
      <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons
              name={confirm === 'surrender' ? 'flag-outline' : 'exit-outline'}
              size={36} color={colors.danger}
            />
            <Text style={styles.modalTitle}>
              {confirm === 'surrender' ? '确认认输？' : '确认退出？'}
            </Text>
            <Text style={styles.modalReason}>
              {confirm === 'surrender' ? '认输后本局判负，无法撤销' : '对局中退出将判负并离开房间'}
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, { backgroundColor: colors.danger }]}
              onPress={() => {
                if (confirm === 'surrender') doSurrender();
                else { setConfirm(null); handleLeaveRoom(); }
              }}
            >
              <Text style={styles.modalBtnPrimaryText}>
                {confirm === 'surrender' ? '确认认输' : '确认退出'}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirm(null)}
            >
              <Text style={styles.modalBtnSecondaryText}>再想想</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Game over modal */}
      <Modal visible={showGameOver} transparent animationType="fade" onRequestClose={handleBackToRoom}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>对局结束</Text>
            {game && (
              <>
                <Text style={styles.modalResult}>
                  {WINNER_LABELS[game.winner || ''] || game.winner}
                </Text>
                <Text style={styles.modalScore}>
                  {score.black} : {score.white}
                </Text>
              </>
            )}
            <TouchableOpacity style={[styles.modalBtn, styles.modalBtnPrimary]} onPress={handleBackToRoom}>
              <Text style={styles.modalBtnPrimaryText}>返回房间</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// --- Styles ---

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, alignItems: 'center' },
  topBar: {
    width: '100%', flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', padding: spacing.md, backgroundColor: colors.card,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  colorDot: { width: 10, height: 10, borderRadius: 5 },
  myColor: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  turnHint: { fontSize: fontSize.sm, color: colors.textSecondary },
  turnActive: { color: colors.primary, fontWeight: '600' },
  scoreRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  scoreItem: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  scoreDot: { width: 12, height: 12, borderRadius: 6 },
  scoreNum: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  scoreSep: { fontSize: fontSize.md, fontWeight: '700', color: colors.textMuted },
  roomCode: {
    fontSize: fontSize.sm, color: colors.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  banner: {
    width: '100%', backgroundColor: colors.warning, paddingVertical: spacing.sm,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
  },
  bannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' },
  boardWrap: {
    position: 'relative', backgroundColor: '#FAFAF8',
    marginTop: spacing.md, borderRadius: 8, overflow: 'hidden',
    borderWidth: 1, borderColor: colors.border,
    shadowColor: colors.black, shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1, shadowRadius: 6, elevation: 2,
  },
  dot: {
    position: 'absolute', backgroundColor: dotsInk,
    zIndex: 2,
  },
  boxFill: { position: 'absolute', borderRadius: 2, zIndex: 0 },
  // 最近一步的黄晕：绝对定位在边的正中央，比玩家色粗 6px（两侧各露 3px）
  lastHalo: {
    position: 'absolute', left: 0, right: 0, top: '50%', marginTop: -3,
    alignItems: 'center', justifyContent: 'center',
  },
  lastHaloV: {
    position: 'absolute', top: 0, bottom: 0, left: '50%', marginLeft: -3,
    alignItems: 'center', justifyContent: 'center',
  },
  edgeTouch: {
    position: 'absolute', alignItems: 'center', justifyContent: 'center',
    zIndex: 1,
  },
  boxCount: {
    marginTop: spacing.sm, fontSize: fontSize.sm, color: colors.textSecondary,
  },
  bottomBar: { flexDirection: 'row', gap: spacing.md, padding: spacing.md },
  surrenderBtn: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.danger, borderRadius: radius.md,
  },
  surrenderBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  leaveBtn: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay,
    justifyContent: 'center', alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.xl, padding: spacing.lg, alignItems: 'center',
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.md },
  modalResult: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.primary, marginBottom: spacing.xs },
  modalScore: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.lg },
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  modalBtn: {
    width: '100%', borderRadius: radius.md,
    paddingVertical: spacing.sm + 4, alignItems: 'center', marginTop: spacing.sm,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
