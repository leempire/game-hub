// 飞行棋对局屏：标准十字棋盘 + 掷骰 + 棋子逐格动画 + 排名结算
import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  Modal, Platform, ActivityIndicator, Animated, Vibration,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Circle, Rect, Path, Line, Polygon, G } from 'react-native-svg';
import { useAuth } from '../auth';
import { RoomSocket } from '../socket';
import { onConfirmLeave, useModalBackGuard } from '../backGuard';
import { GameHelpButton } from '../help';
import { DiceFace } from '../icons';
import LudoBoard, { PieceAnim, pieceCenters } from './LudoBoardView';
import { colors, spacing, radius, fontSize, ludoColors } from '../theme';
import { notify } from '../utils';
import {
  GRID_UNITS, RING, RING_END, PLAYER_OFFSETS, PIECES_PER_PLAYER,
  progressPath, stepDuration, stackOffset, cellCenter, piecePixel, apronSlotPixel,
  trackColorKey, TRACK_COLOR_ORDER, globalIndex,
} from '../ludoBoard';

// ── Constants ──────────────────────────────────────────
const PLAYER_COLORS = ludoColors;
// 棋盘/棋子的「可见边界」。金色 #FFD700 曾经用作可动高亮，但它在米色棋盘上只有
// 1.31:1、在黄子上 1.41:1——两头都看不见。改成深墨后对四家棋色都 ≥3.1:1。
const PIECE_EDGE = 'rgba(0,0,0,0.6)';  // 静态棋子描边（黄子靠它成形）
const PIECE_ACTIVE = '#111827';   // 可动棋子的深墨环
// 棋盘：基地留白、赛道只涂色不画格线、格心一个奶油圆（标准飞行棋的观感）。
// 数值取自 tmp/飞行棋.md 的视觉规格。
const BOARD_BG = '#F1F1F1';       // 棋盘底 / 基地空地
const CREAM = '#F2ECCE';          // 赛道与航道的格心圆
// 格心圆的深色描边：亮黄格（#F2D024）压在浅灰底上只有 1.35:1，格子的边界
// 全靠这圈描边成形——标准棋盘的奶油圆本来就有细描边。
const CREAM_EDGE = 'rgba(0,0,0,0.38)';
const APRON_EDGE = '#222222';     // 停机坪黑框
// 原生端用原生驱动；RNW 不支持，必须降级（项目既有写法）
const NATIVE_DRIVER = Platform.OS !== 'web';
const CAPTURE_MS = 260;
const BOUNCE_MS = 320;   // 迭子同归于尽：自己飞回基地那一段
const JUMP_MS = 220;   // 同色跳格：一步跨 4 格，比走子快、比飞行短
const FLY_MS = 460;    // 虚线飞行：跨 12 格，给足时间看清是「飞过去」的
const FLY_SCALE = 1.3; // 飞行途中放大一档，与走子区分
// 掷骰动效：点按钮立刻起转（即时反馈），收到服务端点数后再落定。
// 本地后端回包只要几毫秒，不兜一个最短时长的话动画等于没播。
// 时长取值是「看得清」与「别拖沓」的折中：掷 6 可以连掷，一轮 6 + 起飞是
// 两段动画串行，起转时长直接乘以连掷次数，所以压到 420ms 而不是更长。
const DICE_TICK_MS = 80;
const DICE_MIN_MS = 420;
// 落定后再留一拍才放行走子：骰子「弹一下」的动效是 200ms，走子抢在它前面
// 会显得两件事糊在一起
const DICE_SETTLE_GAP_MS = 120;
// 兜底超时：服务端没回 dice_rolled 时别让骰子一直转、闸门一直关
const DICE_TIMEOUT_MS = 2500;
// 骰面描边：落定深墨 / 起转主题色——这既是视觉反馈，也是「动画在转」的
// 可观测信号（E2E 靠它断言），别改成纯装饰性的颜色
const DIE_EDGE = '#111827';

interface Props {
  code: string;
  onLeave: () => void;
}

interface LudoPlayer {
  pieces: number[];
  color: string;
  finished_count: number;
  finished: boolean;
}

interface RankingEntry {
  seat: number;
  username: string;
  nickname: string | null;
  color: string;
  finished_count: number;
  rank: number;
}

