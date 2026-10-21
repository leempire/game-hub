// 中国象棋对局屏 —— 9 列 × 10 行、2 人在线对战。
// 照 ReversiScreen 的骨架：WS sync、应用内 Modal、返回双路径、Vibration。
//
// 两条与其它棋类不同的设计：
// 1. **客户端不实现走法规则**。合法着法由服务端随 sync/game_start/move 一起下发
//    （`legal: [fr,fc,tr,tc][]`），客户端只拿它做「可落点」高亮。象棋规则（马腿、
//    象眼、照面、炮架）复杂到没有第二份实现的必要——飞行棋踩过「客户端自己推
//    路径画错」的坑。
// 2. **两步落子**（先点子、再点落点），不是一点即走。棋盘交叉点只有 ~39px
//    （低于 44pt 触控下限，受物理尺寸限制），两步式让误触可以在提交前撤销，
//    这正是小棋盘缺的那层补偿。
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
  xiangqiBoard, xiangqiLine, xiangqiFace, xiangqiRed, xiangqiBlack, xiangqiAccent,
  lastMoveMark, lastMoveMarkEdge,
} from '../theme';
import {
  XIANGQI_ROWS, XIANGQI_COLS, COLOR_LABELS,
  isRedPiece, pieceGlyph, pieceName, flipSquare,
} from '../xiangqiBoard';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';

// --- Types ---

interface XiangqiMove {
  from: number[];
  to: number[];
  piece: string;
  captured: string | null;
}

interface XiangqiState {
  rows: number;
  cols: number;
  /** 10×9，元素是棋子单字符（红大写/黑小写）或 null。 */
  board: (string | null)[][];
  moves: XiangqiMove[];
  next: 'red' | 'black';
  your_color: 'red' | 'black' | '';
  winner: null | 'red' | 'black' | 'draw';
  reason: null | 'checkmate' | 'stalemate' | 'no_capture' | 'surrender' | 'disconnect';
  check: boolean;
  last_move: XiangqiMove | null;
  /** 轮到走的一方全部合法着法，扁平 [fr, fc, tr, tc]。 */
  legal: number[][];
}

interface Props {
  code: string;
  onLeave: () => void;
}

// --- Constants ---

/** 棋盘尺寸上限（平板/桌面端别铺满整屏）。 */
const BOARD_MAX = 520;
/** 走子滑行动画时长：小元素位移，100~200ms 区间。 */
const SLIDE_MS = 170;

const WINNER_LABELS: Record<string, string> = {
  red: '红方胜',
  black: '黑方胜',
  draw: '和棋',
};

const REASON_LABELS: Record<string, string> = {
  checkmate: '将死',
  stalemate: '困毙（无子可动，判负）',
  no_capture: '60 回合无吃子，判和',
  surrender: '对方认输',
  disconnect: '对方断线超时',
};

// --- 棋子 ---

