// Battleship (海战棋) game screen — 10x10 board, 2-player online battle.
// Phases: placement (arrange ships) -> battle (fire shots) -> finished.
// WS messages: placement, fire, shot, battle_start, game_start, game_over.
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, Vibration, ScrollView, Animated, Easing, PanResponder,
  GestureResponderEvent, PanResponderGestureState, PanResponderInstance,
  GestureResponderHandlers,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize,
  playerColors, battleshipShipColors, battleshipWater, withAlpha,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { GameHelpButton } from '../help';
import { useDisconnectCountdown } from '../disconnect';

// 命中/击沉标记：都压在深蓝棋盘上，提亮到 ≥4:1（原 #EF4444 只有 3.06:1）
const HIT_MARK = '#FBBF24';
const HIT_SUNK = '#F87171';

// 动画一律只动 transform/opacity（GPU 合成）；Web 端不支持 native driver 要降级
const NATIVE_DRIVER = Platform.OS !== 'web';
// 开炮动效时长：命中/击沉属「值得注意的事件」，方法论 §2 给 300-400ms
const SHOT_FX_MS = 360;
// 长按拖动判定：与五子棋放大镜 MAG_DELAY 同量级；未成立前的抖动阈值（超过即视为滑动/滚动）
const DRAG_HOLD_MS = 320;
const DRAG_CANCEL_TOL = 10;
// Web 端自定义手势必须告知浏览器别接管（否则手势被当成页面滚动/横滑返回，触摸序列被 cancel）
const WEB_TOUCH_NONE = Platform.OS === 'web' ? ({ touchAction: 'none' } as any) : null;

// --- Types ---

interface ShipDef { id: number; name: string; size: number; }
interface ShipPlacement {
  id: number; x: number | null; y: number | null;
  horizontal: boolean; cells: number[][]; placed: boolean;
}
/** 一件特殊武器的状态（自己的或对手的，挂靠关系双方可见）。 */
interface WeaponState {
  ship_id: number | null;
  ship_name: string | null;
  used: boolean;
  available: boolean;
}

interface GameSync {
  board_size: number;
  /** 这一局的舰队（v13）：组成由服务端下发，客户端只把 `name` 翻成中文。 */
  fleet?: { id: number; name: string; size: number }[];
  phase: 'placing' | 'battle' | 'finished';
  own_board: number[][];
  own_attack_board: number[][];
  incoming_board: number[][];
  own_ships: Array<{ id: number; x: number; y: number; horizontal: boolean }>;
  opp_ships: Array<{ id: number; x: number; y: number; horizontal: boolean; cells: number[][]; sunk: boolean }>;
  opp_ship_count: number;
  own_sunk_count: number;
  opp_sunk_count: number;
  next: string;
  your_color: string;
  winner: string | null;
  reason: string | null;
  placement_ready: Record<string, boolean>;
  shots_fired: number;
  weapon_defs?: Record<string, { name: string; desc: string }>;
  own_weapons?: Record<string, WeaponState>;
  opp_weapons?: Record<string, WeaponState>;
  own_radar?: Array<{ x: number; y: number; has_ship: boolean }>;
}

/** 服务端没有下发 weapon_defs 时的兜底文案（旧后端 / 竞态）。 */
const WEAPON_FALLBACK_DEFS: Record<string, { name: string; desc: string }> = {
  radar: { name: '雷达', desc: '探测周围 3×3 海域内是否有敌舰（只报有无）' },
  salvo: { name: '齐射', desc: '一次性对 3 个格子开炮' },
  strike: { name: '空袭', desc: '对周围 3×3 海域范围打击' },
};
const WEAPON_KEYS = ['radar', 'salvo', 'strike'] as const;
type WeaponKey = typeof WEAPON_KEYS[number];

/** 布阵时武器挂靠的初始分配（与后端 DEFAULT_WEAPON_ASSIGNMENT 一致）。 */
const DEFAULT_WEAPON_ASSIGN: Record<string, number> = { radar: 5, salvo: 4, strike: 3 };

/** 一次雷达探测在攻击面板上留下的标记。 */
interface ScanMark { x: number; y: number; has_ship: boolean; }

interface Props { code: string; onLeave: () => void; }

/** 一次开炮的落点动效参数（mine 决定画在哪块盘上）。 */
interface ShotFx { x: number; y: number; hit: boolean; sunk: boolean; mine: boolean; }

/**
 * **舰队的「组成」由服务端下发，名字在客户端翻译**（v13，棋盘尺寸可变之后）。
 *
 * 为什么这样切：舰队随尺寸档位变（6×6 是 4 艘小船、12×12 是 6 艘），
 * 而**组成只能有一个来源** —— 客户端自己抄一份的话，加一档尺寸就要改两处，
 * 两边一旦不一致就是「布阵放不下 / 服务端回一句布阵不合法」（同「游戏名散在
 * 七处」那条纪律）。名字仍然是客户端的事（英文 id → 中文），因为它只影响显示。
 */
const SHIP_LABELS: Record<string, string> = {
  carrier: '航母', battleship: '战列舰', cruiser: '巡洋舰',
  submarine: '潜艇', destroyer: '驱逐舰', patrol: '巡逻艇',
};
const FALLBACK_FLEET: ShipDef[] = [
  { id: 1, name: '航母', size: 5 },
  { id: 2, name: '战列舰', size: 4 },
  { id: 3, name: '巡洋舰', size: 3 },
  { id: 4, name: '潜艇', size: 3 },
  { id: 5, name: '驱逐舰', size: 2 },
];
const DEFAULT_BOARD = 10;
const SHIP_COLORS = battleshipShipColors;
const WINNER_LABELS: Record<string, string> = { blue: '蓝方胜', red: '红方胜' };
const REASON_LABELS: Record<string, string> = {
  all_sunk: '全部击沉', surrender: '认输', disconnect: '断线判负',
};
const SALVO_SHOTS = 3;
/** 事件提示条的停留时长：够读完一句话，又不至于挡住下一步操作。 */
const NOTICE_MS = 2200;

// --- Random placement generator (client-side) ---

function randomPlacementClient(size: number, fleet: ShipDef[]): ShipPlacement[] {
  const board: number[][] = Array.from({ length: size }, () => Array(size).fill(0));
  const ships: ShipPlacement[] = [];
  for (const sdef of fleet) {
    let placed = false;
    for (let t = 0; t < 300; t++) {
      const horizontal = Math.random() < 0.5;
      const maxX = horizontal ? size - sdef.size : size - 1;
      const maxY = horizontal ? size - 1 : size - sdef.size;
      const x = Math.floor(Math.random() * (maxX + 1));
      const y = Math.floor(Math.random() * (maxY + 1));
      const cells: number[][] = [];
      let ok = true;
      for (let i = 0; i < sdef.size; i++) {
        const cx = horizontal ? x + i : x;
        const cy = horizontal ? y : y + i;
        if (board[cy][cx] !== 0) { ok = false; break; }
        cells.push([cx, cy]);
      }
      if (!ok) continue;
      for (const [cx, cy] of cells) board[cy][cx] = sdef.id;
      ships.push({ id: sdef.id, x, y, horizontal, cells, placed: true });
      placed = true;
      break;
    }
    if (!placed) {
      ships.push({ id: sdef.id, x: null, y: null, horizontal: true, cells: [], placed: false });
    }
  }
  return ships;
}

function shipCells(x: number, y: number, size: number, horizontal: boolean): number[][] {
  return Array.from({ length: size }, (_, i) =>
    horizontal ? [x + i, y] : [x, y + i]
  );
}

function canPlace(
  board: number[][], x: number, y: number, size: number,
  horizontal: boolean, excludeId: number, boardSize: number = DEFAULT_BOARD,
): boolean {
  const cells = shipCells(x, y, size, horizontal);
  for (const [cx, cy] of cells) {
    if (cx < 0 || cx >= boardSize || cy < 0 || cy >= boardSize) return false;
    if (board[cy][cx] !== 0 && board[cy][cx] !== excludeId) return false;
  }
  return true;
}

// --- 布阵拖放（长按整艘舰拖动）---
//
// 两条拖动入口共用同一份拖动态：
//   ① 棋盘手势层：长按盘上已放置的舰（Gomoku 同款 PanResponder，容器 pointerEvents="box-only"）
//   ② 舰船卡片：长按卡片本身（卡片自带 PanResponder，用 pageX/pageY 减棋盘原点换算）
// 落点非法（越界 / 压到别的舰 / 出盘）时松手静默回原位——移动端弹窗打断感太强，
// 与「点选落位」的既有约定一致。
interface ShipDragState {
  shipId: number;
  size: number;
  horizontal: boolean;
  grabDX: number;   // 手指抓住的位置相对舰体左上角的格子偏移，拖动时不跳位
  grabDY: number;
  x: number;        // 当前目标左上角格（可为越界值 → 视为非法）
  y: number;
  valid: boolean;
}

interface DragApi {
  drag: ShipDragState | null;
  boardHandlers: PanResponderInstance;
  cardHandlers: Record<number, PanResponderInstance>;
  pressedId: number | null;
}

