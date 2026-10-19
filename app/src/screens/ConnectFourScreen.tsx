// 重力四子棋对局屏 —— 7 列 × 6 行、2 人在线对战。
// 照 XiangqiScreen 的骨架：WS sync、应用内 Modal、返回双路径、断线倒计时。
//
// 三条本游戏特有的设计：
// 1. **客户端只列号、不算落点行**。点一列发 `{type:'move', x:col}`，落点行由服务端
//    按重力算好，随 move 广播回来（`y` 就是落点行）。客户端自己按"列里还有几个
//    空位"推落点，一旦和权威盘面差一格就画错（飞行棋自己推路径踩过）。
// 2. **落点在列底向上堆**，所以点列 = 选中整列：热区是整列（宽 ~48px、高 6 格），
//    远高于 44pt 下限，不需要象棋那种两步落子来补偿误触。
// 3. **提示列**：轮到自己时标出「这一手能连成四子」和「对手下一手能连成四子
//    （不堵就输）」的列。颜色 + 文字标签 + 不同形状三条通道一起用（WCAG 1.4.1）。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, Vibration, Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize,
  connect4Frame, connect4Slot, connect4Black, connect4White,
  connect4DiscEdge, connect4WinHint, connect4BlockHint, connect4Last,
  connect4LineHint,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';

// --- Types ---

interface Connect4Move {
  x: number;                 // 列
  y: number;                 // 落点行（服务端算的）
  color: 'black' | 'white';
}

interface Connect4State {
  cols: number;
  rows: number;
  board: number[][];         // board[row][col]：0 空 / 1 黑 / 2 白
  moves: Connect4Move[];
  next: 'black' | 'white';
  your_color: 'black' | 'white';
  winner: 'black' | 'white' | 'draw' | null;
  reason: string | null;
}

interface Props {
  code: string;
  onLeave: () => void;
}

// --- 常量 ---

const BOARD_MAX = 460;
const DROP_MS = 160;                 // 落子动画：落点从顶上滑到落点行
const WIN_LEN = 4;

const COLOR_LABELS: Record<string, string> = { black: '黑方', white: '白方' };
const WINNER_LABELS: Record<string, string> = {
  black: '黑方胜', white: '白方胜', draw: '和棋',
};
const REASON_LABELS: Record<string, string> = {
  four: '连成四子', surrender: '对方认输', disconnect: '对方断线超时', draw: '棋盘已满',
};

/** 引擎侧的棋子编号 ↔ 颜色名。 */
const stoneOf = (color: string) => (color === 'black' ? 1 : 2);

// --- 纯逻辑（与 server/app/connect4.py 的四连判据对齐，仅用于本地提示高亮） ---

const DIRS = [[0, 1], [1, 1], [1, 0], [1, -1]];

/** 该列落下后停在哪一行；列满返回 -1。 */
function landingRow(board: number[][], col: number): number {
  for (let r = board.length - 1; r >= 0; r -= 1) {
    if (board[r][col] === 0) return r;
  }
  return -1;
}

/** 在 (r,c) 落子后是否连成四子（不改动棋盘）。 */
function winsAt(board: number[][], r: number, c: number, stone: number): boolean {
  const rows = board.length;
  const cols = board[0].length;
  for (const [dr, dc] of DIRS) {
    let count = 1;
    for (const sign of [1, -1]) {
      let nr = r + dr * sign;
      let nc = c + dc * sign;
      while (nr >= 0 && nr < rows && nc >= 0 && nc < cols && board[nr][nc] === stone) {
        count += 1;
        nr += dr * sign;
        nc += dc * sign;
      }
    }
    if (count >= WIN_LEN) return true;
  }
  return false;
}

/** 某一方「落在这一列就能立刻连四」的列集合。 */
function winningCols(board: number[][], stone: number): Set<number> {
  const out = new Set<number>();
  for (let c = 0; c < board[0].length; c += 1) {
    const r = landingRow(board, c);
    if (r < 0) continue;
    board[r][c] = stone;
    if (winsAt(board, r, c, stone)) out.add(c);
    board[r][c] = 0;
  }
  return out;
}

// --- 棋子圆盘 ---

