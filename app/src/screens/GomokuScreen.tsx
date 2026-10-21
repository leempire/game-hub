import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions, Platform, Vibration,
  PanResponder, Animated, GestureResponderEvent,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize, lastMoveMark, lastMoveMarkEdge,
  gomokuHintWin, gomokuHintBlock,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';

interface GameState {
  board_size: number;
  board: number[][];
  moves: Array<{ x: number; y: number; color: string }>;
  next: 'black' | 'white';
  your_color: 'black' | 'white';
  winner: null | 'black' | 'white' | 'draw';
  reason: null | 'five' | 'resign' | 'surrender' | 'disconnect' | 'draw';
}

interface Props {
  code: string;
  onLeave: () => void;
}

interface Coord { x: number; y: number }

interface DragState {
  aimX: number;
  aimY: number;
  /** 手指位置，**视口坐标**（放大镜要浮在手指上方，它画在视口层里，不跟 scale 走）。 */
  touchX: number;
  touchY: number;
  magnifierVisible: boolean;
}

const BOARD_SIZE = 15;

const STONE_MAP: Record<number, string> = { 1: 'black', 2: 'white' };

const WINNER_LABELS: Record<string, string> = { black: '黑棋胜', white: '白棋胜', draw: '和棋' };

const REASON_LABELS: Record<string, string> = {
  five: '五连', resign: '认输', surrender: '认输', disconnect: '断线判负', draw: '和棋',
};

// 胜利五连的环。金色 #FFD700 在木色棋盘上只有 1.32:1、在白子上 1.29:1——
// 白棋赢的那一局等于没高亮。深红在所有三种背景（木色 3.03 / 黑子 3.10 /
// 白子 5.16）上都过 3:1，见 docs/移动端体验设计方法论.md 第 9 节。
const WIN_RING = '#C62828';

// 脉冲循环次数上限。WCAG 2.2.2：自动播放且持续超过 5 秒的运动必须能暂停/停止；
// 这里用「限次」代替「加暂停按钮」——脉冲本来就只是把注意力拉过来，3 轮足够。
// 每轮 1.6s（回合）/ 2.0s（胜利），都压在 5s 以内。
const TURN_PULSE_CYCLES = 3;
const WIN_PULSE_CYCLES = 2;
const MAG_SIZE = 130;
const MAG_ZOOM = 2;
const MAG_DELAY = 200;

// 双指缩放范围：1× 为原始大小；2.5× 时 15×15 的格宽从 ~25px 涨到 ~62px，
// 越过 44pt 的触控建议值——这是「长按放大镜」之外的另一条精瞄路径（小屏补偿）。
const MIN_ZOOM = 1;
const MAX_ZOOM = 2.5;
// 判定「单指拖动平移」的最小位移：低于它算轻点（放大态下轻点仍然落子）
const PAN_SLOP = 4;
// 原生端用原生驱动；RNW 不支持，必须降级（项目既有写法）
const NATIVE_DRIVER = Platform.OS !== 'web';

// 纯函数：从最后落子位置沿四方向扫描，找 ≥5 连续同色，返回五连坐标
function findWinningFive(
  board: number[][], lastMove: Coord | null,
): Coord[] | null {
  if (!lastMove) return null;
  const { x: lx, y: ly } = lastMove;
  const color = board[ly]?.[lx];
  if (!color) return null;
  const dirs: [number, number][] = [[1, 0], [0, 1], [1, 1], [1, -1]];
  for (const [dx, dy] of dirs) {
    const line: Coord[] = [{ x: lx, y: ly }];
    for (let sign = 1; sign >= -1; sign -= 2) {
      for (let i = 1; i <= 4; i++) {
        const nx = lx + dx * i * sign;
        const ny = ly + dy * i * sign;
        if (nx < 0 || nx >= BOARD_SIZE || ny < 0 || ny >= BOARD_SIZE) break;
        if (board[ny][nx] !== color) break;
        if (sign === 1) line.push({ x: nx, y: ny });
        else line.unshift({ x: nx, y: ny });
      }
    }
    if (line.length >= 5) return line;
  }
  return null;
}

// 纯函数：**假设** (x, y) 已经放了 color 的子，四方向扫一遍是否连成五。
// 只服务本地提示高亮，不改棋盘、也不参与胜负判定 —— 判胜负永远在服务端
// （同重力四子棋的 `winsAt`：提示可以本地算，「谁赢了」不行）。
function winsAtPoint(board: number[][], x: number, y: number, color: number): boolean {
  const dirs: [number, number][] = [[1, 0], [0, 1], [1, 1], [1, -1]];
  for (const [dx, dy] of dirs) {
    let count = 1;
    for (const sign of [1, -1]) {
      for (let i = 1; i <= 4; i++) {
        const nx = x + dx * i * sign;
        const ny = y + dy * i * sign;
        if (nx < 0 || nx >= BOARD_SIZE || ny < 0 || ny >= BOARD_SIZE) break;
        if (board[ny][nx] !== color) break;
        count += 1;
      }
    }
    if (count >= 5) return true;
  }
  return false;
}

/** 全部空点里，「color 落在这里就立刻连成五」的那些。 */
function winningPoints(board: number[][], color: number): Coord[] {
  const out: Coord[] = [];
  for (let y = 0; y < BOARD_SIZE; y++) {
    for (let x = 0; x < BOARD_SIZE; x++) {
      if (board[y][x] !== 0) continue;
      if (winsAtPoint(board, x, y, color)) out.push({ x, y });
    }
  }
  return out;
}

// 触摸坐标 → 交叉点，超出棋盘范围返回 null
function computeIntersection(lx: number, ly: number, cs: number): Coord | null {
  if (lx < 0 || ly < 0 || lx >= cs * BOARD_SIZE || ly >= cs * BOARD_SIZE) return null;
  return {
    x: Math.max(0, Math.min(BOARD_SIZE - 1, Math.floor(lx / cs))),
    y: Math.max(0, Math.min(BOARD_SIZE - 1, Math.floor(ly / cs))),
  };
}

// 落子弹入动画棋子（仅最新落子使用）
function PopInStone({ color, cellSize, isLast, isWinning, winOpacity }: {
  color: string; cellSize: number; isLast: boolean;
  isWinning: boolean; winOpacity: Animated.Value | null;
}) {
  const scale = useRef(new Animated.Value(0.6)).current;
  useEffect(() => {
    Animated.sequence([
      Animated.timing(scale, { toValue: 1.05, duration: 120, useNativeDriver: true }),
      Animated.timing(scale, { toValue: 1, duration: 60, useNativeDriver: true }),
    ]).start();
  }, [scale]);
  const d = cellSize * 0.85;
  const ring = d + 8;
  return (
    <>
      {isWinning && winOpacity && (
        <Animated.View style={[
          styles.winRing,
          { width: ring, height: ring, borderRadius: ring / 2, opacity: winOpacity },
        ]} />
      )}
      <Animated.View style={[
        styles.stone, color === 'black' ? styles.stoneBlack : styles.stoneWhite,
        { width: d, height: d, transform: [{ scale }] },
      ]}>
        {isLast && (
          <View
            style={[styles.lastDot, {
              width: cellSize * 0.26, height: cellSize * 0.26, borderRadius: cellSize * 0.13,
              marginTop: -cellSize * 0.13, marginLeft: -cellSize * 0.13,
            }]}
            testID="gomoku-last-mark"
          />
        )}
      </Animated.View>
    </>
  );
}

