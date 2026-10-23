import React, {
  useState, useEffect, useRef, useCallback, useMemo,
} from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, PanResponder, GestureResponderEvent, PanResponderGestureState,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize,
  tetrisBoard, tetrisSlot, tetrisEdge, tetrisPieceColors, tetrisGarbage,
  tetrisGhost, tetrisGrid, tetrisPanel, tetrisOnPanel, tetrisOnPanelMuted, playerColors,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave, useWebKeys } from '../backGuard';
import { GameHelpButton } from '../help';
import {
  TetrisPiece, TetrisRules, TetrisLeaderRow, gravityMs, GARBAGE_LABEL,
  ID_TO_PIECE, GARBAGE_ID, cellsOf, stepDown, movePiece, rotatePiece,
  dropDistance, stamp, replayPending,
} from '../tetris';

/** 服务端逐人下发的对局状态（`game_start` / `sync` / `tetris_state` 同构）。 */
interface TetrisPayload {
  game: 'tetris';
  cols: number;
  rows: number;
  board: number[][];
  current: TetrisPiece | null;
  next_piece: string | null;
  /** 接下来 3 块（服务端预览队列，旧版服务端可能没有） */
  next_pieces?: string[];
  /** 暂存的方块名（空 = 还没存过） */
  held?: string | null;
  /** 本块是否已经用过暂存（锁一块后重置） */
  hold_used?: boolean;
  start_level?: number;
  shapes: TetrisRules['shapes'];
  kicks: number[];
  your_color: string;
  score: number;
  lines: number;
  level: number;
  garbage_received: number;
  finished: boolean;
  finish_reason: string | null;
  winner: string | null;
  reason: string | null;
  leaderboard: TetrisLeaderRow[];
  /** 只在本人的 `tetris_state` 里有：这次锁定的战果 */
  last_action?: 'lock' | 'garbage';
  cleared?: number;
  score_gained?: number;
  garbage_sent?: number;
  garbage_killed?: number[];
  garbage_from?: number;
  garbage_count?: number;
  player_finished?: string | null;
  /** 只在 `game_over` 之后有 */
  ranking?: RankRow[];
}

interface RankRow {
  seat: number;
  user_id: number;
  username: string;
  nickname?: string | null;
  is_bot?: boolean;
  color: string;
  score: number;
  lines: number;
  level: number;
  garbage_received: number;
  finish_reason: string | null;
  rank: number;
}

interface Props {
  code: string;
  onLeave: () => void;
}

const REASON_LABELS: Record<string, string> = {
  top_out: '方块顶到天花板',
  garbage: '被攻击行压死',
  surrender: '主动认输',
  disconnect: '掉线',
};

/** 对方块 id 上色；未收录的（含攻击行填充块）走兜底。 */
function cellColor(value: number): string | null {
  if (value === 0) return null;
  if (value === GARBAGE_ID) return tetrisGarbage;
  const name = ID_TO_PIECE[value];
  return (name && tetrisPieceColors[name]) || tetrisGarbage;
}

/** 方块 id → 无障碍标签（E2E 靠它读盘面，不然只能去比颜色）。 */
function cellLabel(value: number): string {
  if (value === 0) return 'empty';
  if (value === GARBAGE_ID) return 'garbage';
  return ID_TO_PIECE[value] || 'block';
}

/**
 * HUD 侧栏宽度 + 棋盘之外的固定开销（实测值）。
 *
 * **HUD 放在棋盘右侧而不是上方**：竖屏下高度是稀缺资源（导航栏/顶栏/提示条/
 * 操作区/认输都是固定开销），而 10 列的棋盘横着用不完屏宽——把四格读数与
 * 下一块预览挪到侧栏，棋盘就能吃掉省下来的整段高度。实测 390×844 上格宽
 * 23px → 26px、360×640 上 13px → 18px。面板也不再有「比棋盘宽一大截」的
 * 空白（v1.9 之前 HUD 在顶上时，小屏上面板右半边是空的）。
 */
const SIDE_W = 92;
/** 棋盘之外的开销：导航栏 64 + 顶栏 32 + 容器 8 + 面板内边距与边框 20
 *  + 操作区（47 + 上边距 16）63 + 认输（34 + 上边距 8）46 + 提示条槽 34 ≈ 267，取 280。 */
const CHROME_H = 280;

/** 拖动时「走一格」需要的位移：手指跨过 0.8 格就真的走一格（实时跟手）。 */
const DRAG_STEP_RATIO = 0.8;
/** 位移小于它才算「单击」（棋盘上点一下 = 旋转）。 */
const TAP_SLOP = 12;
/** 纵向滑动的判定阈值：下滑软降 / 上滑旋转。 */
const SWIPE_SLOP = 26;

function useCellSize(rows: number, cols: number) {
  const { width, height } = useWindowDimensions();
  return useMemo(() => {
    const byHeight = Math.floor((height - CHROME_H) / rows);
    // 侧栏 + 面板内边距/边框 + 棋盘自身边框
    const byWidth = Math.floor((width - SIDE_W - spacing.sm - 24) / cols);
    return Math.max(12, Math.min(byHeight, byWidth, 34));
  }, [width, height, rows, cols]);
}