// ── Main component ────────────────────────────────────
export default function LudoScreen({ code, onLeave }: Props) {
  const { token } = useAuth();
  const navigation = useNavigation();

  const [gameState, setGameState] = useState<any>(null);
  const [roomPlayers, setRoomPlayers] = useState<any[]>([]);
  const [mySeat, setMySeat] = useState<number>(-1);
  const [movable, setMovable] = useState<number[]>([]);
  const [phase, setPhase] = useState<string>('roll');
  const [showGameOver, setShowGameOver] = useState(false);
  const [ranking, setRanking] = useState<RankingEntry[]>([]);
  const [gameReason, setGameReason] = useState('');
  const [confirmLeave, setConfirmLeave] = useState(false);
  // 认输二次确认（防误触，与其他对局屏一致）
  const [confirmSurrender, setConfirmSurrender] = useState(false);
  const [message, setMessage] = useState('');
  const [lbOpen, setLbOpen] = useState(false);   // 右上角玩家进度浮层

  const socketRef = useRef<RoomSocket | null>(null);
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  const gameOverShownRef = useRef(false);

  // ── 掷骰动效 + 动画闸门 ──────────────────────────────
  // 骰子面在「起转 → 落定」之间不断跳点数，落定时弹一下并震动。
  //
  // **掷骰动画播完之前，回合推进一律不生效**（玩家与人机一视同仁）：
  // 玩家在骰子还在转时就能点飞机走子、人机在骰子还在转时就把棋子飞出去，
  // 都会让人看不懂「刚才那步是哪来的」。所以：
  //   - `dice_rolled` 只存起来（`pendingRollsRef`），落定后才应用（movable/phase/座位）
  //   - `piece_moved` 落定前一律排队（`pendingMovesRef`），落定后按序补播
  const [dieFace, setDieFace] = useState<number | null>(null);
  const [dieRolling, setDieRolling] = useState(false);
  // 闸门状态也要有 state 版本：`dieBusyRef` 给 socket 回调同步读，
  // 按钮禁用要靠 state 才会重渲染（否则落定到放行之间那 180ms 按钮是亮的，
  // 玩家一点就被服务端顶回「请先移动飞机」）
  const [dieBusy, setDieBusy] = useState(false);
  const dieTickRef = useRef<any>(null);
  const dieSettleRef = useRef<any>(null);
  const dieDrainRef = useRef<any>(null);
  const dieWatchdogRef = useRef<any>(null);
  const dieStartRef = useRef(0);
  const diePopRef = useRef(new Animated.Value(1)).current;
  const dieBusyRef = useRef(false);
  const pendingRollsRef = useRef<any[]>([]);
  const pendingMovesRef = useRef<any[]>([]);
  // 落定后要做的事（应用积压的掷骰/走子）。由 socket effect 注册——骰子动效
  // 不必知道 piece_moved 的细节，也不用把这些回调互相依赖成一团。
  const onDieSettledRef = useRef<(() => void) | null>(null);

  /** 停表并放行闸门（落定、被拒、超时三条路都走这里）。 */
  const endDieRoll = useCallback(() => {
    if (dieTickRef.current) {
      clearInterval(dieTickRef.current);
      dieTickRef.current = null;
    }
    if (dieWatchdogRef.current) {
      clearTimeout(dieWatchdogRef.current);
      dieWatchdogRef.current = null;
    }
    dieBusyRef.current = false;
    setDieBusy(false);
    setDieRolling(false);
  }, []);

  /** 起转（已在转则不动，避免把最短时长重置掉）。 */
  const beginDieRoll = useCallback(() => {
    if (dieTickRef.current) return;
    dieStartRef.current = Date.now();
    dieBusyRef.current = true;
    setDieBusy(true);
    setDieRolling(true);
    dieTickRef.current = setInterval(() => {
      setDieFace(1 + Math.floor(Math.random() * 6));
    }, DICE_TICK_MS);
    // 兜底：起转是乐观的，服务端要是没回 `dice_rolled`（走子被拒、断线），
    // 骰子会一直转、闸门一直关，整个界面就卡死了
    dieWatchdogRef.current = setTimeout(() => {
      dieWatchdogRef.current = null;
      endDieRoll();
      onDieSettledRef.current?.();
    }, DICE_TIMEOUT_MS);
  }, [endDieRoll]);

  /** 落定到服务端的真实点数；起转不满 DICE_MIN_MS 就再等一会儿。 */
  const settleDie = useCallback((value: number) => {
    if (dieSettleRef.current) {
      clearTimeout(dieSettleRef.current);
      dieSettleRef.current = null;
    }
    beginDieRoll();
    const wait = Math.max(0, DICE_MIN_MS - (Date.now() - dieStartRef.current));
    dieSettleRef.current = setTimeout(() => {
      if (dieWatchdogRef.current) {
        clearTimeout(dieWatchdogRef.current);
        dieWatchdogRef.current = null;
      }
      // **必须 clearInterval**，不能只把 ref 置空：置空只是让 endDieRoll 找不到它，
      // 定时器还在后台每 80ms 把骰面改成随机点数——骰子落定后继续乱跳，
      // 玩家永远看不到真实点数（v1.6 起就有的 bug，E2E 只读了描边与点数范围，
      // 没断言「落定后点数不再变」，所以一直没抓到）
      if (dieTickRef.current) {
        clearInterval(dieTickRef.current);
        dieTickRef.current = null;
      }
      dieSettleRef.current = null;
      setDieRolling(false);
      // 注意：这里**不能**放开 dieBusyRef——闸门要等下面那一拍补完才开
      setDieFace(value);
      if (Platform.OS !== 'web') Vibration.vibrate(15);
      Animated.sequence([
        Animated.timing(diePopRef, { toValue: 1.18, duration: 90, useNativeDriver: NATIVE_DRIVER }),
        Animated.timing(diePopRef, { toValue: 1, duration: 110, useNativeDriver: NATIVE_DRIVER }),
      ]).start();
      // 先让骰子把「弹一下」播完，再放行积压的走子——否则人机的棋子在骰子
      // 还没停稳时就飞出去了
      dieDrainRef.current = setTimeout(() => {
        dieDrainRef.current = null;
        dieBusyRef.current = false;
        setDieBusy(false);
        onDieSettledRef.current?.();
      }, DICE_SETTLE_GAP_MS);
    }, wait);
  }, [beginDieRoll, diePopRef]);

  useEffect(() => () => {
    if (dieTickRef.current) clearInterval(dieTickRef.current);
    if (dieSettleRef.current) clearTimeout(dieSettleRef.current);
    if (dieDrainRef.current) clearTimeout(dieDrainRef.current);
    if (dieWatchdogRef.current) clearTimeout(dieWatchdogRef.current);
  }, []);

  // ── 棋子动画 ────────────────────────────────────────
  // 位置只由 Animated.Value 决定，**不由 gameState 推导**——否则动画途中来一次
  // setGameState 会把正在飞的棋子拽走。gameState 只负责颜色/可动/计数/回合。
  const [edge, setEdge] = useState(352);   // 棋盘边长（px）；内圈格 = edge / 16
  const edgeRef = useRef(352);
  edgeRef.current = edge;
  const animsRef = useRef<Map<string, PieceAnim>>(new Map());
  const gameStateRef = useRef<any>(null);
  gameStateRef.current = gameState;

  const getAnim = useCallback((seat: number, pi: number): PieceAnim => {
    const key = `${seat}-${pi}`;
    let a = animsRef.current.get(key);
    if (!a) {
      a = { x: new Animated.Value(0), y: new Animated.Value(0), scale: new Animated.Value(1) };
      animsRef.current.set(key, a);
    }
    return a;
  }, []);

  /** 把全部棋子直接摆到逻辑位置（不播动画）：sync / game_start / 棋盘尺寸变化。 */
  const snapAllPieces = useCallback((gs: any, c: number) => {
    const players = gs?.players;
    if (!players) return;
    Object.keys(players).forEach((seatStr) => {
      const seat = parseInt(seatStr, 10);
      const pieces: number[] = players[seatStr]?.pieces || [];
      const centers = pieceCenters(seat, pieces, c);
      pieces.forEach((_prog, pi) => {
        const a = getAnim(seat, pi);
        a.x.setValue(centers[pi][0]);
        a.y.setValue(centers[pi][1]);
        a.scale.setValue(1);
      });
    });
  }, [getAnim]);

  /**
   * 沿路径逐段飞过去（走 / 同色跳格 / 虚线飞行）；到位后回调，用于撞机与重新叠子。
   *
   * 路径与分段**由服务端给**（`piece_moved` 的 `path` + `segments`）：跳格与虚线
   * 飞行的形状取决于落点（跳一次 + 飞一次、顺序还看是不是跳上虚线格的），客户端
   * 按 `old+dice` 自己推会画错。`segments` 缺失时退回「逐格走」的兜底推算。
   */
  const flyTo = useCallback((
    seat: number, pi: number, from: number, to: number, dice: number, c: number,
    path: number[] | undefined, segments: any[] | undefined,
    onDone?: () => void,
  ) => {
    // c = 棋盘边长（px）
    const a = getAnim(seat, pi);
    const hops = path && path.length > 0 ? path : progressPath(from, to, dice);
    const segs = segments || [];
    const kindOf = (a1: number, b1: number): string => {
      const hit = segs.find((s) => s.from === a1 && s.to === b1);
      return hit ? hit.kind : 'walk';
    };
    const walkDur = stepDuration(hops.filter((p, i) => kindOf(i === 0 ? from : hops[i - 1], p) === 'walk').length);

    let prev = from;
    const steps = hops.map((prog) => {
      const kind = kindOf(prev, prog);
      prev = prog;
      const [tx, ty] = piecePixel(seat, pi, prog, c);
      const dur = kind === 'fly' ? FLY_MS : kind === 'jump' ? JUMP_MS
        : kind === 'bounce' ? BOUNCE_MS : walkDur;
      const move = [
        Animated.timing(a.x, { toValue: tx, duration: dur, useNativeDriver: NATIVE_DRIVER }),
        Animated.timing(a.y, { toValue: ty, duration: dur, useNativeDriver: NATIVE_DRIVER }),
      ];
      if (kind === 'fly') {
        // 飞行时放大一档再落回——「飞过去」和「走过去」要能一眼分开
        move.push(Animated.sequence([
          Animated.timing(a.scale, { toValue: FLY_SCALE, duration: dur / 2, useNativeDriver: NATIVE_DRIVER }),
          Animated.timing(a.scale, { toValue: 1, duration: dur / 2, useNativeDriver: NATIVE_DRIVER }),
        ]));
      }
      return Animated.parallel(move);
    });
    Animated.sequence(steps).start(({ finished }) => { if (finished) onDone?.(); });
  }, [getAnim]);

  // 棋盘尺寸变化时重新贴合（不做补间，避免滑行中改尺寸错位）
  useEffect(() => { snapAllPieces(gameStateRef.current, edge); }, [edge, snapAllPieces]);

  // ── Socket setup ────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    ws.on('sync', (msg: any) => {
      // 整盘重建，动画闸门里积压的旧消息已过时（重连场景）
      pendingRollsRef.current = [];
      pendingMovesRef.current = [];
      dieBusyRef.current = false;
      setDieBusy(false);
      setRoomPlayers(msg.room?.players || []);
      if (msg.game) {
        setGameState(msg.game);
        setPhase(msg.game.phase);
        setMovable(msg.game.movable_pieces || []);
        setDieFace(msg.game.dice_value);   // sync 恢复上一把的点数，别显示空骰面
        snapAllPieces(msg.game, edgeRef.current);
        // 服务端逐人下发我的座位号（seat = room.players 索引）
        if (typeof msg.game.your_seat === 'number') setMySeat(msg.game.your_seat);
        if (msg.game.finished && msg.game.ranking) {
          gameOverRef.current = true;
          if (!gameOverShownRef.current) {
            gameOverShownRef.current = true;
            setRanking(msg.game.ranking);
            setGameReason(msg.game.reason || '');
            setShowGameOver(true);
          }
        }
      }
    });

    ws.on('lobby', (msg: any) => {
      setRoomPlayers(msg.room?.players || []);
    });

    ws.on('game_start', (msg: any) => {
      pendingRollsRef.current = [];
      pendingMovesRef.current = [];
      dieBusyRef.current = false;
      setDieBusy(false);
      if (msg.game) {
        setGameState(msg.game);
        setPhase(msg.game.phase);
        setMovable(msg.game.movable_pieces || []);
        setDieFace(null);
        snapAllPieces(msg.game, edgeRef.current);
        if (typeof msg.game.your_seat === 'number') setMySeat(msg.game.your_seat);
      }
    });

    /** 掷骰结果的**生效**（不是收到）：落定后才调，见「动画闸门」。 */
    const applyDiceRolled = (msg: any) => {
      setMovable(msg.movable || []);
      if (msg.penalty) {
        setMessage('连续三次掷6！所有飞机回基地');
        setTimeout(() => setMessage(''), 3000);
        // 服务端已把该玩家所有飞机打回基地并附带最新位置；不写回的话
        // 客户端棋盘上飞机还留在原地，跟服务端彻底脱节
        if (msg.pieces && typeof msg.seat === 'number') {
          const seat = msg.seat;
          setGameState((prev: any) => {
            if (!prev?.players?.[String(seat)]) return prev;
            return {
              ...prev,
              players: {
                ...prev.players,
                [String(seat)]: { ...prev.players[String(seat)], pieces: msg.pieces },
              },
            };
          });
          msg.pieces.forEach((prog: number, pi: number) => {
            const a = getAnim(seat, pi);
            const [px2, py2] = piecePixel(seat, pi, prog, edgeRef.current);
            a.x.setValue(px2);
            a.y.setValue(py2);
          });
        }
      }
      if (msg.movable?.length > 0) {
        setPhase('move');
      }
      // 无可动飞机时服务端会把回合直接移交，必须同步座位，否则界面停在
      // 「点击骰子掷骰」而实际上已经轮到别人
      if (typeof msg.current_seat === 'number') {
        setGameState((prev: any) => (
          prev ? { ...prev, current_seat: msg.current_seat } : prev
        ));
      }
    };

    /** 落定后统一补播：先应用掷骰结果（movable/phase/座位），再按序放走子。 */
    onDieSettledRef.current = () => {
      const rolls = pendingRollsRef.current;
      pendingRollsRef.current = [];
      rolls.forEach(applyDiceRolled);
      const moves = pendingMovesRef.current;
      pendingMovesRef.current = [];
      moves.forEach(applyPieceMoved);
    };

    ws.on('dice_rolled', (msg: any) => {
      // 起转/延长动画；点数本身要等落定才生效（movable 早生效的话，
      // 玩家能在骰子还在转的时候就把飞机点走）
      settleDie(msg.dice);
      pendingRollsRef.current.push(msg);
    });

    ws.on('piece_moved', (msg: any) => {
      if (dieBusyRef.current) {
        // 掷骰动画还没播完（人机的「掷完立刻走」几乎和掷骰同帧到达）→ 排队
        pendingMovesRef.current.push(msg);
        return;
      }
      applyPieceMoved(msg);
    });

    /** 走子结果的**生效**：落定后（或不在掷骰动画中时）才调。 */
    function applyPieceMoved(msg: any) {
      setGameState((prev: any) => {
        if (!prev) return prev;
        const next = { ...prev };
        const player = { ...next.players[String(msg.seat)] };
        const pieces = [...player.pieces];
        pieces[msg.piece_id] = msg.new_progress;
        player.pieces = pieces;
        if (msg.finished) {
          player.finished_count = (player.finished_count || 0) + 1;
          if (player.finished_count >= PIECES_PER_PLAYER) player.finished = true;
        }
        next.players = { ...next.players, [String(msg.seat)]: player };
        // Handle captures
        if (msg.captures?.length > 0) {
          msg.captures.forEach((cap: any) => {
            const capPlayer = { ...next.players[String(cap.seat)] };
            const capPieces = [...capPlayer.pieces];
            capPieces[cap.piece_id] = -1;
            capPlayer.pieces = capPieces;
            next.players = { ...next.players, [String(cap.seat)]: capPlayer };
          });
        }
        next.current_seat = msg.current_seat;
        next.phase = 'roll';
        return next;
      });
      setPhase('roll');
      setMovable([]);

      // 逐格飞过去（真人、人机走的是同一条广播，动画一视同仁）。
      // old_progress 是服务端给的走子前位置；path/segments 是服务端算好的
      // 「走 + 跳格 + 虚线飞行」完整路径——跳/飞的形状只有服务端知道。
      const c = edgeRef.current;
      const captured: any[] = msg.captures || [];
      flyTo(
        msg.seat, msg.piece_id, msg.old_progress, msg.new_progress, msg.dice, c,
        msg.path, msg.segments,
        () => {
          // 撞机：被撞的飞机从原格飞回基地
          captured.forEach((cap) => {
            const a = getAnim(cap.seat, cap.piece_id);
            const [bx, by] = apronSlotPixel(cap.seat, cap.piece_id, c);
            Animated.parallel([
              Animated.timing(a.x, { toValue: bx, duration: CAPTURE_MS, useNativeDriver: NATIVE_DRIVER }),
              Animated.timing(a.y, { toValue: by, duration: CAPTURE_MS, useNativeDriver: NATIVE_DRIVER }),
            ]).start();
          });
          // 落定后按最终逻辑位置重排（同格叠子要错位，否则 4 架叠成一架）
          setTimeout(() => snapAllPieces(gameStateRef.current, edgeRef.current), CAPTURE_MS + 40);
        },
      );

      // 跳格 / 虚线飞行要说出来——飞机突然跨 12 格，不解释会以为界面出错了
      const segs: any[] = msg.segments || [];
      const flew = segs.some((s) => s.kind === 'fly');
      const jumped = segs.some((s) => s.kind === 'jump');
      const mutual = segs.some((s) => s.kind === 'bounce');
      if (mutual) {
        // 迭子同归于尽：撞掉一整叠，但自己也回基地——不说清楚玩家会以为是 bug
        setMessage(`撞上迭子，双方 ${captured.length} 架一起回基地`);
      } else if (captured.length > 0) {
        setMessage(`击落 ${captured.length} 架敌机！`);
      } else if (flew) {
        setMessage('沿虚线飞行！');
      } else if (jumped) {
        setMessage('同色跳格！');
      } else if (msg.finished) {
        setMessage('一架飞机到达终点！');
      }
      if (mutual || captured.length > 0 || flew || jumped || msg.finished) {
        setTimeout(() => setMessage(''), 2400);
      }
    }

    ws.on('player_surrendered', (msg: any) => {
      setMessage(`${msg.message || '玩家弃权'}`);
      setTimeout(() => setMessage(''), 3000);
      // 服务端已把弃权者的飞机清回基地，客户端要跟着清，否则残留在棋盘上
      if (msg.pieces && typeof msg.seat === 'number') {
        const seat = msg.seat;
        setGameState((prev: any) => {
          if (!prev?.players?.[String(seat)]) return prev;
          return {
            ...prev,
            players: {
              ...prev.players,
              [String(seat)]: { ...prev.players[String(seat)], pieces: msg.pieces },
            },
          };
        });
        // 直接按新位置摆（此刻 gameStateRef 还是旧值，不能走 snapAllPieces）
        msg.pieces.forEach((prog: number, pi: number) => {
          const a = getAnim(seat, pi);
          const [px2, py2] = piecePixel(seat, pi, prog, edgeRef.current);
          a.x.setValue(px2);
          a.y.setValue(py2);
        });
      }
    });

    ws.on('player_eliminated', (msg: any) => {
      setMessage(msg.message || '玩家离开游戏');
      setTimeout(() => setMessage(''), 3000);
    });

    ws.on('game_over', (msg: any) => {
      gameOverRef.current = true;
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setRanking(msg.ranking || []);
        setGameReason(msg.reason || '');
        setShowGameOver(true);
      }
    });

    ws.on('error', (msg: any) => {
      notify('错误', msg.message || '未知错误');
    });

    ws.on('player_disconnected', () => {
      setMessage('有玩家断开连接');
      setTimeout(() => setMessage(''), 3000);
    });

    ws.on('player_reconnected', () => {
      setMessage('玩家已重连');
      setTimeout(() => setMessage(''), 2000);
    });

    ws.connect();
    return () => {
      ws.close();
      // 别让上一轮的闭包在 effect 重建后还被骰子落定回调捞起来
      onDieSettledRef.current = null;
    };
  }, [code, token, snapAllPieces, flyTo, getAnim, settleDie]);

  // ── Back guard (dual path) ──────────────────────────
  useEffect(() => {
    const unsub = navigation.addListener('beforeRemove', (e: any) => {
      if (Platform.OS === 'web') return;
      if (skipRemoveRef.current) return;
      if (gameOverRef.current) {
        skipRemoveRef.current = true;
        navigation.dispatch(e.data.action);
        return;
      }
      e.preventDefault();
      setConfirmLeave(true);
    });
    return unsub;
  }, [navigation]);

  useEffect(() => {
    return onConfirmLeave(() => {
      if (gameOverRef.current) {
        skipRemoveRef.current = true;
        navigation.goBack();
        return;
      }
      setConfirmLeave(true);
    });
  }, [navigation]);

  // ── Derived state ───────────────────────────────────
  const isMyTurn = mySeat >= 0 && gameState?.current_seat === mySeat;
  const myColor = gameState?.players?.[String(mySeat)]?.color;
  const myFinished = gameState?.players?.[String(mySeat)]?.finished_count || 0;
  // 掷骰要同时满足「轮到我」「处于掷骰阶段」「动画闸门已放行」
  const canRoll = isMyTurn && phase === 'roll' && !dieBusy;
  // 危险格：对手任一飞机在它身后 1~6 步内 → 掷任意点数都可能正好踩上来。
  // 只在自己回合显示（别人回合标红没有决策价值，只会让棋盘更花）。
  // 与引擎 _threat_count 同口径：对手走到赛道尽头就拐进自己的航道，不再撞人。
  const dangerCells = React.useMemo(() => {
    const out = new Set<number>();
    if (!isMyTurn || !gameState?.players) return out;
    Object.keys(gameState.players).forEach((seatStr) => {
      const seat = parseInt(seatStr, 10);
      if (seat === mySeat) return;
      const pieces: number[] = gameState.players[seatStr]?.pieces || [];
      pieces.forEach((prog) => {
        if (prog < 0 || prog > RING_END) return;   // 基地/航道里踩不到
        const g = globalIndex(seat, prog);
        for (let k = 1; k <= 6 && prog + k <= RING_END; k++) out.add((g + k) % RING);
      });
    });
    return out;
  }, [gameState, isMyTurn, mySeat]);
  const playerList = roomPlayers.map((p: any, idx: number) => ({
    ...p,
    seat: idx,
    color: gameState?.players?.[String(idx)]?.color || '',
    finishedCount: gameState?.players?.[String(idx)]?.finished_count || 0,
    isFinished: gameState?.players?.[String(idx)]?.finished || false,
  }));

  // ── Actions ─────────────────────────────────────────
  const handleRoll = useCallback(() => {
    if (!canRoll) return;
    // 乐观起转：不等服务端回包，按下就有反馈（<100ms）。
    // 闸门（dieBusy）同时挡住重复点击——起转期间按钮是禁用的
    beginDieRoll();
    socketRef.current?.send({ type: 'roll' });
  }, [canRoll, beginDieRoll]);

  const handleMovePiece = useCallback((pieceId: number) => {
    if (phase !== 'move') return;
    if (!movable.includes(pieceId)) return;
    socketRef.current?.send({ type: 'move_piece', piece_id: pieceId });
  }, [phase, movable]);

  const handleSurrender = useCallback(() => {
    setConfirmSurrender(true);
  }, []);

  const doSurrender = useCallback(() => {
    setConfirmSurrender(false);
    socketRef.current?.send({ type: 'surrender' });
  }, []);

  // 弹窗打开时返回键先关弹窗（项目规范：新弹窗一律接 useModalBackGuard）
  useModalBackGuard(confirmLeave, () => setConfirmLeave(false));
  useModalBackGuard(confirmSurrender, () => setConfirmSurrender(false));

  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);


  if (!gameState && roomPlayers.length === 0) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={styles.loadingText}>加载中...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Board：格子尺寸按可用空间推导，小屏也能整盘放下 */}
      <View
        style={styles.boardContainer}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          const avail = Math.min(width, height) - 2 * spacing.sm;
          setEdge(Math.max(224, Math.floor(avail)));
        }}
      >
        <LudoBoard
          gameState={gameState}
          movable={isMyTurn && phase === 'move' ? movable : []}
          dangerCells={dangerCells}
          onPiecePress={handleMovePiece}
          edge={edge}
          anims={animsRef.current}
          mySeat={mySeat}
        />

        {/* 玩家进度：右上角半透明可收起浮层。
            原来是一整条横排玩家条，4 人局（昵称 + 分数 + 颜色点）在 390px 宽上
            必然挤压换行；改成浮层后不占布局宽度，棋盘也能拿到全部高度。
            浮层是纯展示，pointerEvents 交给子节点自己声明。 */}
        <View style={styles.lbFloat} pointerEvents="box-none">
          <TouchableOpacity
            testID="ludo-lb-toggle"
            style={styles.lbToggle}
            activeOpacity={0.8}
            onPress={() => setLbOpen((v) => !v)}
          >
            <Ionicons name="podium-outline" size={14} color={colors.primary} />
            <View style={[styles.colorDot, { backgroundColor: PLAYER_COLORS[myColor] || '#999' }]} />
            <Text style={styles.lbToggleText}>我 {myFinished}/4</Text>
            <Ionicons
              name={lbOpen ? 'chevron-up' : 'chevron-down'}
              size={14}
              color={colors.primary}
            />
          </TouchableOpacity>

          {lbOpen && (
            <View style={styles.lbPanel} testID="ludo-leaderboard">
              <View style={styles.lbHeaderRow}>
                <Text style={styles.lbTitle}>玩家进度</Text>
                <Text style={styles.lbMine}>{playerList.length} 人局</Text>
              </View>
              {playerList.map((p: any) => {
                const isMe = p.seat === mySeat;
                const isTurn = p.seat === gameState?.current_seat;
                return (
                  <View
                    key={p.seat}
                    testID={`ludo-lb-${p.seat}`}
                    style={[styles.lbRow, isMe && styles.lbRowMe, isTurn && styles.lbRowTurn]}
                  >
                    <View style={[styles.colorDot, { backgroundColor: PLAYER_COLORS[p.color] || '#999' }]} />
                    <Text
                      style={[styles.lbName, (isMe || isTurn) && styles.lbNameStrong]}
                      numberOfLines={1}
                    >
                      {p.nickname || p.username}{isMe ? '（我）' : ''}
                    </Text>
                    {/* 「轮到谁」不能只靠底色——加一个文字标记，色觉障碍用户也读得出 */}
                    {isTurn && <Text style={styles.lbTurn}>当前</Text>}
                    <Text style={[styles.lbScore, isMe && styles.lbScoreMe]}>{p.finishedCount}/4</Text>
                  </View>
                );
              })}
            </View>
          )}
        </View>
      </View>

      {/* Controls */}
      <View style={styles.controls}>
        <View style={styles.infoRow}>
          {/* 骰子面：起转时不断跳点数、落定时弹一下。始终可见——上一把掷了几点
              是接着决策要用的信息，不能只在轮到自己时才显示。 */}
          <Animated.View
            testID="ludo-die"
            style={[styles.dieWrap, { transform: [{ scale: diePopRef }] }]}
          >
            <DiceFace value={dieFace} size={30} border={dieRolling ? colors.primary : DIE_EDGE} />
          </Animated.View>
          {message ? (
            <Text style={styles.message}>{message}</Text>
          ) : (
            <Text style={styles.turnInfo}>
              {isMyTurn
                ? phase === 'roll' ? '点击骰子掷骰' : '点击飞机移动'
                : `等待 ${playerList[gameState?.current_seat]?.nickname || playerList[gameState?.current_seat]?.username || ''} ${playerList[gameState?.current_seat]?.is_bot ? '思考中…' : '操作'}`}
            </Text>
          )}
        </View>
        <View style={styles.btnRow}>
          {/* 掷骰按钮**常驻**，轮不到自己时置灰不可点——隐藏会让按钮在 4 人局里
              反复出现/消失，拇指位置跟着跳；置灰则位置固定，也一直提示「这里能掷骰」 */}
          <TouchableOpacity
            testID="ludo-roll"
            style={[styles.diceBtn, !canRoll && styles.diceBtnIdle]}
            onPress={handleRoll}
            disabled={!canRoll}
            activeOpacity={0.7}
          >
            <Text style={[styles.diceBtnText, !canRoll && styles.diceBtnIdleText]}>掷骰子</Text>
          </TouchableOpacity>
          {!gameOverRef.current && (
            <TouchableOpacity style={styles.surrenderBtn} onPress={handleSurrender}>
              <Text style={styles.surrenderText}>认输</Text>
            </TouchableOpacity>
          )}
          <GameHelpButton game="ludo" size={32} />
        </View>
      </View>

      {/* Leave confirmation modal */}
      <Modal visible={confirmLeave} transparent animationType="fade" onRequestClose={() => setConfirmLeave(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>确认离开？</Text>
            <Text style={styles.modalBody}>对局进行中离开将视为弃权</Text>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnDanger]}
              onPress={() => {
                setConfirmLeave(false);
                skipRemoveRef.current = true;
                socketRef.current?.send({ type: 'surrender' });
                socketRef.current?.close();
                navigation.goBack();
              }}
            >
              <Text style={styles.modalBtnDangerText}>确认离开</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirmLeave(false)}
            >
              <Text style={styles.modalBtnSecondaryText}>继续对局</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 认输二次确认弹窗 */}
      <Modal visible={confirmSurrender} transparent animationType="fade" onRequestClose={() => setConfirmSurrender(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>确认认输？</Text>
            <Text style={styles.modalBody}>认输后本局弃权，排名垫底</Text>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnDanger]}
              onPress={doSurrender}
            >
              <Text style={styles.modalBtnDangerText}>确认认输</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirmSurrender(false)}
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
            <Text style={styles.modalReason}>
              {gameReason === 'all_finished' ? '全部完成' : gameReason === 'last_remaining' ? '排名确定' : gameReason}
            </Text>
            <ScrollView style={styles.rankingScroll} nestedScrollEnabled>
              {ranking.map((r) => (
                <View key={r.seat} style={[styles.rankingRow, r.rank === 1 && styles.rankingFirst]}>
                  <Text style={styles.rankNum}>#{r.rank}</Text>
                  <View style={[styles.colorDot, { backgroundColor: PLAYER_COLORS[r.color] || '#999' }]} />
                  <Text style={styles.rankingName}>{r.nickname || r.username}</Text>
                  <Text style={styles.rankingScore}>{r.finished_count}/4</Text>
                </View>
              ))}
            </ScrollView>
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