// 放大镜：显示手指周边放大棋盘局部 + 十字准星
function MagnifierView({ aimX, aimY, touchX, touchY, cellSize, game, boardPixelSize, zoom }: {
  aimX: number; aimY: number; touchX: number; touchY: number;
  cellSize: number; game: GameState; boardPixelSize: number; zoom: number;
}) {
  const half = MAG_SIZE / 2;
  // 放大倍率**跟着棋盘缩放走**：1× 时就是原来的 2×（逐像素不变）；放大态下取当前
  // zoom —— 那时放大镜的价值不再是「再放大」，而是**把手指压住的那块画面挪出来**，
  // 所以与屏幕持平最合适（再往上放，130px 的窗口里就只剩半格了）。
  const mag = Math.max(MAG_ZOOM, zoom);
  // 默认浮在手指上方；靠近顶部时翻转到下方。上下左右都夹进棋盘内——
  // 只在左边夹过、上边没夹的话，手指停在盘底时放大镜会被视口裁掉半截。
  let magTop = touchY - MAG_SIZE - 50;
  if (magTop < 0) magTop = touchY + 30;
  magTop = Math.max(2, Math.min(magTop, boardPixelSize - MAG_SIZE - 2));
  let magLeft = touchX - half;
  magLeft = Math.max(2, Math.min(magLeft, boardPixelSize - MAG_SIZE - 2));

  const aimCX = (aimX + 0.5) * cellSize;
  const aimCY = (aimY + 0.5) * cellSize;
  const boardR = half / mag; // 可视范围（棋盘像素）
  const minIX = Math.max(0, Math.floor((aimCX - boardR) / cellSize));
  const maxIX = Math.min(BOARD_SIZE - 1, Math.ceil((aimCX + boardR) / cellSize));
  const minIY = Math.max(0, Math.floor((aimCY - boardR) / cellSize));
  const maxIY = Math.min(BOARD_SIZE - 1, Math.ceil((aimCY + boardR) / cellSize));
  const toMX = (bx: number) => (bx - aimCX) * mag + half;
  const toMY = (by: number) => (by - aimCY) * mag + half;

  const stoneR = cellSize * 0.85 / 2 * mag;
  const aimEmpty = game.board[aimY]?.[aimX] === 0;

  return (
    <View style={[styles.magnifier, { left: magLeft, top: magTop, width: MAG_SIZE, height: MAG_SIZE, borderRadius: half }]}>
      <View style={[StyleSheet.absoluteFill, { backgroundColor: colors.boardBg, borderRadius: half }]} />
      {/* 网格线 */}
      {Array.from({ length: maxIX - minIX + 1 }).map((_, i) => {
        const ix = minIX + i;
        const mx = toMX((ix + 0.5) * cellSize);
        return <View key={`mv-${ix}`} style={[styles.magVLine, { left: mx - 0.5 }]} />;
      })}
      {Array.from({ length: maxIY - minIY + 1 }).map((_, i) => {
        const iy = minIY + i;
        const my = toMY((iy + 0.5) * cellSize);
        return <View key={`mh-${iy}`} style={[styles.magHLine, { top: my - 0.5 }]} />;
      })}
      {/* 邻近棋子 */}
      {Array.from({ length: maxIY - minIY + 1 }).map((_, dy) =>
        Array.from({ length: maxIX - minIX + 1 }).map((_, dx) => {
          const ix = minIX + dx;
          const iy = minIY + dy;
          const val = game.board[iy]?.[ix];
          if (!val) return null;
          const c = STONE_MAP[val];
          return (
            <View key={`ms-${ix}-${iy}`} style={[
              styles.magStone,
              c === 'black' ? styles.magStoneBlack : styles.magStoneWhite,
              { left: toMX((ix + 0.5) * cellSize) - stoneR, top: toMY((iy + 0.5) * cellSize) - stoneR,
                width: stoneR * 2, height: stoneR * 2 },
            ]} />
          );
        }),
      )}
      {/* 瞄准格预览子 */}
      {aimEmpty && (
        <View style={[
          styles.magStone,
          game.your_color === 'black' ? styles.magStoneBlack : styles.magStoneWhite,
          { left: half - stoneR, top: half - stoneR, width: stoneR * 2, height: stoneR * 2, opacity: 0.45 },
        ]} />
      )}
      {/* 十字准星 */}
      <View style={[styles.magCross, { top: half - 0.5, left: 0, width: MAG_SIZE, height: 1 }]} />
      <View style={[styles.magCross, { left: half - 0.5, top: 0, width: 1, height: MAG_SIZE }]} />
      {/* 边框 */}
      <View style={[StyleSheet.absoluteFill, { borderRadius: half, borderWidth: 2, borderColor: colors.primary }]} />
    </View>
  );
}