export default function TetrisScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<TetrisPayload | null>(null);
  const [rules, setRules] = useState<TetrisRules | null>(null);
  /** 本地预演的盘面与方块（权威结果以服务端回包为准，见 `app/src/tetris.ts`）。 */
  const [view, setView] = useState<{ board: number[][]; cur: TetrisPiece | null }>({
    board: [], cur: null,
  });
  /** 已本地执行、还没发上去的动作序列。 */
  const pendingRef = useRef<string[]>([]);
  /** 锁定后等回包期间冻结输入（下一块是什么还没到，继续操作就会与重放分叉）。 */
  const [awaiting, setAwaiting] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [lbOpen, setLbOpen] = useState(false);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);

  const socketRef = useRef<RoomSocket | null>(null);
  const boardRef = useRef<number[][]>([]);
  const curRef = useRef<TetrisPiece | null>(null);
  const rulesRef = useRef<TetrisRules | null>(null);
  const awaitingRef = useRef(false);
  const finishedRef = useRef(false);
  /** 本块是否已暂存过（服务端随每次状态下发；用于点击反馈，不自己推规则） */
  const holdUsedRef = useRef(false);
  const bannerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gameOverShownRef = useRef(false);
  /** 程序化导航（回房间/退出）时放行 beforeRemove，别弹确认。 */
  const skipRemoveRef = useRef(false);
  /** nameOf 的 ref 版：WS 回包回调里要用最新房间名单（闭包会过期）。 */
  const nameOfRef = useRef<(uid: number) => string>(() => '玩家');

  const cols = game?.cols ?? 10;
  const rows = game?.rows ?? 20;
  const cell = useCellSize(rows, cols);

  const setLocal = useCallback((board: number[][], cur: TetrisPiece | null) => {
    boardRef.current = board;
    curRef.current = cur;
    setView({ board, cur });
  }, []);

  const flash = useCallback((text: string) => {
    setBanner(text);
    if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
    bannerTimerRef.current = setTimeout(() => {
      bannerTimerRef.current = null;
      setBanner(null);
    }, 1800);
  }, []);

  useEffect(() => () => {
    if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
  }, []);

  const setAwaitRef = (v: boolean) => { awaitingRef.current = v; setAwaiting(v); };
  const setFinishedRef = (v: boolean) => { finishedRef.current = v; };

  /** 把攒下的动作发上去（服务端重放，回包即权威盘面）。 */
  const flush = useCallback(() => {
    const actions = pendingRef.current;
    pendingRef.current = [];
    if (actions.length === 0) return;
    setAwaitRef(true);
    socketRef.current?.tetrisActions(actions);
  }, []);

  /** 本地执行一个动作并记账。锁定的动作由重力/硬降触发，见调用方。 */
  const act = useCallback((action: string) => {
    const r = rulesRef.current;
    const cur = curRef.current;
    if (!r || !cur || awaitingRef.current || finishedRef.current) return;
    const board = boardRef.current;
    if (action === 'left' || action === 'right') {
      const next = movePiece(board, r, cur, action === 'left' ? -1 : 1);
      if (next === cur) return;
      pendingRef.current.push(action);
      setLocal(board, next);
      return;
    }
    if (action === 'rotate_cw' || action === 'rotate_ccw') {
      const next = rotatePiece(board, r, cur, action === 'rotate_cw' ? 1 : -1);
      if (next === cur) return;
      pendingRef.current.push(action);
      setLocal(board, next);
      return;
    }
    if (action === 'hard_drop') {
      const d = dropDistance(board, r, cur);
      pendingRef.current.push('hard_drop');
      setLocal(board, { ...cur, y: cur.y + d });
      flush();
    }
  }, [flush, setLocal]);

  /** 软降/重力：能下一格就下，下不动就补一个 `down` 让服务端锁定。 */
  const stepAndMaybeLock = useCallback(() => {
    const r = rulesRef.current;
    const cur = curRef.current;
    if (!r || !cur || awaitingRef.current || finishedRef.current) return;
    const board = boardRef.current;
    pendingRef.current.push('down');
    const next = stepDown(board, r, cur);
    if (next) {
      setLocal(board, next);
      return;
    }
    // 落不下去的这一下由服务端判定为「锁定」并结算
    flush();
  }, [flush, setLocal]);

  /**
   * 暂存 / 换出当前方块：**本地无法预演**（换出来的是暂存块还是预览队首，
   * 以及新的 next 队列，只有服务端知道），所以立刻把积攒的动作连同 `hold`
   * 发上去，等权威状态回来再放开输入。每块限一次，用过的点击给一句提示
   * （禁用态在本项目等于「点了没反应」，不能只置灰）。
   */
  const holdNow = useCallback(() => {
    if (!curRef.current || awaitingRef.current || finishedRef.current) return;
    if (holdUsedRef.current) {
      flash('这一块已经暂存过了');
      return;
    }
    pendingRef.current.push('hold');
    flush();
  }, [flush, flash]);

  // ── 重力：固定节拍，与移动无关 ────────────────────────────
  // 踩坑预案：如果把定时器挂在 cur/board 上，每动一下都会重置计时 →
  // 一直按着方向键方块就永远不掉（等于无限锁定延迟）。
  const stepRef = useRef(stepAndMaybeLock);
  stepRef.current = stepAndMaybeLock;
  const level = game?.level ?? 1;
  const playing = !!game && !game.winner && !game.finished;
  useEffect(() => {
    if (!playing || awaiting) return;
    const id = setInterval(() => stepRef.current(), gravityMs(level));
    return () => clearInterval(id);
  }, [playing, awaiting, level]);

  // ── 网页键盘：←→/AD 移动、↑/W/X 顺时针、Z 逆时针、↓/S 软降、C 暂存、空格落底 ──
  // 按住方向键靠系统的自动连发；落底/暂存不连发（按住空格会把后面几块全砸下去）
  useWebKeys((key, e) => {
    switch (key) {
      case 'ArrowLeft': case 'a': act('left'); return true;
      case 'ArrowRight': case 'd': act('right'); return true;
      case 'ArrowUp': case 'w': case 'x': act('rotate_cw'); return true;
      case 'z': act('rotate_ccw'); return true;
      case 'ArrowDown': case 's': stepAndMaybeLock(); return true;
      case 'c': if (!e.repeat) holdNow(); return true;
      case ' ': if (!e.repeat) act('hard_drop'); return true;
      default: return false;
    }
  }, playing && confirm === null);

  // ── WS ────────────────────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});

    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    /** 服务端状态 → 本地（唯一入口：所有权威盘面都从这里进来）。 */
    const adopt = (g: TetrisPayload) => {
      if (g.shapes) {
        const r = { shapes: g.shapes, kicks: g.kicks || [0, -1, 1, -2, 2] };
        rulesRef.current = r;
        setRules(r);
      }
      setGame(g);
      pendingRef.current = [];
      setAwaitRef(false);
      setFinishedRef(!!g.finished);
      holdUsedRef.current = !!g.hold_used;
      setLocal(g.board || [], g.current || null);
    };

    const handleSync = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
      if (msg.game) {
        adopt(msg.game as TetrisPayload);
        if ((msg.game as TetrisPayload).winner) setShowGameOver(true);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
    };

    const handleStart = (msg: SocketMessage) => {
      const g = msg.game as TetrisPayload;
      if (!g) return;
      adopt(g);
      setShowGameOver(false);
      gameOverShownRef.current = false;
    };

    /** 我的走子结果 / 我被攻击：两种情况都带权威盘面。 */
    const handleState = (msg: SocketMessage) => {
      const mine = (msg.user_id as number) === user?.id;
      const attacked = msg.last_action === 'garbage';
      if (!mine && !attacked) {
        // 别人走子：只更新排行榜（排行榜是唯一公开信息）
        setGame((prev) => (prev
          ? { ...prev, leaderboard: (msg.leaderboard as TetrisLeaderRow[]) || prev.leaderboard }
          : prev));
        return;
      }
      const g = msg as unknown as TetrisPayload;
      setGame((prev) => (prev ? { ...prev, ...g } : prev));
      if (g.shapes) {
        const r = { shapes: g.shapes, kicks: g.kicks || [0, -1, 1, -2, 2] };
        rulesRef.current = r;
        setRules(r);
      }
      // 攻击通知不是本人动作的回执，不能解冻仍在等待落定/暂存结果的方块。
      if (!(attacked && awaitingRef.current && !g.finished)) {
        if (attacked && !g.finished && g.current && rulesRef.current) {
          const replay = replayPending(g.board, rulesRef.current, g.current, pendingRef.current);
          pendingRef.current = replay.actions;
          setLocal(g.board, replay.current);
          if (replay.locked) flush();
        } else {
          pendingRef.current = [];
          setAwaitRef(false);
          setLocal(g.board || [], g.current || null);
        }
      }
      setFinishedRef(!!g.finished);
      holdUsedRef.current = !!g.hold_used;
      if (attacked) {
        flash(`被 ${nameOfRef.current(g.garbage_from ?? -1)} 顶了 ${g.garbage_count || 0} 行`);
      } else if ((g.garbage_killed?.length ?? 0) > 0) {
        flash('击溃对手！');
      } else if ((g.cleared ?? 0) > 0) {
        flash(GARBAGE_LABEL[g.cleared as number] || `消除 ${g.cleared} 行`);
      }
    };

    const handleLeaderboard = (msg: SocketMessage) => {
      setGame((prev) => (prev
        ? { ...prev, leaderboard: (msg.leaderboard as TetrisLeaderRow[]) || prev.leaderboard }
        : prev));
    };

    const handleGameOver = (msg: SocketMessage) => {
      setGame((prev) => (prev
        ? { ...prev, winner: msg.winner as string, reason: msg.reason as string,
            ranking: msg.ranking as RankRow[] } as TetrisPayload
        : prev));
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (token) getRoom(token, code).then(setRoom).catch(() => {});
    };

    const handleClosed = () => {
      notify('房间已解散');
      ws.close();
      onLeave();
    };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleStart);
    ws.on('tetris_state', handleState);
    ws.on('tetris_leaderboard', handleLeaderboard);
    ws.on('game_over', handleGameOver);
    ws.on('room_closed', handleClosed);
    ws.on('error', (msg) => notify('提示', msg.message as string));
    ws.connect();

    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave, setLocal, flash, flush]);

  // 原生系统/顶栏返回
  useEffect(() => navigation.addListener('beforeRemove', (e: any) => {
    if (Platform.OS === 'web') return;
    if (skipRemoveRef.current) return;
    if (finishedRef.current || gameOverShownRef.current) {
      skipRemoveRef.current = true;
      navigation.dispatch(e.data.action);
      return;
    }
    e.preventDefault();
    setConfirm('leave');
  }), [navigation]);

  // Web 浏览器返回（必须走 backGuard 的助手，原生端自动 no-op）
  useEffect(() => onConfirmLeave(() => {
    if (finishedRef.current || gameOverShownRef.current) {
      navigation.goBack();
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  // ── 手势：单击旋转 / 左右拖动实时移动 / 下滑软降 / 上滑旋转 ──
  const cellRef = useRef(cell);
  cellRef.current = cell;
  const actRef = useRef(act);
  actRef.current = act;
  const stepNowRef = useRef(stepAndMaybeLock);
  stepNowRef.current = stepAndMaybeLock;

  /** 本次手势里**已经兑现**的横向位移（px）——见 `applyDrag`。 */
  const consumedRef = useRef(0);

  /**
   * 左右拖动**实时移动**：手指每跨过 0.8 格就真的走一格。
   *
   * 为什么不是松手时按总位移一次补算：拖动时方块得跟着手指一格格挪，才像在
   * 「搬」这块方块；松手才动的话，手感上和点两次按钮没有区别。
   *
   * `gs.dx` 是**本次手势的总位移**，所以要减去已经兑现的部分，剩下的才是
   * 「还欠多少格」。快速甩动时 move 回调可能只来一两次，于是松手时再跑一遍
   * 同一个函数把余量补上（同一个累加器，不会重复扣款）。
   */
  const applyDrag = (dx: number) => {
    const step = Math.max(14, cellRef.current * DRAG_STEP_RATIO);
    while (dx - consumedRef.current >= step) {
      consumedRef.current += step;
      actRef.current('right');
    }
    while (consumedRef.current - dx >= step) {
      consumedRef.current -= step;
      actRef.current('left');
    }
  };
  const applyDragRef = useRef(applyDrag);
  applyDragRef.current = applyDrag;

  const pan = useMemo(() => {
    const finish = (gs: PanResponderGestureState) => {
      const { dx, dy } = gs;
      // 先补上最后一次 move 之后的余量，再判是不是单击
      applyDragRef.current(dx);
      if (Math.abs(dx) < TAP_SLOP && Math.abs(dy) < TAP_SLOP) {
        actRef.current('rotate_cw');       // 单击棋盘 = 旋转
        return;
      }
      if (Math.abs(dx) > Math.abs(dy)) return;   // 横向：拖动过程中已经实时兑现
      if (Math.abs(dy) < SWIPE_SLOP) return;     // 到不了滑动阈值：什么都不做
      if (dy > 0) stepNowRef.current();          // 下滑 = 软降一格
      else actRef.current('rotate_cw');          // 上滑 = 旋转
    };
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onStartShouldSetPanResponderCapture: () => true,
      onMoveShouldSetPanResponderCapture: () => true,
      onPanResponderGrant: () => { consumedRef.current = 0; },
      onPanResponderMove: (_e: GestureResponderEvent, gs: PanResponderGestureState) => {
        applyDragRef.current(gs.dx);
      },
      onPanResponderRelease: (_e: GestureResponderEvent, gs: PanResponderGestureState) => finish(gs),
      // Web 端浏览器可能中途 cancel 手势，release 拿不到——不补这一刀就是「滑了没反应」
      onPanResponderTerminate: (_e: GestureResponderEvent, gs: PanResponderGestureState) => finish(gs),
      onPanResponderTerminationRequest: () => false,
    });
  }, []);

  // ── 派生视图 ──────────────────────────────────────────────
  const displayBoard = useMemo(() => {
    const r = rules;
    if (!r || !view.cur || view.board.length === 0) return view.board;
    return stamp(view.board, r, view.cur);
  }, [rules, view]);

  const ghostKeys = useMemo(() => {
    const r = rules;
    if (!r || !view.cur || view.board.length === 0) return new Set<string>();
    const d = dropDistance(view.board, r, view.cur);
    if (d <= 0) return new Set<string>();
    return new Set(cellsOf(r, view.cur.piece, view.cur.rotation, view.cur.x, view.cur.y + d)
      .map(([rr, cc]) => `${rr},${cc}`));
  }, [rules, view]);

  const pieceKeys = useMemo(() => {
    const r = rules;
    if (!r || !view.cur) return new Set<string>();
    return new Set(cellsOf(r, view.cur.piece, view.cur.rotation, view.cur.x, view.cur.y)
      .map(([rr, cc]) => `${rr},${cc}`));
  }, [rules, view]);

  /** 预览队列（最多 3 块）；旧服务端只给 `next_piece` 时退回单块预览。 */
  const nextPieces = useMemo(() => {
    const list = game?.next_pieces?.length
      ? game.next_pieces
      : (game?.next_piece ? [game.next_piece] : []);
    return list.slice(0, 3);
  }, [game?.next_pieces, game?.next_piece]);

  const nameOf = (uid: number): string => {
    const p = room?.players?.find((x) => x.id === uid);
    return p?.nickname || p?.username || (uid === user?.id ? '我' : '玩家');
  };
  nameOfRef.current = nameOf;
  const leaderboard = game?.leaderboard || [];
  const ranking = game?.ranking || [];
  const myFinished = !!game?.finished;
  const othersPlaying = leaderboard.filter((r) => r.user_id !== user?.id && !r.finished).length;

  const doSurrender = () => {
    setConfirm(null);
    socketRef.current?.surrender();
  };

  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);

  const handleLeaveRoom = async () => {
    if (!token) return;
    try {
      await leaveRoom(token, code);
    } catch {
      // 继续走本地退出
    }
    socketRef.current?.leave();
    socketRef.current?.close();
    skipRemoveRef.current = true;
    onLeave();
  };

  const boardW = cols * cell + 4;
  const boardH = rows * cell + 4;

  return (
    <View style={styles.container}>
      {/* 顶栏：排行榜胶囊 + 规则入口 */}
      <View style={styles.topBar}>
        <TouchableOpacity
          style={styles.lbCapsule}
          onPress={() => setLbOpen((v) => !v)}
          accessibilityLabel="排行榜"
        >
          <Ionicons name={lbOpen ? 'chevron-up' : 'podium-outline'} size={14} color={colors.textSecondary} />
          <Text style={styles.lbCapsuleText}>
            第 {Math.max(1, leaderboard.findIndex((r) => r.user_id === user?.id) + 1)} 名 · {leaderboard.length} 人
          </Text>
        </TouchableOpacity>
        <GameHelpButton game="tetris" />
      </View>

      {/* 排行榜浮层盖住棋盘一角，所以做成可收起 */}
      {lbOpen && (
        <View style={styles.lbPanel}>
          <Text style={styles.lbTitle}>实时排行</Text>
          {leaderboard.map((r, i) => (
            <View key={r.user_id} style={styles.lbRow}>
              <Text style={styles.lbRank}>{i + 1}</Text>
              <View style={[styles.lbDot, { backgroundColor: playerColors[r.color] || colors.textMuted }]} />
              <Text style={styles.lbName} numberOfLines={1}>{nameOf(r.user_id)}</Text>
              <Text style={styles.lbScore}>{r.score}</Text>
              {r.finished ? <Text style={styles.lbDone}>已结束</Text> : null}
            </View>
          ))}
        </View>
      )}

      {/* 提示条留死高度：消行/被顶的提示不能把棋盘顶下去 */}
      <View style={styles.bannerSlot}>
        {banner ? (
          <View style={styles.banner}><Text style={styles.bannerText}>{banner}</Text></View>
        ) : null}
      </View>

      {/* 街机面板：HUD 与棋盘井合在一块深色体上（二十行的盘面很高，
          HUD 散在浅底上会像一列调试读数，见 theme.ts 的 tetrisPanel 注释） */}
      <View style={styles.panel}>
        <View style={styles.panelRow}>
          <View
            style={[styles.board, { width: boardW, height: boardH }]}
            testID="tetris-board"
            {...pan.panHandlers}
          >
            {displayBoard.map((row, r) => (
              <View key={r} style={styles.boardRow}>
                {row.map((value, c) => {
                  const key = `${r},${c}`;
                  const isGhost = ghostKeys.has(key) && value === 0;
                  const isPiece = pieceKeys.has(key);
                  const filled = cellColor(value);
                  return (
                    <View
                      key={c}
                      testID={`t-c-${r}-${c}`}
                      accessibilityLabel={cellLabel(value)}
                      style={[
                        styles.cell,
                        {
                          width: cell, height: cell,
                          backgroundColor: filled || tetrisSlot,
                          // 空位描一道细格线（tetrisGrid）把 10 列引出来；
                          // 幽灵落点是空心虚线框（位置 + 描边是第二通道，不只靠颜色）
                          borderColor: filled || isPiece
                            ? tetrisEdge
                            : (isGhost ? tetrisGhost : tetrisGrid),
                          borderStyle: isGhost ? 'dashed' : 'solid',
                        },
                      ]}
                    />
                  );
                })}
              </View>
            ))}
          </View>

          {/* HUD 侧栏：四格读数在上、下一块在下（列高与棋盘齐平，两端对齐） */}
          <View style={styles.side}>
            <View style={styles.sideStats}>
              <HudStat label="分数" value={game?.score ?? 0} />
              <HudStat label="行数" value={game?.lines ?? 0} />
              <HudStat label="等级" value={game?.level ?? 1} />
              <HudStat label="被顶" value={game?.garbage_received ?? 0} />
            </View>
            <HoldPreview
              piece={game?.held || null}
              shape={game?.held && rules ? rules.shapes[game.held]?.[0] || [] : []}
              used={!!game?.hold_used}
              cell={Math.max(7, Math.min(11, Math.round(cell * 0.38)))}
              onPress={holdNow}
            />
            <NextPreviews
              pieces={nextPieces}
              shapes={rules?.shapes || {}}
              cell={Math.max(8, Math.min(13, Math.round(cell * 0.42)))}
            />
          </View>
        </View>
      </View>

      {/* 控制区：主要按钮统一 paddingVertical: spacing.sm + 6（≈47px，HIG ≥44pt） */}
      <View style={styles.controls}>
        <TouchableOpacity
          style={styles.ctrlBtn} testID="t-left"
          onPress={() => act('left')} accessibilityLabel="左移"
        >
          <Ionicons name="chevron-back" size={26} color={colors.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.ctrlBtn} testID="t-rotate"
          onPress={() => act('rotate_cw')} accessibilityLabel="旋转"
        >
          <Ionicons name="refresh" size={24} color={colors.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.ctrlBtn} testID="t-down"
          onPress={() => stepAndMaybeLock()} accessibilityLabel="下移"
        >
          <Ionicons name="chevron-down" size={26} color={colors.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.ctrlBtn} testID="t-right"
          onPress={() => act('right')} accessibilityLabel="右移"
        >
          <Ionicons name="chevron-forward" size={26} color={colors.text} />
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.ctrlBtn, styles.ctrlPrimary]} testID="t-drop"
          onPress={() => act('hard_drop')} accessibilityLabel="直接落底"
        >
          <Ionicons name="play-skip-forward" size={22} color={colors.white} />
        </TouchableOpacity>
      </View>

      <TouchableOpacity style={styles.surrenderBtn} onPress={() => setConfirm('surrender')} testID="t-surrender">
        <Text style={styles.surrenderText}>认输</Text>
      </TouchableOpacity>

      {/* 自己已出局、别人还在玩：**必须盖屏**。
          早先这条 bar 挂在页面最底下（认输按钮下面），而 20 行的棋盘很高——
          实测 360×640 上它的按钮 top=657（视口只有 640）、390×844 上 bottom=850，
          页面又没有滚动容器，于是出局后玩家看到的是「盘面冻住、方块不再出现、
          按什么键都没反应」而**看不到任何原因**（用户报的「被顶之后卡住」）。
          出局是必须被看见的状态：盖住整屏 + 说明 + 出口。 */}
      {myFinished && !showGameOver ? (
        <View style={styles.outOverlay} testID="t-out-overlay">
          <View style={styles.outCard}>
            <Text style={styles.outTitle}>
              {othersPlaying > 0 ? '你已结束' : '本局结束'}
            </Text>
            <Text style={styles.outReason}>
              {REASON_LABELS[game?.finish_reason || ''] || '本局已结束'}
              {othersPlaying > 0 ? `\n还有 ${othersPlaying} 位玩家在继续` : ''}
            </Text>
            <TouchableOpacity style={styles.outBtn} onPress={handleBackToRoom} testID="t-back-room">
              <Text style={styles.primaryBtnText}>返回房间</Text>
            </TouchableOpacity>
          </View>
        </View>
      ) : null}

      {/* 结算 */}
      <ModalM visible={showGameOver} onClose={() => {}}>
        <Text style={styles.modalTitle}>本局结束</Text>
        <Text style={styles.modalSub}>
          {ranking.length > 0
            ? `${nameOf(ranking[0].user_id)} 以 ${ranking[0].score} 分获得第一名`
            : REASON_LABELS[game?.reason || ''] || '对局结束'}
        </Text>
        <View style={styles.rankList}>
          {ranking.map((r) => (
            <View key={r.user_id} style={styles.rankRow}>
              <Text style={styles.rankNo}>{r.rank}</Text>
              <View style={[styles.lbDot, { backgroundColor: playerColors[r.color] || colors.textMuted }]} />
              <Text style={styles.rankName} numberOfLines={1}>{nameOf(r.user_id)}</Text>
              <Text style={styles.rankScore}>{r.score} 分 · {r.lines} 行</Text>
            </View>
          ))}
        </View>
        <TouchableOpacity style={styles.primaryBtn} onPress={handleBackToRoom} testID="t-result-back">
          <Text style={styles.primaryBtnText}>返回房间</Text>
        </TouchableOpacity>
      </ModalM>

      {/* 认输 / 退出确认 */}
      <ModalM visible={confirm !== null} onClose={() => setConfirm(null)}>
        <Text style={styles.modalTitle}>{confirm === 'surrender' ? '确认认输？' : '确认离开对局？'}</Text>
        <Text style={styles.modalSub}>
          {confirm === 'surrender'
            ? '认输只结束你这一盘，其他玩家继续。'
            : '离开会结束你这一盘，其他玩家继续。'}
        </Text>
        <View style={styles.modalRow}>
          <TouchableOpacity style={styles.ghostBtn} onPress={() => setConfirm(null)} testID="t-cancel">
            <Text style={styles.ghostBtnText}>继续玩</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.dangerBtn}
            testID="t-confirm"
            onPress={confirm === 'surrender' ? doSurrender : handleLeaveRoom}
          >
            <Text style={styles.primaryBtnText}>{confirm === 'surrender' ? '确认认输' : '确认离开'}</Text>
          </TouchableOpacity>
        </View>
      </ModalM>
    </View>
  );
}

