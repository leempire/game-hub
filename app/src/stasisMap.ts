// 深空休眠：地图几何的**客户端视图**。
//
// ⚠️ **这里没有一行几何规则** —— 全部由服务端随 `game_to_sync` 下发
// （`stasis_map.Map.payload()`：整张格子表 + 每间舱室 / 每扇闸门的格子）。
// 本文件只做一件事：把那份载荷包成一个好用的查询对象（`buildMap`）。
//
// # 为什么改成这样（v9）
//
// v2~v8 的写法是「服务端 `stasis_map.py` 是权威，这里是同一套常量的**绘制副本**，
// 改一边必须同步改另一边」。一张图还能靠纪律维持，**四张图就是四份会漂移的副本**，
// 而且漂移的表现是「预演穿墙」「门画在墙上」这种很难归因的毛病。
//
// 现在服务端把 750 格的字符画（`rows`）连同房间/门/闸门一起发下来，代价是每次
// `game_to_sync` 多约 2 KB —— 而它一局只发几次（开局与离散动作后），**不进 5 Hz
// 的 tick**。换来的是客户端一行几何规则都不用重写，加新地图也不用碰前端。
// 这是项目里反复得出的那条结论：**两边都要算同一个判断时，先问「能不能只让一边算」**。
//
// 唯一保留的第二份实现是 `canStep`（落点预演）：位置必须在本地每帧积分，等不了
// 往返（同俄罗斯方块「客户端只为手感做本地预演」）。它的规则与服务端
// `Map.can_step` 一一对应，而且**输入是同一份格子表**，所以不可能再漂移。

import type { StasisMapPayload, StasisRoomGeom } from './stasisTypes';

/** 一格的边长（px）。44 对齐 HIG 主控件下限，与扫雷同一取值。 */
export const TILE = 44;

export type CellKind = 'space' | 'corridor' | 'wall' | 'door' | 'floor';
export type Side = 'left' | 'right' | 'top' | 'bottom';

const CHAR_KIND: Record<string, CellKind> = {
  '.': 'space', c: 'corridor', w: 'wall', d: 'door', f: 'floor',
};

export interface DoorGeom {
  /** 舱内那一格。 */
  inCell: [number, number];
  /** 门格（舱壁上的缺口）。 */
  doorCell: [number, number];
  /** 通道那一格。 */
  outCell: [number, number];
  side: Side;
}

export interface GateGeom extends DoorGeom {
  id: string;
  /** 这扇闸门挡在谁前面。 */
  rooms: string[];
}

export interface RoomGeom {
  id: string;
  /** 包围盒 `[x, y, w, h]`（含舱壁）—— 舱壁整块铺色用它。 */
  box: [number, number, number, number];
  /** 可建造的地板格。 */
  cells: [number, number][];
  bed: [number, number];
  door: DoorGeom;
  /** 这间房在哪扇闸门后面（null = 直接朝通道开门）。 */
  gate: string | null;
}

/** 把服务端下发的几何包成查询对象。**每局建一次**（`game_to_sync` 里换新图时重建）。 */
export interface MapGeom {
  id: string;
  name: string;
  w: number;
  h: number;
  /** 整张图的字符画，一行一个字符串（`.` 图外 / `c` 通道 / `w` 舱壁 / `d` 门 / `f` 地板）。 */
  rows: string[];
  airlock: [number, number];
  spawns: [number, number][];
  roomIds: string[];
  rooms: Record<string, RoomGeom>;
  gates: Record<string, GateGeom>;
  /** 像素尺寸。相机夹取与地图容器用它。 */
  mapW: number;
  mapH: number;
  kindAt(x: number, y: number): CellKind;
  /** 这一格属于哪间舱室（**含舱壁与门格**）。不在任何舱室里返回 null。 */
  roomAt(x: number, y: number): string | null;
  isDoorCell(x: number, y: number): boolean;
  canStep(a: [number, number], b: [number, number]): boolean;
}

function toDoor(d: { in: number[]; door: number[]; out: number[]; side: string }): DoorGeom {
  return {
    inCell: [d.in[0], d.in[1]],
    doorCell: [d.door[0], d.door[1]],
    outCell: [d.out[0], d.out[1]],
    side: d.side as Side,
  };
}