function useShipDrag(params: {
  cellSize: number;
  ships: ShipPlacement[];
  /** 这一局的舰队（尺寸档位不同、船也不同，v13）—— 查船的格数与位置都靠它。 */
  fleet: { id: number; name: string; size: number }[];
  board: number[][];
  horizontal: boolean;
  disabled: boolean;
  /** 棋盘在窗口中的原点（由 PlacementView 在棋盘挂载/换 Tab 时量好） */
  boardOriginRef: React.RefObject<{ x: number; y: number }>;
  /** 拖动态成立时回调（把分段切回「我的海域」，保证预览可见） */
  onDragArm: () => void;
  onDrop: (shipId: number, x: number, y: number, horizontal: boolean) => void;
  onTapCell: (x: number, y: number) => void;
  onTapShipCard: (shipId: number) => void;
}): DragApi {
  const [drag, setDrag] = useState<ShipDragState | null>(null);
  const [pressedId, setPressedId] = useState<number | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const dragRef = useRef<ShipDragState | null>(null);
  const holdRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boardTapRef = useRef<{ lx: number; ly: number; x: number; y: number } | null>(null);
  const cardTapRef = useRef<{ shipId: number; pageX: number; pageY: number } | null>(null);

  // 以下函数只读写 ref 与稳定的 setState，故可安全地被只创建一次的 PanResponder 闭包捕获
  const apply = (d: ShipDragState | null) => { dragRef.current = d; setDrag(d); };
  const clearHold = () => {
    if (holdRef.current) { clearTimeout(holdRef.current); holdRef.current = null; }
  };
  const updateTarget = (localX: number, localY: number) => {
    const cur = dragRef.current;
    if (!cur) return;
    const p = paramsRef.current;
    const x = Math.floor(localX / p.cellSize) - cur.grabDX;
    const y = Math.floor(localY / p.cellSize) - cur.grabDY;
    apply({ ...cur, x, y, valid: canPlace(p.board, x, y, cur.size, cur.horizontal, cur.shipId) });
  };
  const finish = (commit: boolean) => {
    clearHold();
    setPressedId(null);
    const cur = dragRef.current;
    apply(null);
    if (cur && commit && cur.valid) {
      paramsRef.current.onDrop(cur.shipId, cur.x, cur.y, cur.horizontal);
    }
  };

  // ① 棋盘手势层：pointerEvents="box-only" 让局部坐标相对棋盘本体
  //（原生端若不设，100 个绝对定位格会让 locationX/Y 全部落进 [0,cellSize) → 恒解析成 (0,0)）
  const boardHandlers = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderGrant: (e: GestureResponderEvent) => {
      const p = paramsRef.current;
      if (p.disabled) return;
      const lx = e.nativeEvent.locationX ?? 0;
      const ly = e.nativeEvent.locationY ?? 0;
      const x = Math.floor(lx / p.cellSize);
      const y = Math.floor(ly / p.cellSize);
      boardTapRef.current = { lx, ly, x, y };
      const ship = p.ships.find(
        (s) => s.placed && s.cells.some(([cx, cy]) => cx === x && cy === y),
      );
      if (!ship) return;
      holdRef.current = setTimeout(() => {
        holdRef.current = null;
        const def = paramsRef.current.fleet.find((d) => d.id === ship.id);
        if (!def) return;
        paramsRef.current.onDragArm();
        apply({
          shipId: ship.id, size: def.size, horizontal: ship.horizontal,
          grabDX: x - (ship.x ?? 0), grabDY: y - (ship.y ?? 0),
          x: ship.x ?? 0, y: ship.y ?? 0, valid: true,
        });
      }, DRAG_HOLD_MS);
    },
    onPanResponderMove: (e: GestureResponderEvent) => {
      const lx = e.nativeEvent.locationX ?? 0;
      const ly = e.nativeEvent.locationY ?? 0;
      if (dragRef.current) { updateTarget(lx, ly); return; }
      const start = boardTapRef.current;
      if (start && Math.sqrt((lx - start.lx) ** 2 + (ly - start.ly) ** 2) > DRAG_CANCEL_TOL) {
        clearHold();
      }
    },
    onPanResponderRelease: () => {
      const start = boardTapRef.current;
      boardTapRef.current = null;
      if (dragRef.current) { finish(true); return; }
      clearHold();
      if (start && !paramsRef.current.disabled) paramsRef.current.onTapCell(start.x, start.y);
    },
    onPanResponderTerminate: () => {
      // 手势被上层抢走（滚动等）也走同一套判定：finish 只在落点合法时才落位，
      // 非法一律回原位（方法论 §3.4）
      boardTapRef.current = null;
      if (dragRef.current) finish(true); else clearHold();
    },
  }), []);

  // ② 舰船卡片：卡片不在棋盘上，用 pageX/pageY 减棋盘原点换算成盘内坐标
  const cardHandlers = useMemo(() => {
    const map: Record<number, PanResponderInstance> = {};
    // 舰队是**参数**（随尺寸档位变）——但这份 PanResponder 只建一次，
    // 所以从 `paramsRef` 现读最新值，不进依赖数组（进了会重建整张手势表）。
    for (const def of paramsRef.current.fleet) {
      const shipId = def.id;
      map[shipId] = PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: (_e: GestureResponderEvent, gs: PanResponderGestureState) => {
          const p = paramsRef.current;
          if (p.disabled) return;
          setPressedId(shipId);
          cardTapRef.current = { shipId, pageX: gs.x0, pageY: gs.y0 };
          holdRef.current = setTimeout(() => {
            holdRef.current = null;
            const start = cardTapRef.current;
            if (!start) return;
            const origin = paramsRef.current.boardOriginRef.current;
            paramsRef.current.onDragArm();
            apply({
              shipId, size: def.size, horizontal: p.horizontal,
              grabDX: Math.floor((def.size - 1) / 2), grabDY: 0,
              x: 0, y: 0, valid: false,
            });
            updateTarget(start.pageX - origin.x, start.pageY - origin.y);
          }, DRAG_HOLD_MS);
        },
        onPanResponderMove: (_e: GestureResponderEvent, gs: PanResponderGestureState) => {
          const start = cardTapRef.current;
          if (dragRef.current) {
            const origin = paramsRef.current.boardOriginRef.current;
            updateTarget(gs.moveX - origin.x, gs.moveY - origin.y);
            return;
          }
          if (start && Math.sqrt((gs.moveX - start.pageX) ** 2 + (gs.moveY - start.pageY) ** 2) > DRAG_CANCEL_TOL) {
            clearHold();
          }
        },
        onPanResponderRelease: () => {
          const start = cardTapRef.current;
          cardTapRef.current = null;
          if (dragRef.current) { finish(true); return; }
          clearHold();
          setPressedId(null);
          if (start && !paramsRef.current.disabled) paramsRef.current.onTapShipCard(start.shipId);
        },
        onPanResponderTerminate: () => {
          cardTapRef.current = null;
          if (dragRef.current) finish(true); else { clearHold(); setPressedId(null); }
        },
      });
    }
    return map;
  }, []);

  return { drag, boardHandlers, cardHandlers, pressedId };
}

// --- Sub-components ---

/** 海域分段切换：一次只显示一块盘，把格子放大到接近 44pt。 */
function BoardTabs({
  tab, onChange, incoming,
}: {
  tab: 'own' | 'attack';
  onChange: (t: 'own' | 'attack') => void;
  incoming: number;
}) {
  const seg = (key: 'own' | 'attack', label: string) => {
    const active = tab === key;
    return (
      <TouchableOpacity
        key={key}
        style={[styles.tabItem, active && styles.tabItemActive]}
        onPress={() => onChange(key)}
        accessibilityRole="tab"
        accessibilityState={{ selected: active }}
      >
        {/* 选中态同时用「填充底色 + 加粗 + 下划线」三个通道，不单靠颜色（WCAG 1.4.1） */}
        <Text style={[styles.tabText, active && styles.tabTextActive]}>{label}</Text>
        {key === 'own' && incoming > 0 && (
          <View style={styles.tabBadge}>
            <Text style={styles.tabBadgeText}>被击 {incoming}</Text>
          </View>
        )}
        <View style={[styles.tabUnderline, active && styles.tabUnderlineActive]} />
      </TouchableOpacity>
    );
  };
  return <View style={styles.tabRow}>{seg('own', '我的海域')}{seg('attack', '敌方海域')}</View>;
}

/** 布阵阶段切到「敌方海域」时的占位面板（对方舰位开战前不下发）。 */
function EnemyLockedPanel({ boardPixelSize }: { boardPixelSize: number }) {
  return (
    <View style={[styles.lockedPanel, { width: boardPixelSize, height: boardPixelSize }]}>
      <Ionicons name="lock-closed-outline" size={30} color={colors.textSecondary} />
      <Text style={styles.lockedTitle}>对方海域</Text>
      <Text style={styles.lockedHint}>布阵完成后可见</Text>
    </View>
  );
}

/** 拖动中的舰体预览：合法=实线白边，非法=虚线琥珀边（形状做第二通道）。 */
function DragGhost({ drag, cellSize }: { drag: ShipDragState; cellSize: number }) {
  return (
    <>
      {shipCells(drag.x, drag.y, drag.size, drag.horizontal).map(([cx, cy]) => (
        <View
          key={`ghost-${cx}-${cy}`}
          pointerEvents="none"
          style={[styles.dragGhost, {
            left: cellSize * cx + 2, top: cellSize * cy + 2,
            width: cellSize - 4, height: cellSize - 4,
            backgroundColor: withAlpha(SHIP_COLORS[drag.shipId], drag.valid ? 0.85 : 0.3),
            borderColor: drag.valid ? colors.white : colors.warning,
            borderStyle: drag.valid ? 'solid' : 'dashed',
          }]}
        />
      ))}
    </>
  );
}

/**
 * 开炮落点动效：命中=扩散爆环 + 中心闪光，未命中=水面涟漪。
 * 只动 transform/opacity；单次播放不循环（WCAG 2.2.2），360ms 后停在透明态。
 * 命中/未命中的常驻形状标记由 CellContent 负责，动效只是加强，不替代。
 */