// ── 小部件 ──────────────────────────────────────────────────

/** 面板 HUD 的一格读数（标签小、数值大，中间用竖线分隔——街机计分板的样子）。 */
function HudStat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.hudStat}>
      <Text style={styles.hudLabel}>{label}</Text>
      <Text style={styles.hudValue}>{value}</Text>
    </View>
  );
}

/**
 * 一块方块的 4×4 预览井。
 *
 * 预览自带一小块井底：紫 T / 红 Z / 蓝 J 直接压在面板上只有 2.1~2.3，达不到
 * 图形阈值 3:1（压井底则 ≥4.48，见 theme.ts）。
 */
function PieceWell({ shape, piece, cell }: { shape: number[][]; piece: string | null; cell: number }) {
  const filled = new Set(shape.map(([r, c]) => `${r},${c}`));
  const color = piece ? tetrisPieceColors[piece] : tetrisSlot;
  return (
    <View style={[styles.nextWell, { width: cell * 4 + 8, height: cell * 4 + 8 }]}>
      {[0, 1, 2, 3].map((r) => (
        <View key={r} style={styles.boardRow}>
          {[0, 1, 2, 3].map((c) => (
            <View
              key={c}
              style={{
                width: cell, height: cell,
                backgroundColor: filled.has(`${r},${c}`) ? color : 'transparent',
                borderWidth: filled.has(`${r},${c}`) ? 1 : 0,
                borderColor: tetrisEdge,
              }}
            />
          ))}
        </View>
      ))}
    </View>
  );
}

