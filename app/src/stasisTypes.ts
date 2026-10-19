// 深空休眠的**服务端载荷类型** —— `docs/游戏设计/深空休眠/协议.md` 的字段原样照抄，
// 不增不减。
//
// 单独一个文件的理由：这些形状被两处以上用（对局屏自己、地图渲染层 `StasisBoard`），
// 放在屏幕里就得让地图层反向 import 屏幕（循环）。**协议字段只有这一份定义** ——
// 客户端不发明字段，服务端加字段时先改 `协议.md` 再改这里。

/** 门在载荷里的原始形状（服务端 `stasis_map._door_json`，格子是 `[x, y]`）。 */
export interface StasisDoorPayload {
  in: number[];
  door: number[];
  out: number[];
  side: string;
}

/** 舱室的**几何**（服务端下发，v9 起客户端不再自己算）。 */
export interface StasisRoomGeom {
  /** 包围盒 `[x, y, w, h]`（含舱壁）。 */
  box: number[];
  /** 可建造的地板格。 */
  cells: number[][];
  bed: number[];
  door: StasisDoorPayload;
  /** 这间房在哪扇共用闸门后面（null = 直接朝通道开门）。 */
  gate: string | null;
}

/** 舱室的**状态**（几何 + 战况，服务端把两者并成一个对象下发）。 */
export interface StasisRoomState extends StasisRoomGeom {
  owner: number | null;
  door_level: number;
  door_hp: number;
  door_max: number;
  emp: boolean;
  buildings: { cell: string; kind: string; level: number }[];
}

/**
 * 共用闸门（`suite` 图）：几何 + 状态，**与房间门逐字同构**。
 *
 * ⚠️ 这里曾经多声明过一个 `cell`（门格自己）。服务端当年发的也是 `cell`，
 * 而客户端的 `buildMap` 只有一个 `toDoor()`、读的是 `door` —— **类型与实现
 * 一起撒了谎**，`buildMap` 在 `suite` 图上直接抛异常，唯一症状是「双人套间
 * 打开全是黑的」。现在服务端发 `door`（`Map.gate_door`），类型也照实写。
 * 回归：`server/test/test_stasis.py::TestMapPayload::`
 * `test_a_gate_payload_is_the_same_shape_as_a_room_door`。
 */
/**
 * **共用闸门的出资账本**（v13）：`uid -> {"bio", "alloy", "acts"}`。
 *
 * 为什么要有它：闸门是一份公共品，两个住户都能升能修 ⇒ 天然有搭便车风险
 * （`增强调研.md` §13.3 是那一轮唯一一条明确的反面证据）。这一轮**不做**
 * 「按出资分收益」那类机制（那是另一个系统），只做**可见性**这一档 —— 把
 * 「两个人都等对方升外门」从一个看不见的僵局变成看得见的一行字。
 */
export interface StasisGateContrib {
  bio: number; alloy: number; acts: number;
}

export interface StasisGateState extends StasisDoorPayload {
  /** 它挡在谁前面。 */
  rooms: string[];
  level: number;
  hp: number;
  max: number;
  /** **砸破了** —— 破了的闸门不再挡路，也不再是静默体的目标。 */
  broken: boolean;
  emp: boolean;
  /** 谁在这扇外闸上花过多少钱（键是 uid 的字符串形式）。 */
  contrib: Record<string, StasisGateContrib>;
}

/** `game_to_sync.map`：整张图的几何 + 每间舱室/每扇闸门的状态。 */
export interface StasisMapPayload {
  id: string;
  name: string;
  w: number;
  h: number;
  /** 整张图的字符画（一行一个字符串）。 */
  rows: string[];
  airlock: number[];
  spawns: number[][];
  rooms: Record<string, StasisRoomState>;
  gates: Record<string, StasisGateState>;
}

export interface StasisPlayerState {
  seat: number;
  room: string | null;
  pos: [number, number];
  /** 躺下了（显式状态；躺着不能移动、才产资源）。 */
  lying?: boolean;
  in_bed?: boolean;
  /**
   * **现在能不能躺下** —— 只随 tick 下发（`dynamic_snapshot`），不在 `game_to_sync`
   * 里。客户端不复刻这个判据，按钮的显隐直接读它（见 `StasisScreen` 里 `canLie`）。
   */
  lie?: boolean;
  bio: number;
  alloy: number;
  bed_level: number;
  alive: boolean;
  surrendered: boolean;
  /** 人机的**性格**（`balanced`/`turtle`/`rush`/`miner`/`medic`/`casual`）。
   *  只有人机有；真人恒为 null。全站浮层里显示它，玩家才知道这队友是什么路数。 */
  ai?: string | null;
}

export interface StasisEntityState {
  level: number;
  hp: number;
  max_hp: number;
  state: string;
  pos: [number, number];
  path: number[][];
  /**
   * 正在砸的目标：**房间 id 或 `"gate:G1"`**（v9 起共用闸门也是目标）。
   * 客户端按 `gate:` 前缀分辨，别当成房间号去查 `rooms`。
   */
  target_door: string | null;
  target_uid: number | null;
}

export interface StasisPayload {
  game: string;
  num_players: number;
  seat_uids: number[];
  seat_colors: string[];
  your_seat: number | null;
  your_uid: number;
  phase: 'run' | 'playing' | 'over';
  run_ms: number;
  clock_ms: number;
  game_ms: number;
  map: StasisMapPayload;
  entity: StasisEntityState;
  players: Record<string, StasisPlayerState>;
  finished: boolean;
  reason: string | null;
  winner: string | null;
}

/** 5 Hz 动态层：只带会动的字段（门的血、玩家位置、静默体位置）。 */
export interface StasisTick {
  clock_ms: number;
  run_ms: number;
  phase: StasisPayload['phase'];
  entity: StasisEntityState;
  players: Record<string, Partial<StasisPlayerState>>;
  doors: Record<string, {
    hp: number; max: number;
    /** 应急力场还剩多久（毫秒，0 = 没开）—— 那 3 秒里门不掉血，必须画出来。 */
    shield_ms?: number;
  }>;
  /** 共用闸门的血量（被砸时每 tick 都在掉）。 */
  gates?: Record<string, { hp: number; max: number; broken: boolean }>;
  /**
   * **表现层弹丸**：正在开火的炮台与它们的目标位置。
   *
   * 引擎里没有弹丸实体（伤害是连续 DPS），服务端只把「谁在开火」告诉客户端，
   * 由 `StasisShots` 自己定开火节奏画枪口焰/弹丸/命中环 —— 见 `增强调研.md` §10.3。
   */
  shots?: { cell: string; level: number; tx: number; ty: number }[];
}

export interface StasisEvent {
  type: string;
  uid?: number;
  room?: string;
  /** 门相关事件的**目标 id**：房间 id 或 `"gate:G1"`（`entity_target` /
   *  `entity_attack_start` / `entity_emp`）。 */
  door?: string;
  /** `gate_breached`：被砸破的闸门 id。 */
  gate?: string;
  /** `entity_emp`：被瘫痪的舱室；`gate_breached`：这扇闸门后面的舱室。 */
  rooms?: string[];
  /** `shield_up` / `entity_bound`：持续毫秒数。 */
  ms?: number;
  level?: number;
  /** `player_eliminated` 的死因：`entity`（被静默体抓住）/ `breach` / `surrender` */
  cause?: string;
  auto?: boolean;
}
