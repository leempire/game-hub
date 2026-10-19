// 深空休眠对局屏（2-6 人合作生存，本项目**第一个服务端 tick 的游戏**）。
//
// 设计依据：docs/游戏设计/深空休眠/设计.md（§3 界面构成 / §4 视觉 / §5 可访问性 / §6 交互）
// 复刻调研：docs/游戏设计/深空休眠/增强调研.md（v2 按原型复刻空间与镜头）
// 前后端契约：docs/游戏设计/深空休眠/协议.md（消息形状以它为准，客户端不发明字段）
//
// 骨架照 TetrisScreen / UnoScreen：WS 单 effect + ref 读回调、返回双路径
// （原生 beforeRemove + Web backGuard 的 onConfirmLeave）、应用内 Modal、
// Vibration 带 Platform.OS 守卫、颜色只从 theme.ts 取。
//
// **v2 的关键变化：主视图从「单间舱室的格子」变成「整层楼的俯视平面图」。**
// 地图 25×30 格（`stasisMap.GRID_W/GRID_H`）× 44px = 1100×1320，比屏幕大，
// 所以这一屏有三件别处没有的东西：
//
// 1. **相机**：默认跟随自己的角色；一拖动就进「自由视角」并停在那儿，点
//    `[回到我]` 恢复跟随。硬锁跟随的话「我想看看队友那边」这个操作根本做不出来。
// 2. **虚拟摇杆**（左下角）：`stasis_move` 发方向，服务端积分位置。
//    **客户端做本地预演**（同俄罗斯方块的教训）——5 Hz 的往返直接驱动角色会有
//    200ms 延迟，摇杆手感全无。本地按同一个方向与速度积分，`stasis_tick` 回来后
//    按服务端位置校正（差得远就直接吸附，否则插值）。
// 3. **落点碰撞也在客户端做一份**（`canStand`）——预演如果穿墙，校正时会被拽回来，
//    看起来像瞬移。几何是镜像自 `stasis_map.py` 的，两份必须同步。
//
// 不做任何**规则**推演：静默体位置、门血、资源全由服务端 5 Hz 的 `stasis_tick`
// 下发，客户端只按快照渲染 + 插值。本项目已经踩过两次「客户端自己推路径画错」
// （飞行棋 `piece_moved` 必须带 `path`；象棋的合法着法必须服务端给）。
//
// 状态分三层（协议 §「状态分三层下发」）：全量 `stasis_state`（地图 + 建筑）、
// 动态 `stasis_tick`（会动的东西）、离散 `stasis_events`（提示 + 震动）。
// tick **只更新会动的字段**——把 tick 当全量状态用会把建筑列表抹掉。
//
// 可访问性（设计 §5，本作风险最高）：**禁止无限循环动效**（门体震颤 / 红晕脉动
// 一律限次）、闪烁 < 3 Hz、跟随系统的「减少动效」、颜色永远配第二通道
// （门血低＝颜色+条长+数字+震颤；静默体状态＝颜色+形状+文案）。
//
// ── 这个文件里**只有什么**（v6 拆分后，2709 → 1360 行）──────────────
//
// 拆的判据是「谁需要知道什么」，不是行数。剩下的全是**只有屏幕才知道的事**：
// 对局状态、WS 接线、本地预演与相机、动作回调、以及 HUD / 底栏 / 视口的布局。
// 其余四块各归各的文件，改那边不必读这里：
//
// | 文件 | 管什么 |
// |---|---|
// | `stasisRules.ts` | 费用表 / 产率表 / 门槛表 / 射程公式 / 预演碰撞（**数值只有这一个来源**） |
// | `StasisBoard.tsx` | 地图渲染：静态层 + 门 / 建筑 / 休眠舱 / 空格 / 范围圈 / 角色 + 摇杆 |
// | `StasisPanels.tsx` | 底部 sheet（建造 / 建筑 / 门 / 休眠舱）+ 全站 / 确认 / 结算三个浮层 |
// | `stasisWidgets.tsx` | 通用小部件（按下缩放、血条、资源胶囊、弹层外壳、两种按钮） |
// | `stasisTypes.ts` | 协议载荷类型（`协议.md` 的字段原样照抄） |
//
// **判据不在这里算**：v6 起「能不能躺下」由服务端随 tick 下发
// （`players[uid].lie`），客户端不再复刻（见 `canLie` 的注释）。
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Platform, Animated,
  useWindowDimensions, Vibration, AccessibilityInfo, PanResponder,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  spacing, withAlpha, stasisSpace, stasisPod, stasisInk, stasisRepair,
  stasisDoorOk, stasisBio, stasisAlloy, stasisDoorLow, stasisSeatColors,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { onConfirmLeave } from '../backGuard';
import { GameHelpButton } from '../help';
import { useDisconnectCountdown } from '../disconnect';
import {
  TILE, cellKey, buildMap, EMPTY_MAP, type MapGeom, type Side,
} from '../stasisMap';
import ShotLayer from '../StasisShots';
// 拆出去的三块：规则常量（`stasisRules`）、地图渲染层（`StasisBoard`）、
// 通用小部件（`stasisWidgets`）；协议类型在 `stasisTypes`。见各自文件头。
import {
  BED_RATE, BUILDINGS, PATCH_SELF, PATCH_REMOTE,
  PLAYER_SPEED, RECONCILE_SNAP, RECONCILE_LERP, SEND_MIN_MS, PAN_MARGIN,
  CAM_LERP, CAM_LEAD, ALERT_DOOR_RATIO, OVERLOAD_DOOR_RATIO, PULSE_CYCLES,
  ON_SPACE, ON_SPACE_SOFT, WEB_TOUCH_NONE, WEB_GPU,
  canStand, clampRatio, fmtClock, AI_PROFILE_NAMES,
} from '../stasisRules';
import { TapScale, ResChip, PrimaryButton } from '../stasisWidgets';
import {
  STICK_R, StaticMap, DoorLayer, BuildingLayer, BedLayer, RoomLightLayer, RangeLayer,
  EmptyTileLayer, HeroView, EntityView, Joystick, StickPlaceholder,
} from '../StasisBoard';
import {
  BuildSheet, BuildingSheet, DoorSheet, GateSheet, PodSheet, WholeStation,
  ConfirmDialog, ResultDialog, type DoorHpMap, type GateView,
} from '../StasisPanels';
import type {
  StasisPayload, StasisTick, StasisEvent,
} from '../stasisTypes';

interface Props {
  code: string;
  onLeave: () => void;
}

function buzz(pattern: number | number[]): void {
  if (Platform.OS === 'web') return;
  Vibration.vibrate(pattern);
}

// ── 主组件 ──────────────────────────────────────────────

type Panel =
  | { kind: 'build'; cell: string }
  | { kind: 'building'; cell: string }
  | { kind: 'door'; room: string }
  /** **共用闸门**（v9）：`mine` = 我住在这扇闸门后面，能升级它。 */
  | { kind: 'gate'; gate: string; mine: boolean }
  | { kind: 'pod' };