/**
 * 暂存入口 = 预览框本身（点一下换块），键盘 `C` 也可以。
 *
 * 用的那一块**不置灰到看不见**：标签改成「暂存 · 已用」并压暗一档——禁用态在本
 * 项目等于「点了没反应」，所以仍然可点，点了给一句「这一块已经暂存过了」。
 */
function HoldPreview({ piece, shape, used, cell, onPress }: {
  piece: string | null; shape: number[][]; used: boolean;
  cell: number; onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={[styles.previewBox, used && styles.previewUsed]}
      onPress={onPress}
      testID="t-hold"
      accessibilityLabel={`暂存${piece ? ` ${piece}` : '（空）'}`}
      accessibilityHint="点一下把当前方块暂存，或换出暂存的方块"
    >
      <Text style={[styles.nextLabel, used && styles.previewUsedText]}>
        {used ? '暂存 · 已用' : '暂存'}
      </Text>
      <PieceWell shape={shape} piece={piece} cell={cell} />
    </TouchableOpacity>
  );
}

/** 接下来三块（自上而下）。每块都自带井底，形状与颜色一眼可辨。 */
function NextPreviews({ pieces, shapes, cell }: {
  pieces: string[]; shapes: TetrisRules['shapes']; cell: number;
}) {
  return (
    <View style={styles.previewBox} accessibilityLabel={`接下来 ${pieces.join(' ')}`}>
      <Text style={styles.nextLabel}>接下来</Text>
      {pieces.map((piece, i) => (
        <View key={`${piece}-${i}`} style={i > 0 ? styles.nextSlot : undefined}>
          <PieceWell
            shape={shapes[piece]?.[0] || []}
            piece={piece}
            cell={i === 0 ? cell : Math.max(6, cell - 3)}
          />
        </View>
      ))}
    </View>
  );
}