function ShotEffect({
  x, y, cellSize, hit, sunk,
}: {
  x: number; y: number; cellSize: number; hit: boolean; sunk: boolean;
}) {
  const t = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    t.setValue(0);
    const anim = Animated.timing(t, {
      toValue: 1, duration: SHOT_FX_MS,
      easing: Easing.out(Easing.quad), useNativeDriver: NATIVE_DRIVER,
    });
    anim.start();
    return () => anim.stop();
  }, [x, y, t]);

  const box = Math.round(cellSize * 1.8);
  const off = Math.round((cellSize - box) / 2);
  const ringScale = t.interpolate({ inputRange: [0, 1], outputRange: [0.3, 1] });
  const ringOpacity = t.interpolate({ inputRange: [0, 0.6, 1], outputRange: [0.95, 0.5, 0] });
  const coreScale = t.interpolate({ inputRange: [0, 1], outputRange: [0.25, 0.95] });
  const coreOpacity = t.interpolate({ inputRange: [0, 0.5, 1], outputRange: [1, 0.7, 0] });
  const tint = sunk ? HIT_SUNK : hit ? HIT_MARK : colors.white;
  return (
    <View
      pointerEvents="none"
      style={[styles.shotFx, {
        left: cellSize * x + off, top: cellSize * y + off, width: box, height: box,
      }]}
    >
      <Animated.View style={[styles.shotRing, {
        borderColor: tint, borderRadius: box / 2,
        transform: [{ scale: ringScale }], opacity: ringOpacity,
      }]} />
      {hit && (
        <Animated.View style={[styles.shotCore, {
          backgroundColor: tint, borderRadius: box / 2,
          transform: [{ scale: coreScale }], opacity: coreOpacity,
        }]} />
      )}
    </View>
  );
}