function PieceView({
  piece, size, selected, isLast, checked, canMove, slide,
}: {
  piece: string;
  size: number;
  selected: boolean;
  isLast: boolean;
  checked: boolean;
  /** 轮到己方、且这枚子至少有一个合法落点（残局里一眼看出谁被困死）。 */
  canMove: boolean;
  slide?: { x: Animated.Value; y: Animated.Value } | null;
}) {
  const red = isRedPiece(piece);
  const ink = red ? xiangqiRed : xiangqiBlack;
  // 选中环用强调色（与红黑双方都不同色），环宽加倍——形状 + 颜色两条通道
  const ringWidth = Math.max(1.5, size * 0.07);
  const transform = slide ? [{ translateX: slide.x }, { translateY: slide.y }] : undefined;
  return (
    <Animated.View
      style={[
        styles.piece,
        {
          width: size, height: size, borderRadius: size / 2,
          backgroundColor: xiangqiFace,
          borderColor: selected ? xiangqiAccent : ink,
          borderWidth: selected ? ringWidth * 2 : ringWidth,
        },
        transform ? { transform } : null,
      ]}
    >
      <Text style={[styles.pieceGlyph, { color: ink, fontSize: Math.round(size * 0.52) }]}>
        {pieceGlyph(piece)}
      </Text>
      {/* 最近一步：右上角一个小方块。**不能画在棋子中心**（会盖住汉字），
          所以位置与其它三个棋类不同，但颜色与描边取同一套常量，
          保证「黄 = 最近一步」在四个游戏里含义一致（见 theme.ts）。 */}
      {isLast && (
        <View
          testID="xq-last-mark"
          style={[styles.lastMark, {
            width: size * 0.26, height: size * 0.26,
            borderRadius: size * 0.06,
            top: size * 0.04, right: size * 0.04,
          }]}
        />
      )}
      {/* 有子可动：**左下角**一枚小圆点。位置与形状都避开另外两个标记
          （最近一步在右上角、是方块；选中是整圈粗环），所以「这枚还能走」
          是独立的第三通道，不靠颜色单独承载（WCAG 1.4.1）。
          只在轮到己方时出现 —— 对方回合亮一片等于噪音。 */}
      {canMove && !selected && (
        <View
          testID="xq-movable-dot"
          style={[styles.movableDot, {
            width: size * 0.22, height: size * 0.22, borderRadius: size * 0.11,
            left: size * 0.08, bottom: size * 0.08,
          }]}
        />
      )}
      {/* 被将军的帅/将：外圈再套一圈警示环（形状 + 颜色） */}
      {checked && (
        <View style={[styles.checkRing, {
          width: size * 1.16, height: size * 1.16, borderRadius: size * 0.58,
          borderWidth: Math.max(2, size * 0.08),
        }]} />
      )}
    </Animated.View>
  );
}

// --- Main ---