/** 统一的小弹窗壳（自动接返回手势：visible 时的返回先关弹窗）。 */
function ModalM({ visible, onClose, children }: {
  visible: boolean; onClose: () => void; children: React.ReactNode;
}) {
  useModalBackGuard(visible, onClose);
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalOverlay}>
        <View style={styles.modalBox}>{children}</View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, alignItems: 'center', paddingTop: spacing.sm },
  topBar: {
    width: '100%', flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', paddingHorizontal: spacing.md,
  },
  previewBox: { alignItems: 'center' },
  nextLabel: { fontSize: fontSize.xs, color: tetrisOnPanelMuted, marginBottom: 2 },
  nextWell: {
    backgroundColor: tetrisBoard, borderRadius: radius.sm,
    borderWidth: 1, borderColor: tetrisEdge,
    padding: 4, alignItems: 'center', justifyContent: 'center',
  },
  nextSlot: { marginTop: 4 },
  // 「已用」不是只压暗颜色：标签文字同时改成「暂存 · 已用」（颜色不作为唯一通道）
  previewUsed: { opacity: 0.55 },
  previewUsedText: { color: tetrisOnPanel },

  // 街机面板
  panel: {
    backgroundColor: tetrisPanel,
    borderRadius: radius.lg,
    borderWidth: 2,
    borderColor: tetrisEdge,
    padding: spacing.sm,
    alignItems: 'center',
  },
  panelRow: { flexDirection: 'row', alignItems: 'flex-start' },
  side: {
    width: SIDE_W, marginLeft: spacing.sm, alignSelf: 'stretch',
    justifyContent: 'space-between',
  },
  sideStats: { gap: spacing.sm },
  hudStat: { alignItems: 'center' },
  hudLabel: { fontSize: fontSize.xs, color: tetrisOnPanelMuted },
  hudValue: { fontSize: fontSize.lg, fontWeight: '800', color: tetrisOnPanel },

  lbCapsule: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: spacing.sm + 2, paddingVertical: 6,
    borderRadius: radius.full, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  lbCapsuleText: { fontSize: fontSize.xs, color: colors.textSecondary, fontWeight: '600' },
  lbPanel: {
    position: 'absolute', top: 44, right: spacing.md, zIndex: 20,
    minWidth: 190, backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.border, padding: spacing.sm,
  },
  lbTitle: { fontSize: fontSize.xs, color: colors.textMuted, marginBottom: 4 },
  lbRow: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 3 },
  lbRank: { width: 14, fontSize: fontSize.xs, color: colors.textMuted },
  lbDot: { width: 10, height: 10, borderRadius: 5 },
  lbName: { flex: 1, fontSize: fontSize.sm, color: colors.text },
  lbScore: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text },
  lbDone: { fontSize: fontSize.xs, color: colors.warning },

  // 提示条固定高度：消行/被顶的提示不能把面板顶下去
  bannerSlot: { height: 34, justifyContent: 'center' },
  banner: {
    paddingHorizontal: spacing.md, paddingVertical: 5,
    borderRadius: radius.full, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  bannerText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },

  board: {
    flexDirection: 'column', backgroundColor: tetrisBoard,
    borderWidth: 2, borderColor: tetrisEdge, borderRadius: radius.sm,
    overflow: 'hidden',
  },
  boardRow: { flexDirection: 'row' },
  cell: { borderWidth: 1 },

  controls: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    marginTop: spacing.md,
  },
  ctrlBtn: {
    minWidth: 56, alignItems: 'center', justifyContent: 'center',
    paddingVertical: spacing.sm + 6, paddingHorizontal: spacing.md,
    backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  ctrlPrimary: { backgroundColor: colors.primary, borderColor: colors.primary },

  // 认输是破坏性操作，做成描边幽灵按钮（与 UNO 对局屏一致）：它不该和硬降抢注意力
  surrenderBtn: {
    marginTop: spacing.sm,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.danger,
    backgroundColor: colors.card,
  },
  surrenderText: { fontSize: fontSize.sm, color: colors.danger, fontWeight: '600' },

  // 出局浮层：盖住整屏（棋盘再高也裁不掉它），居中一张卡片说明原因 + 返回房间
  outOverlay: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: colors.overlay,
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  outCard: {
    width: '100%', maxWidth: 320, alignItems: 'center',
    backgroundColor: colors.card, borderRadius: radius.lg, padding: spacing.lg,
  },
  outTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text },
  outReason: {
    fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center',
    marginTop: spacing.xs, marginBottom: spacing.md,
  },
  // 卡片是纵列容器，这里不能用 primaryBtn 的 flex:1（纵向 flexBasis:0 会把它压扁）
  outBtn: {
    alignSelf: 'stretch', alignItems: 'center',
    paddingVertical: spacing.sm + 6,
    backgroundColor: colors.primary, borderRadius: radius.md,
  },

  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay,
    alignItems: 'center', justifyContent: 'center', padding: spacing.lg,
  },
  modalBox: {
    width: '100%', maxWidth: 420, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg,
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center' },
  modalSub: {
    fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center',
    marginTop: spacing.xs, marginBottom: spacing.md,
  },
  modalRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  rankList: { marginBottom: spacing.sm },
  rankRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: 5 },
  rankNo: { width: 18, fontSize: fontSize.sm, fontWeight: '700', color: colors.textMuted },
  rankName: { flex: 1, fontSize: fontSize.md, color: colors.text },
  rankScore: { fontSize: fontSize.sm, color: colors.textSecondary },
  primaryBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm + 6,
    backgroundColor: colors.primary, borderRadius: radius.md,
  },
  primaryBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  ghostBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm + 6,
    backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  ghostBtnText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  dangerBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm + 6,
    backgroundColor: colors.danger, borderRadius: radius.md,
  },
});