export function buildMap(p: StasisMapPayload): MapGeom {
  const rooms: Record<string, RoomGeom> = {};
  const roomIds: string[] = [];
  for (const rid of Object.keys(p.rooms)) {
    const r = p.rooms[rid];
    roomIds.push(rid);
    rooms[rid] = {
      ...r,
      id: rid,
      box: [r.box[0], r.box[1], r.box[2], r.box[3]],
      cells: r.cells.map((c) => [c[0], c[1]] as [number, number]),
      bed: [r.bed[0], r.bed[1]],
      door: toDoor(r.door),
      gate: r.gate ?? null,
    };
  }
  const gates: Record<string, GateGeom> = {};
  for (const gid of Object.keys(p.gates ?? {})) {
    const g = p.gates[gid];
    gates[gid] = { ...toDoor(g), id: gid, rooms: g.rooms ?? [] };
  }

  // 门两侧的格子对（玩家跨舱壁的唯一通道，房间门与闸门一视同仁）。
  // 键是「排序后的两格」，因为预演问的是「能不能从 A 到 B」，方向无所谓。
  const pairs = new Set<string>();
  const pairKey = (a: [number, number], b: [number, number]) => {
    const ka = `${a[0]},${a[1]}`;
    const kb = `${b[0]},${b[1]}`;
    return ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
  };
  for (const rid of roomIds) {
    const d = rooms[rid].door;
    pairs.add(pairKey(d.inCell, d.doorCell));
    pairs.add(pairKey(d.doorCell, d.outCell));
  }
  for (const gid of Object.keys(gates)) {
    const g = gates[gid];
    pairs.add(pairKey(g.inCell, g.doorCell));
    pairs.add(pairKey(g.doorCell, g.outCell));
  }

  const kindAt = (x: number, y: number): CellKind => {
    if (y < 0 || y >= p.rows.length) return 'space';
    const row = p.rows[y];
    if (x < 0 || x >= row.length) return 'space';
    return CHAR_KIND[row[x]] ?? 'space';
  };
  const roomAt = (x: number, y: number): string | null => {
    if (kindAt(x, y) === 'space') return null;
    // **与「先声明者得」同一条规则**：套间里两间房共用一道内舱壁，那一列会同时
    // 落在两个包围盒里 —— 服务端也按声明顺序取第一个，两边必须一致。
    for (const rid of roomIds) {
      const [ox, oy, w, h] = rooms[rid].box;
      if (x >= ox && x < ox + w && y >= oy && y < oy + h) return rid;
    }
    return null;
  };

  return {
    id: p.id,
    name: p.name,
    w: p.w,
    h: p.h,
    rows: p.rows,
    airlock: [p.airlock[0], p.airlock[1]],
    spawns: (p.spawns ?? []).map((s) => [s[0], s[1]] as [number, number]),
    roomIds,
    rooms,
    gates,
    mapW: p.w * TILE,
    mapH: p.h * TILE,
    kindAt,
    roomAt,
    isDoorCell: (x, y) => kindAt(x, y) === 'door',
    canStep: (a, b) => {
      const kb = kindAt(b[0], b[1]);
      if (kb !== 'corridor' && kb !== 'floor' && kb !== 'door') return false;
      const ka = kindAt(a[0], a[1]);
      if (ka === kb) return true;
      return pairs.has(pairKey(a, b));
    },
  };
}

/** 空图：`game_to_sync` 还没到时的占位（屏幕层在拿到 map 之前不渲染地图）。 */
export const EMPTY_MAP: MapGeom = buildMap({
  id: '', name: '', w: 0, h: 0, rows: [], airlock: [0, 0], spawns: [],
  rooms: {}, gates: {},
});

// ── 格子坐标 ↔ 像素 ─────────────────────────────────────────

/** 格中心的像素坐标（`pos` 是浮点格坐标，格心 = 整数 + 0.5）。 */
export function px(pos: [number, number]): [number, number] {
  return [pos[0] * TILE, pos[1] * TILE];
}

/** 浮点格坐标 → 格（`floor`，与服务端 `stasis._cell_of` 同一套约定）。 */
export function cellOf(pos: [number, number]): [number, number] {
  return [Math.floor(pos[0]), Math.floor(pos[1])];
}

/** 格键 `"x,y"` —— 与服务端 `stasis.cell_key` 同格式（建筑的键）。 */
export function cellKey(x: number, y: number): string {
  return `${x},${y}`;
}

export function parseKey(key: string): [number, number] {
  const [a, b] = key.split(',');
  return [Number(a), Number(b)];
}