function Disc({
  size, color, faded = false,
}: { size: number; color: 'black' | 'white'; faded?: boolean }) {
  const fill = color === 'black' ? connect4Black : connect4White;
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: fill,
        borderWidth: Math.max(1.5, size * 0.075),
        borderColor: connect4DiscEdge,
        opacity: faded ? 0.45 : 1,
      }}
    />
  );
}

export default function ConnectFourScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth, height: winHeight } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<Connect4State | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  /** 正在播放落子动画的那一格 key（`r,c`）与它的起始偏移。 */
  const [dropping, setDropping] = useState<string | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  const dropY = useRef(new Animated.Value(0)).current;
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // --- 棋盘尺寸 ---
  // 7 列 × 6 行**格位**（不是交叉点），四周留 0.35 格给边框：
  //   宽 = 7 + 0.7 = 7.7 单位；高 = 6 + 0.7 = 6.7 单位
  const rows = game?.rows ?? 6;
  const cols = game?.cols ?? 7;
  const availW = winWidth - spacing.md * 2;
  const availH = winHeight - 250;
  const cell = Math.max(26, Math.min(
    availW / (cols + 0.7), Math.max(availH, 240) / (rows + 0.7), BOARD_MAX / (cols + 0.7),
  ));
  const pad = cell * 0.35;
  const boardW = cols * cell + 2 * pad;
  const boardH = rows * cell + 2 * pad;
  const discSize = cell * 0.78;
  /** 格位 (r,c) 左上角在棋盘内的像素坐标。 */
  const posOf = useCallback(
    (r: number, c: number): [number, number] => [pad + c * cell, pad + r * cell],
    [pad, cell],
  );

  const myColor = game?.your_color || '';
  const myStone = stoneOf(myColor);
  const isMyTurn = !!game && !game.winner && game.next === myColor;
  const boardFull = !!game && game.board[0].every((v) => v !== 0);

  /** 轮到谁：能立刻连四的列 / 对手能立刻连四的列（本地推演，只用于提示）。 */
  const { winCols, blockCols } = useMemo(() => {
    if (!game || game.winner) return { winCols: new Set<number>(), blockCols: new Set<number>() };
    const empty = new Set<number>();
    if (!isMyTurn) return { winCols: empty, blockCols: empty };
    const mine = winningCols(game.board, myStone);
    const theirs = winningCols(game.board, stoneOf(game.next === 'black' ? 'white' : 'black'));
    // 自己能赢的列优先，同一列不再报「堵」
    for (const c of mine) theirs.delete(c);
    return { winCols: mine, blockCols: theirs };
  }, [game, isMyTurn, myStone]);

  /**
   * 上一手的**连线**：从这一手出发，四个方向上连成的同色段（≥2 连才画）。
   * 「对方在铺哪条线」是这一局最该看出来的东西，但盘面上一片圆盘很难一眼读出来 ——
   * 把那一手所在的连子串成一条线，视线自然就跟过去了。
   *
   * 画在**圆槽之下**：圆盘直径 0.78 格、相邻两格之间有 0.22 格的空隙，线只从
   * 空隙里露出来，看起来是「穿过这几枚圆盘」而不是盖在它们上面（盖上去会挡住
   * 盘面本身，而盘色是判断归属的唯一依据）。两端正好收在首尾两枚圆盘的边缘。
   */
  const lastLines = useMemo(() => {
    const segs: Array<{ r1: number; c1: number; r2: number; c2: number }> = [];
    if (!game || game.moves.length === 0) return segs;
    const m = game.moves[game.moves.length - 1];
    const r0 = m.y;
    const c0 = m.x;
    const stone = game.board[r0]?.[c0] ?? 0;
    if (!stone) return segs;
    const run = (dr: number, dc: number) => {
      let n = 0;
      for (;;) {
        const nr = r0 + dr * (n + 1);
        const nc = c0 + dc * (n + 1);
        if (nr < 0 || nr >= rows || nc < 0 || nc >= cols || game.board[nr][nc] !== stone) break;
        n += 1;
      }
      return n;
    };
    for (const [dr, dc] of DIRS) {
      const fwd = run(dr, dc);
      const back = run(-dr, -dc);
      if (fwd + back >= 1) {
        segs.push({
          r1: r0 - dr * back, c1: c0 - dc * back,
          r2: r0 + dr * fwd, c2: c0 + dc * fwd,
        });
      }
    }
    return segs;
  }, [game, rows, cols]);

  const playDrop = useCallback((r: number, c: number) => {
    const [x, y] = posOf(r, c);
    // 从棋盘顶部（row 0 的上方）滑到落点行
    dropY.setValue(-(y + cell));
    setDropping(`${r},${c}`);
    Animated.timing(dropY, {
      toValue: 0, duration: DROP_MS, useNativeDriver: Platform.OS !== 'web',
    }).start(() => setDropping(null));
  }, [cell, posOf, dropY]);

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
    if (gameOverRef.current) {
      navigation.goBack();
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  // --- WebSocket ---
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const syncOpponent = (roomData: Room) => {
      const opp = roomData.players.find((p) => p.id !== user?.id);
      setOpponentDisconnected(opp ? !opp.connected : false);
    };

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        syncOpponent(roomData);
      }
      if (msg.game) {
        const g = msg.game as Connect4State;
        setGame(g);
        setDropping(null);
        if (g.winner) setShowGameOver(true);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        syncOpponent(roomData);
      }
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as Connect4State;
      if (g) {
        setGame(g);
        setShowGameOver(false);
        gameOverShownRef.current = false;
        setDropping(null);
      }
    };

    const handleMove = (msg: SocketMessage) => {
      const col = msg.x as number;
      const row = msg.y as number;
      const color = msg.color as 'black' | 'white';
      setGame((prev) => {
        if (!prev) return prev;
        const nextBoard = prev.board.map((r) => [...r]);
        nextBoard[row][col] = stoneOf(color);
        return {
          ...prev,
          board: nextBoard,
          moves: [...prev.moves, { x: col, y: row, color }],
          next: msg.next as 'black' | 'white',
        };
      });
      playDrop(row, col);
      if (Platform.OS !== 'web') Vibration.vibrate(10);
    };

    const handleGameOver = (msg: SocketMessage) => {
      setGame((prev) => (prev ? {
        ...prev,
        winner: msg.winner as Connect4State['winner'],
        reason: msg.reason as Connect4State['reason'],
      } : prev));
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (Platform.OS !== 'web') Vibration.vibrate(30);
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
  }, [token, code, user?.id, onLeave, playDrop]);

  // --- 交互 ---
  const handleColumnPress = useCallback((c: number) => {
    if (!game || !isMyTurn) return;
    if (landingRow(game.board, c) < 0) {
      notify('放不下了', '这一列已经满了，换一列吧');
      return;
    }
    if (Platform.OS !== 'web') Vibration.vibrate(10);
    socketRef.current?.drop(c);
  }, [game, isMyTurn]);

  const doSurrender = () => {
    setConfirm(null);
    socketRef.current?.surrender();
  };

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

  // --- 渲染 ---
  if (!game) {
    return (
      <View style={styles.loading}>
        <Text style={styles.loadingText}>加载中…</Text>
      </View>
    );
  }

  const opponent = room?.players.find((p) => p.id !== user?.id);
  const turnText = (() => {
    if (game.winner) return '对局结束';
    if (game.next === myColor) return '轮到你落子';
    if (opponent?.is_bot) return `${opponent.nickname || opponent.username} 思考中…`;
    return '等待对方…';
  })();

  const lastMove = game.moves[game.moves.length - 1];

  /** 连线的像素层：一条圆头长条，中心落在首尾两格中心的中点，再整体旋转。 */
  const lastLineViews = lastLines.map((seg, i) => {
    const [x1, y1] = posOf(seg.r1, seg.c1);
    const [x2, y2] = posOf(seg.r2, seg.c2);
    const cx1 = x1 + cell / 2;
    const cy1 = y1 + cell / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    const th = cell * 0.2;
    return (
      <View
        key={`ll-${i}`}
        style={[styles.lastLine, {
          left: cx1 + dx / 2 - len / 2,
          top: cy1 + dy / 2 - th / 2,
          width: len,
          height: th,
          borderRadius: th / 2,
          transform: [{ rotate: `${(Math.atan2(dy, dx) * 180) / Math.PI}deg` }],
        }]}
        pointerEvents="none"
      />
    );
  });

  /** 棋子 + 空位槽。按 (r,c) 顺序渲染，DOM 文本顺序即盘面顺序（E2E 依赖）。 */
  const cells = [];
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const v = game.board[r][c];
      const [left, top] = posOf(r, c);
      const isLast = !!lastMove && lastMove.x === c && lastMove.y === r;
      cells.push(
        <View
          key={`${r}-${c}`}
          testID={`c4-cell-${r}-${c}`}
          style={[styles.cell, {
            left, top, width: cell, height: cell,
            justifyContent: 'center', alignItems: 'center',
          }]}
          pointerEvents="none"
        >
          <View
            style={{
              width: discSize, height: discSize, borderRadius: discSize / 2,
              backgroundColor: connect4Slot,
            }}
          />
        </View>,
      );
      if (v !== 0) {
        const color = v === 1 ? 'black' : 'white';
        const isDropping = dropping === `${r},${c}`;
        cells.push(
          <Animated.View
            key={`d-${r}-${c}`}
            testID={`c4-disc-${r}-${c}`}
            style={[styles.disc, {
              left, top, width: cell, height: cell,
              justifyContent: 'center', alignItems: 'center',
              transform: isDropping ? [{ translateY: dropY }] : undefined,
            }]}
            pointerEvents="none"
          >
            <Disc size={discSize} color={color} />
            {isLast && (
              <View style={[styles.lastDot, {
                width: Math.max(6, discSize * 0.22),
                height: Math.max(6, discSize * 0.22),
                borderRadius: discSize,
              }]} />
            )}
          </Animated.View>,
        );
      }
    }
  }

  /** 列热区：整列一个可点区域，落在棋盘之上、棋子之下（pointerEvents 分层）。 */
  const columnHits = [];
  if (!game.winner) {
    for (let c = 0; c < cols; c += 1) {
      const full = landingRow(game.board, c) < 0;
      const isWin = winCols.has(c);
      const isBlock = blockCols.has(c);
      columnHits.push(
        <TouchableOpacity
          key={`hit-${c}`}
          testID={`c4-col-${c}`}
          style={[styles.columnHit, {
            left: pad + c * cell, top: pad, width: cell, height: rows * cell,
          }]}
          activeOpacity={0.35}
          onPress={() => handleColumnPress(c)}
          // 满列**不禁用**：禁用后是 pointer-events:none，点上去毫无反应、
          // 也没有提示，用户只会觉得「点了没反应」。留着让它弹「这一列满了」。
          disabled={!isMyTurn}
        >
          {/* 提示条：颜色 + 文字 + 形状三条通道（颜色单用不符合 WCAG 1.4.1） */}
          {isMyTurn && (isWin || isBlock) && (
            <View
              style={[
                styles.hintChip,
                isWin ? styles.hintChipWin : styles.hintChipBlock,
                { width: Math.max(28, cell * 0.86) },
              ]}
              pointerEvents="none"
            >
              <Text style={styles.hintChipText}>{isWin ? '连四' : '堵'}</Text>
            </View>
          )}
        </TouchableOpacity>,
      );
    }
  }

  return (
    <View style={styles.container}>
      {/* 顶栏：我方颜色 / 回合提示 / 帮助 / 房号 */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <View style={[styles.colorDot, {
            backgroundColor: myColor === 'black' ? connect4Black : connect4White,
            borderWidth: 1.5,
            borderColor: connect4DiscEdge,
          }]} />
          <Text style={styles.myColor}>{COLOR_LABELS[myColor] || ''}</Text>
          <Text style={[styles.turnHint, isMyTurn && styles.turnActive]}>{turnText}</Text>
        </View>
        <GameHelpButton game="connect4" size={24} />
        <Text style={styles.roomCode}>#{code}</Text>
      </View>

      {opponentDisconnected && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={14} color={colors.white} />
          <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      {/* 棋盘 */}
      <View style={styles.boardArea}>
        <View
          testID="c4-board"
          style={[styles.board, { width: boardW, height: boardH, backgroundColor: connect4Frame }]}
        >
          {lastLineViews}
          {cells}
          <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
            {columnHits}
          </View>
        </View>
      </View>

      {/* 图例：提示标的含义（颜色之外的第二通道就是标签本身） */}
      {isMyTurn && (winCols.size > 0 || blockCols.size > 0) && (
        <View style={styles.legend}>
          {winCols.size > 0 && (
            <View style={styles.legendItem}>
              <View style={[styles.legendSwatch, { backgroundColor: connect4WinHint }]} />
              <Text style={styles.legendText}>连四：这一手直接赢</Text>
            </View>
          )}
          {blockCols.size > 0 && (
            <View style={styles.legendItem}>
              <View style={[styles.legendSwatch, styles.legendSwatchDiamond, {
                backgroundColor: connect4BlockHint,
              }]} />
              <Text style={styles.legendText}>堵：不堵对方就赢</Text>
            </View>
          )}
        </View>
      )}

      {/* 操作栏 */}
      <View style={styles.bottomBar}>
        {!game.winner && (
          <TouchableOpacity style={styles.surrenderBtn} onPress={() => setConfirm('surrender')}>
            <Ionicons name="flag-outline" size={16} color={colors.white} />
            <Text style={styles.surrenderBtnText}> 认输</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirm('leave')}>
          <Ionicons name="exit-outline" size={16} color={colors.textSecondary} />
          <Text style={styles.leaveBtnText}> 退出</Text>
        </TouchableOpacity>
      </View>

      {/* 认输 / 退出确认 */}
      <Modal visible={confirm !== null} transparent animationType="fade">
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

      {/* 结算 */}
      <Modal visible={showGameOver} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>对局结束</Text>
            <Text style={styles.modalResult}>{WINNER_LABELS[game.winner || ''] || '—'}</Text>
            {!!game.reason && (
              <Text style={styles.modalReason}>{REASON_LABELS[game.reason] || game.reason}</Text>
            )}
            <Text style={styles.modalReason}>
              {boardFull ? '棋盘已满，共 42 步' : `共 ${game.moves.length} 步`}
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary]}
              onPress={handleBackToRoom}
            >
              <Text style={styles.modalBtnPrimaryText}>返回房间</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg },
  loadingText: { color: colors.textSecondary, fontSize: fontSize.md },

  topBar: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  colorDot: { width: 14, height: 14, borderRadius: 7 },
  myColor: { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  turnHint: { fontSize: fontSize.sm, color: colors.textMuted },
  turnActive: { color: colors.primary, fontWeight: '700' },
  roomCode: { fontSize: fontSize.xs, color: colors.textMuted },

  banner: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.warning, paddingVertical: spacing.xs,
  },
  bannerText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },

  boardArea: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  board: { position: 'relative', borderRadius: radius.md, overflow: 'hidden' },
  cell: { position: 'absolute' },
  lastLine: { position: 'absolute', backgroundColor: connect4LineHint },
  disc: { position: 'absolute' },
  lastDot: {
    position: 'absolute',
    backgroundColor: connect4Last,
    borderWidth: 1.5,
    borderColor: connect4DiscEdge,
  },

  columnHit: { position: 'absolute', alignItems: 'center' },
  hintChip: {
    marginTop: 2,
    paddingVertical: 2,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  hintChipWin: { backgroundColor: connect4WinHint },
  hintChipBlock: { backgroundColor: connect4BlockHint },
  hintChipText: { fontSize: 11, fontWeight: '800', color: '#111827' },

  legend: {
    flexDirection: 'row', justifyContent: 'center', gap: spacing.md,
    paddingHorizontal: spacing.md, paddingBottom: spacing.xs,
  },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  legendSwatch: { width: 12, height: 12, borderRadius: 3 },
  legendSwatchDiamond: { transform: [{ rotate: '45deg' }] },
  legendText: { fontSize: fontSize.xs, color: colors.textSecondary },

  bottomBar: {
    flexDirection: 'row', gap: spacing.sm,
    padding: spacing.md, backgroundColor: colors.card,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  surrenderBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.danger, borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
  },
  surrenderBtnText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  leaveBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border,
    borderRadius: radius.md, paddingVertical: spacing.sm + 6,
  },
  leaveBtnText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },

  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.5)',
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg, alignItems: 'center', gap: spacing.sm,
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '800', color: colors.text },
  modalResult: { fontSize: fontSize.lg, fontWeight: '700', color: colors.primary },
  modalReason: { fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center' },
  modalBtn: {
    width: '100%', alignItems: 'center', borderRadius: radius.md,
    paddingVertical: spacing.sm + 6, marginTop: spacing.xs,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});