export default function GomokuScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<GameState | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  // 断线倒计时的起点：优先后端在事件里给的 timeout，其次房间数据里的剩余值
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);
  const [showGameOver, setShowGameOver] = useState(false);
  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;

  // 拖动/放大镜状态
  const [drag, setDrag] = useState<DragState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const magnifierTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 待确认的落点（第一次点击放下预览子，第二次点击同一位置才落子）
  const [pendingMove, setPendingMove] = useState<{ x: number; y: number } | null>(null);

  // 轮次动画
  const turnPulse = useRef(new Animated.Value(1)).current;
  const turnPulseRunning = useRef(false);
  const prevIsMyTurnRef = useRef<boolean | null>(null);
  const hasMountedRef = useRef(false);

  // 胜利脉冲动画
  const winPulse = useRef(new Animated.Value(1)).current;

  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // 尺寸计算
  const boardEdge = Math.min(winWidth - 12, 560);
  const cellSize = Math.floor(boardEdge / BOARD_SIZE);
  const boardPixelSize = cellSize * BOARD_SIZE;

  // refs for PanResponder（避免闭包陈旧值）
  const cellSizeRef = useRef(cellSize);
  cellSizeRef.current = cellSize;
  const gameRef = useRef(game);
  gameRef.current = game;

  const isMyTurn = game ? game.your_color === game.next && !game.winner : false;
  const isMyTurnRef = useRef(isMyTurn);
  isMyTurnRef.current = isMyTurn;

  // ── 双指缩放（只作用于渲染层的 transform） ────────────────────
  // 棋盘内容包在一个 Animated.View 里，变换是 [{translateX},{translateY},{scale}]，
  // 且 transformOrigin 设成 'top left'。于是「视觉位置」与「棋盘坐标」的关系是
  // 最简的一维映射：
  //     v = off + zoom * p        （v 视口坐标、p 未缩放棋盘坐标、off = translate）
  // 反解：p = (v - off) / zoom。
  //
  // **落子、预览、放大镜一律用 p 计算**——缩放只作用在渲染层，视口（手势层）
  // 本身不参与 transform，所以 locationX 恒等于视口坐标。这一条是历史事故的教训：
  // 原生端 locationX 相对「被触摸的子 View」，225 个绝对定位 cell 会让每次触摸都
  // 解析成 (0,0)（点哪都下在左上角）。视口保持 pointerEvents="box-only"，子元素
  // 不是触摸目标，坐标才稳定；缩放后只需再做一次 p = (v - off) / zoom。
  const zoomAnim = useRef(new Animated.Value(1)).current;
  const panXAnim = useRef(new Animated.Value(0)).current;
  const panYAnim = useRef(new Animated.Value(0)).current;
  const zoomRef = useRef(1);
  const offRef = useRef({ x: 0, y: 0 });
  const boardPxRef = useRef(boardPixelSize);
  boardPxRef.current = boardPixelSize;
  // 捏合起点（绝对式换算：每帧都由起点重算，不做增量累加，避免夹取造成的漂移）
  const pinchRef = useRef<null | {
    dist: number; zoom: number; offX: number; offY: number; midX: number; midY: number;
  }>(null);
  // 放大态下的单指拖动起点
  const panStartRef = useRef<null | {
    offX: number; offY: number; lx: number; ly: number; moved: boolean;
  }>(null);
  const [zoomed, setZoomed] = useState(false);
  const zoomedRef = useRef(false);

  const applyZoom = useCallback((z: number, ox: number, oy: number) => {
    zoomRef.current = z;
    offRef.current = { x: ox, y: oy };
    zoomAnim.setValue(z);
    panXAnim.setValue(ox);
    panYAnim.setValue(oy);
    const nowZoomed = z > MIN_ZOOM + 1e-3;
    if (nowZoomed !== zoomedRef.current) {
      zoomedRef.current = nowZoomed;
      setZoomed(nowZoomed);
    }
  }, [zoomAnim, panXAnim, panYAnim]);
  const applyZoomRef = useRef(applyZoom);
  applyZoomRef.current = applyZoom;

  // 「恢复 1×」：缩进去必须出得来，否则放大态就是个死胡同（恢复入口见底部按钮）
  const resetZoom = useCallback(() => {
    zoomRef.current = 1;
    offRef.current = { x: 0, y: 0 };
    if (zoomedRef.current) { zoomedRef.current = false; setZoomed(false); }
    Animated.parallel([
      Animated.timing(zoomAnim, { toValue: 1, duration: 180, useNativeDriver: NATIVE_DRIVER }),
      Animated.timing(panXAnim, { toValue: 0, duration: 180, useNativeDriver: NATIVE_DRIVER }),
      Animated.timing(panYAnim, { toValue: 0, duration: 180, useNativeDriver: NATIVE_DRIVER }),
    ]).start();
  }, [zoomAnim, panXAnim, panYAnim]);

  // 视口尺寸变化（转屏/窗口缩放）→ 把偏移重新夹回「内容盖满视口」的范围
  useEffect(() => {
    const z = zoomRef.current;
    if (z <= MIN_ZOOM + 1e-3) return;
    const off = offRef.current;
    const lo = boardPixelSize * (1 - z);
    const nx = Math.min(Math.max(off.x, lo), 0);
    const ny = Math.min(Math.max(off.y, lo), 0);
    if (nx !== off.x || ny !== off.y) applyZoom(z, nx, ny);
  }, [boardPixelSize, applyZoom]);

  // 原生/顶栏返回
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

  // 清理放大镜计时器
  useEffect(() => () => {
    if (magnifierTimerRef.current) clearTimeout(magnifierTimerRef.current);
  }, []);

  // 回合切换 → 脉冲动画 + 原生端震动
  useEffect(() => {
    if (!game) return;
    if (prevIsMyTurnRef.current === false && isMyTurn) {
      if (hasMountedRef.current && Platform.OS !== 'web') Vibration.vibrate(30);
      if (!turnPulseRunning.current) {
        turnPulseRunning.current = true;
        // 只跑 TURN_PULSE_CYCLES 轮就停下（停在常亮态），不做无限循环：
        // 自动播放且超过 5 秒的运动必须能停（WCAG 2.2.2）。脉冲的用处是「把注意力
        // 拉回屏幕」，拉回来了就该停——一直闪反而变成噪声（方法论 §2 克制有度）。
        Animated.loop(Animated.sequence([
          Animated.timing(turnPulse, { toValue: 0.3, duration: 800, useNativeDriver: true }),
          Animated.timing(turnPulse, { toValue: 1, duration: 800, useNativeDriver: true }),
        ]), { iterations: TURN_PULSE_CYCLES }).start();
      }
    } else if (prevIsMyTurnRef.current === true && !isMyTurn) {
      turnPulseRunning.current = false;
      turnPulse.stopAnimation();
      turnPulse.setValue(1);
    }
    prevIsMyTurnRef.current = isMyTurn;
    hasMountedRef.current = true;
  }, [isMyTurn, game, turnPulse]);

  // 胜利五连计算（useMemo 防重算）
  const lastMove = game && game.moves.length > 0 ? game.moves[game.moves.length - 1] : null;
  const winCoords = useMemo(() => {
    if (!game || !game.winner || game.reason !== 'five') return null;
    return findWinningFive(game.board, lastMove);
  }, [game?.winner, game?.reason, game?.board, lastMove]);

  // 胜利脉冲动画
  useEffect(() => {
    if (!winCoords) { winPulse.setValue(1); return; }
    // 同样限次：金环本身常亮着做标记，脉冲只负责「刚赢的那一刻」的强调，
    // 反复闪下去既超出 WCAG 2.2.2 的 5 秒限制，也会抢走结算弹窗的注意力。
    const anim = Animated.loop(Animated.sequence([
      Animated.timing(winPulse, { toValue: 0.4, duration: 1000, useNativeDriver: true }),
      Animated.timing(winPulse, { toValue: 1, duration: 1000, useNativeDriver: true }),
    ]), { iterations: WIN_PULSE_CYCLES });
    anim.start();
    return () => anim.stop();
  }, [winCoords, winPulse]);

  const winSet = useMemo(() => {
    if (!winCoords) return new Set<string>();
    return new Set(winCoords.map(c => `${c.x},${c.y}`));
  }, [winCoords]);

  /**
   * 落点提示（只在轮到自己时算，且只算给**自己**看）：
   * - `winPoints`：我落这里立刻连五 —— 这一手直接赢；
   * - `blockPoints`：对手落这里立刻连五 —— 不堵就输。
   *
   * 新手看不出「冲四」（对手已经有四连、只差一个点），这两类点是最该被标出来的。
   * 判据是**纯局部**的（落一子扫四方向），与「活三」那种要评估后续的形状不同，
   * 所以本地算不会与服务端漂移；胜负本身仍由服务端裁决（同重力四子棋的提示标）。
   * 同一个点若两边都能连五，按「自己赢优先」只标赢点。
   */
  const { winPoints, blockPoints } = useMemo(() => {
    const empty = { winPoints: [] as Coord[], blockPoints: [] as Coord[] };
    if (!game || game.winner || !isMyTurn) return empty;
    const mine = game.your_color === 'black' ? 1 : 2;
    const theirs = mine === 1 ? 2 : 1;
    const win = winningPoints(game.board, mine);
    const taken = new Set(win.map((p) => `${p.x},${p.y}`));
    return {
      winPoints: win,
      blockPoints: winningPoints(game.board, theirs).filter((p) => !taken.has(`${p.x},${p.y}`)),
    };
  }, [game, isMyTurn]);

  // 落子弹入：追踪 moves 增长，仅新增的最新子播放动画
  const prevMovesLenRef = useRef(-1);
  const [animKey, setAnimKey] = useState(0);
  useEffect(() => {
    if (!game) return;
    const len = game.moves.length;
    if (prevMovesLenRef.current >= 0 && len > prevMovesLenRef.current) {
      setAnimKey(k => k + 1);
    }
    prevMovesLenRef.current = len;
  }, [game?.moves.length]);

  // 回合切换时取消拖动
  useEffect(() => {
    if (drag && !isMyTurn) {
      if (magnifierTimerRef.current) { clearTimeout(magnifierTimerRef.current); magnifierTimerRef.current = null; }
      dragRef.current = null;
      setDrag(null);
    }
  }, [isMyTurn, drag]);

  // WS 连接与消息处理
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
        const g = msg.game as GameState;
        setGame(g);
        if (g.winner) setShowGameOver(true);
      }
      if (roomData) {
        const opponent = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };
    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        const opponent = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };
    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as GameState;
      if (g) { setGame(g); setShowGameOver(false); gameOverShownRef.current = false; }
    };
    const handleMove = (msg: SocketMessage) => {
      setGame((prev) => {
        if (!prev) return prev;
        const x = msg.x as number;
        const y = msg.y as number;
        const color = msg.color as string;
        const next = msg.next as 'black' | 'white';
        const newBoard = prev.board.map((row) => [...row]);
        newBoard[y][x] = color === 'black' ? 1 : 2;
        return { ...prev, board: newBoard, moves: [...prev.moves, { x, y, color }], next };
      });
    };
    const handleGameOver = (msg: SocketMessage) => {
      const winner = msg.winner as string;
      const reason = msg.reason as string;
      setGame((prev) => prev ? { ...prev, winner: winner as GameState['winner'], reason: reason as GameState['reason'] } : prev);
      if (!gameOverShownRef.current) { gameOverShownRef.current = true; setShowGameOver(true); }
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
    const handleError = (msg: SocketMessage) => { notify('提示', msg.message as string); };

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

  // 落子二次确认：第一次点只把半透明预览子放上去，**再点同一位置**才真的落子。
  //
  // 为什么加：五子棋格宽只有 25px 左右（远低于 44pt），一次误触就直接落子、
  // 无法撤销，而五子棋一步失误往往就输了。预览子让「点在哪儿」先变得可见，
  // 第二下才是承诺——这是本项目对小棋盘触控目标不足最直接的一层补偿。
  // 三条输入路径（轻点 / 长按放大镜后拖动 / 放大态轻点）都汇聚到本函数，
  // 所以只需要在这里拦一道。
  const handleCellPress = useCallback((x: number, y: number) => {
    if (!game || !user) return;
    if (game.winner) return;
    // 点到已有棋子 = 撤掉预览（当作「算了」）
    if (game.board[y][x] !== 0) {
      setPendingMove(null);
      return;
    }
    if (game.next !== game.your_color) return;
    if (pendingMove && pendingMove.x === x && pendingMove.y === y) {
      setPendingMove(null);
      if (Platform.OS !== 'web') Vibration.vibrate(10);
      socketRef.current?.move(x, y);
      return;
    }
    setPendingMove({ x, y });
    if (Platform.OS !== 'web') Vibration.vibrate(8);
  }, [game, user, pendingMove]);

  const handleCellPressRef = useRef(handleCellPress);
  handleCellPressRef.current = handleCellPress;

  // PanResponder：单一手势层——单指「轻点落子 / 长按放大镜」，双指「捏合缩放」
  // （放大态下单指拖动 = 平移查看，轻点仍落子）。
  // 为什么两条路径都在 start 就接管：单指的轻点必须拿到 grant 才能落子，
  // 而触摸点的数量只有在事件里才知道（第二个手指落下时不会重走 should-set 协商），
  // 所以单指/双指的分流放在 grant/move/start 回调里按 touches.length 判断。
  const panResponder = useMemo(() => {
    // 视口坐标 → 未缩放棋盘坐标：v = off + zoom * p  ⇒  p = (v - off) / zoom
    const toBoard = (vx: number, vy: number) => {
      const z = zoomRef.current;
      const off = offRef.current;
      return { x: (vx - off.x) / z, y: (vy - off.y) / z };
    };
    // 内容必须始终盖满视口：内容尺寸 = boardPixelSize*zoom ≥ 视口，故 off ∈ [S(1-z), 0]
    const clampOff = (v: number, z: number) => {
      const lo = boardPxRef.current * (1 - z);
      return Math.min(Math.max(v, lo), 0);
    };
    const clearDrag = () => {
      if (magnifierTimerRef.current) { clearTimeout(magnifierTimerRef.current); magnifierTimerRef.current = null; }
      dragRef.current = null;
      setDrag(null);
    };
    // 手指移动 → 挪准星（放大镜与预览子都跟着走）。`lx/ly` 是视口坐标。
    const updateAim = (lx: number, ly: number) => {
      const cur = dragRef.current;
      if (!cur) return;
      const pt = toBoard(lx, ly);
      const ix = computeIntersection(pt.x, pt.y, cellSizeRef.current);
      const next: DragState = {
        ...cur,
        touchX: lx,
        touchY: ly,
        aimX: ix ? ix.x : cur.aimX,
        aimY: ix ? ix.y : cur.aimY,
      };
      dragRef.current = next;
      setDrag(next);
    };
    // 长按不动 → 弹出放大镜（1× 与放大态共用同一条路径）。
    // 放大态下它与「单指拖动平移」抢同一根手指，所以由**定时器**来分派：
    // 手指先动 → `updatePan` 撤销这次精瞄、照常平移；定时器先到 → 把 `panStartRef`
    // 置空，这次手势整个让给精瞄（后续移动改为挪准星，不再平移）。
    const armMagnifier = (lx: number, ly: number) => {
      if (!isMyTurnRef.current || gameRef.current?.winner) return;
      const pt = toBoard(lx, ly);
      const ix = computeIntersection(pt.x, pt.y, cellSizeRef.current);
      if (!ix || gameRef.current?.board[ix.y][ix.x] !== 0) return;
      const ds: DragState = {
        aimX: ix.x, aimY: ix.y, touchX: lx, touchY: ly, magnifierVisible: false,
      };
      dragRef.current = ds;
      setDrag(ds);
      magnifierTimerRef.current = setTimeout(() => {
        panStartRef.current = null;
        setDrag(prev => {
          if (!prev) return null;
          const next = { ...prev, magnifierVisible: true };
          dragRef.current = next;
          return next;
        });
      }, MAG_DELAY);
    };
    // 双指：记录捏合起点（两指中点 + 距离 + 当时的 zoom/off）
    const beginPinch = (e: GestureResponderEvent) => {
      const touches = e.nativeEvent.touches || [];
      if (touches.length < 2) return;
      clearDrag();
      const a = touches[0];
      const b = touches[1];
      const ax = a.locationX ?? e.nativeEvent.locationX ?? 0;
      const ay = a.locationY ?? e.nativeEvent.locationY ?? 0;
      const bx = b.locationX ?? e.nativeEvent.locationX ?? 0;
      const by = b.locationY ?? e.nativeEvent.locationY ?? 0;
      pinchRef.current = {
        dist: Math.max(1, Math.hypot(bx - ax, by - ay)),
        zoom: zoomRef.current,
        offX: offRef.current.x,
        offY: offRef.current.y,
        midX: (ax + bx) / 2,
        midY: (ay + by) / 2,
      };
    };
    const updatePinch = (e: GestureResponderEvent) => {
      const touches = e.nativeEvent.touches || [];
      if (touches.length < 2) return;
      if (!pinchRef.current) { beginPinch(e); return; }
      const p = pinchRef.current;
      const a = touches[0];
      const b = touches[1];
      const ax = a.locationX ?? e.nativeEvent.locationX ?? 0;
      const ay = a.locationY ?? e.nativeEvent.locationY ?? 0;
      const bx = b.locationX ?? e.nativeEvent.locationX ?? 0;
      const by = b.locationY ?? e.nativeEvent.locationY ?? 0;
      const dist = Math.hypot(bx - ax, by - ay);
      const midX = (ax + bx) / 2;
      const midY = (ay + by) / 2;
      const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, p.zoom * (dist / p.dist)));
      // 捏合开始时中点下的那颗棋盘坐标，缩放后仍要停在同一位置 → 缩放围绕手指
      const px = (p.midX - p.offX) / p.zoom;
      const py = (p.midY - p.offY) / p.zoom;
      applyZoomRef.current(
        z,
        clampOff(midX - px * z, z),
        clampOff(midY - py * z, z),
      );
    };
    // 放大态下的单指平移（拖动超过 PAN_SLOP 才生效，轻点留给落子）
    const updatePan = (lx: number, ly: number) => {
      const s = panStartRef.current;
      if (!s) return;
      const dx = lx - s.lx;
      const dy = ly - s.ly;
      if (!s.moved) {
        if (Math.abs(dx) < PAN_SLOP && Math.abs(dy) < PAN_SLOP) return;
        // 手指先动了 → 这次是平移，撤掉还没弹出来的放大镜（定时器可能已挂上）
        clearDrag();
        s.moved = true;
      }
      const z = zoomRef.current;
      applyZoomRef.current(z, clampOff(s.offX + dx, z), clampOff(s.offY + dy, z));
    };

    return PanResponder.create({
      // 单指与双指都要接管：单指轻点需要 grant；双指在 grant/start/move 里按
      // touches.length 切到缩放态（见上）。
      onStartShouldSetPanResponder: (e) => (e.nativeEvent.touches?.length ?? 1) >= 1,
      onMoveShouldSetPanResponder: (e) => (e.nativeEvent.touches?.length ?? 1) >= 1,
      // 第二个手指按下时立刻进入缩放态，不必等第一次移动
      onPanResponderStart: (e, gs) => {
        if (gs.numberActiveTouches >= 2) beginPinch(e);
      },
      onPanResponderGrant: (e) => {
        if ((e.nativeEvent.touches?.length ?? 1) >= 2) { beginPinch(e); return; }
        const lx = e.nativeEvent.locationX ?? 0;
        const ly = e.nativeEvent.locationY ?? 0;
        if (zoomRef.current > MIN_ZOOM + 1e-3) {
          // 放大态：单指**先动**=平移、**先按住**=弹出放大镜精瞄、轻点=落子。
          // 三条路共用一个 grant，由「有没有动 / 定时器到没到」分派（见 armMagnifier）。
          panStartRef.current = { offX: offRef.current.x, offY: offRef.current.y, lx, ly, moved: false };
          armMagnifier(lx, ly);
          return;
        }
        armMagnifier(lx, ly);
      },
      onPanResponderMove: (e) => {
        const touches = e.nativeEvent.touches || [];
        if (touches.length >= 2) { updatePinch(e); return; }
        // 双指抬起一根后残留的单指移动：忽略，等全部抬起（避免缩放中途落子）
        if (pinchRef.current) return;
        const lx = e.nativeEvent.locationX ?? 0;
        const ly = e.nativeEvent.locationY ?? 0;
        // 放大镜已弹出（放大态下它会把 panStartRef 置空）→ 这一手势是精瞄，不是平移
        if (dragRef.current) { updateAim(lx, ly); return; }
        if (panStartRef.current) { updatePan(lx, ly); return; }
      },
      onPanResponderRelease: (e) => {
        pinchRef.current = null;
        const pan = panStartRef.current;
        panStartRef.current = null;
        if (pan) {
          // 拖动过 → 只当平移查看，不落子
          if (pan.moved) return;
          const pt = toBoard(e.nativeEvent.locationX ?? 0, e.nativeEvent.locationY ?? 0);
          const ix = computeIntersection(pt.x, pt.y, cellSizeRef.current);
          const g = gameRef.current;
          if (ix && g && isMyTurnRef.current && !g.winner && g.board[ix.y][ix.x] === 0) {
            handleCellPressRef.current(ix.x, ix.y);
          }
          return;
        }
        if (magnifierTimerRef.current) { clearTimeout(magnifierTimerRef.current); magnifierTimerRef.current = null; }
        const captured = dragRef.current;
        dragRef.current = null;
        setDrag(null);
        if (captured && isMyTurnRef.current && !gameRef.current?.winner) {
          const g = gameRef.current;
          if (g && g.board[captured.aimY][captured.aimX] === 0) {
            handleCellPressRef.current(captured.aimX, captured.aimY);
          }
        }
      },
      onPanResponderTerminate: () => {
        pinchRef.current = null;
        panStartRef.current = null;
        if (magnifierTimerRef.current) { clearTimeout(magnifierTimerRef.current); magnifierTimerRef.current = null; }
        dragRef.current = null;
        setDrag(null);
      },
    });
  }, []);

  const handleSurrender = () => { setConfirm('surrender'); };
  const doSurrender = () => { setConfirm(null); socketRef.current?.surrender(); };

  const handleLeaveRoom = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); } catch { /* WS leave already sent */ }
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

  // 预览只在「轮到自己 + 局面没变」时有效：回合交接、任何一方落子、终局都清掉，
  // 否则上一手的预览子会留在盘上，看起来像多了一颗棋
  useEffect(() => {
    setPendingMove(null);
  }, [game?.moves.length, game?.winner, game?.next]);

  const turnText = (() => {
    if (!game || game.winner) return '';
    if (isMyTurn) return '轮到你落子';
    // 人机回合有 0.8-1.2 秒的思考延迟（服务端故意留的节奏）。只写「等待对方…」
    // 用户既不知道在等谁、也不知道要等多久，会以为卡住了——方法论 §1 响应时间
    // / §5 状态可见性。真人对手维持原文案。
    const opp = room?.players?.find((p) => p.id !== user?.id);
    if (opp?.is_bot) return `${opp.nickname || opp.username} 思考中…`;
    return '等待对方...';
  })();

  const myColorLabel = game ? (game.your_color === 'black' ? '黑棋' : '白棋') : '';
  const myColorIconColor = game ? (game.your_color === 'black' ? colors.stoneBlack : colors.stoneEdge) : colors.textFaint;

  // 预览（非放大镜模式下也显示半透明子）
  // 预览子：拖动中跟随放大镜准星，松手后停在待确认的位置上等第二次点击。
  const previewCoord = drag ? { x: drag.aimX, y: drag.aimY } : pendingMove;

  return (
    <View style={styles.container}>
      {/* 顶栏 */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          {game && <Ionicons name="ellipse" size={16} color={myColorIconColor} />}
          <Text style={styles.myColor}>{myColorLabel}</Text>
          <Animated.Text style={[styles.turnHint, isMyTurn && styles.turnActive, { opacity: isMyTurn ? turnPulse : 1 }]}>
            {turnText}
          </Animated.Text>
        </View>
        <View style={styles.topRight}>
          <GameHelpButton game="gomoku" size={24} />
          <Text style={styles.roomCode}>#{code}</Text>
        </View>
      </View>

      {opponentDisconnected && (
        <View style={styles.banner}>
          <View style={styles.bannerRow}>
            <Ionicons name="warning" size={14} color={colors.white} />
            <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
          </View>
        </View>
      )}

      {/* 棋盘（单一 PanResponder 手势层） */}
      {/* 视口（手势层）尺寸固定、不参与 transform：locationX/locationY 恒为视口坐标，
          再由 p = (v - off) / zoom 换算成棋盘坐标（见组件顶部的换算说明）。
          历史事故：原生端 locationX 相对「被触摸的子 View」，棋盘里 225 个绝对定位
          cell 会让每次触摸的局部坐标都落在 [0,cellSize) → 恒定解析成 (0,0)（点哪都下
          在左上角）。所以视口必须 box-only：子 View 不再是触摸目标，坐标才稳定。
          Web 端 RNW 本就用 currentTarget 计算 locationX，且该值不是合法 CSS，行为不变。 */}
      <View style={[styles.boardArea, { width: boardPixelSize, height: boardPixelSize }]}>
          <View
            {...panResponder.panHandlers}
            pointerEvents="box-only"
            style={[styles.boardWrap, { width: boardPixelSize, height: boardPixelSize }]}
          >
          {/* 内容层：双指缩放/平移只改这里的 transform（渲染层）。
              transformOrigin 设成左上角 → 视觉位置 = off + zoom*棋盘坐标，换算最简。 */}
          <Animated.View
            style={[
              styles.boardContent,
              {
                width: boardPixelSize, height: boardPixelSize,
                transform: [
                  { translateX: panXAnim },
                  { translateY: panYAnim },
                  { scale: zoomAnim },
                ],
                transformOrigin: 'top left',
              },
            ]}
          >
            {/* 网格线 */}
            {Array.from({ length: BOARD_SIZE }).map((_, i) => (
              <React.Fragment key={`lines-${i}`}>
                <View style={[styles.hLine, { top: cellSize * (i + 0.5), left: cellSize * 0.5, width: cellSize * (BOARD_SIZE - 1) }]} />
                <View style={[styles.vLine, { left: cellSize * (i + 0.5), top: cellSize * 0.5, height: cellSize * (BOARD_SIZE - 1) }]} />
              </React.Fragment>
            ))}
            {/* 星位 */}
            {[3, 7, 11].flatMap((r) =>
              [3, 7, 11].map((c) => (
                <View key={`star-${r}-${c}`} style={[styles.starDot, { left: cellSize * (c + 0.5) - 3, top: cellSize * (r + 0.5) - 3 }]} />
              ))
            )}
            {/* E2E 可发现性：225 个带 tabindex 的 cell（无手势处理，仅供测试定位） */}
            {Array.from({ length: BOARD_SIZE }).map((_, y) =>
              Array.from({ length: BOARD_SIZE }).map((_, x) => (
                <View
                  key={`cell-${x}-${y}`}
                  tabIndex={0}
                  style={[styles.cell, { left: cellSize * x, top: cellSize * y, width: cellSize, height: cellSize }]}
                />
              ))
            )}
            {/* 落点提示（渲染在棋子下方，空点上不会有棋子压住它）：
                实心点 = 这一手直接连五，空心环 = 不堵就输。
                两者都靠深墨描边成形 —— 木色底上绿 1.78、琥珀 0.87 都不到 3:1。 */}
            {game && winPoints.map((p) => {
              const d = cellSize * 0.54;
              return (
                <View
                  key={`hint-w-${p.x}-${p.y}`}
                  testID={`gomoku-hint-win-${p.x}-${p.y}`}
                  style={[styles.hintWinDot, {
                    left: cellSize * (p.x + 0.5) - d / 2,
                    top: cellSize * (p.y + 0.5) - d / 2,
                    width: d, height: d, borderRadius: d / 2,
                  }]}
                />
              );
            })}
            {game && blockPoints.map((p) => {
              const d = cellSize * 0.54;
              const inner = d - 5;
              return (
                <View
                  key={`hint-b-${p.x}-${p.y}`}
                  testID={`gomoku-hint-block-${p.x}-${p.y}`}
                  style={[styles.hintRingOuter, {
                    left: cellSize * (p.x + 0.5) - d / 2,
                    top: cellSize * (p.y + 0.5) - d / 2,
                    width: d, height: d, borderRadius: d / 2,
                  }]}
                >
                  <View style={[styles.hintRingInner, {
                    width: inner, height: inner, borderRadius: inner / 2,
                  }]} />
                </View>
              );
            })}
            {/* 胜利光环（渲染在棋子下方） */}
            {game && winCoords && Array.from(winSet).map((key) => {
              const [cx, cy] = key.split(',').map(Number);
              const ringSize = cellSize * 0.85 + 8;
              return (
                <Animated.View
                  key={`wr-${key}`}
                  style={[
                    styles.winRing,
                    {
                      left: cellSize * (cx + 0.5) - ringSize / 2,
                      top: cellSize * (cy + 0.5) - ringSize / 2,
                      width: ringSize, height: ringSize, borderRadius: ringSize / 2,
                      opacity: winPulse,
                    },
                  ]}
                />
              );
            })}
            {/* 棋子 */}
            {game && game.board.map((row, y) =>
              row.map((val, x) => {
                if (!val) return null;
                const color = STONE_MAP[val];
                if (!color) return null;
                const isLast = lastMove && lastMove.x === x && lastMove.y === y;
                const isWin = winSet.has(`${x},${y}`);
                const isLatest = isLast && animKey > 0;
                const d = cellSize * 0.85;
                const sx = cellSize * (x + 0.5) - d / 2;
                const sy = cellSize * (y + 0.5) - d / 2;
                if (isLatest) {
                  return (
                    <View
                      key={`stone-${x}-${y}`}
                      testID={`stone-${x}-${y}`}
                      style={[styles.stoneAnchor, { left: sx, top: sy, width: d, height: d }]}
                    >
                      <PopInStone
                        key={`popin-${animKey}`}
                        color={color} cellSize={cellSize} isLast={!!isLast}
                        isWinning={isWin} winOpacity={isWin ? winPulse : null}
                      />
                    </View>
                  );
                }
                const ring = d + 8;
                return (
                  <React.Fragment key={`stone-${x}-${y}`}>
                    {isWin && (
                      <Animated.View style={[
                        styles.winRing,
                        { left: cellSize * (x + 0.5) - ring / 2, top: cellSize * (y + 0.5) - ring / 2,
                          width: ring, height: ring, borderRadius: ring / 2, opacity: winPulse },
                      ]} />
                    )}
                    <View
                      testID={`stone-${x}-${y}`}
                      style={[
                        styles.stone, color === 'black' ? styles.stoneBlack : styles.stoneWhite,
                        { position: 'absolute', left: sx, top: sy, width: d, height: d },
                      ]}
                    >
                      {isLast && (
          <View
            style={[styles.lastDot, {
              width: cellSize * 0.26, height: cellSize * 0.26, borderRadius: cellSize * 0.13,
              marginTop: -cellSize * 0.13, marginLeft: -cellSize * 0.13,
            }]}
            testID="gomoku-last-mark"
          />
        )}
                    </View>
                  </React.Fragment>
                );
              })
            )}
            {/* 预览子（半透明） */}
            {game && previewCoord && !game.winner && game.board[previewCoord.y]?.[previewCoord.x] === 0 && (
              <View style={[
                styles.stone,
                game.your_color === 'black' ? styles.stoneBlack : styles.stoneWhite,
                {
                  position: 'absolute',
                  left: cellSize * (previewCoord.x + 0.5) - cellSize * 0.85 / 2,
                  top: cellSize * (previewCoord.y + 0.5) - cellSize * 0.85 / 2,
                  width: cellSize * 0.85, height: cellSize * 0.85, opacity: 0.4,
                },
              ]} />
            )}
          </Animated.View>
          {/* 放大镜画在**视口层**（`boardWrap` 之内、内容层之外）：它要浮在手指
              上方，位置是视口坐标，而且必须**不跟 scale/translate 走** —— 放进内容层
              的话会被一起缩放平移，飘到棋盘外面去（旧版就是因此才在放大态下禁用它）。 */}
          {game && drag?.magnifierVisible && (
            <MagnifierView
              aimX={drag.aimX} aimY={drag.aimY}
              touchX={drag.touchX} touchY={drag.touchY}
              cellSize={cellSize} game={game} boardPixelSize={boardPixelSize}
              zoom={zoomRef.current}
            />
          )}
        </View>
        {/* 放大态的复位入口：缩进去必须出得来。只在缩放时出现，不占常态布局 */}
        {zoomed && (
          <TouchableOpacity
            testID="gomoku-zoom-reset"
            style={styles.zoomReset}
            onPress={resetZoom}
            activeOpacity={0.8}
          >
            <Ionicons name="contract-outline" size={14} color={colors.primary} />
            <Text style={styles.zoomResetText}>复位 1×</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* 提示标图例：形状之外再给一行文字，颜色不单独承载信息（WCAG 1.4.1） */}
      {(winPoints.length > 0 || blockPoints.length > 0) && (
        <View style={styles.hintLegend} testID="gomoku-hint-legend">
          {winPoints.length > 0 && (
            <View style={styles.hintLegendItem}>
              <View style={styles.hintLegendDot} />
              <Text style={styles.hintLegendText}>落这里直接连五</Text>
            </View>
          )}
          {blockPoints.length > 0 && (
            <View style={styles.hintLegendItem}>
              <View style={styles.hintLegendRing}>
                <View style={styles.hintLegendRingInner} />
              </View>
              <Text style={styles.hintLegendText}>对手落这里就连五，要堵</Text>
            </View>
          )}
        </View>
      )}

      {/* 待确认提示：半透明预览子本身不解释「接下来要干嘛」，必须配一句文案，
          否则用户会以为已经落子了（手势/状态不可见是移动端最常见的坑） */}
      {pendingMove && !game?.winner && (
        <View style={styles.pendingBar} testID="gomoku-pending-hint">
          <Ionicons name="hand-left-outline" size={14} color={colors.primary} />
          <Text style={styles.pendingText}> 再点一次同一位置确认落子（点别处可改位置）</Text>
        </View>
      )}

      {/* 底部操作栏 */}
      <View style={styles.bottomBar}>
        {game && !game.winner && (
          <TouchableOpacity style={styles.surrenderBtn} onPress={handleSurrender}>
            <Text style={styles.surrenderBtnText}>认输</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirm('leave')}>
          <Text style={styles.leaveBtnText}>退出</Text>
        </TouchableOpacity>
      </View>

      {/* 确认弹窗 */}
      <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons name={confirm === 'surrender' ? 'flag-outline' : 'exit-outline'} size={36} color={colors.danger} />
            <Text style={styles.modalTitle}>{confirm === 'surrender' ? '确认认输？' : '确认退出？'}</Text>
            <Text style={styles.modalReason}>
              {confirm === 'surrender' ? '认输后本局判负，无法撤销' : '对局中退出将判负并离开房间'}
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, { backgroundColor: colors.danger }]}
              onPress={() => { if (confirm === 'surrender') doSurrender(); else { setConfirm(null); handleLeaveRoom(); } }}
            >
              <Text style={styles.modalBtnPrimaryText}>{confirm === 'surrender' ? '确认认输' : '确认退出'}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.modalBtn, styles.modalBtnSecondary]} onPress={() => setConfirm(null)}>
              <Text style={styles.modalBtnSecondaryText}>再想想</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 结算弹窗 */}
      <Modal visible={showGameOver} transparent animationType="fade" onRequestClose={handleBackToRoom}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>对局结束</Text>
            {game && (
              <>
                <Text style={styles.modalResult}>{WINNER_LABELS[game.winner || ''] || game.winner}</Text>
                {game.reason && <Text style={styles.modalReason}>{REASON_LABELS[game.reason] || game.reason}</Text>}
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

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, alignItems: 'center' },
  topBar: {
    width: '100%', flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: spacing.md, backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  topRight: {},
  myColor: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  turnHint: { fontSize: fontSize.sm, color: colors.textSecondary },
  turnActive: { color: colors.primary, fontWeight: '600' },
  roomCode: { fontSize: fontSize.sm, color: colors.textSecondary, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' },
  banner: { width: '100%', backgroundColor: colors.warning, paddingVertical: spacing.sm, alignItems: 'center' },
  bannerRow: { flexDirection: 'row', alignItems: 'center' },
  bannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' },
  // 棋盘外层：阴影/圆角，同时是复位按钮的定位参照（复位按钮在视口之上、不在手势层里）
  boardArea: {
    position: 'relative', marginTop: spacing.md,
    borderRadius: 12, shadowColor: colors.black, shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18, shadowRadius: 6, elevation: 3,
  },
  // 手势视口：尺寸固定、不参与 transform；overflow:hidden 让缩放后的内容不溢出到
  // 顶栏/按钮上。Web 端必须 touch-action:none，否则捏合会被浏览器当成页面缩放抢走。
  boardWrap: {
    position: 'relative', backgroundColor: colors.boardBg,
    borderRadius: 12, overflow: 'hidden',
    ...(Platform.OS === 'web' ? ({ touchAction: 'none' } as any) : null),
  },
  // 内容层：唯一参与 transform 的一层（双指缩放 + 平移）
  boardContent: { position: 'relative' },
  // 「复位 1×」：放大态才出现，浮在棋盘右下角。次要控件，约 40px 高（>24 的 AA 底线）。
  // 边框用 primary（vs 木色棋盘 3.39:1、vs 白底 6.29:1）——borderStrong 压在木色上只有
  // 1.73:1，白底对木色也只有 1.70:1，撑不起控件边界（WCAG 1.4.11 要 3:1）。
  zoomReset: {
    position: 'absolute', right: spacing.sm, bottom: spacing.sm,
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingVertical: spacing.sm + 4, paddingHorizontal: spacing.md,
    borderRadius: radius.full, backgroundColor: colors.card,
    borderWidth: 2, borderColor: colors.primary,
  },
  zoomResetText: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  hLine: { position: 'absolute', height: 1, backgroundColor: colors.boardLine },
  vLine: { position: 'absolute', width: 1, backgroundColor: colors.boardLine },
  starDot: { position: 'absolute', width: 6, height: 6, borderRadius: 3, backgroundColor: colors.boardLine },
  // 落点提示：实心点（能赢）与空心环（要堵）。两者都带深墨描边 ——
  // 木色底上绿 1.78、琥珀 0.87，自己都到不了 3:1（见 theme.ts）。
  hintWinDot: {
    position: 'absolute',
    backgroundColor: gomokuHintWin,
    borderWidth: 2,
    borderColor: colors.stoneEdge,
  },
  hintRingOuter: {
    position: 'absolute', alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: colors.stoneEdge,
  },
  hintRingInner: { borderWidth: 2, borderColor: gomokuHintBlock },
  hintLegend: {
    flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md,
    justifyContent: 'center', marginTop: spacing.xs, paddingHorizontal: spacing.md,
  },
  hintLegendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  hintLegendDot: {
    width: 12, height: 12, borderRadius: 6,
    backgroundColor: gomokuHintWin, borderWidth: 1.5, borderColor: colors.stoneEdge,
  },
  hintLegendRing: {
    width: 14, height: 14, borderRadius: 7,
    borderWidth: 2, borderColor: colors.stoneEdge,
    alignItems: 'center', justifyContent: 'center',
  },
  hintLegendRingInner: {
    width: 8, height: 8, borderRadius: 4,
    borderWidth: 2, borderColor: gomokuHintBlock,
  },
  hintLegendText: { fontSize: fontSize.xs, color: colors.textSecondary },
  cell: { position: 'absolute' },
  stone: { borderRadius: 9999 },
  stoneAnchor: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  stoneBlack: {
    backgroundColor: colors.stoneBlack, shadowColor: colors.black,
    shadowOffset: { width: 1, height: 1 }, shadowOpacity: 0.4, shadowRadius: 2, elevation: 3,
  },
  stoneWhite: {
    backgroundColor: colors.stoneWhite, borderWidth: 1, borderColor: colors.stoneEdge,
    shadowColor: colors.black, shadowOffset: { width: 1, height: 1 }, shadowOpacity: 0.2, shadowRadius: 2, elevation: 2,
  },
  winRing: { position: 'absolute', borderWidth: 3, borderColor: WIN_RING },
  // 最近一步标记：黄点 + 深墨描边（与翻转棋/点格棋/象棋共用一套，见 theme.ts）。
  // 原来只画一个 6px 纯色点（黑子上白点、白子上 #333），既小又和「五连金环」
  // 之类的装饰混在一起，用户看不出哪一手是刚下的。
  lastDot: {
    position: 'absolute', top: '50%', left: '50%',
    backgroundColor: lastMoveMark,
    borderWidth: 1.5, borderColor: lastMoveMarkEdge,
  },
  bottomBar: { flexDirection: 'row', gap: spacing.md, padding: spacing.md },
  // 待确认提示条（预览子已放下、等第二次点击）
  pendingBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.md,
  },
  pendingText: { color: colors.primary, fontSize: fontSize.xs, fontWeight: '600' },
  surrenderBtn: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6, backgroundColor: colors.danger, borderRadius: radius.md },
  surrenderBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  leaveBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  // 放大镜
  magnifier: {
    position: 'absolute', overflow: 'hidden',
    shadowColor: colors.black, shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.3, shadowRadius: 4, elevation: 5,
  },
  magVLine: { position: 'absolute', width: 1, top: 0, height: MAG_SIZE, backgroundColor: colors.boardLine },
  magHLine: { position: 'absolute', height: 1, left: 0, width: MAG_SIZE, backgroundColor: colors.boardLine },
  magStone: { position: 'absolute', borderRadius: 9999 },
  magStoneBlack: { backgroundColor: colors.stoneBlack },
  magStoneWhite: { backgroundColor: colors.stoneWhite, borderWidth: 1, borderColor: colors.stoneEdge },
  magCross: { position: 'absolute', backgroundColor: 'rgba(79,70,229,0.6)' },
  // Modal
  modalOverlay: { flex: 1, backgroundColor: colors.overlay, justifyContent: 'center', alignItems: 'center', padding: spacing.md },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card, borderRadius: radius.xl, padding: spacing.lg, alignItems: 'center',
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.md },
  modalResult: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.primary, marginBottom: spacing.xs },
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  modalBtn: { width: '100%', borderRadius: radius.md, paddingVertical: spacing.sm + 4, alignItems: 'center', marginTop: spacing.sm },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
