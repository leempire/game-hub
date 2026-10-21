import React, {
  useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo, useReducer,
} from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, PanResponder, GestureResponderEvent, PanResponderGestureState, Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, fetchMyStats, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import { colors, spacing, radius, fontSize, g2048Tile, playerColors, withAlpha } from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { GameHelpButton } from '../help';
import { useWebKeys } from '../useWebKeys';

interface LeaderRow {
  user_id: number;
  color: string;
  score: number;
  max_tile: number;
  finished: boolean;
  finish_reason: null | string;
}

interface RankRow {
  seat: number;
  user_id: number;
  username: string;
  nickname?: string | null;
  color: string;
  score: number;
  max_tile: number;
  rank: number;
}

interface GameState {
  board: number[][];
  score: number;
  max_tile: number;
  game_over: boolean;
  finish_reason?: null | string;
  leaderboard?: LeaderRow[];
  player_count?: number;
  ranking?: RankRow[];
  winner: null | string;
  reason: null | string;
  seed: number;
  move_count: number;
  /** 单人局且撤销栈非空时为 true（多人局服务端恒为 false） */
  undo_available?: boolean;
  your_color?: null | string;
}

/** 服务端 move_with_trace 下发的一条走子轨迹（原盘坐标）。 */
interface TraceEntry {
  from: [number, number];
  to: [number, number];
  value: number;
  merged: boolean;
}

/** 一块有身份的方块。动画靠它，而不是靠盘面格子——见 TileView 注释。 */
interface Tile {
  id: number;
  r: number;
  c: number;
  value: number;
  /** 合并到位后要变成的值（滑行阶段仍显示自身旧值） */
  pendingValue?: number;
  /** 合并后被吃掉的那块：滑到目标格后消失 */
  dying?: boolean;
  isNew?: boolean;
  merged?: boolean;
}

function colorHex(color?: string | null): string {
  return playerColors[color || ''] || colors.textMuted;
}

interface Props {
  code: string;
  onLeave: () => void;
}

// Classic 2048 tile colors

/** 胜利线：合成到这个数才叫「赢」，其余一律只是「游戏结束」（与服务端 WIN_TILE 一致）。 */
const WIN_TILE = 2048;

const REASON_LABELS: Record<string, string> = {
  reach_2048: '合成 2048',
  both_stuck: '双方无法移动',
  surrender: '认输',
  disconnect: '断线判负',
};

const FINISH_LABELS: Record<string, string> = {
  reach_2048: '已合成 2048',
  stuck: '无路可走',
  surrender: '已认输',
};

const BOARD_N = 4;
const PAD = 5;          // 棋盘内边距
const GAP = 6;          // 格子间隙
const MIN_BOARD = 168;  // 格宽 ~35px 是 4 位数可读的下限
const SLIDE_MS = 110;   // 滑行时长
const POP_MS = 100;     // 合并/新块弹跳的单程时长
// 原生端用原生驱动；RNW 不支持，必须降级（项目既有写法）
const NATIVE_DRIVER = Platform.OS !== 'web';

let _tileId = 0;
function nextTileId(): number {
  _tileId += 1;
  return _tileId;
}

function emptyGrid(): number[][] {
  return Array.from({ length: BOARD_N }, () => new Array(BOARD_N).fill(0));
}

function maxTileOf(board: number[][]): number {
  let max = 0;
  for (const row of board) for (const v of row) if (v > max) max = v;
  return max;
}

/** 从权威盘面重建棋子（用于 sync / 对账失败时的兜底）。 */
function tilesFromBoard(board: number[][]): Tile[] {
  const out: Tile[] = [];
  for (let r = 0; r < BOARD_N; r++) {
    for (let c = 0; c < BOARD_N; c++) {
      if (board[r][c] > 0) out.push({ id: nextTileId(), r, c, value: board[r][c] });
    }
  }
  return out;
}

interface Advance {
  /** 第一阶段：滑行中的棋子（合并双方都还在，各自保持旧值） */
  sliding: Tile[];
  /** 第二阶段：滑行结束后提交（合并生效、吃掉消失、新块弹入） */
  settle: Tile[];
}

/**
 * 把一条走子轨迹应用到当前棋子模型。
 *
 * 先用轨迹推出「期望盘面」，与权威盘面（扣掉新块）对账；不一致说明客户端模型
 * 已经漂移（例如丢过消息），返回 null 让调用方直接按权威盘面重建——宁可跳一下，
 * 也不能显示错的盘。
 */