export default function XiangqiScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth, height: winHeight } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<XiangqiState | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  // 断线倒计时的起点：优先后端在事件里给的 timeout，其次房间数据里的剩余值
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const [selected, setSelected] = useState<[number, number] | null>(null);
  const [slideAt, setSlideAt] = useState<string | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  const slideX = useRef(new Animated.Value(0)).current;
  const slideY = useRef(new Animated.Value(0)).current;
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // --- 棋盘尺寸 ---
  // 棋盘是 9 列 × 10 行**交叉点**，棋子落在交叉点上。每个交叉点留 1 单位，
  // 四周再留 0.6 单位给棋子出界（边缘的棋子有一半在棋盘外）：
  //   宽 = 8 格 + 2×0.6 = 9.2 单位；高 = 9 格 + 1.2 = 10.2 单位
  const availW = winWidth - spacing.md * 2;
  const availH = winHeight - 250;                    // 顶部/底部 chrome 估算
  const cell = Math.max(24, Math.min(
    availW / 9.2, Math.max(availH, 240) / 10.2, BOARD_MAX / 9.2,
  ));
  const pad = cell * 0.6;
  const boardW = 8 * cell + 2 * pad;
  const boardH = 9 * cell + 2 * pad;
  const pieceSize = cell * 0.86;
  /** 交叉点 (r,c) 在棋盘内的像素中心。 */
  const centerOf = useCallback(
    (r: number, c: number): [number, number] => [pad + c * cell, pad + r * cell],
    [pad, cell],
  );

  const myColor = game?.your_color || '';
  const flipped = myColor === 'black';               // 黑方视角把棋盘转 180°
  const isMyTurn = !!game && !game.winner && game.next === myColor;

  /** 选中棋子的全部合法落点（key = "r,c"）。 */
  const dests = useMemo(() => {
    if (!selected || !game || !isMyTurn) return new Set<string>();
    return new Set(
      game.legal
        .filter((m) => m[0] === selected[0] && m[1] === selected[1])
        .map((m) => `${m[2]},${m[3]}`),
    );
  }, [selected, game, isMyTurn]);

  /** 有子可动的格（选中另一枚己方子时判断要不要切换选择）。 */
  const movable = useMemo(() => {
    if (!game) return new Set<string>();
    return new Set(game.legal.map((m) => `${m[0]},${m[1]}`));
  }, [game]);

  /** 正被将军的帅/将位置（要给它套警示环）。 */
  const checkedKing = useMemo(() => {
    if (!game || !game.check) return null;
    const target = game.next === 'red' ? 'K' : 'k';
    for (let r = 0; r < XIANGQI_ROWS; r++) {
      for (let c = 0; c < XIANGQI_COLS; c++) {
        if (game.board[r][c] === target) return `${r},${c}`;
      }
    }
    return null;
  }, [game]);

  const playSlide = useCallback((from: number[], to: number[]) => {
    const [vr1, vc1] = flipSquare(from[0], from[1], flipped);
    const [vr2, vc2] = flipSquare(to[0], to[1], flipped);
    slideX.setValue((vc1 - vc2) * cell);
    slideY.setValue((vr1 - vr2) * cell);
    setSlideAt(`${to[0]},${to[1]}`);
    Animated.parallel([
      Animated.timing(slideX, { toValue: 0, duration: SLIDE_MS, useNativeDriver: Platform.OS !== 'web' }),
      Animated.timing(slideY, { toValue: 0, duration: SLIDE_MS, useNativeDriver: Platform.OS !== 'web' }),
    ]).start(() => setSlideAt(null));
  }, [cell, flipped, slideX, slideY]);

  // --- 返回双路径 ---
  // 原生：beforeRemove 弹确认；Web：backGuard 拦下 popstate 后派发 onConfirmLeave
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

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) setRoom(roomData);
      if (msg.game) {
        const g = msg.game as XiangqiState;
        setGame(g);
        setSelected(null);
        setSlideAt(null);
        if (g.winner) setShowGameOver(true);
      }
      if (roomData) {
        const opp = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opp ? !opp.connected : false);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        const opp = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opp ? !opp.connected : false);
      }
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as XiangqiState;
      if (g) {
        setGame(g);
        setShowGameOver(false);
        gameOverShownRef.current = false;
        setSelected(null);
        setSlideAt(null);
      }
    };

    const handleMove = (msg: SocketMessage) => {
      const from = msg.from as number[];
      const to = msg.to as number[];
      setGame((prev) => {
        if (!prev) return prev;
        const board = prev.board.map((row) => [...row]);
        board[to[0]][to[1]] = board[from[0]][from[1]];
        board[from[0]][from[1]] = null;
        const move: XiangqiMove = {
          from, to,
          piece: msg.piece as string,
          captured: (msg.captured as string | null) ?? null,
        };
        return {
          ...prev,
          board,
          moves: [...prev.moves, move],
          next: msg.next as 'red' | 'black',
          check: !!msg.check,
          legal: (msg.legal as number[][]) || [],
          last_move: move,
        };
      });
      setSelected(null);
      playSlide(from, to);
      if (msg.captured && Platform.OS !== 'web') Vibration.vibrate(20);
    };

    const handleGameOver = (msg: SocketMessage) => {
      setGame((prev) => (prev ? {
        ...prev,
        winner: msg.winner as XiangqiState['winner'],
        reason: msg.reason as XiangqiState['reason'],
        legal: [],
        check: false,
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
  }, [token, code, user?.id, onLeave, playSlide]);

  // --- 交互 ---
  const handleSquarePress = useCallback((r: number, c: number) => {
    if (!game || !isMyTurn) return;
    const key = `${r},${c}`;
    // 点合法落点 → 落子
    if (selected && dests.has(key)) {
      if (Platform.OS !== 'web') Vibration.vibrate(10);
      socketRef.current?.xiangqiMove(selected[1], selected[0], c, r);
      setSelected(null);
      return;
    }
    const piece = game.board[r][c];
    // 点自己的子 → 选中 / 换选（无可走子则提示一下，否则像点空了）
    if (piece && isRedPiece(piece) === (myColor === 'red')) {
      if (movable.has(key)) {
        setSelected([r, c]);
      } else {
        setSelected(null);
        notify('无处可走', `${pieceName(piece)}这一步没有合法落点`);
      }
      return;
    }
    setSelected(null);
  }, [game, isMyTurn, selected, dests, movable, myColor]);

  const handleSurrender = () => setConfirm('surrender');
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

  // --- 派生显示 ---
  const lastKey = game?.last_move
    ? `${game.last_move.to[0]},${game.last_move.to[1]}`
    : null;
  const opponent = room?.players.find((p) => p.id !== user?.id);
  const turnText = (() => {
    if (!game) return '加载中…';
    if (game.winner) return '对局结束';
    if (game.next === myColor) return '轮到你走';
    if (opponent?.is_bot) return `${opponent.nickname || opponent.username} 思考中…`;
    return '等待对方…';
  })();

  /** 棋盘网格线（含河界断口与九宫斜线）。 */
  const gridLines = useMemo(() => {
    const nodes: React.ReactNode[] = [];
    const x0 = pad, x1 = pad + 8 * cell;
    const y0 = pad, y1 = pad + 9 * cell;
    // 横线 10 条（贯通全宽）
    for (let j = 0; j <= 9; j++) {
      const y = pad + j * cell;
      nodes.push(<View key={`h${j}`} style={[styles.gLine, { left: x0, top: y - 0.5, width: x1 - x0, height: 1 }]} />);
    }
    // 竖线 9 条：最外两条贯通，中间 7 条在河界处断开
    for (let i = 0; i <= 8; i++) {
      const x = pad + i * cell;
      if (i === 0 || i === 8) {
        nodes.push(<View key={`v${i}`} style={[styles.gLine, { left: x - 0.5, top: y0, width: 1, height: y1 - y0 }]} />);
      } else {
        const mid1 = pad + 4 * cell;
        const mid2 = pad + 5 * cell;
        nodes.push(<View key={`v${i}a`} style={[styles.gLine, { left: x - 0.5, top: y0, width: 1, height: mid1 - y0 }]} />);
        nodes.push(<View key={`v${i}b`} style={[styles.gLine, { left: x - 0.5, top: mid2, width: 1, height: y1 - mid2 }]} />);
      }
    }
    // 九宫斜线：上宫 (0,3)-(2,5)、下宫 (7,3)-(9,5)，各两条对角线
    const diagLen = 2 * cell * Math.SQRT2;
    [[0, 3, 1], [0, 5, -1], [7, 3, -1], [7, 5, 1]].forEach(([r, c, sign], idx) => {
      const [cx, cy] = centerOf(r + 1, c + (sign > 0 ? 1 : -1));
      nodes.push(
        <View
          key={`d${idx}`}
          style={[styles.gLine, {
            left: cx - diagLen / 2, top: cy - 0.5,
            width: diagLen, height: 1,
            transform: [{ rotate: `${sign > 0 ? 45 : -45}deg` }],
          }]}
        />,
      );
    });
    // 炮兵位的角标（传统棋盘在炮位与兵位画四个角括号）—— 纯装饰。
    // 括号**贴在棋子外沿**（gap 取棋子半径稍外），与实物棋盘一致：有子时刚好从
    // 棋子四角露出来，空位时看着就是一个「这里该摆子」的框。最外两列朝外的那两个
    // 括号不画（真棋盘也省 —— 画了正好顶到棋盘边上）。
    // 这 14 个点在 180° 旋转下是**自映射**的（炮 2/7 行、兵 3/6 行且列对称），
    // 所以这里直接按棋盘坐标画，不必过 `flipSquare`。
    const t = Math.max(1, cell * 0.03);
    const gap = pieceSize * 0.54;
    const arm = cell * 0.13;
    const marks: Array<[number, number]> = [
      [2, 1], [2, 7], [7, 1], [7, 7],                        // 炮位
      [3, 0], [3, 2], [3, 4], [3, 6], [3, 8],                // 兵位（上半）
      [6, 0], [6, 2], [6, 4], [6, 6], [6, 8],                // 兵位（下半）
    ];
    marks.forEach(([r, c], mi) => {
      const [cx, cy] = centerOf(r, c);
      ([[-1, -1], [1, -1], [-1, 1], [1, 1]] as Array<[number, number]>).forEach(([sx, sy], di) => {
        if ((c === 0 && sx < 0) || (c === 8 && sx > 0)) return;
        const ix = cx + sx * gap;                     // 括号的内角
        const iy = cy + sy * gap;
        nodes.push(<View key={`mk${mi}-${di}h`} style={[styles.gLine, {
          left: sx < 0 ? ix - arm : ix, top: iy - t / 2, width: arm, height: t,
        }]} />);
        nodes.push(<View key={`mk${mi}-${di}v`} style={[styles.gLine, {
          left: ix - t / 2, top: sy < 0 ? iy - arm : iy, width: t, height: arm,
        }]} />);
      });
    });
    return nodes;
  }, [pad, cell, centerOf, pieceSize]);

  /** 交叉点热区 + 棋子层（都按显示坐标排布）。 */
  const squares = useMemo(() => {
    const nodes: React.ReactNode[] = [];
    for (let r = 0; r < XIANGQI_ROWS; r++) {
      for (let c = 0; c < XIANGQI_COLS; c++) {
        const [vr, vc] = flipSquare(r, c, flipped);
        const [cx, cy] = centerOf(vr, vc);
        const piece = game?.board[r][c] ?? null;
        const key = `${r},${c}`;
        const isDest = dests.has(key);
        const canCapture = isDest && !!piece;
        nodes.push(
          <TouchableOpacity
            key={`sq-${key}`}
            testID={`xq-${r}-${c}`}
            accessibilityLabel={`${vr + 1} 行 ${vc + 1} 列 ${pieceName(piece)}`}
            style={[styles.hit, {
              left: cx - cell / 2, top: cy - cell / 2, width: cell, height: cell,
            }]}
            onPress={() => handleSquarePress(r, c)}
            activeOpacity={1}
          >
            {/* 空位可落点：实心圆点 */}
            {isDest && !canCapture && (
              <View style={[styles.destDot, {
                width: cell * 0.30, height: cell * 0.30, borderRadius: cell * 0.15,
              }]} />
            )}
          </TouchableOpacity>,
        );
        if (piece) {
          nodes.push(
            <View
              key={`pc-${key}`}
              testID={`xq-piece-${r}-${c}`}
              pointerEvents="none"
              style={[styles.pieceSlot, {
                left: cx - cell / 2, top: cy - cell / 2, width: cell, height: cell,
              }]}
            >
              <PieceView
                piece={piece}
                size={pieceSize}
                selected={!!selected && selected[0] === r && selected[1] === c}
                isLast={lastKey === key}
                checked={checkedKing === key}
                canMove={isMyTurn && movable.has(key)}
                slide={slideAt === key ? { x: slideX, y: slideY } : null}
              />
              {/* 可吃子：在敌子外面套一圈强调环（形状区分「吃」与「走」） */}
              {canCapture && (
                <View style={[styles.captureRing, {
                  width: pieceSize * 1.14, height: pieceSize * 1.14,
                  borderRadius: pieceSize * 0.57,
                  borderWidth: Math.max(2, cell * 0.07),
                }]} pointerEvents="none" />
              )}
            </View>,
          );
        }
      }
    }
    return nodes;
  }, [game, flipped, dests, selected, lastKey, checkedKing, cell, pieceSize,
      centerOf, handleSquarePress, slideAt, slideX, slideY, isMyTurn, movable]);

  if (!game) {
    return (
      <View style={styles.loading}>
        <Text style={styles.loadingText}>加载对局中…</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* 顶栏：我方颜色 / 回合提示 / 帮助 / 房号 */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <View style={[styles.colorDot, {
            backgroundColor: myColor === 'red' ? xiangqiRed : xiangqiBlack,
          }]} />
          <Text style={styles.myColor}>{COLOR_LABELS[myColor] || ''}</Text>
          <Text style={[styles.turnHint, isMyTurn && styles.turnActive]}>{turnText}</Text>
        </View>
        <GameHelpButton game="xiangqi" size={24} />
        <Text style={styles.roomCode}>#{code}</Text>
      </View>

      {opponentDisconnected && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={14} color={colors.white} />
          <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      {game.check && !game.winner && (
        <View style={styles.checkBanner}>
          <Ionicons name="alert-circle" size={14} color={colors.white} />
          <Text style={styles.bannerText}>
            {game.next === myColor ? ' 你被将军了，必须应将' : ' 将军！'}
          </Text>
        </View>
      )}

      {/* 棋盘 */}
      <View style={styles.boardArea}>
        <View
          style={[styles.board, { width: boardW, height: boardH, backgroundColor: xiangqiBoard }]}
        >
          <View style={StyleSheet.absoluteFill} pointerEvents="none">{gridLines}</View>
          {/* 河界文字：楚河 / 汉界 */}
          <View
            pointerEvents="none"
            style={[styles.river, { left: pad, top: pad + 4 * cell, width: 8 * cell, height: cell }]}
          >
            <Text style={[styles.riverText, { fontSize: Math.max(11, Math.round(cell * 0.34)) }]}>
              楚 河
            </Text>
            <Text style={[styles.riverText, { fontSize: Math.max(11, Math.round(cell * 0.34)) }]}>
              汉 界
            </Text>
          </View>
          {squares}
        </View>
      </View>

      {/* 操作栏 */}
      <View style={styles.bottomBar}>
        {!game.winner && (
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

      {/* 认输 / 退出确认 */}
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

      {/* 结算 */}
      <Modal visible={showGameOver} transparent animationType="fade" onRequestClose={handleBackToRoom}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>对局结束</Text>
            <Text style={styles.modalResult}>{WINNER_LABELS[game.winner || ''] || '—'}</Text>
            {!!game.reason && (
              <Text style={styles.modalReason}>{REASON_LABELS[game.reason] || game.reason}</Text>
            )}
            <Text style={styles.modalReason}>共 {game.moves.length} 步</Text>
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
  checkBanner: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.danger, paddingVertical: spacing.xs,
  },
  bannerText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },

  boardArea: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  board: { position: 'relative', borderRadius: radius.sm, overflow: 'visible' },
  gLine: { position: 'absolute', backgroundColor: xiangqiLine },
  river: {
    position: 'absolute', flexDirection: 'row',
    alignItems: 'center', justifyContent: 'space-around',
  },
  riverText: { color: xiangqiLine, opacity: 0.55, fontWeight: '600', letterSpacing: 2 },

  hit: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  destDot: { backgroundColor: xiangqiAccent },
  pieceSlot: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  piece: { alignItems: 'center', justifyContent: 'center' },
  pieceGlyph: { fontWeight: '700', textAlign: 'center' },
  lastMark: {
    position: 'absolute',
    backgroundColor: lastMoveMark,
    borderWidth: 1.5,
    borderColor: lastMoveMarkEdge,
  },
  // 有子可动的小圆点：xiangqiAccent vs 棋子面 4.89:1 ✓（图形阈值 3:1）
  movableDot: { position: 'absolute', backgroundColor: xiangqiAccent },
  checkRing: {
    position: 'absolute', borderColor: colors.danger,
  },
  captureRing: { position: 'absolute', borderColor: xiangqiAccent },

  bottomBar: { flexDirection: 'row', gap: spacing.md, padding: spacing.md },
  surrenderBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.danger, borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
  },
  surrenderBtnText: { color: colors.white, fontSize: fontSize.md, fontWeight: '600' },
  leaveBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
    paddingVertical: spacing.sm + 6,
  },
  leaveBtnText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },

  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay,
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  modalCard: {
    width: '100%', maxWidth: 340, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg, alignItems: 'center', gap: spacing.sm,
  },
  modalTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  modalResult: { fontSize: fontSize.xl, fontWeight: '800', color: colors.primary },
  modalReason: { fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center' },
  modalBtn: {
    width: '100%', alignItems: 'center', justifyContent: 'center',
    borderRadius: radius.md, paddingVertical: spacing.sm + 6, marginTop: spacing.xs,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.border },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