// ── Styles ────────────────────────────────────────────
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  loadingText: { marginTop: spacing.md, color: colors.textSecondary, fontSize: fontSize.md },
  colorDot: { width: 10, height: 10, borderRadius: 5, marginRight: 4 },
  boardContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.sm },
  // 玩家进度浮层：贴棋盘右上角，不占布局宽度（4 人局横排玩家条在 390px 上会挤压换行）
  lbFloat: { position: 'absolute', top: spacing.sm, right: spacing.sm, alignItems: 'flex-end' },
  lbToggle: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingHorizontal: spacing.sm, paddingVertical: 5,
    borderRadius: radius.full, backgroundColor: 'rgba(255,255,255,0.88)',
    borderWidth: 1, borderColor: 'rgba(79,70,229,0.25)',
  },
  lbToggleText: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  lbPanel: {
    marginTop: spacing.xs, minWidth: 168, maxWidth: 240,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    borderRadius: radius.md, backgroundColor: 'rgba(255,255,255,0.94)',
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  lbHeaderRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginBottom: spacing.xs,
  },
  lbTitle: { fontSize: fontSize.xs, fontWeight: '700', color: colors.textSecondary },
  lbMine: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  lbRow: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    paddingVertical: 2, paddingHorizontal: spacing.xs, borderRadius: radius.sm,
  },
  lbRowMe: { backgroundColor: 'rgba(79,70,229,0.10)' },
  lbRowTurn: { borderWidth: 1, borderColor: colors.primary },
  lbName: { flex: 1, fontSize: fontSize.xs, color: colors.text },
  lbNameStrong: { fontWeight: '700' },
  lbTurn: { fontSize: fontSize.xs, fontWeight: '700', color: colors.primary },
  lbScore: { minWidth: 26, textAlign: 'right', fontSize: fontSize.xs, fontWeight: '700', color: colors.text },
  lbScoreMe: { color: colors.primary },
  controls: {
    padding: spacing.sm, backgroundColor: colors.card,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  infoRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.sm, marginBottom: spacing.xs,
  },
  dieWrap: { width: 30, height: 30 },
  turnInfo: { fontSize: fontSize.sm, color: colors.textSecondary, flexShrink: 1 },
  message: { fontSize: fontSize.sm, color: colors.warning, fontWeight: '700', flexShrink: 1 },
  btnRow: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: spacing.sm },
  diceBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.md,
    backgroundColor: colors.primary, borderRadius: radius.md,
  },
  diceBtnDisabled: { opacity: 0.5 },
  // 轮不到自己：置灰但**不隐藏**——位置固定，拇指不用找
  diceBtnIdle: { backgroundColor: colors.border },
  diceBtnIdleText: { color: colors.textMuted },
  diceBtnText: { color: '#FFF', fontSize: fontSize.md, fontWeight: '700' },
  surrenderBtn: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
    borderRadius: radius.sm, borderWidth: 1, borderColor: colors.danger,
  },
  surrenderText: { color: colors.danger, fontSize: fontSize.sm },
  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center', alignItems: 'center', padding: spacing.xl,
  },
  modalCard: {
    backgroundColor: colors.card, borderRadius: radius.lg,
    padding: spacing.lg, width: '100%', maxWidth: 360, alignItems: 'center',
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  modalBody: { fontSize: fontSize.sm, color: colors.textSecondary, marginBottom: spacing.md, textAlign: 'center' },
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.md },
  rankingScroll: { maxHeight: 200, width: '100%', marginBottom: spacing.md },
  rankingRow: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: 6,
    paddingHorizontal: spacing.sm, borderRadius: radius.sm,
  },
  rankingFirst: { backgroundColor: 'rgba(79,70,229,0.08)' },
  rankNum: { width: 30, fontSize: fontSize.sm, fontWeight: '700', color: colors.textSecondary },
  rankingName: { flex: 1, fontSize: fontSize.sm, color: colors.text },
  rankingScore: { fontSize: fontSize.sm, fontWeight: '700', color: colors.primary },
  modalBtn: { width: '100%', paddingVertical: spacing.sm, borderRadius: radius.md, alignItems: 'center', marginTop: spacing.sm },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: '#FFF', fontSize: fontSize.md, fontWeight: '700' },
  modalBtnDanger: { backgroundColor: colors.danger },
  modalBtnDangerText: { color: '#FFF', fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { borderWidth: 1, borderColor: colors.borderStrong },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md },
});