function ShipPanel({
  ships, fleet, selectedId, horizontal, onRotate, cardHandlers, pressedId, draggingId,
}: {
  ships: ShipPlacement[]; selectedId: number | null;
  /** 这一局的舰队（随尺寸档位变，v13）—— 卡片列表与名字都从它来。 */
  fleet: { id: number; name: string; size: number }[];
  onRotate: () => void; horizontal: boolean;
  cardHandlers: Record<number, PanResponderInstance>;
  pressedId: number | null; draggingId: number | null;
}) {
  return (
    <View style={styles.shipPanel}>
      <Text style={styles.shipPanelTitle}>舰船</Text>
      <View style={styles.shipList}>
        {ships.map((s) => {
          const def = fleet.find((d) => d.id === s.id)!;
          const isSelected = selectedId === s.id;
          // 卡片用 PanResponder（不是 TouchableOpacity）：长按要能直接拖到棋盘上，
          // 点按（未拖动就松手）走 responder 的 release 分支 → 仍然是「选中」。
          return (
            <View
              key={s.id}
              {...(cardHandlers[s.id]?.panHandlers ?? {})}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              style={[
                styles.shipItem, WEB_TOUCH_NONE,
                isSelected && styles.shipItemSelected,
                pressedId === s.id && styles.shipItemPressed,
                draggingId === s.id && styles.shipItemDragging,
              ]}
            >
              <View style={[styles.shipPreview, { width: def.size * 12, height: 12,
                backgroundColor: SHIP_COLORS[s.id] }]} />
              <Text style={[styles.shipName, isSelected && styles.shipNameSelected]}>
                {def.name}({def.size})
              </Text>
              {s.placed && <Text style={styles.shipPlaced}>已布</Text>}
            </View>
          );
        })}
      </View>
      <TouchableOpacity style={styles.rotateBtn} onPress={onRotate}>
        <Ionicons name="refresh-outline" size={18} color={colors.primary} />
        <Text style={styles.rotateBtnText}>
          {horizontal ? '横向' : '纵向'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

// --- Main Component ---

export default function BattleshipScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<GameSync | null>(null);
  // **棋盘边长与舰队来自这一局的载荷**（v13）：6×6 / 10×10 / 12×12 共用这一份渲染。
  // 放在 `game` 一拿到就算出来 —— 下面的布阵 state 初值（随机布阵）也要用它。
  const boardSize = game?.board_size ?? DEFAULT_BOARD;
  const fleet: ShipDef[] = useMemo(
    () => (game?.fleet ?? FALLBACK_FLEET).map((d) => ({
      id: d.id, size: d.size, name: SHIP_LABELS[d.name] ?? d.name,
    })),
    [game?.fleet],
  );
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

  // Placement state (local, before submitting)
  const [placementShips, setPlacementShips] = useState<ShipPlacement[]>(
    () => randomPlacementClient(DEFAULT_BOARD, FALLBACK_FLEET));
  const [selectedShipId, setSelectedShipId] = useState<number | null>(null);
  const [horizontal, setHorizontal] = useState(true);
  const [placementSubmitted, setPlacementSubmitted] = useState(false);

  // 系统/浏览器返回拦截：对局中弹退出确认，终局后放行
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

  // Web popstate 拦截（原生端 no-op）
  useEffect(() => onConfirmLeave(() => {
    if (gameOverRef.current) { navigation.goBack(); return; }
    setConfirm('leave');
  }), [navigation]);

  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  // mine=true 表示这一炮是自己打出去的（决定「最后一炮」标记画在哪块盘上）
  const [lastShot, setLastShot] = useState<{ x: number; y: number; mine: boolean } | null>(null);
  const [shotFx, setShotFx] = useState<ShotFx | null>(null);
  const [viewTab, setViewTab] = useState<'own' | 'attack'>('own');
  const [unseenIncoming, setUnseenIncoming] = useState(0);

  // --- v1.10 特殊武器 ---
  // 布阵期的挂靠选择（本地，随 placement 一起提交）
  const [weaponAssign, setWeaponAssign] = useState<Record<string, number>>(
    () => ({ ...DEFAULT_WEAPON_ASSIGN }),
  );
  // 瞄准中的武器：radar/strike 选 1 格，salvo 选 3 格
  const [weaponMode, setWeaponMode] = useState<{ weapon: WeaponKey; cells: number[][] } | null>(null);
  // 一次性事件提示（命中续射 / 击沉 / 武器结果）
  const [notice, setNotice] = useState<string | null>(null);

  // Board sizing：单盘显示后棋盘可以铺满宽度（原先两块盘上下叠放，每格仅约 30px）。
  // 10 列在 390pt 竖屏上的物理上限约 37px（44pt 需要 440pt 宽），故横向铺满 + 桌面端封顶 460。
  const boardEdge = Math.min(winWidth - spacing.sm * 2, 460);
  const cellSize = Math.floor(boardEdge / boardSize);
  const boardPixelSize = cellSize * boardSize;

  const phase = game?.phase || 'placing';

  // 分段默认：布阵看自己的盘，开战直接停在敌方海域（主要操作就在那儿）
  useEffect(() => {
    if (phase === 'battle') setViewTab('attack');
    else if (phase === 'placing') setViewTab('own');
  }, [phase]);

  // 回到「我的海域」即视为已读，清掉 Tab 上的被击提示
  useEffect(() => {
    if (viewTab === 'own') setUnseenIncoming(0);
  }, [viewTab, game]);

  // 动效播完就撤掉挂载（单次播放；也避免切 Tab 重挂载时重放）
  useEffect(() => {
    if (!shotFx) return;
    const t = setTimeout(() => setShotFx(null), SHOT_FX_MS + 60);
    return () => clearTimeout(t);
  }, [shotFx]);

  // 事件提示条自动消失（不常驻，免得一直占着版面）
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(t);
  }, [notice]);

  // Build placement board from local ships
  const placementBoard = useMemo(() => {
    const b: number[][] = Array.from({ length: boardSize }, () => Array(boardSize).fill(0));
    for (const s of placementShips) {
      if (s.placed && s.cells) {
        for (const [cx, cy] of s.cells) b[cy][cx] = s.id;
      }
    }
    return b;
  }, [placementShips]);

  const allPlaced = placementShips.every((s) => s.placed);

  // --- WS setup ---
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
        const g = msg.game as GameSync;
        setGame(g);
        if (g.phase !== 'placing') {
          setPlacementSubmitted(true);
        }
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
      const g = msg.game as GameSync;
      if (g) {
        setGame(g);
        setShowGameOver(false);
        setLastShot(null);
        setShotFx(null);
        setWeaponMode(null);
        setNotice(null);
        gameOverShownRef.current = false;
        if (g.phase !== 'placing') setPlacementSubmitted(true);
      }
    };

    const handlePlacementReady = (msg: SocketMessage) => {
      setGame((prev) => {
        if (!prev) return prev;
        const uid = msg.user_id as number;
        return {
          ...prev,
          placement_ready: { ...prev.placement_ready, [String(uid)]: true },
        };
      });
    };

    const handleBattleStart = (msg: SocketMessage) => {
      const g = msg.game as GameSync;
      if (g) { setGame(g); setLastShot(null); setShotFx(null); setPlacementSubmitted(true); }
    };

    const handleShot = (msg: SocketMessage) => {
      const g = msg.game as GameSync;
      if (!g) return;
      setGame(g);
      const sx = msg.x as number;
      const sy = msg.y as number;
      const result = msg.result as string;
      // 每一炮都逐人下发（含 shooter_color），据此区分「我打出去的」与「打我的」
      const mine = msg.shooter_color === g.your_color;
      // 只有自己的炮才在攻击面板上留「最后一炮」标记——对方的炮不该标在我的攻击盘上
      setLastShot({ x: sx, y: sy, mine });
      setShotFx({ x: sx, y: sy, hit: result !== 'miss', sunk: result === 'sunk', mine });
      if (!mine) setUnseenIncoming((n) => n + 1);
      if (Platform.OS !== 'web' && (result === 'hit' || result === 'sunk')) Vibration.vibrate(15);
      // 命中续射是这一版最容易让人懵的规则——「怎么还是我的回合」必须给出解释
      if (mine) {
        if (result === 'sunk') setNotice('击沉！继续开炮');
        else if (result === 'hit') {
          setNotice(msg.keeps_turn ? '命中！继续开炮' : '命中！换对方了');
        } else setNotice('未命中，换对方');
      }
    };

    // 武器使用的公开广播（双方都收到）：出牌轨迹 + 结算结果
    const handleWeaponUsed = (msg: SocketMessage) => {
      const g = msg.game as GameSync;
      if (!g) return;
      setGame(g);
      const weapon = msg.weapon as WeaponKey;
      const mine = msg.shooter_color === g.your_color;
      const name = (g.weapon_defs || WEAPON_FALLBACK_DEFS)[weapon]?.name || weapon;
      const shots = (msg.shots as Array<{ x: number; y: number; result: string }>) || [];
      if (mine) {
        const sunk = shots.filter((s) => s.result === 'sunk').length;
        const hits = shots.filter((s) => s.result !== 'miss').length;
        if (weapon === 'radar') setNotice('雷达扫描完成');
        else if (sunk) setNotice(`${name}：击沉 ${sunk} 艘！`);
        else if (hits) setNotice(`${name}：命中 ${hits} 格`);
        else setNotice(`${name}：全部落空`);
      } else {
        setNotice(`对方使用${name}`);
      }
      // 武器打出的炮也要在盘上留痕（最后一炮标记 + 落点动效）
      const last = shots[shots.length - 1];
      if (last) {
        setLastShot({ x: last.x, y: last.y, mine });
        setShotFx({
          x: last.x, y: last.y, hit: last.result !== 'miss',
          sunk: last.result === 'sunk', mine,
        });
        if (!mine) setUnseenIncoming((n) => n + shots.length);
      }
      if (Platform.OS !== 'web' && shots.some((s) => s.result !== 'miss')) Vibration.vibrate(15);
    };

    // 雷达结果只发给使用者本人（对手只知道他放了一次雷达）
    const handleRadarResult = (msg: SocketMessage) => {
      setGame((prev) => prev ? {
        ...prev,
        own_radar: [
          ...(prev.own_radar || []),
          { x: msg.x as number, y: msg.y as number, has_ship: msg.has_ship as boolean },
        ],
      } : prev);
      setNotice(msg.has_ship ? '雷达：该海域有敌舰' : '雷达：该海域没有敌舰');
    };

    const handleGameOver = (msg: SocketMessage) => {
      const winner = msg.winner as string;
      const reason = msg.reason as string;
      setGame((prev) => prev ? {
        ...prev, winner: winner, reason: reason, phase: 'finished',
      } : prev);
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
    const handleError = (msg: SocketMessage) => { notify('提示', msg.message as string); };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleGameStart);
    ws.on('placement_ready', handlePlacementReady);
    ws.on('battle_start', handleBattleStart);
    ws.on('shot', handleShot);
    ws.on('weapon_used', handleWeaponUsed);
    ws.on('radar_result', handleRadarResult);
    ws.on('game_over', handleGameOver);
    ws.on('player_disconnected', handlePlayerDisconnected);
    ws.on('player_reconnected', handlePlayerReconnected);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);
    ws.connect();
    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave]);

  // --- Placement actions ---
  const handleReroll = useCallback(() => {
    setPlacementShips(randomPlacementClient(boardSize, fleet));
    setSelectedShipId(null);
  }, [boardSize, fleet]);

  const handleRotate = useCallback(() => {
    setHorizontal((h) => !h);
  }, []);

  const handleSelectShip = useCallback((id: number) => {
    setSelectedShipId((prev) => (prev === id ? null : id));
  }, []);

  const handlePlacementCellPress = useCallback((x: number, y: number) => {
    // If clicking on a placed ship, pick it up
    const clickedShip = placementShips.find(
      (s) => s.placed && s.cells.some(([cx, cy]) => cx === x && cy === y),
    );
    if (clickedShip && !selectedShipId) {
      setPlacementShips((prev) => prev.map((s) =>
        s.id === clickedShip.id ? { ...s, x: null, y: null, cells: [], placed: false } : s,
      ));
      setSelectedShipId(clickedShip.id);
      return;
    }
    // Place selected ship
    if (selectedShipId === null) return;
    const def = fleet.find((d) => d.id === selectedShipId);
    if (!def) return;
    if (!canPlace(placementBoard, x, y, def.size, horizontal, selectedShipId, boardSize)) return;
    const cells = shipCells(x, y, def.size, horizontal);
    setPlacementShips((prev) => prev.map((s) =>
      s.id === selectedShipId ? { ...s, x, y, horizontal, cells, placed: true } : s,
    ));
    setSelectedShipId(null);
  }, [placementShips, selectedShipId, placementBoard, horizontal, fleet, boardSize]);

  // 拖放落位：整艘舰搬到 (x,y)，保持朝向。落点非法时静默回原位（不弹窗）。
  const handleMoveShip = useCallback((shipId: number, x: number, y: number, horizontal: boolean) => {
    const def = fleet.find((d) => d.id === shipId);
    if (!def) return;
    if (!canPlace(placementBoard, x, y, def.size, horizontal, shipId, boardSize)) return;
    const cells = shipCells(x, y, def.size, horizontal);
    setPlacementShips((prev) => prev.map((s) =>
      s.id === shipId ? { ...s, x, y, horizontal, cells, placed: true } : s,
    ));
    setSelectedShipId(null);
  }, [placementBoard, fleet, boardSize]);

  const handleSubmitPlacement = useCallback(() => {
    if (!allPlaced) return;
    const ships = placementShips.map((s) => ({
      id: s.id, x: s.x!, y: s.y!, horizontal: s.horizontal,
    }));
    socketRef.current?.send({
      type: 'placement', ships, weapons: { ...weaponAssign },
    });
    setPlacementSubmitted(true);
  }, [allPlaced, placementShips, weaponAssign]);

  // 换一件武器的携带舰：在 5 条船里循环，跳过已被别的武器占用的船
  // （服务端要求三件武器挂靠三条不同的船，同一艘会形成「全堆最难找的船」的支配策略）
  const cycleWeaponShip = useCallback((weapon: WeaponKey) => {
    setWeaponAssign((prev) => {
      const taken = WEAPON_KEYS.filter((w) => w !== weapon).map((w) => prev[w]);
      const options = fleet.map((d) => d.id).filter((id) => !taken.includes(id));
      if (options.length === 0) return prev;
      const cur = options.indexOf(prev[weapon]);
      return { ...prev, [weapon]: options[(cur + 1) % options.length] };
    });
  }, [fleet]);

  // --- Battle actions ---
  const myWeapons = game?.own_weapons || {};
  const weaponName = useCallback(
    (w: string) => (game?.weapon_defs || WEAPON_FALLBACK_DEFS)[w]?.name || w,
    [game],
  );

  const handleFirePress = useCallback((x: number, y: number) => {
    if (!game || !user || game.winner) return;
    if (game.phase !== 'battle') return;
    if (game.next !== game.your_color) return;
    if (game.own_attack_board[y][x] !== 0) return;
    if (Platform.OS !== 'web') Vibration.vibrate(10);
    socketRef.current?.send({ type: 'fire', x, y });
  }, [game, user]);

  // 进入瞄准：雷达/空袭各选 1 格，齐射选 3 格。
  // **顺手切到「敌方海域」**：三件武器的目标都在敌方海域上，武器条又挂在分段 Tab
  // 上方（任何分段都能点），不切的话用户会在己方盘上进入瞄准，然后点哪都没反应。
  const startWeapon = useCallback((weapon: WeaponKey) => {
    if (!game || game.winner || game.phase !== 'battle') return;
    if (game.next !== game.your_color) return;
    if (!myWeapons[weapon]?.available) return;
    if (Platform.OS !== 'web') Vibration.vibrate(10);
    setViewTab('attack');
    setWeaponMode({ weapon, cells: [] });
  }, [game, myWeapons]);

  const cancelWeapon = useCallback(() => setWeaponMode(null), []);

  // 分段切换：切到己方海域就**放弃瞄准**。瞄准只对攻击面板生效，留着的话提示条
  // 写着「点击要探测的海域中心」而点哪都没反应——比报错更让人以为游戏坏了。
  const handleTabChange = useCallback((tab: 'own' | 'attack') => {
    if (tab === 'own' && weaponMode) {
      setWeaponMode(null);
      notify('已取消瞄准', '武器目标只能选在「敌方海域」');
    }
    setViewTab(tab);
  }, [weaponMode]);

  // 己方海域盘的点击：那是只读盘，点它本来什么也不该发生，但**必须说一句**。
  // 实测报过的现象：「点雷达 → 点棋盘中的一个位置 → 没反应，之后直接点棋盘也
  // 没法普通攻击」——用户当时停在己方海域，武器条照样可点，于是进入了瞄准态，
  // 然后在只读盘上反复点击，全程零反馈。
  const handleOwnBoardPress = useCallback(() => {
    if (weaponMode) {
      setViewTab('attack');
      notify('武器目标要选在「敌方海域」', '已经帮你切回敌方海域，继续选择目标');
      return;
    }
    notify('这是你的海域，只能查看', '开炮与武器目标都在「敌方海域」上进行');
  }, [weaponMode]);

  const sendWeapon = useCallback((
    weapon: WeaponKey, cells: number[][],
  ) => {
    const [first] = cells;
    const payload: Record<string, unknown> = { type: 'use_weapon', weapon };
    if (weapon === 'salvo') payload.cells = cells;
    else if (first) { payload.x = first[0]; payload.y = first[1]; }
    socketRef.current?.send(payload);
    setWeaponMode(null);
  }, []);

  // 盘上点击：瞄准中点格 → 攒齐目标发武器，否则是普通开炮
  const handleBoardPress = useCallback((x: number, y: number) => {
    if (!weaponMode) { handleFirePress(x, y); return; }
    const { weapon, cells } = weaponMode;
    if (weapon === 'radar') { sendWeapon(weapon, [[x, y]]); return; }
    if (weapon === 'strike') { sendWeapon(weapon, [[x, y]]); return; }
    // 齐射：同名格不能重复（服务端会拒），攒满 3 格自动发
    if (cells.some(([cx, cy]) => cx === x && cy === y)) {
      setNotice('这个格子已经选过了');
      return;
    }
    const next = [...cells, [x, y]];
    if (next.length >= SALVO_SHOTS) { sendWeapon(weapon, next); return; }
    setWeaponMode({ weapon, cells: next });
  }, [weaponMode, handleFirePress, sendWeapon]);

  const handleSurrender = () => setConfirm('surrender');
  const doSurrender = () => { setConfirm(null); socketRef.current?.surrender(); };

  const handleLeaveRoom = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); } catch { /* continue */ }
    socketRef.current?.leave();
    socketRef.current?.close();
    skipRemoveRef.current = true;
    onLeave();
  };

  // 结算弹窗「返回」：关弹窗 + 关闭本页 socket + goBack，不发 REST leave、不发 socket leave
  // 房间保留（finished 状态），玩家仍留在房间里，回大厅后可直接开始下一局
  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);

  // --- Derived state ---
  const isMyTurn = game ? game.your_color === game.next && !game.winner : false;
  const myColorLabel = game ? (game.your_color === 'blue' ? '蓝方' : '红方') : '';
  const turnText = (() => {
    if (!game || game.winner) return '';
    if (game.phase === 'placing') return '布阵阶段';
    if (isMyTurn) return '轮到你开炮';
    // 人机回合有 0.8-1.2 秒的思考延迟（服务端故意留的节奏）。只写「等待对方…」
    // 用户既不知道在等谁、也不知道要等多久，会以为卡住了——方法论 §1 响应时间
    // / §5 状态可见性。真人对手维持原文案。
    const opp = room?.players?.find((p) => p.id !== user?.id);
    if (opp?.is_bot) return `${opp.nickname || opp.username} 思考中…`;
    return '等待对方...';
  })();

  const showPlacement = phase === 'placing';
  const showBattle = phase === 'battle' || phase === 'finished';

  // 雷达标记：只画在攻击面板上（那是自己打出去的信息面板）
  const scanMarks: ScanMark[] = (game?.own_radar || []).map(
    (r) => ({ x: r.x, y: r.y, has_ship: r.has_ship }),
  );
  // 瞄准中已选的格子（齐射时高亮，让「还差几格」看得见）
  const targetMarks = weaponMode ? weaponMode.cells : [];
  const targetingHint = (() => {
    if (!weaponMode) return null;
    const name = weaponName(weaponMode.weapon);
    if (weaponMode.weapon === 'radar') return `点击要探测的海域中心（${name}）`;
    if (weaponMode.weapon === 'strike') return `点击空袭中心，周围 3×3 全部打击（${name}）`;
    return `选择齐射目标 ${weaponMode.cells.length}/${SALVO_SHOTS}`;
  })();

  return (
    <View style={styles.container}>
      {/* Top bar */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <Ionicons name="ellipse" size={16}
            color={game?.your_color === 'blue' ? playerColors.blue : playerColors.red} />
          <Text style={styles.myColor}>{myColorLabel}</Text>
          <Text style={[styles.turnHint, isMyTurn && styles.turnActive]}>{turnText}</Text>
        </View>
        <View style={styles.topRight}>
          <GameHelpButton game="battleship" size={24} />
          <Text style={styles.roomCode}>#{code}</Text>
        </View>
      </View>

      {/* Disconnected banner */}
      {opponentDisconnected && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={14} color={colors.white} />
          <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* === Placement phase === */}
        {showPlacement && (
          <PlacementView
            board={placementBoard}
            ships={placementShips}
            fleet={fleet}
            selectedShipId={selectedShipId}
            horizontal={horizontal}
            cellSize={cellSize}
            boardPixelSize={boardPixelSize}
            allPlaced={allPlaced}
            placementSubmitted={placementSubmitted}
            game={game}
            viewTab={viewTab}
            onTabChange={setViewTab}
            onCellPress={handlePlacementCellPress}
            onSelectShip={handleSelectShip}
            onRotate={handleRotate}
            onReroll={handleReroll}
            onSubmit={handleSubmitPlacement}
            onMoveShip={handleMoveShip}
            weaponAssign={weaponAssign}
            onCycleWeaponShip={cycleWeaponShip}
          />
        )}

        {/* === Battle phase === */}
        {showBattle && game && (
          <BattleView
            game={game}
            cellSize={cellSize}
            boardPixelSize={boardPixelSize}
            boardSize={boardSize}
            isMyTurn={isMyTurn}
            lastShot={lastShot}
            shotFx={shotFx}
            viewTab={viewTab}
            onTabChange={handleTabChange}
            unseenIncoming={unseenIncoming}
            onFirePress={handleBoardPress}
            onOwnBoardPress={handleOwnBoardPress}
            weaponMode={weaponMode}
            targetingHint={targetingHint}
            scanMarks={scanMarks}
            targetMarks={targetMarks}
            onStartWeapon={startWeapon}
            onCancelWeapon={cancelWeapon}
          />
        )}

        {/* 事件提示条：命中续射 / 击沉 / 武器结果——留死高度，出现消失不推挤棋盘 */}
        {notice && (
          <View style={styles.noticeBar}>
            <Text style={styles.noticeText}>{notice}</Text>
          </View>
        )}
      </ScrollView>

      {/* Bottom bar */}
      <View style={styles.bottomBar}>
        {game && !game.winner && game.phase === 'battle' && (
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
              size={36} color={colors.danger}
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
            {game && (
              <>
                <Text style={styles.modalResult}>
                  {WINNER_LABELS[game.winner || ''] || game.winner}
                </Text>
                {game.reason && (
                  <Text style={styles.modalReason}>
                    {REASON_LABELS[game.reason] || game.reason}
                  </Text>
                )}
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

// --- Placement sub-view ---

function PlacementView({
  board, ships, fleet, selectedShipId, horizontal, cellSize, boardPixelSize,
  allPlaced, placementSubmitted, game, viewTab, onTabChange, onCellPress,
  onSelectShip, onRotate, onReroll, onSubmit, onMoveShip,
  weaponAssign, onCycleWeaponShip,
}: {
  board: number[][]; ships: ShipPlacement[]; selectedShipId: number | null;
  fleet: { id: number; name: string; size: number }[];
  horizontal: boolean; cellSize: number; boardPixelSize: number;
  allPlaced: boolean; placementSubmitted: boolean;
  game: GameSync | null; viewTab: 'own' | 'attack';
  onTabChange: (t: 'own' | 'attack') => void;
  onCellPress: (x: number, y: number) => void;
  onSelectShip: (id: number) => void; onRotate: () => void;
  onReroll: () => void; onSubmit: () => void;
  onMoveShip: (shipId: number, x: number, y: number, horizontal: boolean) => void;
  weaponAssign: Record<string, number>;
  onCycleWeaponShip: (weapon: WeaponKey) => void;
}) {
  const oppReady = game?.placement_ready
    ? Object.entries(game.placement_ready).some(([, v]) => !v)
    : false;
  const boardRef = useRef<View>(null);
  const boardOriginRef = useRef({ x: 0, y: 0 });
  // 拖动预览要画在**棋盘容器之外**（棋盘是 `overflow: hidden`，舰体拖到盘外会被整条裁掉
  // —— 从舰船卡片起手时手指还在盘上方，预览就整个看不见）。所以量两个原点，
  // 差就是「棋盘左上角相对本容器左上角」的偏移。
  const wrapRef = useRef<View>(null);
  const [ghostOffset, setGhostOffset] = useState({ x: 0, y: 0 });
  // 卡片拖动要用棋盘在窗口中的原点换算坐标；棋盘挂载 / 尺寸 / 所在分段变化时重新量
  useEffect(() => {
    boardRef.current?.measureInWindow((bx, by) => {
      boardOriginRef.current = { x: bx, y: by };
      wrapRef.current?.measureInWindow((wx, wy) => setGhostOffset({ x: bx - wx, y: by - wy }));
    });
  }, [cellSize, viewTab]);
  const { drag, boardHandlers, cardHandlers, pressedId } = useShipDrag({
    cellSize, ships, fleet, board, horizontal, disabled: placementSubmitted, boardOriginRef,
    onDragArm: () => onTabChange('own'),
    onDrop: onMoveShip, onTapCell: onCellPress, onTapShipCard: onSelectShip,
  });

  return (
    <View ref={wrapRef} style={styles.phaseWrap}>
      <ShipPanel ships={ships} fleet={fleet} selectedId={selectedShipId}
        horizontal={horizontal} onRotate={onRotate}
        cardHandlers={cardHandlers} pressedId={pressedId} draggingId={drag?.shipId ?? null} />
      <BoardTabs tab={viewTab} onChange={onTabChange} incoming={0} />
      {viewTab === 'own' ? (
        <>
          {/* 棋盘容器同时是拖放手势层：pointerEvents="box-only" 让局部坐标相对棋盘本体 */}
          <GridBoard board={board} cellSize={cellSize} boardPixelSize={boardPixelSize}
            boardSize={board.length} onPress={onCellPress} mode="placement" attackBoard={null}
            oppShips={[]} lastShot={null} disabled={placementSubmitted}
            panHandlers={boardHandlers.panHandlers} containerRef={boardRef} />
          <View style={styles.dragHintRow}>
            <Text style={[styles.dragHint, drag && !drag.valid && styles.dragHintBad]}>
              {drag ? (drag.valid ? '松手放置' : '此处放不下，松手回原位') : '长按舰船可拖动摆放'}
            </Text>
          </View>
        </>
      ) : (
        <EnemyLockedPanel boardPixelSize={boardPixelSize} />
      )}
      {/* 拖动中的舰体预览：挂在棋盘**外面**（零尺寸锚点 + 绝对定位的子节点），
          于是拖到盘外也完整可见 —— 之前它画在棋盘里，被 `overflow: hidden`
          按盘边裁掉，手指还在舰船卡片上时预览整个是空的。 */}
      {drag && (
        <View
          pointerEvents="none"
          style={[styles.ghostLayer, { left: ghostOffset.x, top: ghostOffset.y }]}
        >
          <DragGhost drag={drag} cellSize={cellSize} />
        </View>
      )}
      {/* 特殊武器挂靠：布阵阶段决定「哪条船带着哪件武器」，
          该船被击沉武器就失效——所以布阵要为武器舰留掩护 */}
      <WeaponSetupPanel
        assign={weaponAssign}
        defs={game?.weapon_defs || WEAPON_FALLBACK_DEFS}
        disabled={placementSubmitted}
        fleet={fleet}
        onCycle={onCycleWeaponShip}
      />
      <View style={styles.placementActions}>
        {!placementSubmitted && (
          <>
            <TouchableOpacity style={styles.rerollBtn} onPress={onReroll}>
              <Text style={styles.rerollBtnText}>重新布阵</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.submitBtn, !allPlaced && styles.submitBtnDisabled]}
              onPress={onSubmit} disabled={!allPlaced}
            >
              <Text style={styles.submitBtnText}>完成布阵</Text>
            </TouchableOpacity>
          </>
        )}
        {placementSubmitted && (
          <Text style={styles.waitingText}>
            {oppReady ? '等待对方布阵...' : '等待双方准备...'}
          </Text>
        )}
      </View>
    </View>
  );
}

// --- Battle sub-view ---

function BattleView({
  game, cellSize, boardPixelSize, boardSize, isMyTurn, lastShot, shotFx,
  viewTab, onTabChange, unseenIncoming, onFirePress, onOwnBoardPress,
  weaponMode, targetingHint, scanMarks, targetMarks,
  onStartWeapon, onCancelWeapon,
}: {
  game: GameSync; cellSize: number; boardPixelSize: number;
  /** 棋盘边长（v13）：坐标刻度与放大镜跨度都按它算。 */
  boardSize: number;
  isMyTurn: boolean; lastShot: { x: number; y: number; mine: boolean } | null;
  shotFx: ShotFx | null; viewTab: 'own' | 'attack';
  onTabChange: (t: 'own' | 'attack') => void; unseenIncoming: number;
  onFirePress: (x: number, y: number) => void;
  /** 己方海域盘的点击：那是只读盘，点击只用来给一句解释（不能静默吞掉） */
  onOwnBoardPress: () => void;
  weaponMode: { weapon: WeaponKey; cells: number[][] } | null;
  targetingHint: string | null;
  scanMarks: ScanMark[];
  targetMarks: number[][];
  onStartWeapon: (w: WeaponKey) => void;
  onCancelWeapon: () => void;
}) {
  const isFinished = game.phase === 'finished';
  const showAttack = viewTab === 'attack';
  // 瞄准中：即使还没轮到你也不再禁用（否则点了没反应，用户会以为坏了）
  const aiming = weaponMode !== null;
  return (
    <View style={styles.phaseWrap}>
      {/* Status row */}
      <View style={styles.battleStatus}>
        <Text style={styles.statusLabel}>
          击沉: {game.opp_sunk_count}/{game.opp_ship_count}
        </Text>
        <Text style={styles.statusLabel}>
          己方被沉: {game.own_sunk_count}/{game.opp_ship_count}
        </Text>
      </View>
      <WeaponBar
        defs={game.weapon_defs || WEAPON_FALLBACK_DEFS}
        own={game.own_weapons || {}}
        opp={game.opp_weapons || {}}
        isMyTurn={isMyTurn}
        finished={isFinished}
        aiming={weaponMode?.weapon ?? null}
        onStart={onStartWeapon}
      />
      {/* 瞄准提示 + 取消：选到一半要能退出，否则整局卡在瞄准态 */}
      {aiming && targetingHint && (
        <View style={styles.aimBar}>
          <Text style={styles.aimText}>{targetingHint}</Text>
          <TouchableOpacity style={styles.aimCancel} onPress={onCancelWeapon}>
            <Text style={styles.aimCancelText}>取消</Text>
          </TouchableOpacity>
        </View>
      )}
      {/* 一次只显示一块盘：格子从约 30px 放大到约 37px（390pt 竖屏下 10 列的物理上限） */}
      <BoardTabs tab={viewTab} onChange={onTabChange} incoming={unseenIncoming} />
      {showAttack ? (
        <>
          {/* attack 模式的 CellContent 读的是 attackBoard（atkVal），早先传 null 导致
              命中/未命中标识永远不渲染——炮打完了面板还是空白 */}
          <Text style={styles.boardLabel}>攻击面板</Text>
          <GridBoard board={game.own_attack_board} cellSize={cellSize}
            boardPixelSize={boardPixelSize} boardSize={boardSize}
            onPress={onFirePress} mode="attack"
            attackBoard={game.own_attack_board} oppShips={game.opp_ships}
            lastShot={lastShot && lastShot.mine ? lastShot : null}
            shotEffect={shotFx && shotFx.mine ? shotFx : null}
            scanMarks={scanMarks} targetMarks={targetMarks}
            disabled={(!isMyTurn && !aiming) || isFinished} />
        </>
      ) : (
        <>
          <Text style={styles.boardLabel}>己方海域</Text>
          {/* 己方盘是**只读**盘（对手未沉舰的位置服务端不下发），但**不能禁用**：
              禁用后 RNW 给的是 pointer-events:none，点上去既没反应也没提示，用户只会
              觉得「点了没反应」。这里让点击到达 onOwnBoardPress，由它给一句解释。 */}
          <GridBoard board={game.own_board} cellSize={cellSize}
            boardPixelSize={boardPixelSize} boardSize={boardSize}
            onPress={onOwnBoardPress} mode="own"
            attackBoard={game.incoming_board} oppShips={[]}
            lastShot={lastShot && !lastShot.mine ? lastShot : null}
            shotEffect={shotFx && !shotFx.mine ? shotFx : null}
            disabled={false} />
        </>
      )}
    </View>
  );
}

// --- 特殊武器：布阵期挂靠面板 ---

function WeaponSetupPanel({
  assign, defs, disabled, fleet, onCycle,
}: {
  assign: Record<string, number>;
  defs: Record<string, { name: string; desc: string }>;
  disabled: boolean;
  /** 这一局的舰队 —— 武器挂靠的船名从它查（尺寸档位不同，船也不同）。 */
  fleet: { id: number; name: string; size: number }[];
  onCycle: (w: WeaponKey) => void;
}) {
  const shipName = (id: number) => fleet.find((d) => d.id === id)?.name || '—';
  return (
    <View style={styles.weaponPanel}>
      <Text style={styles.weaponPanelTitle}>特殊武器（每件每局一次 · 携带舰被击沉则失效）</Text>
      <View style={styles.weaponChips}>
        {WEAPON_KEYS.map((w) => (
          <TouchableOpacity
            key={w}
            testID={`bs-weapon-assign-${w}`}
            style={[styles.weaponChip, disabled && styles.weaponChipDisabled]}
            onPress={() => onCycle(w)}
            disabled={disabled}
          >
            <Text style={styles.weaponChipName}>{defs[w]?.name || w}</Text>
            <Text style={styles.weaponChipShip}>{shipName(assign[w])}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <Text style={styles.weaponPanelHint}>点一下换携带舰船（三件武器各挂一条不同的船）</Text>
    </View>
  );
}

// --- 特殊武器：对局期操作条 ---

function WeaponBar({
  defs, own, opp, isMyTurn, finished, aiming, onStart,
}: {
  defs: Record<string, { name: string; desc: string }>;
  own: Record<string, WeaponState>;
  opp: Record<string, WeaponState>;
  isMyTurn: boolean;
  finished: boolean;
  aiming: WeaponKey | null;
  onStart: (w: WeaponKey) => void;
}) {
  const oppAlive = WEAPON_KEYS.filter((w) => opp[w]?.available).length;
  return (
    <View style={styles.weaponBar}>
      <View style={styles.weaponBarTop}>
        <Text style={styles.weaponBarTitle}>我的武器</Text>
        {/* 对手还剩几件——「击沉携带舰」这条反制线索得让人看见 */}
        <Text style={styles.weaponBarOpp}>对方武器 {oppAlive}/3</Text>
      </View>
      <View style={styles.weaponChips}>
        {WEAPON_KEYS.map((w) => {
          const st = own[w];
          const usable = !!st?.available && isMyTurn && !finished;
          const label = !st
            ? '—'
            : st.used ? '已用'
              : !st.available ? '已随舰沉没' : '可用';
          return (
            <TouchableOpacity
              key={w}
              testID={`bs-weapon-${w}`}
              style={[
                styles.weaponChip,
                !usable && styles.weaponChipDisabled,
                aiming === w && styles.weaponChipActive,
              ]}
              onPress={() => onStart(w)}
              disabled={!usable}
            >
              <Text style={styles.weaponChipName}>{defs[w]?.name || w}</Text>
              {/* 状态不只用颜色：文字直接说明「可用 / 已用 / 已随舰沉没」 */}
              <Text style={styles.weaponChipShip}>{label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

// --- Grid board component ---

function GridBoard({
  board, cellSize, boardPixelSize, boardSize, onPress, mode, attackBoard,
  oppShips, lastShot, disabled, panHandlers, containerRef, shotEffect,
  scanMarks, targetMarks,
}: {
  board: number[][]; cellSize: number; boardPixelSize: number;
  /** 棋盘边长（v13：坐标刻度与放大镜的跨度都按它算，不再写死 10）。 */
  boardSize: number;
  onPress: (x: number, y: number) => void;
  mode: 'placement' | 'attack' | 'own';
  attackBoard: number[][] | null;
  oppShips: Array<{ cells: number[][]; sunk: boolean }>;
  lastShot: { x: number; y: number } | null;
  disabled: boolean;
  /** 传入即把整块盘改成单一手势层（布阵拖放用）：格子不再是触摸目标 */
  panHandlers?: GestureResponderHandlers;
  containerRef?: React.RefObject<View | null>;
  shotEffect?: { x: number; y: number; hit: boolean; sunk: boolean } | null;
  /** 雷达探测留下的区域标记（只有自己看得到） */
  scanMarks?: ScanMark[];
  /** 瞄准中已选的格子（齐射用） */
  targetMarks?: number[][];
}) {
  // Build sunk cells set for rendering
  const sunkCells = useMemo(() => {
    const set = new Set<string>();
    for (const s of oppShips) {
      if (s.sunk) for (const [cx, cy] of s.cells) set.add(`${cx},${cy}`);
    }
    return set;
  }, [oppShips]);

  const gesture = !!panHandlers;

  return (
    <View
      ref={containerRef}
      {...(panHandlers ?? {})}
      pointerEvents={gesture ? 'box-only' : 'auto'}
      style={[styles.gridBoard, { width: boardPixelSize, height: boardPixelSize },
        gesture && WEB_TOUCH_NONE]}
    >
      {/* Grid lines */}
      {Array.from({ length: boardSize + 1 }).map((_, i) => (
        <React.Fragment key={`gl-${i}`}>
          {i <= boardSize && (
            <View style={[styles.gLine, { top: cellSize * i, left: 0, width: boardPixelSize }]} />
          )}
          {i <= boardSize && (
            <View style={[styles.gLineV, { left: cellSize * i, top: 0, height: boardPixelSize }]} />
          )}
        </React.Fragment>
      ))}
      {/* Cells */}
      {Array.from({ length: boardSize }).map((_, y) =>
        Array.from({ length: boardSize }).map((_, x) => {
          const cellVal = board[y][x];
          const atkVal = attackBoard ? attackBoard[y][x] : 0;
          const key = `${x},${y}`;
          const isLast = lastShot?.x === x && lastShot?.y === y;
          const cellStyle = [styles.gridCell, { left: cellSize * x, top: cellSize * y,
            width: cellSize, height: cellSize }];
          const content = (
            <CellContent
              cellVal={cellVal} atkVal={atkVal} mode={mode}
              isSunkCell={sunkCells.has(key)} isLast={isLast}
              cellSize={cellSize} testIDBase={`bs-${mode}-${x}-${y}`}
            />
          );
          // 手势层模式下格子只是纯展示（触摸由容器统一接管）
          if (gesture) {
            return (
              <View key={`c-${x}-${y}`} testID={`bs-${mode}-${x}-${y}`} style={cellStyle}>
                {content}
              </View>
            );
          }
          return (
            <TouchableOpacity
              key={`c-${x}-${y}`}
              testID={`bs-${mode}-${x}-${y}`}
              style={cellStyle}
              onPress={() => onPress(x, y)}
              disabled={disabled}
              activeOpacity={disabled ? 1 : 0.7}
            >
              {content}
            </TouchableOpacity>
          );
        }),
      )}
      {/* 雷达区域标记：覆盖整个 3×3（雷达只报「这片海有没有舰」，不是某一格）。
          有舰 = 实线环 + 「舰」；无舰 = 虚线环 + 「空」——颜色之外还有形状与文字 */}
      {(scanMarks || []).map((m, i) => {
        const span = Math.min(3, boardSize) * cellSize;
        const left = Math.max(0, (m.x - 1)) * cellSize;
        const top = Math.max(0, (m.y - 1)) * cellSize;
        const w = Math.min(span, boardPixelSize - left);
        const h = Math.min(span, boardPixelSize - top);
        return (
          <View
            key={`scan-${i}`}
            testID={`bs-scan-${m.x}-${m.y}-${m.has_ship ? 'yes' : 'no'}`}
            pointerEvents="none"
            style={[styles.scanRing,
              m.has_ship ? styles.scanRingYes : styles.scanRingNo,
              { left, top, width: w, height: h }]}
          >
            <Text style={[styles.scanGlyph, m.has_ship && styles.scanGlyphYes]}>
              {m.has_ship ? '舰' : '空'}
            </Text>
          </View>
        );
      })}
      {/* 瞄准中的已选格（齐射）：虚线框 + 序号，让「还差几格」一眼可见 */}
      {(targetMarks || []).map(([tx, ty], i) => (
        <View
          key={`tgt-${tx}-${ty}`}
          testID={`bs-target-${tx}-${ty}`}
          pointerEvents="none"
          style={[styles.targetMark, {
            left: cellSize * tx, top: cellSize * ty,
            width: cellSize, height: cellSize,
          }]}
        >
          <Text style={styles.targetMarkText}>{i + 1}</Text>
        </View>
      ))}
      {shotEffect && (
        <ShotEffect x={shotEffect.x} y={shotEffect.y} cellSize={cellSize}
          hit={shotEffect.hit} sunk={shotEffect.sunk} />
      )}
    </View>
  );
}

// --- Cell content renderer ---

function CellContent({
  cellVal, atkVal, mode, isSunkCell, isLast, cellSize, testIDBase,
}: {
  cellVal: number; atkVal: number; mode: string;
  isSunkCell: boolean; isLast: boolean; cellSize: number;
  testIDBase: string;
}) {
  const innerSize = Math.max(cellSize - 4, 4);
  // Placement mode: show ships by ID color
  if (mode === 'placement') {
    if (cellVal > 0) {
      return (
        <View style={[styles.cellFill, {
          width: innerSize, height: innerSize,
          backgroundColor: SHIP_COLORS[cellVal] || '#888',
          borderRadius: 3,
        }]} />
      );
    }
    return null;
  }
  // Attack mode: show shots（atkVal: -1 未命中 / -2 命中）
  if (mode === 'attack') {
    if (atkVal === -1) {
      // **未中画圈**（v13）：纸笔海战棋的通用记号，比一个实心点好在它是
      // **形状通道**（WCAG 1.4.1：颜色不能单独承载信息）——
      // 灰度打印出来、或红绿色盲的玩家，靠「圈/叉」就能分清中没中。
      const ring = Math.max(innerSize * 0.62, 6);
      return (
        <View testID={`${testIDBase}-miss`}
          style={[styles.hitMarker, { width: innerSize, height: innerSize }]}>
          <View style={[styles.missRing, {
            width: ring, height: ring, borderRadius: ring / 2,
            borderWidth: Math.max(2, innerSize * 0.12),
          }]} />
          {isLast && <View style={styles.lastPing} />}
        </View>
      );
    }
    if (atkVal === -2) {
      // **命中画叉**（v13）：两笔斜杠（圆珠笔的写法）。已沉的那几格仍然画实心块 ——
      // 「叉」与「整块」本身就是两个形状通道，一眼分得出「打中一格」与「这艘沉了」。
      return (
        <View testID={`${testIDBase}-hit`}
          style={[styles.hitMarker, {
            width: innerSize, height: innerSize,
            backgroundColor: isSunkCell ? HIT_SUNK : 'transparent',
            borderRadius: 3,
          }]}>
          {!isSunkCell && (
            <>
              <View style={[styles.inkBar, {
                width: innerSize * 0.92, height: Math.max(2, innerSize * 0.14),
                backgroundColor: HIT_MARK, transform: [{ rotate: '45deg' }],
              }]} />
              <View style={[styles.inkBar, {
                width: innerSize * 0.92, height: Math.max(2, innerSize * 0.14),
                backgroundColor: HIT_MARK, transform: [{ rotate: '-45deg' }],
              }]} />
            </>
          )}
          {isLast && <View style={styles.lastPing} />}
        </View>
      );
    }
    // Sunk ship cells (from opp_ships)
    if (isSunkCell) {
      return (
        <View style={[styles.hitMarker, {
          width: innerSize, height: innerSize,
          backgroundColor: HIT_SUNK, borderRadius: 3,
        }]} />
      );
    }
    return null;
  }
  // Own board: show ships + incoming attacks（attackBoard 传的是 incoming_board）
  if (mode === 'own') {
    const hasShip = cellVal > 0;
    const incoming = atkVal;
    if (incoming === -2) {
      return (
        <View testID={`${testIDBase}-incoming-hit`}
          style={[styles.hitMarker, {
            width: innerSize, height: innerSize,
            backgroundColor: HIT_SUNK, borderRadius: 3,
          }]}>
          {hasShip && <View style={[styles.shipUnder, {
            width: innerSize * 0.5, height: innerSize * 0.5,
            backgroundColor: SHIP_COLORS[cellVal] || '#888',
          }]} />}
        </View>
      );
    }
    if (incoming === -1) {
      return (
        <View testID={`${testIDBase}-incoming-miss`}
          style={[styles.missDot, { width: innerSize * 0.3, height: innerSize * 0.3 }]} />
      );
    }
    if (hasShip) {
      return (
        <View style={[styles.cellFill, {
          width: innerSize, height: innerSize,
          backgroundColor: SHIP_COLORS[cellVal] || '#888',
          borderRadius: 3,
        }]} />
      );
    }
    return null;
  }
  return null;
}

// --- Styles ---

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  scrollContent: { alignItems: 'center', paddingBottom: spacing.xl },
  topBar: {
    width: '100%', flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', padding: spacing.md,
    backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  myColor: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  turnHint: { fontSize: fontSize.sm, color: colors.textSecondary },
  turnActive: { color: colors.primary, fontWeight: '600' },
  roomCode: {
    fontSize: fontSize.sm, color: colors.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  banner: {
    width: '100%', backgroundColor: colors.warning,
    paddingVertical: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
  },
  bannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' },
  phaseWrap: { alignItems: 'center', paddingTop: spacing.md },
  // Ship panel
  shipPanel: {
    width: '100%', paddingHorizontal: spacing.md, marginBottom: spacing.sm,
  },
  shipPanelTitle: { fontSize: fontSize.md, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  shipList: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  shipItem: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingVertical: 4, paddingHorizontal: 8,
    backgroundColor: colors.card, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.borderStrong,
  },
  shipItemSelected: { borderColor: colors.primary, backgroundColor: '#EEF2FF' },
  shipItemPressed: { opacity: 0.6 },
  shipItemDragging: { borderColor: colors.primary, backgroundColor: colors.bg },
  shipPreview: { borderRadius: 3 },
  shipName: { fontSize: fontSize.xs, color: colors.text },
  shipNameSelected: { color: colors.primary, fontWeight: '700' },
  shipPlaced: { fontSize: fontSize.xs, color: colors.success, fontWeight: '600' },
  rotateBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    marginTop: spacing.xs, alignSelf: 'flex-start',
    paddingVertical: 4, paddingHorizontal: 8,
    backgroundColor: colors.card, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.borderStrong,
  },
  rotateBtnText: { fontSize: fontSize.xs, color: colors.primary, fontWeight: '600' },
  // 海域分段切换（触控目标高度 ≈47px，HIG ≥44pt）
  tabRow: {
    flexDirection: 'row', width: '100%', marginBottom: spacing.sm,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  tabItem: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.xs, paddingVertical: spacing.sm + 6,
  },
  tabItemActive: { backgroundColor: colors.card },
  tabText: { fontSize: fontSize.md, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: colors.primary, fontWeight: '800' },
  tabUnderline: {
    position: 'absolute', left: 0, right: 0, bottom: -1, height: 3,
    backgroundColor: 'transparent',
  },
  tabUnderlineActive: { backgroundColor: colors.primary },
  tabBadge: {
    paddingHorizontal: 6, paddingVertical: 1, borderRadius: radius.full,
    backgroundColor: colors.warning,
  },
  tabBadgeText: { fontSize: fontSize.xs, color: colors.white, fontWeight: '700' },
  lockedPanel: {
    alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    backgroundColor: colors.card, borderRadius: 8,
    borderWidth: 2, borderColor: colors.borderStrong, borderStyle: 'dashed',
  },
  lockedTitle: { fontSize: fontSize.md, fontWeight: '700', color: colors.textSecondary },
  lockedHint: { fontSize: fontSize.sm, color: colors.textMuted },
  // 拖动预览与提示
  dragGhost: { position: 'absolute', borderRadius: 3, borderWidth: 2 },
  // 拖动预览的零尺寸锚点：子节点按「棋盘左上角」为原点绝对定位
  ghostLayer: { position: 'absolute', width: 0, height: 0 },
  dragHintRow: { height: 20, justifyContent: 'center', marginTop: spacing.xs },
  dragHint: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  dragHintBad: { color: colors.warning },
  // 开炮落点动效
  shotFx: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  shotRing: { position: 'absolute', width: '100%', height: '100%', borderWidth: 3 },
  shotCore: { position: 'absolute', width: '55%', height: '55%' },
  // Grid board
  gridBoard: {
    position: 'relative', backgroundColor: battleshipWater,
    borderRadius: 8, overflow: 'hidden',
    shadowColor: colors.black, shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.2, shadowRadius: 4, elevation: 3,
  },
  gLine: { position: 'absolute', height: 1, backgroundColor: 'rgba(255,255,255,0.15)' },
  gLineV: { position: 'absolute', width: 1, backgroundColor: 'rgba(255,255,255,0.15)' },
  gridCell: {
    position: 'absolute', alignItems: 'center', justifyContent: 'center',
  },
  cellFill: { borderRadius: 3 },
  missDot: { borderRadius: 99, backgroundColor: 'rgba(255,255,255,0.9)' },
  // **未中画圈**：只有描边、没有填充 —— 与「命中画叉」一起构成纸笔海战棋的
  // 形状通道（v13）。描边取与原来那个圆点同一个白（vs 海面 11.50 ✓）。
  missRing: { borderColor: 'rgba(255,255,255,0.92)', backgroundColor: 'transparent' },
  // 圆珠笔笔画：绝对定位 + 旋转，两条交叉就是「叉」
  inkBar: { position: 'absolute', borderRadius: 2 },
  hitMarker: { alignItems: 'center', justifyContent: 'center' },
  shipUnder: { borderRadius: 99 },
  lastPing: {
    position: 'absolute', width: 8, height: 8, borderRadius: 4,
    backgroundColor: '#FFFFFF',
  },
  // Placement actions
  placementActions: {
    flexDirection: 'row', gap: spacing.md, marginTop: spacing.md,
    alignItems: 'center',
  },
  rerollBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.card, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  rerollBtnText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  submitBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.primary, borderRadius: radius.md,
  },
  submitBtnDisabled: { opacity: 0.5 },
  submitBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  waitingText: { fontSize: fontSize.md, color: colors.textSecondary, fontWeight: '600' },
  // Battle
  battleStatus: {
    flexDirection: 'row', gap: spacing.lg, marginBottom: spacing.sm,
  },
  statusLabel: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  boardLabel: {
    fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600',
    marginBottom: spacing.xs, alignSelf: 'flex-start', marginLeft: spacing.md,
  },
  // ── 特殊武器（v1.10）──
  // 挂靠面板（布阵期）：三枚芯片，点一下换携带舰
  weaponPanel: {
    width: '100%', marginTop: spacing.md, paddingHorizontal: spacing.md,
  },
  weaponPanelTitle: {
    fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600',
    marginBottom: spacing.xs,
  },
  weaponPanelHint: {
    fontSize: fontSize.xs, color: colors.textMuted, marginTop: spacing.xs,
  },
  weaponChips: { flexDirection: 'row', gap: spacing.sm },
  // 芯片高 ≈47px（HIG 主控件 ≥44pt），亮底深字保证对比度
  weaponChip: {
    flex: 1, minHeight: 47, paddingVertical: spacing.sm, borderRadius: radius.md,
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.borderStrong,
    alignItems: 'center', justifyContent: 'center',
  },
  weaponChipDisabled: { backgroundColor: colors.bg, borderColor: colors.border, opacity: 0.6 },
  weaponChipActive: { borderWidth: 2, borderColor: colors.primary, backgroundColor: colors.card },
  weaponChipName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  weaponChipShip: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  // 对局期武器条
  weaponBar: { width: '100%', paddingHorizontal: spacing.md, marginBottom: spacing.sm },
  weaponBarTop: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginBottom: spacing.xs,
  },
  weaponBarTitle: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  weaponBarOpp: { fontSize: fontSize.xs, color: colors.textMuted },
  // 瞄准提示条：留死高度，出现/消失不推挤棋盘
  aimBar: {
    width: '100%', minHeight: 36, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs, marginBottom: spacing.xs,
    backgroundColor: colors.card, borderTopWidth: 1, borderBottomWidth: 1,
    borderColor: colors.border,
  },
  aimText: { flex: 1, fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  aimCancel: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs + 2,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
  },
  aimCancelText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  // 事件提示条：命中续射 / 击沉 / 武器结果
  noticeBar: {
    minHeight: 34, marginTop: spacing.sm, paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs, borderRadius: radius.md,
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.borderStrong,
    alignItems: 'center', justifyContent: 'center',
  },
  noticeText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  // 雷达区域标记：实线「舰」/ 虚线「空」——形状 + 文字，不靠颜色单独承载
  scanRing: {
    position: 'absolute', borderWidth: 2, borderRadius: 6,
    alignItems: 'center', justifyContent: 'center',
  },
  scanRingYes: { borderStyle: 'solid', borderColor: HIT_MARK, backgroundColor: 'rgba(251,191,36,0.22)' },
  scanRingNo: { borderStyle: 'dashed', borderColor: colors.white, backgroundColor: 'rgba(255,255,255,0.10)' },
  scanGlyph: { fontSize: fontSize.xs, fontWeight: '700', color: colors.white },
  scanGlyphYes: { color: colors.white },
  // 齐射已选格：虚线框 + 序号
  targetMark: {
    position: 'absolute', borderWidth: 2, borderStyle: 'dashed',
    borderColor: colors.white, backgroundColor: 'rgba(79,70,229,0.30)',
    alignItems: 'center', justifyContent: 'center',
  },
  targetMarkText: { fontSize: fontSize.xs, fontWeight: '700', color: colors.white },
  // Bottom bar
  bottomBar: { flexDirection: 'row', gap: spacing.md, padding: spacing.md },
  surrenderBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.danger, borderRadius: radius.md,
  },
  surrenderBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  leaveBtn: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  // Modal
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
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  modalBtn: {
    width: '100%', borderRadius: radius.md, paddingVertical: spacing.sm + 4,
    alignItems: 'center', marginTop: spacing.sm,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