function advanceTiles(
  tiles: Tile[],
  trace: TraceEntry[],
  spawned: [number, number, number] | null,
  board: number[][],
): Advance | null {
  const at = new Map<string, Tile>();
  tiles.forEach((t) => at.set(`${t.r},${t.c}`, t));

  const groups = new Map<string, TraceEntry[]>();
  for (const e of trace) {
    const key = `${e.to[0]},${e.to[1]}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(e);
    else groups.set(key, [e]);
  }

  // 对账：期望盘面 vs 权威盘面（扣掉新块）
  const expected = emptyGrid();
  let complete = true;
  groups.forEach((entries) => {
    const [r, c] = entries[0].to;
    expected[r][c] = entries[0].value;
    for (const e of entries) {
      if (!at.has(`${e.from[0]},${e.from[1]}`)) complete = false;
    }
  });
  const authoritative = board.map((row) => row.slice());
  if (spawned) authoritative[spawned[0]][spawned[1]] = 0;
  if (!complete || JSON.stringify(expected) !== JSON.stringify(authoritative)) return null;

  const sliding: Tile[] = [];
  const settle: Tile[] = [];
  groups.forEach((entries) => {
    const [r, c] = entries[0].to;
    const sources = entries
      .map((e) => at.get(`${e.from[0]},${e.from[1]}`))
      .filter((t): t is Tile => !!t);
    const keeper = sources[0];
    const absorbed = sources.slice(1);
    // 滑行阶段：双方都移到目标格，各自保留旧值
    sliding.push({ ...keeper, r, c, value: keeper.value });
    absorbed.forEach((s) => sliding.push({ ...s, r, c, dying: true }));
    // 提交阶段：只有存活的这块留下，值变成合并后的值并弹一下
    settle.push({
      id: keeper.id, r, c,
      value: entries[0].value,
      merged: entries[0].merged,
    });
  });
  if (spawned) {
    settle.push({
      id: nextTileId(), r: spawned[0], c: spawned[1], value: spawned[2], isNew: true,
    });
  }
  // 滑行阶段的顺序必须与提交阶段一致（React 按 id 复用节点）
  sliding.sort((a, b) => a.id - b.id);
  settle.sort((a, b) => a.id - b.id);
  return { sliding, settle };
}

/**
 * 单块方块。位置由**持续存在的动画值** ax/ay（目标像素坐标）决定，渲染层只挂
 * `transform: translate`——方块本身 `left/top` 恒为 0。
 *
 * 为什么不用 FLIP（把 offset 设成「源−目标」再补间回 0）：FLIP 每次都要
 * `setValue` 一个瞬时 offset，快速连滑时会把正在补间的方块**拽回上一格再重新滑**，
 * 视觉上跳一下。改成「动画值直接朝目标补间」后，新目标会停掉同一个 Value 上未完成
 * 的补间并以**当前视觉位置**为起点继续（JS 驱动下就是当前 `_value`；原生驱动下
 * 原生节点保留自己的当前值），所以后一次走子天然接着前一次的位置，不跳、也不用
 * 去读中途值（原生驱动读不到）。
 *
 * DOM 文本顺序仍恒等于权威盘面：渲染顺序由父组件按 (r,c) 排序（`orderedTiles`），
 * 与方块自身坐标无关（`e2e_2048_smoke.py` 读 `g2048-board` 的 inner_text）。
 */
const TileView = React.memo(function TileView({
  tile, cellSize, step,
}: {
  tile: Tile; cellSize: number; step: number;
}) {
  const targetX = PAD + tile.c * step;
  const targetY = PAD + tile.r * step;
  const ax = useRef(new Animated.Value(targetX)).current;
  const ay = useRef(new Animated.Value(targetY)).current;
  const scale = useRef(new Animated.Value(1)).current;
  const prevRef = useRef({ r: tile.r, c: tile.c, step });

  useLayoutEffect(() => {
    const prev = prevRef.current;
    if (prev.step !== step) {
      // 棋盘尺寸变了（窗口缩放/转屏）：直接落位，别把 resize 播成一段滑行
      ax.setValue(targetX);
      ay.setValue(targetY);
    } else if (prev.r !== tile.r || prev.c !== tile.c) {
      Animated.parallel([
        Animated.timing(ax, { toValue: targetX, duration: SLIDE_MS, useNativeDriver: NATIVE_DRIVER }),
        Animated.timing(ay, { toValue: targetY, duration: SLIDE_MS, useNativeDriver: NATIVE_DRIVER }),
      ]).start();
    }
    prevRef.current = { r: tile.r, c: tile.c, step };
  }, [tile.r, tile.c, step, targetX, targetY, ax, ay]);

  useEffect(() => {
    if (tile.merged || tile.isNew) {
      scale.setValue(tile.isNew ? 0.6 : 1);
      Animated.sequence([
        Animated.timing(scale, { toValue: 1.18, duration: POP_MS, useNativeDriver: NATIVE_DRIVER }),
        Animated.timing(scale, { toValue: 1, duration: POP_MS, useNativeDriver: NATIVE_DRIVER }),
      ]).start();
    }
  }, [tile.merged, tile.isNew, scale]);

  const tileColor = g2048Tile(tile.value);
  const label = Math.round(cellSize * (tile.value >= 1024 ? 0.34 : tile.value >= 128 ? 0.40 : 0.46));

  return (
    <Animated.View
      testID={`tile-${tile.r}-${tile.c}`}
      pointerEvents="none"
      style={[
        styles.cell,
        {
          left: 0, top: 0, width: cellSize, height: cellSize,
          backgroundColor: tileColor.bg,
          opacity: tile.dying ? 0.85 : 1,
          transform: [{ translateX: ax }, { translateY: ay }, { scale }],
        },
      ]}
    >
      <Text style={[styles.cellText, { color: tileColor.text, fontSize: label }]}>
        {tile.value}
      </Text>
    </Animated.View>
  );
});

export default function Game2048Screen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<GameState | null>(null);
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const [lbOpen, setLbOpen] = useState(false);
  /** 历史最佳分（从战绩聚合；只用于对比展示，不参与裁决） */
  const [bestScore, setBestScore] = useState<number | null>(null);
  const [boardBox, setBoardBox] = useState<{ width: number; height: number } | null>(null);
  const socketRef = useRef<RoomSocket | null>(null);
  // 我方颜色由服务端 sync/game_start 下发（host=blue、加入者=red），ref 避免闭包过期
  const myColorRef = useRef<string>('blue');
  const gameOverShownRef = useRef(false);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // 个人最佳分：战绩聚合接口一次拿回，之后本局刷新纪录就本地更新
  useEffect(() => {
    if (!token) return;
    fetchMyStats(token)
      .then((s) => setBestScore(s.best_scores?.['2048'] ?? null))
      .catch(() => {});
  }, [token]);

  // ── Board sizing ────────────────────────────────────
  // 棋盘随「实际剩余空间」缩放：竖向溢出就是因为原来只按宽度算、写死 400。
  const boardEdge = useMemo(() => {
    const avail = boardBox
      ? Math.min(boardBox.width, boardBox.height)
      : Math.min(winWidth - 2 * spacing.md, 400);
    return Math.max(MIN_BOARD, Math.min(avail, 400));
  }, [boardBox, winWidth]);
  const cellSize = Math.max(
    1, Math.floor((boardEdge - 2 * PAD - (BOARD_N - 1) * GAP) / BOARD_N),
  );
  const step = cellSize + GAP;

  // ── Tile model ──────────────────────────────────────
  // ref 是真相、state 只负责触发重渲染，这样「滑行结束提交」可以同步 flush，
  // 快速连滑时不会读到过期的 state。
  const tilesRef = useRef<Tile[]>([]);
  const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settleFnRef = useRef<(() => void) | null>(null);
  const [, bumpRender] = useReducer((x: number) => x + 1, 0);

  const commitTiles = useCallback((next: Tile[]) => {
    tilesRef.current = next;
    setTiles(next);
  }, []);

  /** 取消未完成的「滑行结束提交」并立刻执行，保证下一次走子从最终态出发。 */
  const flushSettle = useCallback(() => {
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    const fn = settleFnRef.current;
    settleFnRef.current = null;
    if (fn) fn();
  }, []);

  const resetTiles = useCallback((board: number[][]) => {
    if (settleTimerRef.current) {
      clearTimeout(settleTimerRef.current);
      settleTimerRef.current = null;
    }
    settleFnRef.current = null;
    commitTiles(tilesFromBoard(board));
  }, [commitTiles]);

  useEffect(() => () => {
    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
  }, []);

  // 原生系统/顶栏返回
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

  // Web 浏览器返回
  useEffect(() => onConfirmLeave(() => {
    if (gameOverRef.current) {
      navigation.goBack();
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  // 收到自己走子的结果：先滑行，滑完再提交合并/新块
  const applyMove = useCallback((msg: SocketMessage) => {
    const board = msg.board as number[][] | undefined;
    if (!board) return;
    flushSettle();
    const spawned = (msg.spawned as [number, number, number] | null) ?? null;
    const trace = (msg.trace as TraceEntry[]) || [];
    const advance = advanceTiles(tilesRef.current, trace, spawned, board);
    if (!advance) {
      // 对账失败：直接按权威盘面重建，不播动画
      commitTiles(tilesFromBoard(board));
      return;
    }
    commitTiles(advance.sliding);
    settleFnRef.current = () => commitTiles(advance.settle);
    settleTimerRef.current = setTimeout(() => {
      settleTimerRef.current = null;
      const fn = settleFnRef.current;
      settleFnRef.current = null;
      if (fn) fn();
    }, SLIDE_MS + 20);
  }, [commitTiles, flushSettle]);

  // Fetch room and connect WS
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
        if (g.your_color) myColorRef.current = g.your_color;
        setGame(g);
        resetTiles(g.board);
        if (g.winner) setShowGameOver(true);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) setRoom(roomData);
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as GameState;
      if (g) {
        if (g.your_color) myColorRef.current = g.your_color;
        setGame(g);
        resetTiles(g.board);
        setShowGameOver(false);
        gameOverShownRef.current = false;
      }
    };

    const handleMove2048 = (msg: SocketMessage) => {
      const fromMe = (msg.user_id as number) === user?.id;
      if (fromMe) applyMove(msg);
      setGame((prev) => {
        if (!prev) return prev;
        const next: GameState = {
          ...prev,
          leaderboard: (msg.leaderboard as LeaderRow[]) || prev.leaderboard,
        };
        if (fromMe) {
          // 只有自己走子时服务端才下发盘面（盘面是私有的）
          if (msg.board) {
            next.board = msg.board as number[][];
            next.max_tile = maxTileOf(msg.board as number[][]);
            next.move_count = prev.move_count + 1;
          }
          next.score = msg.score as number;
          // 单人局每走一步就多一层撤销栈（服务端上限 20 步）
          next.undo_available = next.player_count === 1 && next.move_count > 0;
          if (msg.player_finished) {
            next.game_over = true;
            next.finish_reason = msg.player_finished as string;
          }
        }
        return next;
      });
    };

    /** 撤销成功：盘面整体回退，**不播滑行动画**（直接重建棋子） */
    const handleUndo2048 = (msg: SocketMessage) => {
      const board = msg.board as number[][] | undefined;
      if (!board) return;
      flushSettle();
      resetTiles(board);
      setGame((prev) => (prev ? {
        ...prev,
        board,
        score: msg.score as number,
        move_count: msg.move_count as number,
        max_tile: maxTileOf(board),
        undo_available: !!msg.undo_available,
        leaderboard: (msg.leaderboard as LeaderRow[]) || prev.leaderboard,
      } : prev));
    };

    const handleGameOver = (msg: SocketMessage) => {
      const winner = msg.winner as string;
      const reason = msg.reason as string;
      setGame((prev) => prev ? { ...prev, winner, reason } : prev);
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (token) getRoom(token, code).then(setRoom).catch(() => {});
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
    ws.on('move2048', handleMove2048);
    ws.on('undo2048', handleUndo2048);
    ws.on('game_over', handleGameOver);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);

    ws.connect();

    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave, applyMove, resetTiles, flushSettle]);

  const undoMove = useCallback(() => {
    if (!game || game.winner || game.game_over) return;
    if (game.player_count !== 1) {
      notify('提示', '多人对局不支持撤销');
      return;
    }
    if (!game.undo_available) {
      notify('提示', '还没有可以撤销的步数');
      return;
    }
    socketRef.current?.undo2048();
  }, [game]);

  const sendMove = useCallback((direction: string) => {
    if (!game || game.winner || game.game_over) return;
    socketRef.current?.send({ type: 'move2048', direction });
  }, [game]);

  // Web 键盘：方向键与屏幕方向键走同一条 sendMove（PC 浏览器不必再点按钮）
  const sendMoveRef = useRef(sendMove);
  sendMoveRef.current = sendMove;
  const undoRef = useRef(undoMove);
  undoRef.current = undoMove;
  useWebKeys((event) => {
    // Ctrl/⌘ + Z 撤销（桌面浏览器；移动端用底部按钮）
    if ((event.ctrlKey || event.metaKey) && (event.key === 'z' || event.key === 'Z')) {
      event.preventDefault();
      undoRef.current();
      return;
    }
    const dir = {
      ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    }[event.key];
    if (!dir) return;
    event.preventDefault();
    sendMoveRef.current(dir);
  });

  // ── 排行榜浮层：可拖动 ───────────────────────────────────
  // 浮层展开后会盖住棋盘一角，所以允许拖走。拖动只由「胶囊」和「面板标题行」两个
  // 热区发起（见下），面板本体与名次行保持 pointerEvents="none"，指针穿透到棋盘——
  // 否则滑动手势经过浮层上方会被 responder 终止（连 release 都不触发，表现为
  // 「滑到一半没反应」）。
  const lbPan = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const lbOffsetRef = useRef({ x: 0, y: 0 });
  const lbSizeRef = useRef({ w: 0, h: 0 });
  const lbDragStartRef = useRef({ x: 0, y: 0 });
  const [lbPressed, setLbPressed] = useState(false);

  // 浮层只能停在棋盘范围内。基准位置是 top/right = spacing.sm（见 styles.lbFloat），
  // 所以 left = boardEdge - spacing.sm - 浮层宽 + x，clamp 到 [0, boardEdge - 浮层宽]。
  const lbClamp = useCallback((x: number, y: number) => {
    const { w, h } = lbSizeRef.current;
    const axis = (v: number, lo: number, hi: number) => (lo <= hi ? Math.min(Math.max(v, lo), hi) : 0);
    return {
      x: axis(x, -(boardEdge - spacing.sm - w), spacing.sm),
      y: axis(y, -spacing.sm, boardEdge - h - spacing.sm),
    };
  }, [boardEdge]);
  const lbClampRef = useRef(lbClamp);
  lbClampRef.current = lbClamp;

  // onTap 为 null 时是「纯拖动」热区（面板标题行），非 null 时是胶囊（点一下展开/收起）。
  const makeLbDrag = useCallback((onTap: null | (() => void)) => PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderGrant: () => {
      lbDragStartRef.current = { ...lbOffsetRef.current };
      if (onTap) setLbPressed(true);
    },
    onPanResponderMove: (_e, gs) => {
      const next = lbClampRef.current(lbDragStartRef.current.x + gs.dx, lbDragStartRef.current.y + gs.dy);
      lbOffsetRef.current = next;
      lbPan.setValue(next);
    },
    onPanResponderRelease: (_e, gs) => {
      if (onTap) setLbPressed(false);
      // 位移小于阈值算「点了一下」：拖动结束停在原地，不弹回
      if (onTap && Math.abs(gs.dx) < 6 && Math.abs(gs.dy) < 6) onTap();
    },
    onPanResponderTerminate: () => { if (onTap) setLbPressed(false); },
    // 拖动中拒绝把手势交出去（被下层棋盘/祖先抢走会拖到一半断掉）
    onPanResponderTerminationRequest: () => false,
  }), [lbPan]);

  const lbDragTap = useMemo(() => makeLbDrag(() => setLbOpen((v) => !v)), [makeLbDrag]);
  const lbDragHeader = useMemo(() => makeLbDrag(null), [makeLbDrag]);

  // PanResponder 手势：必须用 useMemo + ref 读取易变状态。
  // 踩坑：原来用 useRef(PanResponder.create(...)).current，回调永远闭包在首帧的
  // sendMove 上，而首帧 game === null，被守卫拦掉 → 滑动完全没反应（方向键正常）。
  // `sendMoveRef` 在上面的键盘区声明（滑动与键盘共用同一份最新回调）。
  const boardEdgeRef = useRef(boardEdge);
  boardEdgeRef.current = boardEdge;

  const panResponder = useMemo(() => {
    // 松手与被取消走同一套判定：Web 端浏览器有时会在滑动中途把手势 cancel 掉
    // （系统横滑返回、被当成页面平移/滚动），这时只有 onPanResponderTerminate，
    // 没有 release。不在这里补一刀，表现就是「滑到一半没反应」。
    const finishSwipe = (gs: PanResponderGestureState) => {
      const { dx, dy } = gs;
      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);
      // 阈值随棋盘尺寸自适应（小屏上固定 30px 偏大，轻扫会被吞掉）
      if (Math.max(absDx, absDy) < Math.max(20, boardEdgeRef.current * 0.08)) return;
      if (absDx > absDy) sendMoveRef.current(dx > 0 ? 'right' : 'left');
      else sendMoveRef.current(dy > 0 ? 'down' : 'up');
    };
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      // 捕获阶段就抢下手势：否则祖先/兄弟节点有机会先接管
      onStartShouldSetPanResponderCapture: () => true,
      onMoveShouldSetPanResponderCapture: () => true,
      onPanResponderRelease: (_evt: GestureResponderEvent, gs: PanResponderGestureState) => {
        finishSwipe(gs);
      },
      onPanResponderTerminate: (_evt: GestureResponderEvent, gs: PanResponderGestureState) => {
        finishSwipe(gs);
      },
      // 手势中途被要求移交时先拒绝，减少被中断的机会
      onPanResponderTerminationRequest: () => false,
    });
  }, []);

  const handleSurrender = () => setConfirm('surrender');

  const doSurrender = () => {
    setConfirm(null);
    socketRef.current?.surrender();
  };

  const handleLeaveRoom = async () => {
    if (!token) return;
    try {
      await leaveRoom(token, code);
    } catch {
      // continue
    }
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

  const isMyGameOver = game?.game_over || false;
  const nameOf = (uid: number): string => {
    const p = room?.players?.find((x) => x.id === uid);
    return p?.nickname || p?.username || (uid === user?.id ? '我' : '玩家');
  };
  const leaderboard = game?.leaderboard || [];
  const othersPlaying = leaderboard.filter((r) => r.user_id !== user?.id && !r.finished).length;
  const myRank = leaderboard.findIndex((r) => r.user_id === user?.id) + 1;

  // 渲染顺序固定按 (r,c)：方块位置由 transform 决定、与 DOM 顺序解耦，所以顺序
  // 只能由这里保证——DOM 文本顺序必须始终等于权威盘面（e2e_2048_smoke.py 读盘）。
  const orderedTiles = useMemo(
    () => [...tiles].sort((a, b) => (a.r - b.r) || (a.c - b.c) || (a.id - b.id)),
    [tiles],
  );

  return (
    <View style={styles.container}>
      {/* Top info bar */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <Text style={styles.myColor}>2048 竞速</Text>
          {bestScore != null && (
            <Text style={styles.bestText}>最佳 {bestScore}</Text>
          )}
        </View>
        <View style={styles.topRight}>
          <GameHelpButton game="2048" size={24} />
          <Text style={styles.roomCode}>#{code}</Text>
        </View>
      </View>

      {/* 自己已结束但别人还在玩 */}
      {game && isMyGameOver && !showGameOver && (
        <View style={styles.doneBanner}>
          <Text style={styles.doneBannerText}>
            {FINISH_LABELS[game.finish_reason || ''] || '你已结束'}
            {othersPlaying > 0 ? ` · 等待其他 ${othersPlaying} 位玩家…` : ''}
          </Text>
        </View>
      )}

      {/* Board area：棋盘按实测剩余空间自适应，排行榜浮层贴在棋盘右上角 */}
      <View
        style={styles.boardArea}
        onLayout={(e) => setBoardBox({
          width: e.nativeEvent.layout.width,
          height: e.nativeEvent.layout.height,
        })}
      >
        <View style={{ width: boardEdge, height: boardEdge }}>
          <View
            testID="g2048-board"
            style={[styles.boardWrap, { width: boardEdge, height: boardEdge }]}
            {...panResponder.panHandlers}
          >
            {/* 静态底层格子 */}
            {Array.from({ length: BOARD_N * BOARD_N }, (_, i) => {
              const r = Math.floor(i / BOARD_N);
              const c = i % BOARD_N;
              return (
                <View
                  key={`bg-${r}-${c}`}
                  style={[styles.cell, {
                    left: PAD + c * step, top: PAD + r * step,
                    width: cellSize, height: cellSize, backgroundColor: '#cdc1b4',
                  }]}
                />
              );
            })}
            {/* 棋子层 */}
            {orderedTiles.map((t) => (
              <TileView key={t.id} tile={t} cellSize={cellSize} step={step} />
            ))}
          </View>

          {/* 实时排行榜：右上角半透明浮层，默认收起，不占布局高度，可拖动改位置 */}
          {game && (
            <Animated.View
              style={[styles.lbFloat, { transform: [{ translateX: lbPan.x }, { translateY: lbPan.y }] }]}
              pointerEvents="box-none"
              onLayout={(e) => {
                // 尺寸随「展开/收起」变化，重新夹一次位置，别让面板探出棋盘
                lbSizeRef.current = {
                  w: e.nativeEvent.layout.width,
                  h: e.nativeEvent.layout.height,
                };
                const c = lbClampRef.current(lbOffsetRef.current.x, lbOffsetRef.current.y);
                if (c.x !== lbOffsetRef.current.x || c.y !== lbOffsetRef.current.y) {
                  lbOffsetRef.current = c;
                  lbPan.setValue(c);
                }
              }}
            >
              <View
                testID="g2048-lb-toggle"
                style={[styles.lbToggle, lbPressed && styles.lbTogglePressed]}
                {...lbDragTap.panHandlers}
              >
                <Ionicons name="podium-outline" size={14} color={colors.primary} />
                <Text style={styles.lbToggleText}>
                  {myRank > 0 ? `第${myRank}/${leaderboard.length}` : '排名'} · {game.score}
                </Text>
                <Ionicons
                  name={lbOpen ? 'chevron-up' : 'chevron-down'}
                  size={14}
                  color={colors.primary}
                />
              </View>

              {lbOpen && (
                // box-none + 名次行 none：面板是纯展示，指针事件必须穿透到棋盘，
                // 否则滑动手势经过面板上方会被 responder 终止（RNW 下直接终止，
                // 连 onPanResponderRelease 都不触发，表现为「滑到一半没反应」）。
                // 唯一的例外是标题行——它是拖动热区（拖动中拒绝移交，见 makeLbDrag）。
                <View style={styles.lbPanel} testID="g2048-leaderboard" pointerEvents="box-none">
                  <View style={styles.lbHeaderRow} {...lbDragHeader.panHandlers}>
                    <Ionicons name="move-outline" size={12} color={colors.textMuted} />
                    <Text style={styles.lbTitle}>实时排名</Text>
                    <View style={styles.lbHeaderSpacer} />
                    <Text style={styles.lbMine}>最大 {game.max_tile}</Text>
                  </View>
                  <View pointerEvents="none">
                    {leaderboard.map((row, i) => {
                      const isMe = row.user_id === user?.id;
                      return (
                        <View
                          key={row.user_id}
                          testID={`g2048-lb-${row.user_id}`}
                          style={[styles.lbRow, isMe && styles.lbRowMe]}
                        >
                          <Text style={styles.lbRank}>{i + 1}</Text>
                          <View style={[styles.lbDot, { backgroundColor: colorHex(row.color) }]} />
                          <Text style={[styles.lbName, isMe && styles.lbNameMe]} numberOfLines={1}>
                            {nameOf(row.user_id)}{isMe ? '（我）' : ''}
                          </Text>
                          {row.finished && (
                            <Text style={styles.lbDone}>
                              {row.finish_reason === 'reach_2048' ? '2048!' : '已结束'}
                            </Text>
                          )}
                          <Text style={[styles.lbScore, isMe && styles.lbScoreMe]}>{row.score}</Text>
                          <Text style={styles.lbTile}>{row.max_tile}</Text>
                        </View>
                      );
                    })}
                  </View>
                </View>
              )}
            </Animated.View>
          )}
        </View>
      </View>

      {/* Direction buttons */}
      <View style={styles.dirPad}>
        <View style={styles.dirRow}>
          <TouchableOpacity testID="dir-up" style={styles.dirBtn} onPress={() => sendMove('up')}>
            <Ionicons name="chevron-up" size={26} color={colors.text} />
          </TouchableOpacity>
        </View>
        <View style={styles.dirRow}>
          <TouchableOpacity testID="dir-left" style={styles.dirBtn} onPress={() => sendMove('left')}>
            <Ionicons name="chevron-back" size={26} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.dirBtnPlaceholder} />
          <TouchableOpacity testID="dir-right" style={styles.dirBtn} onPress={() => sendMove('right')}>
            <Ionicons name="chevron-forward" size={26} color={colors.text} />
          </TouchableOpacity>
        </View>
        <View style={styles.dirRow}>
          <TouchableOpacity testID="dir-down" style={styles.dirBtn} onPress={() => sendMove('down')}>
            <Ionicons name="chevron-down" size={26} color={colors.text} />
          </TouchableOpacity>
        </View>
      </View>

      {/* Action buttons */}
      <View style={styles.bottomBar}>
        {/* 撤销只在单人局出现：多人局由服务端拒绝，客户端也不展示入口 */}
        {game && !game.winner && game.player_count === 1 && (
          <TouchableOpacity style={styles.undoBtn} onPress={undoMove} testID="g2048-undo">
            <Ionicons name="arrow-undo-outline" size={18} color={colors.text} />
            <Text style={styles.undoBtnText}>撤销</Text>
          </TouchableOpacity>
        )}
        {game && !game.winner && (
          <TouchableOpacity style={styles.surrenderBtn} onPress={handleSurrender}>
            <Text style={styles.surrenderBtnText}>认输</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirm('leave')}>
          <Text style={styles.leaveBtnText}>退出</Text>
        </TouchableOpacity>
      </View>

      {/* Confirm modal */}
      <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons
              name={confirm === 'surrender' ? 'flag-outline' : 'exit-outline'}
              size={36}
              color={colors.danger}
            />
            <Text style={styles.modalTitle}>
              {confirm === 'surrender' ? '确认认输？' : '确认退出？'}
            </Text>
            <Text style={styles.modalReason}>
              {confirm === 'surrender'
                ? '认输后本局判负，无法撤销'
                : '对局中退出将判负并离开房间'}
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
            {game && (() => {
              const rows: RankRow[] = game.ranking?.length
                ? game.ranking
                : leaderboard.map((r, i) => ({
                    seat: i, user_id: r.user_id, username: '', color: r.color,
                    score: r.score, max_tile: r.max_tile, rank: i + 1,
                  }));
              const mine = rows.find((r) => r.user_id === user?.id);
              const champion = rows.find((r) => r.max_tile >= WIN_TILE);
              // 2048 是各玩各的跑分竞速，**不是胜负对抗**：只有真的合出 2048
              // 才叫胜利，其余（分数最高 / 卡死 / 认输）一律「游戏结束」。
              // 千万别用 rank === 1 当胜负——排名第一只是分高，不是赢。
              const title = mine && mine.max_tile >= WIN_TILE ? '合成 2048，胜利！' : '游戏结束';
              const subtitle = mine
                ? `第 ${mine.rank} / ${rows.length} 名` +
                  (champion && champion.user_id !== user?.id
                    ? ` · ${nameOf(champion.user_id)} 合成 2048`
                    : '')
                : '';
              return (
                <>
                  <Text style={styles.modalResult}>{title}</Text>
                  <Text style={styles.modalReason}>{subtitle}</Text>
                  {game.reason && REASON_LABELS[game.reason] && (
                    <Text style={styles.modalReason}>{REASON_LABELS[game.reason]}</Text>
                  )}
                  {bestScore != null && (
                    <Text style={[styles.modalReason, game.score > bestScore && styles.recordText]}>
                      {game.score > bestScore
                        ? `新纪录！个人最佳 ${game.score}`
                        : `个人最佳 ${bestScore}`}
                    </Text>
                  )}
                  <View style={styles.rankList}>
                    {rows.map((r) => (
                      <View key={r.user_id}
                        style={[styles.rankRow, r.user_id === user?.id && styles.lbRowMe]}>
                        <Text style={styles.rankNo}>{r.rank}</Text>
                        <View style={[styles.lbDot, { backgroundColor: colorHex(r.color) }]} />
                        <Text style={styles.rankName} numberOfLines={1}>
                          {nameOf(r.user_id)}
                        </Text>
                        <Text style={styles.rankTile}>最大 {r.max_tile}</Text>
                        <Text style={styles.rankScore}>{r.score}</Text>
                      </View>
                    ))}
                  </View>
                </>
              );
            })()}
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
    width: '100%', flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', paddingVertical: spacing.sm, paddingHorizontal: spacing.md,
    backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  myColor: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  bestText: { fontSize: fontSize.xs, color: colors.textSecondary, fontWeight: '600' },
  recordText: { color: colors.success, fontWeight: '700' },
  roomCode: {
    fontSize: fontSize.sm, color: colors.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  boardArea: {
    flex: 1, width: '100%', alignItems: 'center', justifyContent: 'center',
    paddingVertical: spacing.sm, paddingHorizontal: spacing.sm,
  },
  // 排行榜浮层：贴棋盘右上角，不参与布局（否则小屏上会把棋盘挤没），可拖动
  lbFloat: {
    position: 'absolute', top: spacing.sm, right: spacing.sm, alignItems: 'flex-end',
    // Web 端：拖动热区也要声明 touch-action，别让浏览器把拖动当成页面平移。
    // touch-action 不继承，棋盘上的那一条管不到这个兄弟节点。
    ...(Platform.OS === 'web' ? ({ touchAction: 'none' } as any) : null),
  },
  lbToggle: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingHorizontal: spacing.sm, paddingVertical: 5,
    borderRadius: radius.full, backgroundColor: withAlpha(colors.white, 0.88),
    borderWidth: 1, borderColor: withAlpha(colors.primary, 0.25),
  },
  lbToggleText: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  // 按下态：立即反馈（<100ms，见方法论 §1），并提示这枚胶囊是可拖动的
  lbTogglePressed: { backgroundColor: colors.white, borderColor: colors.primary },
  lbPanel: {
    marginTop: spacing.xs, minWidth: 232, maxWidth: 300,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    borderRadius: radius.md, backgroundColor: withAlpha(colors.white, 0.92),
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  // 标题行是面板里唯一的触摸目标（拖动热区）；其余行 pointerEvents="none"
  lbHeaderRow: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingVertical: spacing.xs, marginBottom: spacing.xs,
  },
  lbHeaderSpacer: { flex: 1 },
  lbTitle: { fontSize: fontSize.xs, fontWeight: '700', color: colors.textSecondary },
  lbMine: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  lbRow: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingVertical: 2, paddingHorizontal: spacing.xs, borderRadius: radius.sm,
  },
  lbRowMe: { backgroundColor: withAlpha(colors.primary, 0.10) },
  lbRank: { width: 14, fontSize: fontSize.xs, fontWeight: '700', color: colors.textMuted },
  lbDot: { width: 10, height: 10, borderRadius: 5 },
  lbName: { flex: 1, fontSize: fontSize.xs, color: colors.text },
  lbNameMe: { fontWeight: '700' },
  lbDone: { fontSize: fontSize.xs, color: colors.success, fontWeight: '700' },
  lbScore: { minWidth: 44, textAlign: 'right', fontSize: fontSize.xs, fontWeight: '700', color: colors.text },
  lbScoreMe: { color: colors.primary },
  lbTile: { minWidth: 36, textAlign: 'right', fontSize: fontSize.xs, color: colors.textMuted },
  doneBanner: {
    width: '100%', backgroundColor: colors.success, paddingVertical: spacing.xs,
    alignItems: 'center',
  },
  doneBannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '700' },
  rankList: { width: '100%', marginBottom: spacing.md },
  rankRow: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    paddingVertical: spacing.xs, paddingHorizontal: spacing.sm, borderRadius: radius.sm,
  },
  rankNo: { width: 18, fontSize: fontSize.md, fontWeight: '800', color: colors.textSecondary },
  rankName: { flex: 1, fontSize: fontSize.md, color: colors.text },
  rankTile: { fontSize: fontSize.xs, color: colors.textMuted },
  rankScore: { minWidth: 64, textAlign: 'right', fontSize: fontSize.md, fontWeight: '800', color: colors.text },
  boardWrap: {
    position: 'relative', backgroundColor: '#bbada0', borderRadius: radius.md,
    overflow: 'hidden',
    // Web 端：告诉浏览器别把这块的平移当成页面滚动/横滑返回手势——被系统手势
    // 抢走时 touch 序列会被 cancel，PanResponder 收不到 release，表现就是
    // 「横滑没反应」。RNW 支持该属性（映射到 CSS touch-action），但 RN 的
    // ViewStyle 类型里没有，所以只给 Web 加上。
    ...(Platform.OS === 'web' ? ({ touchAction: 'none' } as any) : null),
    shadowColor: colors.black, shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18, shadowRadius: 6, elevation: 3,
  },
  cell: {
    position: 'absolute', alignItems: 'center', justifyContent: 'center',
    borderRadius: radius.sm,
  },
  cellText: { fontWeight: '800' },
  dirPad: { alignItems: 'center', marginTop: spacing.xs },
  dirRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: 4 },
  dirBtn: {
    width: 48, height: 48, alignItems: 'center', justifyContent: 'center',
    backgroundColor: colors.card, borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  dirBtnPlaceholder: { width: 48, height: 48 },
  bottomBar: {
    flexDirection: 'row', gap: spacing.md, paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm, paddingBottom: spacing.md,
  },
  undoBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  undoBtnText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  surrenderBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.danger, borderRadius: radius.md,
  },
  surrenderBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  leaveBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg, borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay, justifyContent: 'center',
    alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.xl, padding: spacing.lg, alignItems: 'center',
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.md },
  modalResult: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.primary, marginBottom: spacing.xs },
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  scoreRow: { flexDirection: 'row', gap: spacing.lg, marginBottom: spacing.md },
  scoreLabel: { fontSize: fontSize.sm, color: colors.textSecondary },
  modalBtn: {
    width: '100%', borderRadius: radius.md, paddingVertical: spacing.sm + 4,
    alignItems: 'center', marginTop: spacing.sm,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