export default function StasisScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<StasisPayload | null>(null);
  const [showGameOver, setShowGameOver] = useState(false);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [wholeOpen, setWholeOpen] = useState(false);
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [shakeKey, setShakeKey] = useState(0);
  const [runLeft, setRunLeft] = useState(0);
  /** 我的死因（`entity` 被静默体抓住 / `breach` 舱室失压 / `surrender` 弃守）。
   *  v4 之前只有两种死法，靠 `my.surrendered` 分得开；**「在走廊里被抓住」是
   *  第三种**，而且它的提示必须告诉玩家「是因为没躺下」，否则这条规则学不会。 */
  const [deathCause, setDeathCause] = useState<string | null>(null);
  /**
   * 门的当前血量。**刻意从 `game.map.rooms` 里拆出来单独放**：tick 5 Hz 都会带
   * 门血，若并进 `rooms`，`rooms` 就每 tick 换一次引用 —— 于是「有人的舱室叠色」
   * 「休眠舱」「建筑」这些只依赖**归属**的层会跟着每 tick 重建一遍，整张地图
   * （几百个 View）5 Hz 全量 reconcile，帧率直接塌下来。
   * 拆开之后 `rooms` 只在 `stasis_state`（离散动作）时变，只有门那一层按 tick 更新。
   */
  // 每扇门的实时血量覆盖；`shield_ms` 是应急力场的剩余护盾（v13，见 `DoorHpMap`）。
  const [doorHp, setDoorHp] = useState<DoorHpMap>({});
  /** 共用闸门的血量（每 tick 下发，最多 6 条）。 */
  const [gateHp, setGateHp] = useState<Record<string, { hp: number; max: number; broken: boolean }>>({});
  /** 相机是否跟随自己。拖动地图后置 false，点 [回到我] 恢复。 */
  const [following, setFollowing] = useState(true);

  const socketRef = useRef<RoomSocket | null>(null);
  const skipRemoveRef = useRef(false);
  const gameOverShownRef = useRef(false);
  const playersRef = useRef<Room['players']>([]);
  const myUidRef = useRef<number | undefined>(undefined);
  const prevAttackingRef = useRef(false);

  /** 本地预演位置（格坐标）。服务端是权威，这里只为手感。 */
  const predRef = useRef<[number, number]>([0, 0]);
  /** 我是不是躺着 —— 预演循环与摇杆回调都要读它（都在渲染之外跑，走 ref）。 */
  const lyingRef = useRef(false);
  /** 相机有没有为本局对齐过一次（见下面那条 effect；新一局在 `handleStart` 复位）。 */
  const camSnappedRef = useRef(false);
  /**
   * 这一局的几何（服务端下发，v9 起客户端不再自己算）。
   *
   * **必须是 ref**：主循环（每帧）与摇杆回调都要读它，而它们不能进 effect 的
   * 依赖数组（进一次就重建一次循环）。`game.map` 变时（开局 / 重连 / 换图）
   * 同步刷新这里。
   */
  const mapRef = useRef<MapGeom>(EMPTY_MAP);
  const mapSigRef = useRef('');
  /** 地图像素尺寸 `[w, h]`。**相机夹取在每帧现取它**（见 `clampCam` 的注释：
   *  主循环的 effect 依赖里没有地图几何，闭包里那份尺寸会是「还没有地图」时的
   *  0×0，把相机夹到屏幕外去）。 */
  const mapSizeRef = useRef<[number, number]>([0, 0]);
  const dirRef = useRef<[number, number]>([0, 0]);
  /** 自己角色的像素位置（30 fps 直接 setValue，不走 Animated.timing）。 */
  const heroXY = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  /** 相机平移量（像素）。 */
  const camXY = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const camRef = useRef<[number, number]>([0, 0]);
  /** 拖动中：不要被跟随逻辑覆盖。 */
  const draggingRef = useRef(false);
  /** 已经上行过的方向与时刻（发送节流用；与预演朝向 `dirRef` 分开）。 */
  const sentDirRef = useRef<[number, number]>([0, 0]);
  const sentAtRef = useRef(0);
  /** 上一次刷进 `heroXY` 的位置（不变就不刷，省掉一次全图重排）。 */
  const lastHeroRef = useRef<[number, number]>([-1, -1]);
  /**
   * 队友与静默体的位置走 **Animated 直接写**，不走 React state。
   *
   * 这一屏的每一帧元素创建在 Metro dev 构建下要 ~0.6ms（`jsxDEV` +
   * `validateProperty` + `logComponentRender` 这些 dev-only 开销），所以
   * 「每 tick 重渲染整个对局屏」的代价高得离谱 —— CPU ×6 降速实测 p95 达 134ms。
   * 位置是唯一**每 tick 都变**的东西，把它挪出 React 之后，tick 只需要按
   * 2.5 Hz 更新门血/资源/头像胶囊这些慢变量。
   */
  const posAnims = useRef<Map<number, Animated.ValueXY>>(new Map());
  const entAnim = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const tickSeqRef = useRef(0);
  /**
   * 正在开火的炮台格键（**排序去重后的稳定数组**，只在集合变化时换引用）。
   * 位置与它分开：位置每 tick 都变、走 ref；集合很少变、走 state。
   */
  const [firing, setFiring] = useState<string[]>([]);
  const firingKeyRef = useRef('');
  /** 弹丸的落点（静默体当前位置，像素）。 */
  const shotTargetRef = useRef<[number, number]>([0, 0]);
  /** 正在被砸的那扇门的抖动位移（按房号缓存）。 */
  const doorShake = useRef<Map<string, Animated.ValueXY>>(new Map());
  // **十二扇门的抖动值一次性建好**：懒创建的话，`DoorView` 在「静默体刚锁定这扇门」
  // 的那一帧拿不到值（`undefined`），抖动要等下一次重渲染才生效 —— 而那一刻正是
  // 最该抖的时候。
  if (doorShake.current.size === 0) {
    // 房间门与**共用闸门**共用这一张表 —— 两者都可能是静默体的目标，
    // 抖动的驱动逻辑只有一处（见主循环）。
    for (const rid of mapRef.current.roomIds) {
      doorShake.current.set(rid, new Animated.ValueXY({ x: 0, y: 0 }));
    }
    for (const gid of Object.keys(mapRef.current.gates)) {
      doorShake.current.set(gid, new Animated.ValueXY({ x: 0, y: 0 }));
    }
  }
  /** 正在被砸的房号（主循环每 33ms 读一次，所以走 ref 不进 state）。 */
  const attackedRoomRef = useRef<string | null>(null);
  const lastShakenRef = useRef<string | null>(null);

  const players = room?.players ?? [];
  playersRef.current = players;
  const myUid = user?.id;
  myUidRef.current = myUid;

  const nameOf = useCallback((uid: number | null | undefined): string => {
    if (uid === null || uid === undefined) return '玩家';
    if (uid === myUidRef.current) return '你';
    const p = playersRef.current.find((x) => x.id === uid);
    return p?.nickname || p?.username || '玩家';
  }, []);

  const flash = useCallback((text: string) => { notify(text); }, []);

  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => { if (alive) setReduceMotion(v); })
      .catch(() => { /* 平台不支持时按「不减少」处理 */ });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', (v: boolean) => setReduceMotion(v));
    return () => { alive = false; sub.remove(); };
  }, []);

  // ── WS ────────────────────────────────────────────────────
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});

    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const adopt = (g: StasisPayload) => {
      if (!g || !g.players) return;
      setGame(g);
      setRunLeft(g.run_ms ?? 0);
      if (g.map?.rows) {
        // **几何只在真的变了才重建**。`stasis_state`（离散动作后）会带一份完整
        // 载荷，每次都是新对象；无条件 `buildMap` 的话 `StaticMap` 的 `memo`
        // 每建一次造一次就被判成「变了」，几百个格子节点跟着重建 —— 而几何
        // 一局之内是**不变**的（房型在 `create_game` 时定下，之后只变状态）。
        // 签名取「图 id + 整张字符画」，几百字节的比较，比重建便宜三个数量级。
        const sig = `${g.map.id}|${g.map.rows.join('')}`;
        if (sig !== mapSigRef.current) {
          mapSigRef.current = sig;
          mapRef.current = buildMap(g.map);
          mapSizeRef.current = [mapRef.current.mapW, mapRef.current.mapH];
        }
      }
      if (g.map?.rooms) {
        const hp: Record<string, { hp: number; max: number }> = {};
        Object.keys(g.map.rooms).forEach((rid) => {
          const r = g.map.rooms[rid];
          if (r.owner != null) hp[rid] = { hp: r.door_hp, max: r.door_max };
        });
        setDoorHp(hp);
      }
      Object.keys(g.players || {}).forEach((uidStr) => {
        const uid = Number(uidStr);
        const pos = g.players[uidStr]?.pos;
        if (!pos || uid === myUidRef.current) return;
        let a = posAnims.current.get(uid);
        if (!a) { a = new Animated.ValueXY({ x: 0, y: 0 }); posAnims.current.set(uid, a); }
        a.setValue({ x: pos[0] * TILE, y: pos[1] * TILE });
      });
      if (g.entity?.pos) entAnim.setValue({ x: g.entity.pos[0] * TILE, y: g.entity.pos[1] * TILE });
      const mine = g.players[String(myUidRef.current)];
      if (mine?.pos) {
        predRef.current = [mine.pos[0], mine.pos[1]];
        heroXY.setValue({ x: mine.pos[0] * TILE, y: mine.pos[1] * TILE });
      }
    };

    const handleSync = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
      if (msg.game && typeof msg.game === 'object') adopt(msg.game as StasisPayload);
    };

    /**
     * `stasis_state` 的载荷是**扁平展开**的（协议 §「状态分三层下发」：不套 `game`
     * 键），而且它的 `game` 字段是**字符串** `"stasis"` —— 当成 `sync` 处理会被
     * `adopt` 的 `!g.players` 挡掉。表现是「建造/升级完界面上什么也没变」
     * （建筑不出现、门不升级），E2E 抓到。
     */
    const handleState = (msg: SocketMessage) => {
      if (msg.players) adopt(msg as unknown as StasisPayload);
    };
    const handleLobby = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
    };
    const handleStart = (msg: SocketMessage) => {
      const g = msg.game as StasisPayload;
      if (!g) return;
      gameOverShownRef.current = false;
      setShowGameOver(false);
      setPanel(null);
      setFollowing(true);
      camSnappedRef.current = false;      // 新一局重新对齐一次相机
      adopt(g);
    };

    /**
     * 动态快照（5 Hz）——**只更新会动的东西**。
     *
     * 把 tick 当全量状态用会把建筑列表、门的等级、舱室归属全部抹掉（协议明确
     * 分层下发）。所以这里只做字段级合并；同时用服务端位置校正本地预演。
     */
    const handleTick = (msg: SocketMessage) => {
      const tick = msg as unknown as StasisTick;

      // ① 位置：直接写 Animated，**不触发任何 React 重渲染**
      const dynAll = tick.players || {};
      Object.keys(dynAll).forEach((uidStr) => {
        const pos = dynAll[uidStr]?.pos;
        if (!pos) return;
        const uid = Number(uidStr);
        if (uid === myUidRef.current) return;              // 自己由本地预演驱动
        let a = posAnims.current.get(uid);
        if (!a) { a = new Animated.ValueXY({ x: 0, y: 0 }); posAnims.current.set(uid, a); }
        a.setValue({ x: pos[0] * TILE, y: pos[1] * TILE });
      });
      if (tick.entity?.pos) {
        entAnim.setValue({ x: tick.entity.pos[0] * TILE, y: tick.entity.pos[1] * TILE });
        // 弹丸的落点每 tick 都变，**走 ref 不进 state**（同位置那一类）
        shotTargetRef.current = [tick.entity.pos[0] * TILE, tick.entity.pos[1] * TILE];
      }
      // 正在被砸的那扇门（门抖动的驱动源，见主循环）
      // 目标可能是房间，也可能是**共用闸门**（`"gate:G1"`）—— 两者都要抖。
      attackedRoomRef.current = tick.entity?.state === 'attack'
        ? (tick.entity.target_door ?? null) : null;

      // ② 「躺下」按钮的显隐 —— **服务端说能躺才画**（v6，理由见 `canLie` 的注释）。
      //    和开火集合同一类：走快路径、只在布尔值翻转时 setState，既不落后到
      //    2.5 Hz 那一档，也不会每 tick 重渲染整屏。
      const canLieNow = !!tick.players?.[String(myUidRef.current)]?.lie;
      if (canLieNow !== canLieRef.current) {
        canLieRef.current = canLieNow;
        setCanLie(canLieNow);
      }

      // ③ 开火集合：**只在集合真的变了才 setState**。`shots` 每 tick 都是新数组，
      //    直接进 state 会让特效层 5 Hz 重建（同「setGame({...prev}) 即使内容
      //    没变也是新对象」那条教训）。排序是为了让同一个集合算出同一个 key。
      const shotKeys = (tick.shots ?? []).map((s) => s.cell).sort();
      const shotKey = shotKeys.join('|');
      if (shotKey !== firingKeyRef.current) {
        firingKeyRef.current = shotKey;
        setFiring(shotKeys);
      }

      // ② 其余慢变量按 **2.5 Hz** 更新（门血条、资源、头像胶囊、状态文案）。
      //    它们变化很慢，5 Hz 纯属浪费 —— 而每一次 setState 都要重建整屏元素。
      tickSeqRef.current = (tickSeqRef.current + 1) % 2;
      // **位置已经用 Animated 直接写完了，慢变量 2.5 Hz 更新一次就够。**
      // 这里必须**整个跳过 setState**，不能只是把内容换成旧值 —— `setGame({...prev})`
      // 每次都是新对象，React 照样重渲染整屏（只降内容不降调用是白降）。
      if (tickSeqRef.current !== 0) return;

      setGame((prev) => {
        if (!prev) return prev;
        const nextPlayers = { ...prev.players };
        const dyn = tick.players || {};
        Object.keys(dyn).forEach((uid) => {
          const cur = nextPlayers[uid];
          if (!cur) return;
          nextPlayers[uid] = { ...cur, ...dyn[uid] };
        });
        return {
          ...prev,
          clock_ms: typeof tick.clock_ms === 'number' ? tick.clock_ms : prev.clock_ms,
          run_ms: typeof tick.run_ms === 'number' ? tick.run_ms : prev.run_ms,
          phase: (tick.phase as StasisPayload['phase']) || prev.phase,
          entity: tick.entity || prev.entity,
          players: nextPlayers,
        };
      });
      if (tick.doors) setDoorHp(tick.doors);
      if (tick.gates) setGateHp(tick.gates);

      // 用权威位置校正本地预演
      const mine = tick.players?.[String(myUidRef.current)];
      if (mine?.pos) {
        const [sx, sy] = mine.pos;
        const [px, py] = predRef.current;
        const d = Math.hypot(sx - px, sy - py);
        if (d > RECONCILE_SNAP) {
          predRef.current = [sx, sy];
        } else {
          predRef.current = [
            px + (sx - px) * RECONCILE_LERP,
            py + (sy - py) * RECONCILE_LERP,
          ];
        }
      }
      setRunLeft(tick.run_ms ?? 0);
    };

    const handleEvents = (msg: SocketMessage) => {
      const list = (msg.events as StasisEvent[]) || [];
      list.forEach((e) => {
        if (e.type === 'door_breached') {
          flash(e.uid === myUidRef.current ? '你的舱室失压了' : `${nameOf(e.uid)}的舱室失压了`);
          buzz([0, 40, 60, 40]);
        } else if (e.type === 'entity_catch') {
          // **碰到即出局**（v4）。走廊里只可能是「还没躺下」的人 —— 所以这句话
          // 就是这条规则的执法提示，要说得让人明白是自己没躺。
          flash(e.uid === myUidRef.current
            ? '你还没躺下就被静默体抓住了 —— 出局'
            : `${nameOf(e.uid)}在走廊里被静默体抓住了`);
          buzz([0, 60, 40, 60]);
        } else if (e.type === 'player_eliminated') {
          // `cause` 区分三种死法：被鬼抓（entity）/ 舱室失压（breach）/ 弃守（surrender）
          if (e.uid === myUidRef.current) {
            setDeathCause(e.cause ?? 'breach');
            if (e.cause === 'entity') {
              flash('出局：倒计时结束前必须躺下，走廊里遇到静默体就是死');
            }
          }
        } else if (e.type === 'entity_evolve') {
          flash(`静默体进化到 Lv${e.level}`);
        } else if (e.type === 'entity_rage') {
          // **过载**（v13 起它是一个显式事件）：门血跌破 20% 之后它攻速 ×2 ——
          // 这件事原来只有「血条变红」这一个暗示，而它是守门人最该知道的一刻。
          flash('静默体过载了 —— 攻速翻倍，快修门或升门');
        } else if (e.type === 'entity_bound') {
          flash(`束缚弹射器命中，静默体被定住 ${Math.round((e.ms ?? 3000) / 1000)} 秒`);
        } else if (e.type === 'shield_up') {
          flash(`${e.room} 舱的应急力场展开了，${Math.round((e.ms ?? 3000) / 1000)} 秒内门不掉血`);
        } else if (e.type === 'entity_retreat') {
          flash('静默体被打退了，正在负伤撤退');
        } else if (e.type === 'entity_killed') {
          flash('静默体被击杀！');
        } else if (e.type === 'airlock_open') {
          flash('气闸开启，静默体进场了');
          buzz([0, 30, 60, 30]);
        } else if (e.type === 'room_claimed' && e.auto) {
          flash(`自动分配到 ${e.room} 舱`);
        }
      });
    };

    /**
     * 终局。深空休眠的 `game_over` **不带 `game` 载荷**（只有 winner / reason /
     * ranking，见 `ws.broadcast_stasis_game_over`），所以不能像别的对局屏那样
     * 合并 `msg.game` —— 必须自己把 `finished` 置上，否则结算弹窗永远不弹
     * （E2E 抓到：两人都弃守后停在原地）。
     */
    const handleOver = (msg: SocketMessage) => {
      if (msg.room) setRoom(msg.room as Room);
      setGame((prev) => (prev ? {
        ...prev,
        finished: true,
        winner: (msg.winner as string) ?? prev.winner,
        reason: (msg.reason as string) ?? prev.reason,
      } : prev));
    };
    const handleError = (msg: SocketMessage) => { if (msg.error) notify(String(msg.error)); };

    ws.on('sync', handleSync);
    ws.on('room_update', handleLobby);
    ws.on('game_start', handleStart);
    ws.on('stasis_tick', handleTick);
    ws.on('stasis_events', handleEvents);
    ws.on('stasis_state', handleState);
    ws.on('game_over', handleOver);
    ws.on('error', handleError);
    // **构造函数不连接** —— 必须先注册 handler 再 connect，否则握手回来的
    // `sync` 会在 handler 注册之前被丢掉（新屏白屏、只剩一个空地图）。
    ws.connect();

    return () => {
      ws.off('sync', handleSync);
      ws.off('room_update', handleLobby);
      ws.off('game_start', handleStart);
      ws.off('stasis_tick', handleTick);
      ws.off('stasis_events', handleEvents);
      ws.off('stasis_state', handleState);
      ws.off('game_over', handleOver);
      ws.off('error', handleError);
      ws.close();
    };
    // 依赖是稳定的 token/code —— **不要**把 myUid 放进来（v1.9 的教训：
    // 座位号开局前是 null、收到 game_start 后才变，放进依赖会让 socket 重连一次，
    // 服务端把这次「连接关闭」当成掉线，自己那格就一直显示断线）。
  }, [token, code, flash, heroXY, nameOf]);

  // 返回双路径（原生 beforeRemove + Web backGuard）
  useEffect(() => {
    const un = navigation.addListener('beforeRemove', (e: any) => {
      if (Platform.OS === 'web' || skipRemoveRef.current) return;
      e.preventDefault();
      setConfirm('leave');
    });
    const off = onConfirmLeave(() => setConfirm('leave'));
    return () => { un(); off(); };
  }, [navigation]);

  // ── 派生状态 ──────────────────────────────────────────────
  const phase = game?.phase || 'run';
  const rooms = game?.map?.rooms ?? {};
  // 几何来自 ref（主循环与它共用同一份），渲染读它 —— `game.map` 变时上面
  // 已经重建过了，所以这里不会读到旧的。
  const map = mapRef.current;
  const MAP_W = map.mapW;
  const MAP_H = map.mapH;
  /** 共用闸门的显示状态：几何（`map.gates`）＋ 状态（`game.map.gates`）＋ tick 血量。 */
  const gateView = useMemo(() => {
    const out: Record<string, GateView> = {};
    for (const gid of Object.keys(map.gates)) {
      const st = game?.map?.gates?.[gid];
      if (!st) continue;
      const tick = gateHp[gid];
      out[gid] = {
        level: st.level, broken: tick?.broken ?? st.broken,
        hp: tick?.hp ?? st.hp, max: tick?.max ?? st.max,
      };
    }
    return out;
  }, [map, game?.map?.gates, gateHp]);
  // **门的点击回调必须是稳定身份**（v13）：`DoorLayer` 是 `memo` 的，而
  // `() => setPanel(...)` 每次渲染都是新闭包 ⇒ props 永远浅比较不等，memo 白写。
  // 做法与 `lyingRef` 同一条：把最新实现放进 ref，回调本身 `useCallback([])`。
  const doorPressRef = useRef<(rid: string) => void>(() => {});
  const onDoorPress = useCallback((rid: string) => doorPressRef.current(rid), []);
  const gatePressRef = useRef<(gid: string) => void>(() => {});
  const onGatePress = useCallback((gid: string) => gatePressRef.current(gid), []);

  const my = myUid != null ? game?.players?.[String(myUid)] : undefined;
  const myRoom = my?.room ?? null;
  const myAlive = !!my?.alive && !my?.surrendered;
  const myLying = !!my?.lying;
  // 门的点击行为（每帧重新赋值，但回调身份不变 —— 见 `onDoorPress` 的注释）
  doorPressRef.current = (rid: string) => {
    if (rid !== myRoom) return;
    // 门在跑动阶段也能点（升门 = 直接满血，是倒计时里最划算的一笔）
    if (!myLying) { notify('要先躺进休眠舱才能升级'); return; }
    setPanel({ kind: 'door', room: rid });
  };
  gatePressRef.current = (gid: string) => {
    if (!myLying) { notify('要先躺进休眠舱才能修补'); return; }
    setPanel({ kind: 'gate', gate: gid,
               mine: myRoom != null && !!map.gates[gid]?.rooms.includes(myRoom) });
  };
  // 给渲染之外的两个循环（预演 + 摇杆回调）读的镜像。写成 useEffect 会在
  // 「tick 刚把 lying 置上、effect 还没跑」的那一帧里读到旧值，所以直接赋值。
  lyingRef.current = myLying;

  /**
   * 「躺下」按钮的显隐 —— **服务端说了算**（v6）。
   *
   * 判据是「人在休眠舱的九宫格之内」，但它**不由客户端计算**：服务端每 tick 在
   * `players[uid].lie` 里发一个布尔值，这里只读它（见 `handleTick` 的快路径）。
   *
   * 为什么不再自己算：这条路走过三段弯路，每一段都是「看起来对、其实错」——
   * 1. **只看服务端 `my.pos`**（原实现）：位置只在 2.5 Hz 那条慢变量路径上，
   *    玩家已经站到床边了按钮还要等最多 0.4 秒。
   * 2. **只看预演位置**：按钮立刻出现，但预演与服务端稳态差约 0.14 格、跨格子
   *    边界时能差出一整格 —— 「按钮在、点下去被服务端拒」，**UI 在骗玩家**。
   * 3. **两者取交集**：不骗人了，但**规则太窄**（5 格），那 0.14 格跨一次边界就够
   *    把服务端甩出去，于是「屏幕上明明站在床上、按钮就是不出现」；玩家**没有任何
   *    办法自查**，E2E 约 1/3 概率红。
   * 4. v5 一度**把判据放宽到整间舱室**（42 格）来掩盖它 —— 边界推远了，但「客户端
   *    持有一份服务端判据的副本」这件事本身没变。
   *
   * v6 把判据收回九宫格（用户要求），同时**把判定权整个交给服务端**：不再有第二份
   * 实现，也就不可能漂移。这与项目里象棋 `legal` / UNO `playable` / 飞行棋 `path` /
   * 俄罗斯方块 `shapes` 是同一条约定 —— **客户端一格规则都不重写**。
   *
   * 代价是按钮最多晚一个 tick（200ms）出现。玩家这时正在**站定**准备点它，
   * 200ms 落在「1 秒」那一档里；换来的是**按钮永不撒谎**。
   */
  const [canLie, setCanLie] = useState(false);
  const canLieRef = useRef(false);
  const entity = game?.entity ?? null;
  // 渲染形态三档：撤退（浅蓝）/ 过载（橙，门血见底时攻速 ×2）/ 常态（红）。
  // **不是颜色单独承载信息** —— 旁边永远有 `Lv.n 静默体` 文字与状态条文案。
  const entityMoodNow = (() => {
    if (!entity) return 'normal';
    if (entity.state === 'retreat') return 'retreat';
    // 目标可能是房间门，也可能是**共用闸门**（`gate:G1`）—— 后者的过载判据
    // 取闸门自己的血量（它就是当下在挨打的那扇门）。
    const tid = entity.target_door;
    if (tid && tid.startsWith('gate:')) {
      const g = gateView[tid.slice(5)];
      return g && g.hp <= OVERLOAD_DOOR_RATIO * g.max ? 'overload' : 'normal';
    }
    const t = tid ? rooms[tid] : null;
    if (t && t.door_hp <= OVERLOAD_DOOR_RATIO * t.door_max) return 'overload';
    return 'normal';
  })();
  // 「正在砸我」—— 目标是我的房门，**或**是我这间房所在套间的共用闸门
  // （闸门破了就是我暴露，同样该震一下）。
  const attackingMe = !!entity && entity.state === 'attack' && myRoom != null
    && (entity.target_door === myRoom
        || entity.target_door === `gate:${map.rooms[myRoom]?.gate ?? ''}`);

  useEffect(() => {
    if (attackingMe && !prevAttackingRef.current) setShakeKey((k) => k + 1);
    prevAttackingRef.current = attackingMe;
  }, [attackingMe]);

  const seatColorOf = useCallback((uid: number | null | undefined): string => {
    if (uid === null || uid === undefined) return ON_SPACE_SOFT;
    const p = game?.players?.[String(uid)];
    const key = p ? game?.seat_colors?.[p.seat] : undefined;
    // 身份色在深色底上要提亮一档才能读（playerColors 压在 stasisSpace 上只有
    // 2.98~3.88）。`stasisSeatColors` 就是 theme 里那一档（实测 vs 页面底
    // 9.39~15.03 全 ≥4.5 ✓），这里直接复用，不另写 hex。
    return (key && stasisSeatColors[key]) || ON_SPACE_SOFT;
  }, [game?.players, game?.seat_colors]);

  /** 对局阶段顶部那栏是**正计时**（v8）。
   *
   *  原来是倒计时（`game_ms - clock_ms`，最后一分钟转红）。用户要求「不要求 6 分钟
   *  内打死，时间不限」之后**没有终点可倒**了 —— 一局打到分出胜负为止（打死它，
   *  或者全员失压），所以这栏改成「已经守了多久」。压力不再由倒计时提供，而是由
   *  **静默体的等级**提供（v13 起它**只在砸门时**累积进化进度：啃够
   *  `EVOLVE_ATTACK_MS` 就升一级，10 级封顶）—— 所以「打跑它」现在是真的
   *  能让它停止变强，而不是只让它晚回来一点。 */
  const elapsedMs = game?.clock_ms ?? 0;

  const dcSeat = players.findIndex((p) => !p.connected && p.id !== myUid);
  const dcLeft = useDisconnectCountdown(dcSeat >= 0 && phase !== 'run',
    dcTimeout ?? (dcSeat >= 0 ? players[dcSeat]?.reconnect_left : null));

  const statusText = (() => {
    if (phase === 'run') return '气闸即将开启 —— 找一间舱室进去';
    if (!entity) return '静默体正在通道中游荡';
    const lv = `Lv${entity.level}`;
    // **`target_door` 可能是 null**（游荡中/刚撤退完），这时 `rooms[null]` 是
    // undefined、`nameOf(undefined)` 会返回兜底的「玩家」，文案就成了
    // 「正在逼近玩家的舱室」—— 像个人名但不是。所以先判空再取名。
    //
    // 目标还可能是**共用闸门**（`gate:G1`）：它不属于任何一个人，所以文案取
    // 「住在它后面的那几间房」的房号，而不是某个玩家的名字。
    const td = entity.target_door;
    if (td && td.startsWith('gate:')) {
      const g = map.gates[td.slice(5)];
      const who = g ? g.rooms.join('/') : '套间';
      if (entity.state === 'attack') return `静默体 ${lv} 正在撞${who}的共用外闸`;
      return `静默体 ${lv} 正在逼近${who}的共用外闸`;
    }
    const targetOwner = td ? rooms[td]?.owner : null;
    const who = targetOwner != null ? nameOf(targetOwner) : '';
    if (entity.state === 'attack') return who ? `静默体 ${lv} 正在撞${who}的气密门` : `静默体 ${lv} 正在撞门`;
    if (entity.state === 'chase') return `静默体 ${lv} 正在通道里追${nameOf(entity.target_uid)}`;
    if (entity.state === 'approach') return who ? `静默体 ${lv} 正在逼近${who}的舱室` : `静默体 ${lv} 正在逼近`;
    if (entity.state === 'retreat') return `静默体 ${lv} 负伤撤退`;
    if (entity.state === 'regenerate') return `静默体 ${lv} 退回气闸再生`;
    return `静默体 ${lv} 在通道里游荡`;
  })();

  const myDoor = myRoom ? rooms[myRoom] : null;
  const myDoorRatio = myDoor
    ? clampRatio(doorHp[myRoom as string]?.hp ?? myDoor.door_hp,
                 doorHp[myRoom as string]?.max ?? myDoor.door_max)
    : 0;
  const lowDoor = myAlive && myDoorRatio > 0 && myDoorRatio < ALERT_DOOR_RATIO;
  const glow = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!lowDoor) { glow.setValue(0); return; }
    if (reduceMotion) { glow.setValue(1); return; }
    glow.setValue(0.2);
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(glow, { toValue: 1, duration: 900, useNativeDriver: Platform.OS !== 'web' }),
        Animated.timing(glow, { toValue: 0.2, duration: 900, useNativeDriver: Platform.OS !== 'web' }),
      ]),
      { iterations: PULSE_CYCLES },
    );
    anim.start();
    return () => anim.stop();
  }, [lowDoor, reduceMotion, glow]);

  // ── 本地预演 + 相机 ───────────────────────────────────────
  const { width: vw, height: vh } = useWindowDimensions();
  const viewportH = Math.max(200, vh - 150 - 132);

  /**
   * 相机夹取。**四个边都多给 `PAN_MARGIN` 的越界余量**。
   *
   * 原来夹死在 `[vw - MAP_W, 0]`：地图正好铺满视口的那个方向（竖屏下是宽度）
   * 根本推不动，而**地图最外圈的格子在视口边缘只能露出半格** —— 那半格既看不全
   * 也点不准（用户实测：「地图边界需要支持多滑动一点出去，不然边界处的格子点不到」）。
   * 给两格余量之后，最外圈也能被推到屏幕中间。
   *
   * ⚠️ **地图尺寸从 ref 里现取，不从闭包里取**（v9 修，E2E 抓到）。v9 起几何是
   * React state（`mapRef`），而下面那个主循环的 effect 依赖里**没有地图几何** ——
   * 于是它在挂载时捕获的 `clampCam` 是用**还没有地图时**（`EMPTY_MAP`，mapW=mapH=0）
   * 的尺寸算的，而且**再也不会更新**：夹取区间变成 `[vw-88, 88]`（宽度）与
   * `[viewportH-88, 88]`（高度），相机于是被推到 (302, 466) 这种位置 ——
   * **整张地图被推出屏幕，玩家在跑动阶段看不到自己站在哪**（E2E 的症状是
   * 「视口里找不到一块落在地图上的空地下手」）。
   * v9 之前几何是模块常量，所以这个坑是**那一轮重写新引入的**。
   * 用 ref 读尺寸，闭包再旧也拿到的是当前值；尺寸还没到时按视口宽兜底，
   * 夹取区间退化成 `[-88, 88]` 这种正常的小范围。
   */
  const clampCam = (x: number, y: number): [number, number] => {
    const mw = mapSizeRef.current[0] || vw;
    const mh = mapSizeRef.current[1] || viewportH;
    return [
      Math.max(vw - mw - PAN_MARGIN, Math.min(PAN_MARGIN, x)),
      Math.max(viewportH - mh - PAN_MARGIN, Math.min(PAN_MARGIN, y)),
    ];
  };

  useEffect(() => {
    let last = Date.now();
    let raf = 0;
    /**
     * 主循环：**本地预演 + 相机 + 上行节流 + 门抖动**，一帧跑一次。
     *
     * ⚠️ **v8 从 `setInterval(..., 33)` 换成 `requestAnimationFrame`**（用户实测
     * 报「帧率太低、移动的手感不好」）。33ms 的定时器**把画面更新钉死在 30 Hz**，
     * 而显示器是 60 Hz —— rAF 实测恒为 60.0 fps（p95 16.7ms），主线程根本不忙，
     * 也就是说那 30 Hz 是**我们自己设的上限**，不是性能上限。用 MutationObserver
     * 数下来：移动中 4 秒里角色与地图各被写了 123 次 = 30.8 Hz，正好是定时器的
     * 节拍。换 rAF 之后角色是逐帧平滑移动，而不是每秒 30 跳。
     *
     * rAF 还有两个附带好处：①标签页不可见时浏览器会停掉它（定时器不会，
     * 后台标签一直在算）；②回调的节拍跟显示刷新率走，120 Hz 的屏上也能跑满。
     */
    const step = () => {
      raf = requestAnimationFrame(step);
      const now = Date.now();
      // **用真实流逝时间，不用固定 0.033**。回调被主线程挤晚（60ms 才跑一次）
      // 时，固定步长会让本地预演比服务端**走得慢**，于是每个 tick 都被权威位置
      // 往前拽一截 —— 看起来就是「一冲一顿」。两边都用真实时间才会同步。
      // 换 rAF 之后这一条更关键：帧间隔本来就不固定（16.7 / 33.3 交替）。
      const dt = Math.min(0.2, (now - last) / 1000);
      last = now;

      const [dx, dy] = dirRef.current;
      const p = predRef.current;
      if ((dx !== 0 || dy !== 0) && !lyingRef.current) {
        const step = PLAYER_SPEED * dt;
        const gm = mapRef.current;
        const nx = p[0] + dx * step;
        if (canStand(gm, p, [nx, p[1]])) p[0] = nx;
        const ny = p[1] + dy * step;
        if (canStand(gm, p, [p[0], ny])) p[1] = ny;
      }
      // **只在真的变了才 setValue**。这两行原本每 33ms 无条件刷一次，而
      // `camXY` 动的是装着几百个 DOM 节点的整张地图 —— 站着不动的时候也在
      // 白白重排，实测（CPU ×6 降速）它是 tick 之外最大的一笔开销。
      if (p[0] !== lastHeroRef.current[0] || p[1] !== lastHeroRef.current[1]) {
        lastHeroRef.current = [p[0], p[1]];
        heroXY.setValue({ x: p[0] * TILE, y: p[1] * TILE });
      }

      // 「躺下」按钮的显隐**不在这里算**（v6）—— 它是服务端随 tick 下发的
      // `players[uid].lie`，见 `handleTick` 的快路径。本地预演的位置只用来画
      // 自己那个小人，不再参与任何判定。

      // 上行：**发送节流设在主循环里，不设在摇杆里**（摇杆那个节流会把本地预演
      // 的朝向一起拖慢，就是「移动非常卡」）。起始/停止两个边沿立即发，
      // 中途转向最多晚 SEND_MIN_MS —— 服务端一个 tick 200ms，80ms 足够让它
      // 每次积分都拿到不早于半个 tick 的方向。
      const [sx, sy] = sentDirRef.current;
      if (dx !== sx || dy !== sy) {
        const stopping = dx === 0 && dy === 0;
        const starting = sx === 0 && sy === 0;
        if (stopping || starting || now - sentAtRef.current >= SEND_MIN_MS) {
          sentDirRef.current = [dx, dy];
          sentAtRef.current = now;
          socketRef.current?.stasisMove(dx, dy);
        }
      }

      // **门被砸时的抖动**（一帧一次的正弦位移，振幅 2.2px）。
      // 它读的是「静默体正在撞哪扇门」，而不是门血 —— 血条是**结果**（每帧都在
      // 变，看不出事件），抖动是**事件**，一眼就抓得住。被砸的门上另有红框与
      // 「◤被锁定」，三条通道叠加（WCAG 1.4.1）。
      // `reduceMotion` 下完全不抖（WCAG 2.2.2），红框与文字照旧。
      //
      // ⚠️ 相位用 `now / 42`：这个 42 是**按 30 Hz 调的**，换 rAF 之后同样的
      // 42 会让抖动快一倍（每帧的相位差减半）。除以 2 保持原来的频率观感。
      const ar = reduceMotion ? null : attackedRoomRef.current;
      if (ar) {
        doorShake.current.get(ar)?.setValue({ x: Math.sin(now / 84) * 2.2, y: 0 });
      }
      if (lastShakenRef.current && lastShakenRef.current !== ar) {
        doorShake.current.get(lastShakenRef.current)?.setValue({ x: 0, y: 0 });
      }
      lastShakenRef.current = ar;

      if (following && !draggingRef.current) {
        // **阻尼跟随**（不是硬跟随）：服务端每 tick 都会把本地预演往权威位置
        // 拉一截，硬跟随会把这截校正**原样传进画面** —— 走起来是「一冲一顿」。
        // 加一层 lerp 之后，校正被抹成平滑的位移（见 `增强调研.md` §10.5）。
        // 另外沿移动方向留一点点**前视**，给「往哪走」留出可视空间。
        const leadX = dirRef.current[0] * CAM_LEAD * TILE;
        const leadY = dirRef.current[1] * CAM_LEAD * TILE;
        const [tx, ty] = clampCam(
          vw / 2 - p[0] * TILE + leadX, viewportH / 2 - p[1] * TILE + leadY);
        // **阻尼要跟帧率无关**：`CAM_LERP` 是按「33ms 一帧」标定的（0.14/帧
        // ≈ 0.2 秒到位）。rAF 下一帧只有 16.7ms，照抄 0.14 会让相机快一倍 ——
        // 手感一变，之前调它的理由（「再大就等于硬跟随了」）就不成立了。
        // 按 dt 折算成等效时间常数，30 Hz / 60 Hz / 120 Hz 上观感一致。
        const lerp = reduceMotion ? 1 : 1 - Math.pow(1 - CAM_LERP, dt / 0.033);
        let ax = camRef.current[0] + (tx - camRef.current[0]) * lerp;
        let ay = camRef.current[1] + (ty - camRef.current[1]) * lerp;
        // 差得够近就吸附 —— 否则会永远在「差 0.01px」上重排整张地图
        if (Math.abs(tx - ax) < 0.4) ax = tx;
        if (Math.abs(ty - ay) < 0.4) ay = ty;
        if (Math.abs(ax - camRef.current[0]) > 0.2 || Math.abs(ay - camRef.current[1]) > 0.2) {
          camRef.current = [ax, ay];
          camXY.setValue({ x: ax, y: ay });
        }
      }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [following, heroXY, camXY, vw, viewportH, reduceMotion]);

  // 初始把相机摆到自己身上 —— **一局只对齐一次**（`camSnappedRef`）。
  //
  // 依赖里原来写的是 `myRoom === null`，那是「舱室一旦占下就不再变」时代的
  // 替身。v11 起**舱室跟着人走**（躺下之前可以随便换房），这个布尔值在跑动
  // 阶段会来回翻转 —— 每翻一次就把相机猛地拉回自己身上，玩家刚拖开看地图
  // 就被拽回来。改成显式的一次性守卫：第一次拿到位置时对齐，之后交给
  // 跟随/拖动（新开一局在 `handleStart` 里复位）。
  const hasMyPos = !!my?.pos;
  useEffect(() => {
    if (!hasMyPos || camSnappedRef.current || !my?.pos) return;
    camSnappedRef.current = true;
    const [tx, ty] = clampCam(vw / 2 - my.pos[0] * TILE, viewportH / 2 - my.pos[1] * TILE);
    camRef.current = [tx, ty];
    camXY.setValue({ x: tx, y: ty });
  }, [hasMyPos, vw, viewportH]);

  // 气闸开启那一刻（`run` → `playing`）**把相机拉回自己身上并恢复跟随**。
  //
  // 这一刻所有人被送进自己的舱室（没占到房的由服务端自动分配并**传送**过去），
  // 镜头不回来的话玩家会盯着一片没人的地图、而自己已经换了地方 —— 而且拖动过
  // 地图的人 `following` 是 false，连跟随都不会恢复。
  //
  // 上面那条一次性对齐原来兼着这件事（`myRoom` 在那一刻由 null 变成房间号），
  // v11 之后它不能再兼了（跑动阶段会来回翻转）。所以这里**按阶段变化显式触发**：
  // 这是「拖动过地图」之后唯一一次自动复位，其余时刻一律尊重玩家的平移。
  const camPhaseRef = useRef(phase);
  useEffect(() => {
    const prev = camPhaseRef.current;
    camPhaseRef.current = phase;
    if (prev !== 'run' || phase !== 'playing' || !my?.pos) return;
    setFollowing(true);
    const [tx, ty] = clampCam(vw / 2 - my.pos[0] * TILE, viewportH / 2 - my.pos[1] * TILE);
    camRef.current = [tx, ty];
    camXY.setValue({ x: tx, y: ty });
  }, [phase, my?.pos, vw, viewportH]);

  /** 拖动地图 = 平移相机并退出跟随。用 `onMoveShouldSetPanResponder` 而不是
   *  `onStartShouldSet...`：**单击要能穿透到格子上的按钮**（建造/升级），
   *  只有真的拖动了才抢手势。 */
  const panResponder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => false,
    // **必须用 capture 阶段**：门与休眠舱是 `TouchableOpacity`，按下那一刻就
    // 抢走了 responder，父层的 `onMoveShouldSetPanResponder` 从此不会被问到 ——
    // 表现是「拖动屏幕地图纹丝不动」。capture 让父层在冒泡之前先看一眼，
    // 位移超过 6px 才接管（单击照旧穿透给格子上的按钮）。
    onMoveShouldSetPanResponderCapture: (_e, g) => Math.hypot(g.dx, g.dy) > 6,
    onMoveShouldSetPanResponder: (_e, g) => Math.hypot(g.dx, g.dy) > 6,
    onPanResponderGrant: () => { draggingRef.current = true; setFollowing(false); },
    onPanResponderMove: (_e, g) => {
      const [bx, by] = camRef.current;
      const [nx, ny] = clampCam(bx + g.dx, by + g.dy);
      camXY.setValue({ x: nx, y: ny });
    },
    onPanResponderRelease: (_e, g) => {
      const [bx, by] = camRef.current;
      camRef.current = clampCam(bx + g.dx, by + g.dy);
      draggingRef.current = false;
    },
    onPanResponderTerminate: () => { draggingRef.current = false; },
  }), [camXY, vw, viewportH]);

  const backToMe = () => {
    setFollowing(true);
    const p = predRef.current;
    const [tx, ty] = clampCam(vw / 2 - p[0] * TILE, viewportH / 2 - p[1] * TILE);
    camRef.current = [tx, ty];
    camXY.setValue({ x: tx, y: ty });
  };

  // ── 动作 ──────────────────────────────────────────────────
  const canAfford = (res: 'bio' | 'alloy', amount: number): boolean => {
    const have = res === 'bio' ? (my?.bio ?? 0) : (my?.alloy ?? 0);
    return have >= amount;
  };

  /** 摇杆回调：**只更新预演朝向**，上行走主循环的节流（见那边的注释）。
   *
   *  **躺着的时候一律压成 0**：服务端本来就会丢掉躺着的移动输入，
   *  客户端要是照着自己的预演把人画着走了，两边就分叉了（起身那一下会被拽回来）。
   */
  const onDir = useCallback((dx: number, dy: number) => {
    if (lyingRef.current) {
      dirRef.current = [0, 0];
      if (dx !== 0 || dy !== 0) flash('你已经躺下了 · 点「起身」才能走动');
      return;
    }
    dirRef.current = [dx, dy];
  }, [flash]);

  // **只有「躺下」，没有「起身」**（v4）：躺下不可逆，躺下之后整局都在这张床上。
  // 「起身去救队友」的玩法已经删掉（`增强调研.md` §9.3），`stasis_stand` 连协议
  // 一起删了 —— 所以这里也没有对应的回调。
  const doLie = () => { socketRef.current?.stasisLie(); setPanel(null); };

  const doBuild = (kind: string, cell: string) => {
    const info = BUILDINGS[kind];
    if (!info) return;
    if (!canAfford(info.res, info.cost)) {
      const have = info.res === 'bio' ? (my?.bio ?? 0) : (my?.alloy ?? 0);
      // 买不起**不禁用按钮**：RNW 下 disabled = pointer-events:none，点了既没反应
      // 也没提示，用户只会觉得「点了没反应」（重力四子棋满列的教训）
      notify(`${info.name}还差 ${info.cost - have} ${info.res === 'bio' ? '生物能' : '合金'}`);
      return;
    }
    socketRef.current?.stasisBuild(kind, cell);
    setPanel(null);
  };

  /**
   * 升级。`alloy` 是**第 6 级起额外要的那份合金**（v7）。
   *
   * **两个资源都要在客户端先判一道**：只判生物能的话，第 6 级往上的按钮看起来是
   * 可点的，点下去却被服务端以「合金不够」拒掉 —— 玩家只会觉得「点了没反应」
   * （原型那条「有前置但弹窗里看不出来」是同一类事故）。
   */
  const doUpgrade = (target: string, cost: number | null, res: 'bio' | 'alloy', alloy = 0) => {
    if (cost === null) { notify('已经是满级'); return; }
    if (!canAfford(res, cost)) {
      const have = res === 'bio' ? (my?.bio ?? 0) : (my?.alloy ?? 0);
      notify(`还差 ${cost - have} ${res === 'bio' ? '生物能' : '合金'}`);
      return;
    }
    if (alloy > 0 && (my?.alloy ?? 0) < alloy) {
      notify(`还差 ${Math.ceil(alloy - (my?.alloy ?? 0))} 合金`);
      return;
    }
    socketRef.current?.stasisUpgrade(target);
  };

  const doPatch = (uid: number | null) => {
    const cost = uid == null || uid === myUid ? PATCH_SELF : PATCH_REMOTE;
    if (!canAfford('bio', cost)) {
      notify(`修补还差 ${cost - (my?.bio ?? 0)} 生物能`);
      return;
    }
    socketRef.current?.stasisPatch(uid);
  };

  /** 修补**共用闸门**（v9）。一律按「帮别人」的 1.5 倍算 —— 引擎那边也是这么定的
   *  （它是两个人的前线，但不该免费）。 */
  const doPatchGate = (gid: string) => {
    if (!gid) return;
    if (!canAfford('bio', PATCH_REMOTE)) {
      notify(`修补外闸还差 ${PATCH_REMOTE - (my?.bio ?? 0)} 生物能`);
      return;
    }
    socketRef.current?.stasisPatchGate(gid);
    setPanel(null);
  };

  const doSurrender = () => {
    setConfirm(null);
    setPanel(null);
    socketRef.current?.surrender();
  };

  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    setPanel(null);
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

  // 终局弹窗（服务端 finished 或 phase=over 都算）
  useEffect(() => {
    if ((game?.finished || phase === 'over') && !gameOverShownRef.current) {
      gameOverShownRef.current = true;
      setShowGameOver(true);
    }
  }, [game?.finished, phase]);

  // ── 渲染 ──────────────────────────────────────────────────
  const myBuildings = useMemo(() => {
    if (!myRoom || !rooms[myRoom]) return [] as { cell: string; kind: string; level: number }[];
    return rooms[myRoom].buildings;
  }, [myRoom, rooms]);

  // 自己舱室里各类建筑各有几座 —— 建造菜单用它显示「纳米修复器 3/4」。
  // 上限在 `stasisRules.BUILDINGS[kind].max`（与服务端 `stasis.MAX_REPAIRS` 同值）。
  const myBuiltCount = useMemo(() => {
    const m: Record<string, number> = {};
    myBuildings.forEach((b) => { m[b.kind] = (m[b.kind] ?? 0) + 1; });
    return m;
  }, [myBuildings]);

  const buildingAt = useMemo(() => {
    const m: Record<string, { kind: string; level: number; owner: boolean }> = {};
    Object.keys(rooms).forEach((rid) => {
      const r = rooms[rid];
      if (!r.owner) return;
      const mine = r.owner === myUid;
      r.buildings.forEach((b) => { m[b.cell] = { kind: b.kind, level: b.level, owner: mine }; });
    });
    return m;
  }, [rooms, myUid]);

  /**
   * 床位那两个派生值用**内容签名**当依赖。
   *
   * `game.players` 每 tick 都是新对象（tick 会字段级合并出新的 player 对象），
   * 直接拿它当依赖会让 `bedRates` / `inBedRooms` 每 tick 换引用 —— 于是
   * `BedLayer` 的 `memo` 形同虚设，12 张床（含 SVG 图标）跟着 5 Hz 重建。
   * 签名只编码「谁在哪间房、几级床、躺没躺」，这三件事变了才重建。
   */
  const bedKey = useMemo(() => map.roomIds.map((rid) => {
    const owner = rooms[rid]?.owner;
    if (owner == null) return '-';
    const p = game?.players?.[String(owner)];
    return `${owner}:${p?.bed_level ?? 1}:${p?.in_bed ? 1 : 0}`;
  }).join('|'), [map, rooms, game?.players]);

  const bedRates = useMemo(() => {
    const m: Record<string, number> = {};
    map.roomIds.forEach((rid, i) => {
      const seg = bedKey.split('|')[i];
      if (seg === '-') return;
      m[rid] = BED_RATE[Number(seg.split(':')[1]) - 1] ?? 1;
    });
    return m;
  }, [map, bedKey]);

  const inBedRooms = useMemo(() => {
    const m: Record<string, boolean> = {};
    map.roomIds.forEach((rid, i) => {
      const seg = bedKey.split('|')[i];
      if (seg === '-' || seg.split(':')[2] !== '1') return;
      m[rid] = true;
    });
    return m;
  }, [map, bedKey]);

  // **回调必须稳定**，否则上面几个 `memo` 图层每 tick 都会被判定成「变了」。
  const openTileRef = useRef<(x: number, y: number) => void>(() => {});
  const panelRef = useRef(setPanel);
  panelRef.current = setPanel;
  const myRoomRef = useRef<string | null>(null);
  myRoomRef.current = myRoom;
  const onBedPress = useCallback((rid: string) => {
    if (rid === myRoomRef.current) panelRef.current({ kind: 'pod' });
  }, []);
  const onBuildingPress = useCallback((key: string, owner: boolean) => {
    if (owner) panelRef.current({ kind: 'building', cell: key });
  }, []);

  const onOpenTile = useCallback((x: number, y: number) => {
    openTileRef.current(x, y);
  }, []);

  const openTileImpl = (x: number, y: number) => {
    // **跑动阶段也能点**（v4）：原型就是倒计时内上床、并且「在倒计时最后五秒
    // 攒够 25 金币就把床升上去」。原来这里写的是 `phase !== 'playing'`，等于
    // 倒计时里点床点门全都没反应。
    if (!myAlive || phase === 'over') return;
    const key = cellKey(x, y);
    const rid = mapRef.current.roomAt(x, y);
    if (rid !== myRoom) { notify('只能在自己的舱室里建造'); return; }
    const bed = mapRef.current.rooms[myRoom as string].bed;
    if (bed[0] === x && bed[1] === y) { setPanel({ kind: 'pod' }); return; }
    // 没躺下就建不了（服务端的硬规则）—— 在这里先拦一道并说清原因，
    // 别让玩家打开面板、点了按钮才收到一句 error（「点了没反应」那一类）。
    if (!myLying) { notify('要先躺进休眠舱才能建造与升级'); return; }
    const b = buildingAt[key];
    if (b) {
      if (b.owner) setPanel({ kind: 'building', cell: key });
      else notify('这是队友的舱室');
      return;
    }
    setPanel({ kind: 'build', cell: key });
  };
  openTileRef.current = openTileImpl;

  const mapLayer = (
    <Animated.View
      testID="stasis-map"
      style={[styles.map, { width: MAP_W, height: MAP_H, transform: camXY.getTranslateTransform() }]}
    >
      <StaticMap map={map} />

      {/* 占用亮灯：有人的舱室叠一层暖色 + 描边（静态层画的是「空置」态）。
          用**色相**而不是明度 —— 加亮会和地板糊在一起，换色才一眼看得出哪间有人。 */}
      <RoomLightLayer map={map} rooms={rooms} />

      {/* 攻击范围圈：点开炮台面板时画（见 `RangeLayer`）。画在建筑**之下** ——
          圈是背景信息，压住建筑会让「这一格是什么」读不出来。 */}
      <RangeLayer
        cell={panel?.kind === 'building' && buildingAt[panel.cell]?.kind === 'turret'
          ? panel.cell : null}
        level={panel?.kind === 'building' ? (buildingAt[panel.cell]?.level ?? 1) : 1}
      />

      {/* **门与共用闸门的一整层**（v13，`DoorLayer` 是 `memo` 的）。
          原来这两段直接长在这里 —— 每次 2.5 Hz 的慢变量刷新都要重建 12~18 棵
          `DoorView` 子树（每个含一张 SVG 贴图）。抽层 + **稳定回调**之后，
          只有内容真的变了的那几扇门才重建。 */}
      <DoorLayer
        map={map}
        rooms={rooms}
        gates={gateView}
        doorHp={doorHp}
        targeted={entity?.target_door ?? null}
        shakes={doorShake.current}
        onDoorPress={onDoorPress}
        onGatePress={onGatePress}
      />

      {/* 休眠舱 */}
      <BedLayer map={map} rooms={rooms} inBed={inBedRooms} bedRate={bedRates} onPress={onBedPress} />

      {/* 建筑 */}
      <BuildingLayer buildings={buildingAt} onPress={onBuildingPress} />

      {/* 空格提示「+」：只在**自己舱室**画，全画会让整张图糊成一片 */}
      <EmptyTileLayer
        map={map}
        myRoom={myRoom}
        rooms={rooms}
        buildings={buildingAt}
        onPress={onOpenTile}
        highlight={panel?.kind === 'build' ? panel.cell : null}
      />

      {/* 弹道表现层（枪口焰 / 弹丸 / 命中环）—— 纯客户端，不碰平衡 */}
      <ShotLayer firing={firing} targetRef={shotTargetRef} reduceMotion={reduceMotion} />

      {/* 队友与自己的角色 */}
      {Object.keys(game?.players || {}).map((uidStr) => {
        const p = game?.players?.[uidStr];
        if (!p || !p.alive || !p.pos) return null;
        const uid = Number(uidStr);
        if (uid === myUid) return null; // 自己由 Animated 容器单独画
        const a = posAnims.current.get(uid);
        if (!a) return null;
        return (
          <Animated.View
            key={`h${uid}`}
            testID={`stasis-hero-${uid}`}
            // **角色一律不吃点击**（见自己那一个的注释）。
            pointerEvents="none"
            style={[styles.heroAbs, { transform: a.getTranslateTransform() }]}
          >
            <View style={{ marginLeft: -16, marginTop: -26 }}>
              <HeroView color={seatColorOf(uid)} name={nameOf(uid)} mine={false} lying={!!p.lying} />
            </View>
          </Animated.View>
        );
      })}
      {myAlive ? (
        <Animated.View
          testID="stasis-hero-me"
          // **必须 `pointerEvents="none"`**：角色是画在地图上的一个绝对定位块，
          // 而**你要躺的那张床就在你脚下** —— 头像默认吃点击，于是「点休眠舱
          // 打开升级弹窗」永远点不开（实测量到：强制点击也打不开，床的点击被
          // `stasis-hero-me` 子树整个吞掉）。用户报的「生物能够了也没法升级、
          // 点升级没反应」就是它，不是服务端拒绝。队友与静默体同理：站在谁身上
          // 就点不到谁脚下的门/床/建筑。
          pointerEvents="none"
          style={[styles.heroAbs, { transform: heroXY.getTranslateTransform() }]}
        >
          <View style={{ marginLeft: -16, marginTop: -26 }}>
            <HeroView color={seatColorOf(myUid)} name="你" mine lying={!!my?.lying} />
          </View>
        </Animated.View>
      ) : null}

      {/* 静默体 */}
      {entity ? (
        <Animated.View
          testID="stasis-entity"
          // 静默体也画在通道/门口上，同样不能吃点击（否则挡住它背后的门）。
          pointerEvents="none"
          style={[styles.heroAbs, { transform: entAnim.getTranslateTransform() }]}
        >
          <View style={{ marginLeft: -20, marginTop: -40 }}>
            <EntityView mood={entityMoodNow} level={entity.level} hp={entity.hp} maxHp={entity.max_hp} />
          </View>
        </Animated.View>
      ) : null}
    </Animated.View>
  );

  const topBar = (
    <View style={styles.topBar}>
      <View style={styles.topRow}>
        <ResChip label="生物能" value={my?.bio ?? 0} color={stasisBio} />
        <ResChip label="合金" value={my?.alloy ?? 0} color={stasisAlloy} />
        <View style={styles.clockBox}>
          {/* **v8：这一栏不再是倒计时**。救援舰不会来、也没有击杀期限（用户要求
              「时间不限」），一局打到分出胜负为止 —— 所以它读作**已坚守多久**。
              原来的红色紧迫态（最后 30 秒）随之取消：没有终点，就没有「快到了」。 */}
          <Text style={styles.clockLabel}>{phase === 'run' ? '气闸开启' : '已坚守'}</Text>
          <Text testID="stasis-timer" style={styles.clockValue}>
            {phase === 'run' ? fmtClock(runLeft) : fmtClock(elapsedMs)}
          </Text>
        </View>
      </View>
      <View style={styles.chipRow} testID="stasis-chips">
        {game?.seat_uids?.map((uid) => {
          const p = game.players[String(uid)];
          if (!p) return null;
          const mine = uid === myUid;
          const dead = !p.alive;
          return (
            <View key={uid} style={[styles.chip, mine && styles.chipMine, dead && styles.chipDead]}>
              <View style={[styles.chipFace, { backgroundColor: seatColorOf(uid) }]} />
              <Text style={styles.chipLv} numberOfLines={1}>
                {dead ? '出局' : p.room ? p.room : '—'}
              </Text>
              {mine ? <Text style={styles.chipTag}>你</Text> : null}
            </View>
          );
        })}
      </View>
    </View>
  );

  return (
    <View style={styles.root}>
      {topBar}

      {/* 主视图：整张地图 + 相机 */}
      <View
        testID="stasis-room-panel"
        style={[styles.viewport, { height: viewportH }, WEB_TOUCH_NONE]}
        {...panResponder.panHandlers}
      >
        {mapLayer}
        {lowDoor && myAlive ? (
          <Animated.View
            pointerEvents="none"
            style={[styles.alertGlow, { opacity: glow }]}
          />
        ) : null}

        {/* 状态条：静默体在干什么（留死高度，出现/消失不推挤布局） */}
        {/* 状态条与引导条**都是纯信息，必须 `pointerEvents="none"`** ——
            它们横跨整个视口顶部/底部，会挡住下面格子上的点击（E2E 抓到：
            点 `stasis-tile-0-0` 被状态条 intercept）。 */}
        <View testID="stasis-event-bar" pointerEvents="none" style={styles.statusBar}>
          <Text testID="stasis-status" style={styles.statusText} numberOfLines={1}>{statusText}</Text>
        </View>

        {/* 右上角工具 */}
        <View style={styles.tools}>
          <TouchableOpacity
            testID="stasis-wholemap"
            style={styles.toolBtn}
            onPress={() => setWholeOpen(true)}
          >
            <Ionicons name="map-outline" size={17} color={ON_SPACE} />
          </TouchableOpacity>
          <TouchableOpacity testID="stasis-recenter" style={styles.toolBtn} onPress={backToMe}>
            <Ionicons name={following ? 'locate' : 'locate-outline'} size={17} color={following ? stasisDoorOk : ON_SPACE} />
          </TouchableOpacity>
          <GameHelpButton game="stasis" />
        </View>

        {/* 跑动阶段的提示。**这段文案现在承担一条规则的教学**（v4）：倒计时结束
            还没躺下的人，会以「站着」的状态进入对局 —— 建不了东西，而且走廊里
            遇到静默体直接出局。所以要把「必须在倒计时结束前躺下」说在明处。 */}
        {phase === 'run' ? (
          <View testID="stasis-run-panel" pointerEvents="none" style={styles.runBanner}>
            <Text style={styles.runTitle}>
              {myLying ? '躺好了 —— 现在就能升床、建炮台' : '跑进一间空舱室，进去之后点「躺下」'}
            </Text>
            <Text style={styles.runSub}>
              {myLying
                ? '左下角摇杆已经用不上了 · 气闸开启前把床升上去'
                : '左下角摇杆移动 · 躺下之后不能再起身 · 没躺下的活不到天亮'}
            </Text>
          </View>
        ) : null}

        {/* 出局浮层 */}
        {!myAlive && phase === 'playing' ? (
          <View testID="stasis-out-overlay" style={styles.outOverlay}>
            <Text style={styles.outTitle}>
              {deathCause === 'entity' ? '你被静默体抓住了'
                : my?.surrendered ? '你已弃守' : '你的舱室失压了'}
            </Text>
            <Text style={styles.outSub}>
              {deathCause === 'entity'
                ? '它只抓没躺下的人 —— 下次在倒计时结束前躺好'
                : my?.surrendered ? '遗产已分给队友，他们还在打' : '队友还在继续，你可以看着'}
            </Text>
            <PrimaryButton testID="stasis-back-room" onPress={handleBackToRoom}>返回房间</PrimaryButton>
          </View>
        ) : null}
      </View>

      {/* 底部：摇杆 + 操作 */}
      <View style={styles.bottomBar}>
        {/* 摇杆**跑动阶段就要有** —— 那正是最需要移动的时候（30 秒内跑进舱室）。 */}
        {myAlive ? <Joystick onDir={onDir} /> : <StickPlaceholder />}

        {/* 躺下 —— 床边那一颗主按钮。**只有这一颗，躺下之后不再有「起身」**（v4）。
            躺着才产资源、才建得了东西，而「站着也在产」这件事玩家看不出来（用户
            实测过：「进房间没法躺下、生物能没有增加」）。所以做成一颗**明确的主
            按钮**，不靠自动判定。
            **跑动阶段就显示**（`phase==='run'` 时也满足 `myRoom && canLie`）——
            原型就是倒计时内上床，`增强调研.md` §9.3 规则 1。 */}
        {myAlive && myRoom && !myLying && canLie ? (
          <TapScale testID="stasis-lie" wrapStyle={styles.lieBtnWrap} style={styles.lieBtn} onPress={doLie}>
            <Ionicons name="bed-outline" size={20} color={stasisInk} />
            <Text style={styles.lieBtnText}>躺下</Text>
          </TapScale>
        ) : null}

        <View style={styles.bottomRight}>
          <TapScale
            testID="stasis-patch"
            style={styles.roundBtn}
            onPress={() => doPatch(myUid ?? null)}
          >
            <Ionicons name="build-outline" size={20} color={stasisRepair} />
            <Text style={styles.roundBtnText}>维修</Text>
          </TapScale>
          <TapScale
            testID="stasis-surrender"
            style={styles.surrenderBtn}
            onPress={() => setConfirm('surrender')}
          >
            <Text style={styles.surrenderText}>弃守</Text>
          </TapScale>
        </View>
        <View style={styles.hintBar}>
          <Text style={styles.hintText} numberOfLines={1}>
            {!myRoom
              ? '还没有舱室 · 跑进一间空舱室就能占下'
              : myLying
                ? `你的舱室 ${myRoom} · 已躺下，开始产生物能`
                : canLie
                  ? `你的舱室 ${myRoom} · 点「躺下」（躺下就不能再起来，也只能躺着建造）`
                  : `你的舱室 ${myRoom} · 走到休眠舱旁边才能躺下`}
          </Text>
        </View>
      </View>

      {/* ── 面板层：底部 sheet + 三个浮层 ─────────────────────
          全部在 `StasisPanels.tsx`（见那边的文件头：拆的判据是「谁需要知道什么」，
          屏幕只管「面板开没开、点了调谁」）。 */}
      <BuildSheet
        visible={panel?.kind === 'build'}
        onClose={() => setPanel(null)}
        cell={panel?.kind === 'build' ? panel.cell : null}
        onBuild={doBuild}
        canAfford={canAfford}
        built={myBuiltCount}
      />
      <BuildingSheet
        visible={panel?.kind === 'building'}
        onClose={() => setPanel(null)}
        cell={panel?.kind === 'building' ? panel.cell : null}
        kind={panel?.kind === 'building' ? buildingAt[panel.cell]?.kind ?? null : null}
        level={panel?.kind === 'building' ? buildingAt[panel.cell]?.level ?? 1 : 1}
        onUpgrade={doUpgrade}
        onDemolish={(cell) => { socketRef.current?.stasisDemolish(cell); setPanel(null); }}
      />
      <DoorSheet
        visible={panel?.kind === 'door'}
        onClose={() => setPanel(null)}
        rid={panel?.kind === 'door' ? panel.room : null}
        level={panel?.kind === 'door' ? rooms[panel.room]?.door_level ?? 1 : 1}
        hp={panel?.kind === 'door' ? doorHp[panel.room]?.hp ?? rooms[panel.room]?.door_hp ?? 0 : 0}
        max={panel?.kind === 'door' ? doorHp[panel.room]?.max ?? rooms[panel.room]?.door_max ?? 0 : 0}
        mine={panel?.kind === 'door' && panel.room === myRoom}
        ownerUid={panel?.kind === 'door' ? (rooms[panel.room]?.owner ?? null) : null}
        onUpgrade={(cost, alloy) => doUpgrade('door', cost, 'bio', alloy)}
        onPatch={doPatch}
      />
      <GateSheet
        visible={panel?.kind === 'gate'}
        onClose={() => setPanel(null)}
        gate={panel?.kind === 'gate' ? panel.gate : null}
        level={panel?.kind === 'gate' ? gateView[panel.gate]?.level ?? 1 : 1}
        hp={panel?.kind === 'gate' ? gateView[panel.gate]?.hp ?? 0 : 0}
        max={panel?.kind === 'gate' ? gateView[panel.gate]?.max ?? 0 : 0}
        mine={panel?.kind === 'gate' && panel.mine}
        contrib={(panel?.kind === 'gate'
          && game?.map?.gates?.[panel.gate]?.contrib) || {}}
        nameOf={(uid) => (uid === myUid ? '你' : nameOf(uid))}
        onUpgrade={(cost, alloy) =>
          doUpgrade(`gate:${panel?.kind === 'gate' ? panel.gate : ''}`, cost, 'bio', alloy)}
        onPatch={() => doPatchGate(panel?.kind === 'gate' ? panel.gate : '')}
      />
      <PodSheet
        visible={panel?.kind === 'pod'}
        onClose={() => setPanel(null)}
        bedLevel={my?.bed_level ?? 1}
        doorLevel={myDoor?.door_level ?? null}
        lying={myLying}
        onLie={doLie}
        onUpgrade={(cost, alloy) => doUpgrade('bed', cost, 'bio', alloy)}
      />
      <WholeStation
        visible={wholeOpen}
        onClose={() => setWholeOpen(false)}
        map={map}
        rooms={rooms}
        doorHp={doorHp}
        gates={gateView}
        myUid={myUid ?? null}
        entity={entity}
        nameOf={nameOf}
        aiOf={(uid) => {
          const key = game?.players?.[String(uid)]?.ai;
          return key ? AI_PROFILE_NAMES[key] ?? key : null;
        }}
        seatColorOf={seatColorOf}
        onPatch={doPatch}
        onPatchGate={doPatchGate}
      />
      <ConfirmDialog
        kind={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm === 'surrender') doSurrender();
          else { setConfirm(null); handleLeaveRoom(); }
        }}
      />
      <ResultDialog
        visible={showGameOver}
        game={game}
        nameOf={nameOf}
        seatColorOf={seatColorOf}
        onBack={handleBackToRoom}
      />
      {/* 断线倒计时（服务端给秒数，客户端只递减） */}
      {dcSeat >= 0 && dcLeft != null ? (
        <View style={styles.dcBanner}>
          <Text style={styles.dcText}>
            {players[dcSeat]?.nickname || players[dcSeat]?.username || '队友'} 已断线，
            等待重连（{dcLeft} 秒）
          </Text>
        </View>
      ) : null}
    </View>
  );
}

