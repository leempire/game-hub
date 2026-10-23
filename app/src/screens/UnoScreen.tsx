// UNO 对局屏（2-4 人回合制牌类）。
//
// 骨架照 TetrisScreen（同为「私有数据逐人下发」），回合判定照 LudoScreen / XiangqiScreen。
//
// **客户端一格规则都不实现**：
// - 哪些牌能出 → 服务端随状态下发 `playable`，客户端只做高亮（`XiangqiScreen`
//   用 `legal` 做高亮是同一套做法）。
// - 「我现在能做什么」→ `can_draw` / `can_pass` / `can_call_uno` 三个旗标。
// 象棋的合法着法、飞行棋的走子路径、俄罗斯方块的方块形状都是这条教训：
// 多一份实现必然与服务端漂移。
//
// **手牌私有**：`hand` 只包含本人的牌，别人的只有 `hand_counts`。所以屏上永远
// 不存在「对手的牌面」这种数据——不是靠渲染时藏起来，而是压根没收到。
//
// 视觉（v1.9 联网调研后重做）：深色毡面牌桌 + 对手座位扇形牌背 + 中央弃牌堆
// 套一圈**当前颜色的实心环** + 自己手牌扇形重叠（可出的牌抬高 + 双环）。
// 设计依据见 docs/游戏设计/UNO/设计.md「界面构成」与「交互设计」两节。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, ScrollView, Platform, Animated, Easing,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize, playerColors,
  unoColors, unoTable, unoOnTable, unoOnTableMuted, unoSeatColors, unoSeatActiveBg,
  unoInk,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { GameHelpButton } from '../help';
import { useDisconnectCountdown } from '../disconnect';
import { UnoCardView, UnoHandBacks, UNO_CARD_H, UNO_CARD_W } from '../UnoCardView';
import { UnoColorBadge } from '../icons';
import { UNO_COLOR_KEYS, UNO_COLOR_NAMES, cardLabel, cardKind } from '../uno';

/** 扇形手牌：相邻牌重叠这么多像素（露出 `UNO_CARD_W - OVERLAP`）。 */
const OVERLAP = 22;
/** 扇形每侧最大倾角（度）。再大就有牌会转出可读范围。 */
const TILT = 5;
/** 按下时在可出抬高的基础上再抬这么多。 */
const PRESS_LIFT = 10;
/** 弃牌堆的尺寸（比手牌大一档：它是全桌的视觉焦点）。 */
const PILE_W = 88;
const PILE_H = 132;

/** 服务端逐人下发的对局状态（`game_start` / `sync` / `uno_state` 同构）。 */
interface UnoPayload {
  game: 'uno';
  num_players: number;
  seat_uids: number[];
  /** 座位 → 玩家身份色（theme.playerColors 的键）。 */
  seat_colors: string[];
  your_seat: number | null;
  /** **只有本人的手牌**（牌码，如 `R5` / `RS` / `W+4`）。 */
  hand: string[];
  /** 本人手里此刻能出的牌（服务端给的唯一判据）。 */
  playable: string[];
  /**
   * 本人此刻能**抢出**的牌（`jump_in` 房规）。与 `playable` 是两个不同的东西：
   * `playable` 回答「轮到我了能出什么」，`jumpable` 回答「不轮到我时我能抢什么」。
   * 两者**永远不同时非空**（轮到我时服务端不下发 jumpable）。
   */
  jumpable?: string[];
  /** 7 换手要选人：可以换的座位。空表 = 现在不用选人。 */
  swap_targets?: number[];
  /**
   * **出牌前必须先选一个颜色的牌面**（服务端 `uno.WILD_NEEDS_COLOR`）。
   *
   * 这张表由服务端下发，客户端**不许自己抄一份**：黑牌一共六种，抄漏一张的后果
   * 不是渲染错，而是**点了没反应** —— 不带颜色把牌发上去，服务端整手拒收，
   * 而玩家没有任何办法补上那个颜色（v1.14 的 `W+6`/`W+10`/`W+RD4` 就这么漏过一轮）。
   * 颜色轮盘 `+RO` **不在**表里：它的颜色由下家点名。
   */
  color_kinds?: string[];
  /** 颜色轮盘（`W+RO`）正等着哪个座位点名；没有挂起时 null。 */
  roulette_seat?: number | null;
  /** 本局生效的房规（客户端**不用它判规则**，只用它把提示文案说对）。 */
  rules?: Record<string, string | boolean>;
  /** 座位 → 手牌张数（公开信息）。 */
  hand_counts: Record<string, number>;
  discard_top: string | null;
  /** 当前有效颜色（变色牌之后由出牌者指定）。 */
  current_color: string;
  turn: number;
  /** 1 顺时针 / -1 逆时针。 */
  direction: number;
  pending_draw: number;
  pending_kind: string | null;
  uno_called: number[];
  /**
   * 已出局的座位（认输 / 判负）。
   *
   * **组队档里也包含「已经出完手牌」的人** —— 他们不再被轮到，但队伍还在打。
   * 界面据此把那一格画成「已出完」而不是「已出局」（读 `emptied` 分辨）。
   */
  out: number[];
  /**
   * 组队档（`teams=2v2`）：座位 → 队伍，没开时全是 null。
   *
   * **对家配对由服务端算**（`seat % 2`），客户端不自己推 —— 配对是规则，规则只在
   * 服务端（同「可出判定不由客户端算」）。
   */
  seat_teams?: (number | null)[];
  /** 已经出完手牌的座位（组队档才有意义）。 */
  emptied?: number[];
  /** 我属于哪一队；没开组队时 null。 */
  your_team?: number | null;
  /** 获胜的队伍；没开组队时 null。**组队档的胜负是队伍，不是某个座位。** */
  winner_team?: number | null;
  draw_pile_count: number;
  can_draw: boolean;
  can_pass: boolean;
  can_call_uno: boolean;
  /** 能替谁喊 UNO（组队档里可能只有队友那一张）。服务端下发，客户端不自己推。 */
  uno_targets?: number[];
  winner: number | null;
  reason: string | null;
  finished: boolean;
  /** 只在 `game_over` 里。 */
  ranking?: UnoRankRow[];
  points?: Record<string, number>;
}

interface UnoRankRow {
  seat: number;
  username: string;
  nickname: string | null;
  cards: number;
  points: number;
  surrendered: boolean;
  rank: number;
}