// ── 样式 ────────────────────────────────────────────────

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: stasisSpace },

  // 顶部
  topBar: { paddingTop: Platform.OS === 'web' ? 8 : 44, paddingBottom: 6, paddingHorizontal: 10, backgroundColor: stasisSpace },
  topRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  clockBox: { marginLeft: 'auto', alignItems: 'flex-end' },
  clockLabel: { color: ON_SPACE_SOFT, fontSize: 10 },
  clockValue: { color: ON_SPACE, fontSize: 16, fontWeight: '700', fontVariant: ['tabular-nums'] },
  chipRow: { flexDirection: 'row', gap: 6, marginTop: 7, flexWrap: 'wrap' },
  chip: {
    width: 40, height: 40, borderRadius: 9, backgroundColor: '#182436',
    borderWidth: 1, borderColor: '#2A3A52', alignItems: 'center', justifyContent: 'flex-end',
    paddingBottom: 2, overflow: 'hidden',
  },
  chipMine: { borderColor: stasisPod, borderWidth: 2 },
  chipDead: { opacity: 0.42 },
  chipFace: { position: 'absolute', left: 3, right: 3, top: 3, height: 16, borderRadius: 5 },
  chipLv: { color: ON_SPACE, fontSize: 9.5, fontWeight: '700' },
  chipTag: {
    position: 'absolute', top: 0, right: 0, fontSize: 8, fontWeight: '700',
    color: stasisInk, backgroundColor: stasisPod, paddingHorizontal: 3,
    borderBottomLeftRadius: 5,
  },

  // 地图
  viewport: { flex: 1, overflow: 'hidden', backgroundColor: stasisSpace, position: 'relative' },
  // `willChange`（`WEB_GPU`）**只对 Web 生效**：整张地图是 700+ 个 DOM 节点，
  // 每帧改一次 `transform` 而不提升图层的话，浏览器要把这 700 个节点重画一遍。
  // 声明之后 transform 变成纯合成操作，实测（CPU ×6 降速）帧率明显回稳。
  map: { position: 'absolute', left: 0, top: 0, ...WEB_GPU },



  heroAbs: { position: 'absolute', zIndex: 8, ...WEB_GPU },


  statusBar: {
    position: 'absolute', left: 8, right: 8, top: 8, height: 26,
    justifyContent: 'center', paddingHorizontal: 10, borderRadius: 8,
    backgroundColor: withAlpha(stasisSpace, 0.82),
  },
  statusText: { color: ON_SPACE, fontSize: 11.5 },
  tools: { position: 'absolute', right: 8, top: 42, gap: 8 },
  toolBtn: {
    width: 34, height: 34, borderRadius: 17, alignItems: 'center', justifyContent: 'center',
    backgroundColor: withAlpha('#182436', 0.92), borderWidth: 1, borderColor: '#2A3A52',
  },
  alertGlow: { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: withAlpha(stasisDoorLow, 0.16) },

  runBanner: {
    position: 'absolute', left: 10, right: 10, bottom: 10, borderRadius: 10,
    paddingVertical: 9, paddingHorizontal: 12, backgroundColor: withAlpha('#182436', 0.94),
    borderWidth: 1, borderColor: '#2A3A52',
  },
  runTitle: { color: ON_SPACE, fontSize: 13, fontWeight: '700' },
  runSub: { color: ON_SPACE_SOFT, fontSize: 11, marginTop: 2 },

  outOverlay: {
    position: 'absolute', left: 0, right: 0, top: 0, bottom: 0,
    backgroundColor: withAlpha(stasisSpace, 0.9),
    alignItems: 'center', justifyContent: 'center', padding: 24, zIndex: 20,
  },
  outTitle: { color: ON_SPACE, fontSize: 20, fontWeight: '800', marginBottom: 6 },
  outSub: { color: ON_SPACE_SOFT, fontSize: 13, textAlign: 'center', marginBottom: 18 },

  // 底部
  bottomBar: { height: 132, paddingHorizontal: 16, paddingTop: 8, backgroundColor: stasisSpace },
  // 躺下/起身：摇杆右侧的主按钮。底色用 `stasisPod`（休眠舱的亮色），
  // 让它在暗底上抢眼 —— 这是对局里最该被按到的一颗（不按就没有收入）。
  lieBtnWrap: { position: 'absolute', left: 18 + STICK_R * 2 + 12, bottom: 34 },
  lieBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 14, paddingVertical: spacing.sm + 4,
    borderRadius: 12, backgroundColor: stasisPod,
    borderWidth: 2, borderColor: stasisInk,
  },
  lieBtnText: { color: stasisInk, fontSize: 15, fontWeight: '800' },
  bottomRight: { position: 'absolute', right: 18, bottom: 26, alignItems: 'center', gap: 10 },
  roundBtn: {
    width: 62, height: 62, borderRadius: 31, alignItems: 'center', justifyContent: 'center',
    backgroundColor: '#152238', borderWidth: 2, borderColor: stasisRepair, gap: 1,
  },
  roundBtnText: { color: stasisRepair, fontSize: 11, fontWeight: '700' },
  surrenderBtn: { paddingVertical: 6, paddingHorizontal: 14, borderRadius: 8, borderWidth: 1, borderColor: withAlpha(stasisDoorLow, 0.7) },
  surrenderText: { color: stasisDoorLow, fontSize: 12, fontWeight: '600' },
  hintBar: { position: 'absolute', left: 16, right: 16, bottom: 4, alignItems: 'center' },
  hintText: { color: ON_SPACE_SOFT, fontSize: 11 },

  dcBanner: {
    position: 'absolute', left: 12, right: 12, bottom: 140, padding: 8, borderRadius: 8,
    backgroundColor: withAlpha(stasisDoorLow, 0.18), borderWidth: 1, borderColor: withAlpha(stasisDoorLow, 0.6),
  },
  dcText: { color: ON_SPACE, fontSize: 12, textAlign: 'center' },
});