interface Props {
  code: string;
  onLeave: () => void;
}

const REASON_LABELS: Record<string, string> = {
  emptied: '有人出完手牌',
  last_remaining: '其余人已出局',
  team_emptied: '一队两人都出完了手牌',
  surrender: '认输',
  disconnect: '掉线',
};

/** `uno_event` 的载荷：公开事件（谁做了什么），供提示条与动效。 */
interface UnoEvent {
  kind: string;
  seat: number | null;
  card?: string;
  color?: string;
  effect?: string;
  skipped?: number[];
  pending_draw?: number;
  drawn?: number;
  penalty?: number;
  kept_turn?: boolean;
  /** 这一手是不是**抢出来**的（提示条要说「抢出」而不是「打出」）。 */
  jump?: boolean;
  /** 7 换手的对象座位。 */
  target?: number | null;
  /** 组队档：这一手顺带把队友的一半手牌交了过来，或者把场外的队友摸了回来。 */
  takeover?: { seat: number; from: number; count: number; reason: string | null } | null;
}

/** `uno_event{kind:"roulette"}`：谁点了什么颜色、翻出几张（`color` / `drawn` 复用）。 */

export default function UnoScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<UnoPayload | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  /** 待选色的黑牌（点黑牌先弹选色，选完才真正出牌）。 */
  const [pickColorFor, setPickColorFor] = useState<string | null>(null);
  /**
   * 待选换手对象的 7（7-0 换手房规）。与黑牌选色同一套做法：**先问清楚再出牌** ——
   * 服务端也要求带 `target`，不带会把整手拒掉（而且拒之前状态不能动，见引擎的
   * `_can_swap_with` 校验顺序）。
   */
  const [pickTargetFor, setPickTargetFor] = useState<{ card: string; jump: boolean } | null>(null);
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  /** 按下中的手牌下标（按下即抬高一档，HIG 要求「按下时」就给反馈）。 */
  const [pressed, setPressed] = useState<number | null>(null);

  const socketRef = useRef<RoomSocket | null>(null);
  const skipRemoveRef = useRef(false);
  const gameOverShownRef = useRef(false);
  const bannerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 事件回调里要用最新的房间名单，闭包里的会过期。 */
  const playersRef = useRef<Room['players']>([]);

  const players = room?.players ?? [];
  playersRef.current = players;

  const mySeat = game?.your_seat ?? null;
  /**
   * 座位号的 ref 版。
   *
   * **WS 的 effect 不能依赖 `mySeat`**：开局前它是 null、收到 `game_start` 后变成
   * 座位号，依赖一变 effect 就重跑 → 旧 socket 被 close、新 socket 才注册，服务端
   * 中间那次「连接关闭」会把这个玩家标成掉线，而房间数据在游戏中不会再刷新，
   * 于是**自己那一格一直显示「掉线」**（实测踩到）。回调里改用 ref 读。
   */
  const mySeatRef = useRef<number | null>(null);
  mySeatRef.current = mySeat;
  const isMyTurn = !!game && mySeat !== null && game.turn === mySeat && !game.finished;
  const playing = !!game && !game.finished;
  const hand = game?.hand ?? [];
  const playableSet = useMemo(() => new Set(game?.playable ?? []), [game?.playable]);
  // 抢出（`jump_in` 房规）：不轮到我时能用手上「与牌堆顶完全相同」的牌打出去。
  // 服务端在我自己的回合不下发它，所以与 `playableSet` 天然互斥。
  const jumpableSet = useMemo(() => new Set(game?.jumpable ?? []), [game?.jumpable]);
  const swapTargets = game?.swap_targets ?? [];
  /** 出牌前要先选色的牌面（服务端下发，见 `UnoPayload.color_kinds` 的注释）。 */
  const colorKinds = useMemo(
    () => new Set(game?.color_kinds ?? []), [game?.color_kinds]);
  /**
   * 颜色轮盘正等着我点名。**弹框由服务端这个旗标驱动**，不是我自己推的：
   * 挂起时 `playable` / `can_*` 全是空的，屏上除了选色没有别的可做。
   */
  const rouletteSeat = game?.roulette_seat ?? null;
  const rouletteYours = rouletteSeat !== null && rouletteSeat === mySeat;
  const colorPickOpen = pickColorFor !== null || rouletteYours;
  const outSet = useMemo(() => new Set(game?.out ?? []), [game?.out]);
  const unoSet = useMemo(() => new Set(game?.uno_called ?? []), [game?.uno_called]);

  const nameOfSeat = useCallback((seat: number | null | undefined): string => {
    if (seat === null || seat === undefined) return '玩家';
    const p = playersRef.current[seat];
    if (!p) return `座位 ${seat + 1}`;
    return p.nickname || p.username || `座位 ${seat + 1}`;
  }, []);

  const flash = useCallback((text: string) => {
    setBanner(text);
    if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
    bannerTimerRef.current = setTimeout(() => {
      bannerTimerRef.current = null;
      setBanner(null);
    }, 2200);
  }, []);
  useEffect(() => () => {
    if (bannerTimerRef.current) clearTimeout(bannerTimerRef.current);
  }, []);

  /** 掉线倒计时：取第一个掉线的对手（2-4 人局通常就一个）。
   *  秒数由服务端给，**界面上不写死 120**——测试会把超时调成 5 秒。 */
  const dcSeat = players.findIndex((p) => !p.connected && p.id !== user?.id);
  const dcActive = dcSeat >= 0 && !!playing;
  const dcLeft = useDisconnectCountdown(
    dcActive,
    dcTimeout ?? (dcSeat >= 0 ? players[dcSeat]?.reconnect_left : null),
  );

  // ── 弃牌堆落牌动效 ─────────────────────────────────────────
  // 新的牌一压上来就播一次「从上落下 + 收正 + 淡入」。只对**换牌**触发，
  // 同一张牌因为别的状态变化重渲染时不重播。
  const dropAnim = useRef(new Animated.Value(1)).current;
  const lastDiscardRef = useRef<string | null>(null);
  useEffect(() => {
    const top = game?.discard_top ?? null;
    if (!top || top === lastDiscardRef.current) return;
    const first = lastDiscardRef.current === null;
    lastDiscardRef.current = top;
    if (first) return;
    dropAnim.setValue(0);
    Animated.timing(dropAnim, {
      toValue: 1,
      duration: 260,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: Platform.OS !== 'web',
    }).start();
  }, [game?.discard_top, dropAnim]);

  // ── WS ────────────────────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});

    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const adopt = (g: UnoPayload) => {
      setGame(g);
      if (g.winner !== null && g.winner !== undefined) setShowGameOver(true);
    };

    const handleSync = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
      if (msg.game) adopt(msg.game as UnoPayload);
    };
    const handleLobby = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
    };
    const handleStart = (msg: SocketMessage) => {
      const g = msg.game as UnoPayload;
      if (!g) return;
      gameOverShownRef.current = false;
      setShowGameOver(false);
      setPickColorFor(null);
      setPickTargetFor(null);
      adopt(g);
    };
    /** 权威状态：手牌、可出牌、回合、罚牌全在这里。 */
    const handleState = (msg: SocketMessage) => {
      adopt(msg as unknown as UnoPayload);
    };
    /** 公开事件：只用来做提示条，不参与任何判定。 */
    const handleEvent = (msg: SocketMessage) => {
      const ev = msg as unknown as UnoEvent;
      const who = nameOfSeat(ev.seat);
      const mine = ev.seat === mySeatRef.current;
      const subject = mine ? '你' : who;
      if (ev.kind === 'play') {
        const card = ev.card || '';
        // 罚几种、罚几张全由服务端给（`pending_draw`），这里**不列罚牌清单** ——
        // 加了新罚牌（+6 / +10 / 变色反转+4）也不必回来改这一行。
        const extra = ev.effect === 'rev_draw4'
          ? `（转向后累计罚 ${ev.pending_draw} 张）`
          : (ev.pending_draw ?? 0) > 0
            ? `（累计罚 ${ev.pending_draw} 张）`
            : ev.effect === 'swap'
              ? `（与 ${nameOfSeat(ev.target)} 换手）`
              : ev.effect === 'rotate'
                ? '（所有人把手牌传给下家）'
                : ev.effect === 'skip_all'
                  ? '（跳过所有人，再来一轮）'
                  : ev.effect === 'discard_all'
                    ? '（弃掉手里所有同色牌）'
                    : ev.effect === 'team_play'
                      ? `（回合交给队友${ev.skipped && ev.skipped.length > 0
                          ? `，跳过 ${ev.skipped.map((s) => nameOfSeat(s)).join('、')}` : ''}）`
                      : ev.effect === 'team_draw4'
                        ? '（下家和他的队友各摸 2 张，下家停一轮）'
                        : (ev.skipped && ev.skipped.length > 0)
                          ? `（跳过 ${ev.skipped.map((s) => nameOfSeat(s)).join('、')}）`
                          : '';
        // 组队档的手牌交接：张数会**当场跳变**，不解释一句玩家只会觉得是 bug
        // （自己忽然多了几张、队友忽然少了一半）。两种都来自服务端的 `takeover`。
        const handover = ev.takeover
          ? ev.takeover.reason === 'tag_in'
            ? ` · ${nameOfSeat(ev.takeover.seat)} 被摸回场上（分到 ${ev.takeover.count} 张）`
            : ` · ${nameOfSeat(ev.takeover.seat)} 接管了 ${nameOfSeat(ev.takeover.from)} 的一半手牌（${ev.takeover.count} 张）`
          : '';
        // **抢出与正常出牌要说成两件事** —— 它是这条房规的全部乐趣所在
        flash(`${subject}${ev.jump ? '抢出' : '打出'} ${cardLabel(card)}${extra}${handover}`);
      } else if (ev.kind === 'draw') {
        // 摸牌是 TAGGING BACK IN 最常触发的时机：队友摸到牌就把场外的你拉回来，
        // 两边的手牌张数都会当场跳变 —— 不解释一句玩家只会觉得是 bug
        const tagIn = ev.takeover?.reason === 'tag_in'
          ? ` · ${nameOfSeat(ev.takeover.seat)} 被摸回场上（分到 ${ev.takeover.count} 张）`
          : '';
        flash((ev.penalty
          ? `${subject}被罚摸 ${ev.penalty} 张并停一轮`
          : `${subject}摸了一张`) + tagIn);
      } else if (ev.kind === 'pass') {
        flash(`${subject}过牌`);
      } else if (ev.kind === 'roulette') {
        flash(`${subject}点了${UNO_COLOR_NAMES[ev.color || ''] || ''}色 · `
              + `翻出 ${ev.drawn ?? 0} 张`);
      } else if (ev.kind === 'uno') {
        flash(`${subject}喊了 UNO！`);
      } else if (ev.kind === 'autopass') {
        flash(`${subject}已掉线 · 自动过牌`);
      } else if (ev.kind === 'surrender') {
        flash(`${subject}认输退出`);
      } else if (ev.kind === 'leave') {
        flash(`${subject}离开了牌局`);
      }
    };
    const handleDisconnected = (msg: SocketMessage) => {
      setDcTimeout((msg.timeout as number) ?? null);
      flash(`${nameOfSeat(msg.seat as number)}断线了`);
    };
    const handleGameOver = (msg: SocketMessage) => {
      setGame((prev) => (prev
        ? {
            ...prev,
            winner: msg.winner as number,
            reason: msg.reason as string,
            // 组队档的胜负是**队伍**：结算文案读 `winner_team`，漏了这一句就退回
            // 「某某率先出完手牌获胜」——队友看起来跟输家一样
            winner_team: (msg.winner_team as number | null) ?? prev.winner_team ?? null,
            seat_teams: (msg.seat_teams as (number | null)[]) ?? prev.seat_teams,
            emptied: (msg.emptied as number[]) ?? prev.emptied,
            ranking: (msg.ranking as UnoRankRow[]) || [],
            points: (msg.points as Record<string, number>) || {},
            finished: true,
          }
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
    ws.on('uno_state', handleState);
    ws.on('uno_event', handleEvent);
    ws.on('player_disconnected', handleDisconnected);
    ws.on('game_over', handleGameOver);
    ws.on('room_closed', handleClosed);
    ws.on('error', (msg) => notify('提示', msg.message as string));
    ws.connect();

    return () => { ws.close(); };
  }, [token, code, nameOfSeat, onLeave, flash]);

  // 原生系统/顶栏返回
  useEffect(() => navigation.addListener('beforeRemove', (e: any) => {
    if (Platform.OS === 'web') return;
    if (skipRemoveRef.current) return;
    if (gameOverShownRef.current) {
      skipRemoveRef.current = true;
      navigation.dispatch(e.data.action);
      return;
    }
    e.preventDefault();
    setConfirm('leave');
  }), [navigation]);

  // Web 浏览器返回（走 backGuard 的助手，原生端自动 no-op）
  useEffect(() => onConfirmLeave(() => {
    if (gameOverShownRef.current) {
      navigation.goBack();
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  // ── 动作 ──────────────────────────────────────────────────
  const onCardPress = (card: string) => {
    // 抢出：不轮到我时打出一张与牌堆顶完全相同的牌。服务端在动作落地那一刻还会
    // 再校验一次（抢出是竞态的，同一张牌可能好几个人都有），**抢输会回一句明确的
    // 错**，由 toast 显示 —— 不能静默。
    const jump = !isMyTurn;
    if (jump ? !jumpableSet.has(card) : !playableSet.has(card)) return;
    if (colorKinds.has(cardKind(card))) {
      // 黑牌要先选色（服务端也要求带 color，否则整手拒收）。**哪些牌算黑牌由服务端
      // 下发**（`color_kinds`）—— 自己抄一份的话，漏掉的那张就是「点了没反应」。
      setPickColorFor(card);
      return;
    }
    if (cardKind(card) === '7' && swapTargets.length > 0) {
      // 7 换手要选人，同样先问清楚再出牌
      setPickTargetFor({ card, jump });
      return;
    }
    socketRef.current?.unoPlay(card, undefined, jump);
  };

  const onPickColor = (key: string) => {
    // 颜色轮盘：这一选是**点给下家的那个颜色**，不是给某张牌选色
    if (rouletteYours) {
      socketRef.current?.unoRoulette(key);
      return;
    }
    const card = pickColorFor;
    setPickColorFor(null);
    if (!card) return;
    // 黑牌不可能是 7，所以这里不必再看换手
    socketRef.current?.unoPlay(card, key);
  };

  const onPickTarget = (seat: number) => {
    const play = pickTargetFor;
    setPickTargetFor(null);
    if (play) socketRef.current?.unoPlay(play.card, undefined, play.jump, seat);
  };

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

  // ── 派生文案 ──────────────────────────────────────────────
  const iAmOut = mySeat !== null && outSet.has(mySeat);
  const othersPlaying = players.filter((_, i) => !outSet.has(i)).length;
  // 组队档（v1.16）：对家是队友，一队两人都出完手牌才算赢。配对读服务端下发的
  // `seat_teams`（客户端不自己算 `seat % 2`）。
  const seatTeams = game?.seat_teams ?? [];
  const teamMode = seatTeams.some((t) => t !== null && t !== undefined);
  const emptiedSet = new Set(game?.emptied ?? []);
  const myTeam = game?.your_team ?? null;
  const mateSeats = teamMode && mySeat !== null
    ? seatTeams.map((t, s) => (t === myTeam && s !== mySeat ? s : -1)).filter((s) => s >= 0)
    : [];
  const isMateSeat = (seat: number) => teamMode && mySeat !== null
    && seat !== mySeat && seatTeams[seat] === myTeam;
  // 能替谁喊 UNO：服务端下发 `uno_targets`（先自己、再队友），取第一个就是按钮该
  // 做的事。组队档里自己手里两张、队友只剩一张时，这个按钮是「替队友喊」——
  // 而那是唯一能把队友从「被对家抓到罚摸 2 张」里救下来的动作。
  const unoTarget = (game?.uno_targets ?? [])[0];
  const unoForMate = unoTarget !== undefined && unoTarget !== mySeat;
  const turnName = nameOfSeat(game?.turn);
  const turnIsBot = !!(game && players[game.turn]?.is_bot);
  const turnText = (() => {
    if (!game) return '加载中…';
    if (game.finished) return '本局结束';
    // 组队档里「出完手牌」是好事（队伍的一半任务完成了），不能写「你已出局」
    if (iAmOut) {
      return emptiedSet.has(mySeat as number)
        ? (othersPlaying > 0 ? '你已经出完了 · 等队友打完' : '本局结束')
        : '你已出局 · 等其他人打完';
    }
    // **颜色轮盘要排在本回合判断之前**：轮盘挂起时 `turn` 就是那个要点名的座位，
    // 放在后面的话轮到我自己时会被「轮到你出牌」挡住，而那时其实只有选色能做。
    if (rouletteYours) return '颜色轮盘：点一个颜色';
    if (rouletteSeat !== null) return `等 ${nameOfSeat(rouletteSeat)} 点颜色`;
    if (isMyTurn) return game.can_pass ? '你摸到了牌：出牌或过牌' : '轮到你出牌';
    // 抢出（`jump_in` 房规）：不轮到我时手上也可能有能打的牌。**必须说出来** ——
    // 牌被抬起来却没有一句解释的话，玩家只会以为界面出了毛病。
    if (jumpableSet.size > 0) return `抢出机会：点带「抢出」角标的牌`;
    return turnIsBot ? `${turnName} 思考中…` : `等待 ${turnName} 出牌`;
  })();
  const ranking = game?.ranking ?? [];
  const points = game?.points ?? {};
  const curColor = game?.current_color || 'W';
  const ringColor = unoColors[curColor] || unoOnTable;

  const seatColorOf = (seat: number) => (
    unoSeatColors[game?.seat_colors?.[seat] || ''] || unoOnTableMuted
  );

  // 结算文案。**组队档的胜负是队伍**：只说「某某率先出完手牌获胜」的话，赢的那队
  // 里另一个人（同样出完了、同样算赢）在结算里看起来跟输家一样。
  const resultText = (() => {
    if (teamMode && game?.winner_team != null) {
      const winners = seatTeams
        .map((t, s) => (t === game.winner_team ? s : -1))
        .filter((s) => s >= 0);
      const names = winners.map((s) => (s === mySeat ? '你' : nameOfSeat(s))).join(' 和 ');
      return game.winner_team === myTeam
        ? `${names} 两人都出完了手牌，你们这一队获胜`
        : `${names} 两人都出完了手牌，他们这一队获胜`;
    }
    if (ranking.length > 0) {
      return `${ranking[0].nickname || ranking[0].username} 率先出完手牌获胜`;
    }
    return REASON_LABELS[game?.reason || ''] || '对局结束';
  })();

  return (
    <View style={styles.container}>
      {/* 规则入口（与象棋 / 四子棋一样放在对局屏内，不占牌桌空间） */}
      <View style={styles.topBar}>
        <GameHelpButton game="uno" size={24} />
      </View>

      {/* ── 牌桌（深色毡面） ────────────────────────────────── */}
      <View style={styles.table}>
        {/* 座位：昵称 + 扇形牌背 + 张数（自己那一格也画出来，一眼看清全桌） */}
        <View style={styles.seats}>
          {players.map((p, seat) => {
            const isTurn = game?.turn === seat && !game?.finished;
            const out = outSet.has(seat);
            const isMe = seat === mySeat;
            // 不给自己显示「掉线」：人就在看着屏幕，房间数据在游戏中又不会刷新，
            // 万一收到一条过期的断线消息就会一直挂在自己头上（上面那条 ref 修的是
            // 根因，这里是兜底）
            const dc = !p.connected && !out && !isMe;
            const sc = seatColorOf(seat);
            const cnt = game?.hand_counts?.[String(seat)] ?? 0;
            const mate = isMateSeat(seat);
            return (
              <View
                key={p.id}
                testID={`u-seat-${seat}`}
                style={[
                  styles.seat,
                  isTurn && { borderColor: sc, backgroundColor: unoSeatActiveBg },
                  mate && styles.seatMate,
                  out && styles.seatOut,
                ]}
              >
                {/* 第一行：身份色点 + 昵称（自己的座位写「我」） */}
                <View style={styles.seatTop}>
                  <View style={[styles.seatDot, { backgroundColor: sc }]} />
                  <Text style={[styles.seatName, isTurn && { color: sc }]} numberOfLines={1}>
                    {isMe ? '我' : (p.nickname || p.username)}
                  </Text>
                  {/* 组队档：队友必须有**文字**标识，不能只靠边框（WCAG 1.4.1） */}
                  {mate ? (
                    <Text style={styles.seatMateTag} testID={`u-mate-${seat}`}
                      numberOfLines={1}>队友</Text>
                  ) : null}
                </View>

                {/* 第二行：牌背扇形 + 张数；出局/掉线时换成状态文案 */}
                {out ? (
                  <Text style={styles.seatStatus}>
                    {emptiedSet.has(seat) ? '已出完' : '已出局'}
                  </Text>
                ) : dc ? (
                  <Text style={styles.seatStatus}>
                    掉线{dcSeat === seat ? ` ${dcLeft}s` : ''}
                  </Text>
                ) : (
                  <View style={styles.seatHandRow}>
                    <UnoHandBacks count={cnt} />
                    {/* 张数写出来（不只是数牌背）：E2E 直接断言这个数字，
                        比数"牌背元素有几个"这类启发式可靠得多 */}
                    <Text style={styles.seatCount} testID={`u-count-${seat}`}>
                      {`${cnt} 张`}
                    </Text>
                  </View>
                )}

                {/* 第三行：UNO 徽标 / 出牌中标记（高度固定，座位才对得齐） */}
                <View style={styles.seatFoot}>
                  {isTurn ? (
                    <Text style={[styles.seatTurnMark, { color: sc }]}>出牌中</Text>
                  ) : unoSet.has(seat) && !out ? (
                    <Text style={styles.seatUno}>UNO!</Text>
                  ) : null}
                </View>
              </View>
            );
          })}
        </View>

        {/* 中央：抽牌堆（左）+ 弃牌堆 + 当前色环（右） */}
        <View style={styles.center}>
          <View style={styles.drawCol}>
            <TouchableOpacity
              style={[styles.drawPile, !(isMyTurn && game?.can_draw) && styles.idle]}
              onPress={() => isMyTurn && game?.can_draw && socketRef.current?.unoDraw()}
              disabled={!(isMyTurn && game?.can_draw)}
              testID="u-draw"
              accessibilityLabel="摸牌"
            >
              {/* 三张错位叠影：牌堆要有厚度，一张孤零零的牌背看起来像占位符 */}
              <View style={styles.drawStack}>
                <View style={[styles.stackLayer, { transform: [{ rotate: '-6deg' }] }]}>
                  <UnoCardView card="W" faceDown width={54} height={81} />
                </View>
                <View style={[styles.stackLayer, { transform: [{ rotate: '-2deg' }] }]}>
                  <UnoCardView card="W" faceDown width={54} height={81} />
                </View>
                <UnoCardView card="W" faceDown width={54} height={81} />
              </View>
              <Text style={styles.drawCount}>{`剩 ${game?.draw_pile_count ?? 0} 张`}</Text>
              <Text style={styles.drawHint}>
                {game && game.pending_draw > 0 ? `摸 ${game.pending_draw} 张` : '摸牌'}
              </Text>
            </TouchableOpacity>
          </View>

          <View style={styles.pileCol}>
            {/* 当前颜色的**实心环**：颜色是本局最要紧的状态，做成弃牌堆外圈
                （vs 毡面 3.55~11.08 ✓），比自己另找一列写文字显眼得多。
                环与牌之间留一圈毡色空隙，红牌配红环时也分得开 */}
            <Animated.View
              style={[
                styles.colorRing,
                { borderColor: ringColor },
                {
                  opacity: dropAnim.interpolate({ inputRange: [0, 0.4, 1], outputRange: [0, 1, 1] }),
                  transform: [
                    { translateY: dropAnim.interpolate({ inputRange: [0, 1], outputRange: [-52, 0] }) },
                    { scale: dropAnim.interpolate({ inputRange: [0, 1], outputRange: [1.16, 1] }) },
                    { rotate: dropAnim.interpolate({ inputRange: [0, 1], outputRange: ['-12deg', '0deg'] }) },
                  ],
                },
              ]}
            >
              {game?.discard_top ? (
                <UnoCardView
                  card={game.discard_top}
                  width={PILE_W}
                  height={PILE_H}
                  chosenColor={game.current_color}
                />
              ) : (
                <View style={styles.pileEmpty} />
              )}
            </Animated.View>
          </View>
        </View>

        {/* 事件提示条固定在桌面里（高度留死，出现/消失不推挤上下布局）*/}
        <View style={styles.bannerSlot}>
          {banner ? <Text style={styles.banner} testID="u-banner">{banner}</Text> : null}
        </View>

        {/* 桌沿：当前颜色 / 方向 / 待罚 + 回合文案 */}
        <View style={styles.foot}>
          <View style={styles.footRow}>
            <View style={styles.footItem}>
              <UnoColorBadge color={curColor} size={14} fill={ringColor} />
              <Text style={styles.footText}>{`当前颜色 ${UNO_COLOR_NAMES[curColor] || ''}`}</Text>
            </View>
            <View style={styles.footItem}>
              <Ionicons
                name={game?.direction === -1 ? 'refresh-outline' : 'sync-outline'}
                size={14}
                color={unoOnTableMuted}
              />
              <Text style={styles.footText}>
                {game?.direction === -1 ? '逆时针' : '顺时针'}
              </Text>
            </View>
            {game && game.pending_draw > 0 ? (
              <View style={styles.pending} testID="u-pending">
                <Text style={styles.pendingText}>{`待罚 ${game.pending_draw} 张`}</Text>
              </View>
            ) : null}
          </View>
          <Text
            style={[styles.turnText, isMyTurn && styles.turnTextMine]}
            testID="u-turn"
          >
            {turnText}
          </Text>
        </View>
      </View>

      {/* ── 自己的手牌：扇形重叠，可出的牌抬高 + 双环 ─────────── */}
      <ScrollView
        horizontal
        style={styles.handScroll}
        contentContainerStyle={styles.handContent}
        showsHorizontalScrollIndicator={false}
      >
        {hand.map((card, i) => {
          const canPlay = isMyTurn && playableSet.has(card);
          // **抢出与可出是两条不同的通道**：可出 = 轮到我 + 位移 + 双环；
          // 抢出 = 不轮到我 + 一枚写着「抢出」的角标。只靠高亮的话，玩家分不清
          // 「轮到我了」和「我可以抢」—— 而这两件事的后果完全不同。
          const canJump = !isMyTurn && jumpableSet.has(card);
          const can = canPlay || canJump;
          const n = hand.length;
          const mid = (n - 1) / 2;
          const t = n > 1 ? (i - mid) / mid : 0;
          const held = pressed === i;
          return (
            <TouchableOpacity
              key={`${card}-${i}`}
              activeOpacity={0.85}
              onPressIn={() => setPressed(i)}
              onPressOut={() => setPressed(null)}
              onPress={() => onCardPress(card)}
              disabled={!can}
              style={[
                styles.handItem,
                i > 0 && styles.handOverlap,
                {
                  zIndex: held ? 99 : i,
                  // 弧线 + 倾斜 + 按下反馈。变换不占布局，所以横向滚动宽度
                  // 仍然是「一张整牌 + N × 露出宽度」
                  transform: [
                    { translateY: (held ? -PRESS_LIFT : 0) + Math.abs(t) * 6 },
                    { rotate: `${t * TILT}deg` },
                  ],
                },
              ]}
              testID={canPlay ? `u-play-${i}` : canJump ? `u-jump-${i}` : `u-card-${i}`}
              accessibilityLabel={
                `${cardLabel(card)}${canPlay ? '（可出）' : canJump ? '（可以抢出）' : ''}`
              }
            >
              <UnoCardView
                card={card}
                width={UNO_CARD_W}
                height={UNO_CARD_H}
                playable={canPlay}
              />
              {canJump ? (
                <View style={styles.jumpBadge} testID={`u-jumpbadge-${i}`}>
                  <Text style={styles.jumpBadgeText}>抢出</Text>
                </View>
              ) : null}
            </TouchableOpacity>
          );
        })}
        {hand.length === 0 && !game?.finished ? (
          <Text style={styles.handEmpty}>（没有手牌）</Text>
        ) : null}
      </ScrollView>

      {/* ── 操作区（拇指可达） ──────────────────────────────── */}
      {iAmOut && !showGameOver ? (
        <View style={styles.waitBar}>
          <Text style={styles.waitText}>
            {othersPlaying <= 0 ? '本局结束'
              : emptiedSet.has(mySeat as number)
                ? '你已经出完了，等队友打完…'
                : '你已出局，等待其他玩家打完…'}
          </Text>
          <TouchableOpacity style={styles.waitBtn} onPress={handleBackToRoom} testID="u-back-room">
            <Text style={styles.waitBtnText}>返回房间</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={styles.actions}>
          <TouchableOpacity
            style={[styles.actBtn, !(isMyTurn && game?.can_pass) && styles.idle]}
            onPress={() => isMyTurn && game?.can_pass && socketRef.current?.unoPass()}
            disabled={!(isMyTurn && game?.can_pass)}
            testID="u-pass"
          >
            <Text style={styles.actBtnText}>过牌</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.actBtn, styles.unoBtn, !game?.can_call_uno && styles.idle]}
            onPress={() => unoTarget !== undefined && socketRef.current?.unoCall(unoTarget)}
            disabled={!game?.can_call_uno}
            testID="u-uno"
          >
            <Text style={styles.unoBtnText}>{unoForMate ? '替队友喊' : '喊 UNO'}</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.surrenderBtn}
            onPress={() => setConfirm('surrender')}
            testID="u-surrender"
          >
            <Text style={styles.surrenderText}>认输</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* 选色：黑牌选色 / 颜色轮盘点名，**共用同一个弹窗**（两件事都是「点一个颜色」，
          分成两个框只会多一份要维护的样式与返回键处理） */}
      <ModalM
        visible={colorPickOpen}
        onClose={() => (rouletteYours ? undefined : setPickColorFor(null))}
      >
        <Text style={styles.modalTitle}>
          {rouletteYours ? '点名一个颜色' : '选择接下来的颜色'}
        </Text>
        <Text style={styles.modalSub}>
          {rouletteYours
            ? '从抽牌堆一张张翻开，一直翻到这个颜色为止 —— 翻出来的牌全归你，然后你这轮就结束了。挑牌堆里剩得最多的那一色最划算。'
            : '对手接下来必须跟这个颜色出牌（除非出黑牌）。'}
        </Text>
        <View style={styles.colorPickRow}>
          {UNO_COLOR_KEYS.map((key) => (
            <TouchableOpacity
              key={key}
              style={styles.colorPickBtn}
              onPress={() => onPickColor(key)}
              testID={`u-color-${key}`}
              accessibilityLabel={`${UNO_COLOR_NAMES[key]}色`}
            >
              <UnoColorBadge color={key} size={22} />
              <Text style={styles.colorPickText}>{UNO_COLOR_NAMES[key]}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ModalM>

      {/* 7 换手：选一个人换手牌（7-0 房规）。与黑牌选色同一套「先问清楚再出牌」。 */}
      <ModalM visible={pickTargetFor !== null} onClose={() => setPickTargetFor(null)}>
        <Text style={styles.modalTitle}>和谁换手牌</Text>
        <Text style={styles.modalSub}>
          打出的 7 会与对方交换全部手牌 —— 你把手里的牌给他，拿他那一手。
          跟手牌最少的人换通常最划算。
        </Text>
        <View style={styles.targetPickRow}>
          {swapTargets.map((seat) => {
            const cnt = game?.hand_counts?.[String(seat)] ?? 0;
            return (
              <TouchableOpacity
                key={seat}
                style={styles.targetPickBtn}
                onPress={() => onPickTarget(seat)}
                testID={`u-target-${seat}`}
                accessibilityLabel={`和 ${nameOfSeat(seat)} 换手牌，他手里 ${cnt} 张`}
              >
                <Text style={styles.targetPickName}>{nameOfSeat(seat)}</Text>
                <Text style={styles.targetPickCount}>{`${cnt} 张`}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </ModalM>

      {/* 结算 */}
      <ModalM visible={showGameOver} onClose={() => {}}>
        <Text style={styles.modalTitle}>本局结束</Text>
        <Text style={styles.modalSub}>{resultText}</Text>
        <View style={styles.rankList}>
          {ranking.map((r) => (
            <View key={r.seat} style={styles.rankRow}>
              <Text style={styles.rankNo}>{r.rank}</Text>
              <View style={[styles.pDot, { backgroundColor: playerColors[game?.seat_colors?.[r.seat] || ''] || colors.textMuted }]} />
              <Text style={styles.rankName} numberOfLines={1}>
                {r.nickname || r.username}
                {r.seat === mySeat ? '（我）' : isMateSeat(r.seat) ? '（队友）' : ''}
                {r.surrendered ? '（弃权）' : ''}
              </Text>
              <Text style={styles.rankScore}>{`${r.cards} 张 · ${points[String(r.seat)] ?? r.points} 分`}</Text>
            </View>
          ))}
        </View>
        <TouchableOpacity style={styles.primaryBtn} onPress={handleBackToRoom} testID="u-result-back">
          <Text style={styles.primaryBtnText}>返回房间</Text>
        </TouchableOpacity>
      </ModalM>

      {/* 认输 / 退出确认 */}
      <ModalM visible={confirm !== null} onClose={() => setConfirm(null)}>
        <Text style={styles.modalTitle}>{confirm === 'surrender' ? '确认认输？' : '确认离开对局？'}</Text>
        <Text style={styles.modalSub}>
          {confirm === 'surrender'
            ? '认输只让你出局，牌局继续；只剩一个人时才算结束。'
            : '离开会让你出局，牌局继续；只剩一个人时才算结束。'}
        </Text>
        <View style={styles.modalRow}>
          <TouchableOpacity style={styles.ghostBtn} onPress={() => setConfirm(null)} testID="u-cancel">
            <Text style={styles.ghostBtnText}>继续玩</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.dangerBtn}
            testID="u-confirm"
            onPress={confirm === 'surrender' ? doSurrender : handleLeaveRoom}
          >
            <Text style={styles.primaryBtnText}>{confirm === 'surrender' ? '确认认输' : '确认离开'}</Text>
          </TouchableOpacity>
        </View>
      </ModalM>
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
  container: { flex: 1, backgroundColor: colors.bg, alignItems: 'center' },
  topBar: {
    alignSelf: 'stretch',
    alignItems: 'flex-end',
    paddingHorizontal: spacing.md,
    paddingTop: spacing.xs,
  },

  // ── 牌桌 ─────────────────────────────────────────────────
  table: {
    alignSelf: 'stretch',
    marginHorizontal: spacing.sm,
    marginTop: spacing.xs,
    flex: 1,
    minHeight: 300,
    backgroundColor: unoTable,
    borderRadius: radius.xl,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
    justifyContent: 'space-between',
  },

  // 座位
  seats: {
    flexDirection: 'row',
    alignItems: 'stretch',
    justifyContent: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
  },
  seat: {
    flex: 1,
    maxWidth: 112,
    alignItems: 'center',
    borderRadius: radius.md,
    borderWidth: 2,
    borderColor: 'transparent',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.xs,
  },
  // 出局座位整体压暗一档（视觉上退到背景里），但**不能压到 0.5**：
  // 「已出局」是这个座位的状态文字（12px，按正文 4.5:1 算），整体 opacity 0.5 时
  // 它合成到毡底上只剩 3.19 ✗；0.72 时 5.19 ✓、昵称 9.06 ✓（实测见 tmp/out_op.py）。
  seatOut: { opacity: 0.72 },
  // 组队档：队友那一格换一条实心亮边框（**文字标识才是主通道**，边框只是加速
  // 扫视 —— 只靠边框的话颜色一糊就分不出谁是队友）
  seatMate: { borderColor: unoOnTable, borderWidth: 2 },
  seatMateTag: {
    // **不许被压扁**：座位很窄，名字一长就会把「队友」挤成两行（实测过）。
    // 该让位的是名字（`seatName` 有 `flexShrink: 1`），不是这枚标识。
    flexShrink: 0,
    fontSize: fontSize.xs,
    fontWeight: '700',
    color: unoTable,          // 深墨字压在浅色药丸上（毡底上唯一够对比度的做法）
    backgroundColor: unoOnTable,
    borderRadius: 4,
    paddingHorizontal: 4,
    overflow: 'hidden',
  },
  seatTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, maxWidth: '100%' },
  seatDot: { width: 8, height: 8, borderRadius: 4 },
  seatName: {
    fontSize: fontSize.sm,
    fontWeight: '700',
    color: unoOnTable,
    flexShrink: 1,
  },
  seatHandRow: { alignItems: 'center', marginTop: 4 },
  seatCount: { fontSize: fontSize.xs, color: unoOnTableMuted, marginTop: 2 },
  seatStatus: {
    fontSize: fontSize.xs,
    fontWeight: '700',
    color: unoSeatColors.red,  // 出局/掉线都用它（vs 毡底 8.87 ✓）
    marginTop: 8,
  },
  seatFoot: { height: 16, justifyContent: 'center', marginTop: 2 },
  seatTurnMark: { fontSize: fontSize.xs, fontWeight: '700' },
  seatUno: {
    fontSize: fontSize.xs,
    fontWeight: '800',
    color: colors.white,
    backgroundColor: colors.danger,
    borderRadius: 3,
    paddingHorizontal: 4,
    overflow: 'hidden',
  },

  // 中央
  center: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.lg,
  },
  drawCol: { alignItems: 'center' },
  drawPile: { alignItems: 'center' },
  drawStack: { width: 54, height: 81 },
  stackLayer: { position: 'absolute', top: 0, left: 0 },
  drawCount: { fontSize: fontSize.xs, color: unoOnTableMuted, marginTop: 4 },
  drawHint: { fontSize: fontSize.sm, color: unoOnTable, fontWeight: '700' },
  pileCol: { alignItems: 'center', justifyContent: 'center' },
  colorRing: {
    borderWidth: 5,
    borderRadius: radius.lg + 4,
    padding: 7,          // 环与牌之间露出毡面，同色牌也不会和环粘成一块
    alignItems: 'center',
    justifyContent: 'center',
  },
  pileEmpty: { width: PILE_W, height: PILE_H },

  // 桌沿
  foot: { alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md },
  footRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    flexWrap: 'wrap',
    gap: spacing.md,
  },
  footItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  footText: { fontSize: fontSize.xs, color: unoOnTable, fontWeight: '600' },
  pending: {
    backgroundColor: colors.warning,
    borderRadius: radius.sm,
    paddingVertical: 3,
    paddingHorizontal: spacing.sm,
  },
  pendingText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '700' },
  turnText: {
    fontSize: fontSize.md,
    fontWeight: '600',
    color: unoOnTableMuted,
  },
  turnTextMine: { color: unoOnTable, fontWeight: '800' },

  bannerSlot: {
    height: 22,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
  },
  banner: {
    fontSize: fontSize.sm,
    fontWeight: '600',
    color: unoOnTable,
    textAlign: 'center',
  },

  // 手牌
  // 高度 = 牌高 + 抬高余量（可出牌抬起 12 + 按下再抬 10 + 弧形下沉 6）
  handScroll: { width: '100%', maxHeight: UNO_CARD_H + 40, marginTop: spacing.sm },
  handContent: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'flex-end',
    paddingHorizontal: spacing.md,
    paddingTop: 30,
    paddingBottom: 6,
  },
  handItem: { justifyContent: 'flex-end', position: 'relative' },
  handOverlap: { marginLeft: -OVERLAP },
  handEmpty: { fontSize: fontSize.sm, color: colors.textMuted, alignSelf: 'center' },
  // 抢出的角标：**文字是主通道**，底色只是让它跳出来。可出牌靠「位移 + 双环」，
  // 抢出靠这枚写着字的角标 —— 两条通道必须一眼分得开（WCAG 1.4.1）。
  jumpBadge: {
    position: 'absolute',
    top: -6,
    left: 2,
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 6,
    backgroundColor: unoOnTable,
    borderWidth: 1,
    borderColor: unoInk,
  },
  jumpBadgeText: { fontSize: 10, fontWeight: '800', color: unoInk },

  // 操作
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
  actBtn: {
    paddingVertical: spacing.sm + 6,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  actBtnText: { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  unoBtn: { backgroundColor: colors.danger, borderColor: colors.danger },
  unoBtnText: { fontSize: fontSize.md, fontWeight: '700', color: colors.white },
  // 认输是破坏性操作，做成**描边幽灵按钮**而不是实心红块：它不该和
// 「喊 UNO」抢注意力（分级确认已经拦了一道，见 useModalBackGuard）
  surrenderBtn: {
    paddingVertical: spacing.sm + 6,
    paddingHorizontal: spacing.md,
    marginLeft: 'auto',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.danger,
    backgroundColor: colors.card,
  },
  surrenderText: { fontSize: fontSize.sm, color: colors.danger, fontWeight: '600' },
  idle: { opacity: 0.4 },

  waitBar: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
  waitText: { fontSize: fontSize.sm, color: colors.textSecondary },
  waitBtn: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
  },
  waitBtnText: { color: colors.white, fontWeight: '600' },

  // 弹窗
  modalOverlay: {
    flex: 1,
    backgroundColor: colors.overlay,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  modalBox: {
    width: '100%',
    maxWidth: 360,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.lg,
  },
  modalTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  modalSub: { fontSize: fontSize.sm, color: colors.textSecondary, marginBottom: spacing.md },
  modalRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  colorPickRow: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  colorPickBtn: {
    flex: 1,
    alignItems: 'center',
    gap: spacing.xs,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  colorPickText: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text },
  // 换手选人：每个候选一张卡片，**必须带上他手里有几张** —— 换手是双向的，
  // 「跟手牌最少的人换」才是这条房规的全部策略。
  targetPickRow: { gap: spacing.xs },
  targetPickBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm + 4,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
  },
  targetPickName: { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  targetPickCount: { fontSize: fontSize.sm, color: colors.textMuted },
  rankList: { marginBottom: spacing.md },
  rankRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  rankNo: { width: 22, fontSize: fontSize.sm, fontWeight: '700', color: colors.textSecondary },
  pDot: { width: 10, height: 10, borderRadius: 5, marginRight: spacing.xs },
  rankName: { flex: 1, fontSize: fontSize.sm, color: colors.text },
  rankScore: { fontSize: fontSize.sm, color: colors.textSecondary },
  primaryBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
    alignItems: 'center',
  },
  primaryBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  ghostBtn: {
    flex: 1,
    borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  ghostBtnText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  dangerBtn: {
    flex: 1,
    backgroundColor: colors.danger,
    borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
    alignItems: 'center',
  },
});