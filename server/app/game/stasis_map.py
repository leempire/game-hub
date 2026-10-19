"""深空休眠：货舱区平面图的**权威几何**（纯函数，无状态）。

v2 起这张图是「3 列 × 4 行、12 间单人舱室」的一张常量表。v9 起它是**一个地图
注册表**：每张图由一份**声明式规格**（`MapSpec`：房间盒、通道矩形、门厅、闸门、
气闸、出生点）描述，`build()` 把它编译成一张只读的 `Map`，引擎与客户端都只跟
编译结果打交道。

    spec（人写的、看得懂）  ──build()──▶  Map（查得到的几何）  ──payload()──▶  客户端

三条设计约束，改这个文件之前先读：

1. **服务端是唯一权威，客户端不再镜像一份几何**。v2~v8 的 `app/src/stasisMap.ts`
   是这套常量的**第二份实现**（「改一边必须同步改另一边」），一张图还能靠纪律维持，
   四张图就是四份会漂移的副本。现在 `Map.payload()` 把**整张格子表**（750 格 =
   750 字节的字符串数组）连同房间/门/闸门一起下发，客户端只负责画。这是项目里
   反复得出的那条结论：**两边都要算同一个判断时，先问「能不能只让一边算」**。
2. **格子类型与房型分开**。`kind_grid` 只记「这格是通道 / 舱壁 / 门 / 地板」，
   房型（`SHAPES`）决定**地板格里哪些其实是舱壁**。房型每局随机分配，所以
   `kind(x, y, shapes)` 要连 `shapes` 一起看，不能只看坐标。
3. **几何一律在 `build()` 里校验**，不在运行时猜。房型池必须覆盖房间的内部尺寸、
   门的内侧格与休眠舱格必须是地板、门厅必须与它那扇闸门相邻 —— 三条都有断言，
   加新地图时写错了会在**建图那一刻**炸，而不是在某一局跑到一半才炸。

# 三张图

| id | 名字 | 拓扑 | 特点 |
|---|---|---|---|
| `dorm` | 标准货舱区 | 3×4 网格、12 间单人舱 | v2 起的老图，格状通道 |
| `suite` | 双人套间 | 6 个套间 × 2 间舱 + **共用闸门** | 合作图：一扇外闸门挡住两间房 |
| `atrium` | 中庭货舱 | 12 间舱围着一个大中庭 | 中庭是共享杀伤区，射程覆盖决定成败 |

## 共用闸门（`suite` 的核心，v9）

一个**套间**是一块 9×10 的舱段：上面两间 3×6 的舱室、下面一条 7 格宽的**门厅**，
门厅与外通道之间是一扇**外闸门**：

        外通道
          │
        ╔═╪═════╗          闸门是**两间房共用的**：
        ║ │A │ B ║          静默体必须先砸开它才能进门厅，
        ║ │  │   ║          再在门厅里挑一扇内门砸。
        ║ └┬─┴┬──║
        ║  门厅   ║          → 「一个人发展经济、一个人发展武器」
        ╚═══╪═══╝             有了物理基础：闸门还在的时候两个人都是安全的。
            │
          外通道

引擎侧的实现只有一条：**门厅的格子平时不在静默体的可达图里**，闸门被砸破之后
才加进去（`Map.ghost_graph(open_gates)`）。玩家不受影响 —— 门是玩家自己开的，
闸门只拦静默体（原型也是这个语义）。

## 坐标

格子坐标 `(x, y)`，x 向右、y 向下，原点左上。**玩家与静默体用浮点格子坐标**
（如 `(8.4, 2.7)`），只有寻路和建造才取整成格。**格 `(x, y)` 的中心是
`(x + 0.5, y + 0.5)`**（见 `stasis._cell_of`）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache

# ── 格子类型 ────────────────────────────────────────────────

SPACE = "space"          # 图外（不画、不可走）
CORRIDOR = "corridor"    # 通道（含门厅）
WALL = "wall"            # 舱壁
DOOR = "door"            # 门格（舱壁上的缺口，房间门与闸门都是它）
FLOOR = "floor"          # 舱室内部的地板（房型遮罩还可能把它变成舱壁）

KIND_CHAR = {SPACE: ".", CORRIDOR: "c", WALL: "w", DOOR: "d", FLOOR: "f"}
CHAR_KIND = {v: k for k, v in KIND_CHAR.items()}

SIDES = ("left", "right", "top", "bottom")
# 每个方向：从门格往外走一步的偏移（通道那一侧）
OUT_STEP = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}
IN_STEP = {k: (-v[0], -v[1]) for k, v in OUT_STEP.items()}
# 门开在包围盒的哪条边上（沿门法线的内侧边界）
DOOR_EDGE = {"left": (0, 0), "right": (1, 0), "top": (0, 0), "bottom": (0, 1)}


# ── 房型遮罩 ────────────────────────────────────────────────
#
# 1 = 可建造地板，0 = 舱壁。**凹形决定炮台摆位**（原型里「凹形房间最合适」就是
# 这个意思）—— 房间不是矩形，塔放哪儿能同时覆盖门和来路是要想的。
#
# 每条房型有两条硬约束（`build()` 会逐个房间 × 逐个房型验，写错直接炸）：
#   ① 门的内侧格必须是地板   ② 休眠舱那一格必须是地板
#
# **按内部尺寸分池**：房型遮罩的尺寸必须与房间内部一致，所以不同内部尺寸的房间
# 用不同的池（`SHAPE_POOLS`）。加新房型尺寸时要同时给池子。
SHAPE_POOLS: dict[tuple[int, int], dict[str, list[str]]] = {
    # 5×4 —— 标准舱室（`dorm` / `atrium` 的 7×6 包围盒）
    (5, 4): {
        "rect":   ["11111", "11111", "11111", "11111"],   # 20 格
        "notch":  ["11111", "11111", "00111", "00111"],   # 16 格（左下缺角）
        "lshape": ["11111", "11111", "11100", "11100"],   # 16 格（右下缺角）
        "groove": ["11011", "11011", "11111", "11111"],   # 18 格（顶部中间凹进去）
        "pillar": ["11111", "10111", "10111", "11111"],   # 18 格（中间一根柱）
        "tshape": ["11111", "11111", "01110", "01110"],   # 16 格（底部收窄）
    },
    # 3×6 —— 套间里那两间窄舱（5×8 包围盒）。门在下边、床在上边，所以
    # **门的内侧格是第 6 行第 2 列、休眠舱是第 1 行第 1 列**，两个都必须恒为 1。
    (3, 6): {
        "rect":     ["111", "111", "111", "111", "111", "111"],   # 18 格
        "leftbay":  ["111", "111", "011", "011", "111", "111"],   # 16 格
        "rightbay": ["111", "111", "110", "110", "111", "111"],   # 16 格
        "pillar":   ["111", "101", "111", "111", "101", "111"],   # 16 格
        "endcap":   ["111", "111", "111", "111", "110", "110"],   # 16 格
        "alcove":   ["101", "111", "111", "111", "111", "111"],   # 17 格
    },
}


# ── 规格 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoomSpec:
    """一间舱室的**声明**。坐标一律是格坐标。

    `door_at` 是门在「沿门那条边」方向上的**内部下标**（0 起）：
    门在上下边时它是列下标，在左右边时它是行下标。
    `bed` 是休眠舱在**内部坐标**里的位置（0 起，不含舱壁）。
    """

    rid: str
    x: int
    y: int
    w: int
    h: int
    door: str
    door_at: int
    bed: tuple[int, int]
    gate: str | None = None       # 这间房在哪扇闸门后面（None = 直接朝通道开门）


@dataclass(frozen=True)
class GateSpec:
    """**共用闸门**：挡在一组舱室与通道之间的那扇门。

    `cell` 是门格自己，`side` 是它朝**通道**开的那一侧 —— 与房间门的 `side`
    同一套语义（`out = cell + OUT_STEP[side]`，`in = cell + IN_STEP[side]`）。
    """

    gid: str
    rooms: tuple[str, ...]
    cell: tuple[int, int]
    side: str


@dataclass(frozen=True)
class MapSpec:
    id: str
    name: str
    w: int
    h: int
    rooms: tuple[RoomSpec, ...]
    corridors: tuple[tuple[int, int, int, int], ...]      # (x, y, w, h)
    airlock: tuple[int, int]
    spawns: tuple[tuple[int, int], ...]
    gates: tuple[GateSpec, ...] = ()
    vestibules: tuple[tuple[str, int, int, int, int], ...] = ()   # (gid, x, y, w, h)
    blurb: str = ""                                        # 大厅里给玩家看的一句话


# ── 编译结果 ────────────────────────────────────────────────


class Map:
    """一张**编译好**的地图。全部几何在建图时算完并缓存，运行时只有查表。

    只读：`build()` 之后再没有任何东西会改它，所以可以跨局共享（`get_map` 缓存）。
    """

    def __init__(self, spec: MapSpec):
        self.spec = spec
        self.id = spec.id
        self.name = spec.name
        self.w, self.h = spec.w, spec.h
        self.airlock = tuple(spec.airlock)
        self.spawns = [tuple(s) for s in spec.spawns]
        self.blurb = spec.blurb

        self.rooms = {r.rid: r for r in spec.rooms}
        self.room_ids = [r.rid for r in spec.rooms]
        self.gates = {g.gid: g for g in spec.gates}

        self._build_base()
        self._build_rooms()
        self._build_gates()
        self._cache_derived()
        self._validate()

    # ── 建图 ────────────────────────────────────────────────

    def _build_base(self) -> None:
        """铺基础格子表：先通道、再门厅、再房间（包围盒外环 = 舱壁，内部 = 地板）。"""
        self.base = [[SPACE] * self.w for _ in range(self.h)]
        self.room_grid: list[list[str | None]] = [[None] * self.w for _ in range(self.h)]

        for x, y, cw, ch in self.spec.corridors:
            for yy in range(y, y + ch):
                for xx in range(x, x + cw):
                    self.base[yy][xx] = CORRIDOR

        # 门厅：也是通道格，但**受闸门保护**（静默体的可达图平时不含它们）
        self.gated: dict[str, frozenset[tuple[int, int]]] = {}
        for gid, x, y, cw, ch in self.spec.vestibules:
            cells = set()
            for yy in range(y, y + ch):
                for xx in range(x, x + cw):
                    self.base[yy][xx] = CORRIDOR
                    cells.add((xx, yy))
            self.gated[gid] = frozenset(cells)

        for r in self.spec.rooms:
            for yy in range(r.y, r.y + r.h):
                for xx in range(r.x, r.x + r.w):
                    lx, ly = xx - r.x, yy - r.y
                    edge = lx in (0, r.w - 1) or ly in (0, r.h - 1)
                    self.base[yy][xx] = WALL if edge else FLOOR
                    # **重叠的包围盒由「先声明者得」**（套间里两间房共用一道内舱壁，
                    # 那一列会同时落在两个盒子里）。墙格归谁不影响任何判据 ——
                    # 地板格不可能重叠，而所有按房间判定的逻辑（建造、占房、
                    # 客户端点格子）都只对地板格提问。
                    if self.room_grid[yy][xx] is None:
                        self.room_grid[yy][xx] = r.rid

        for r in self.spec.rooms:
            d = self.room_door(r.rid)
            self.base[d["door"][1]][d["door"][0]] = DOOR
            self.room_grid[d["door"][1]][d["door"][0]] = r.rid

    def _build_rooms(self) -> None:
        for rid, r in self.rooms.items():
            iw, ih = r.w - 2, r.h - 2
            if (iw, ih) not in SHAPE_POOLS:
                raise ValueError(
                    f"地图 {self.id}：舱室 {rid} 的内部尺寸 {iw}×{ih} 没有房型池")

    def _build_gates(self) -> None:
        for g in self.gates.values():
            self.base[g.cell[1]][g.cell[0]] = DOOR

    def _cache_derived(self) -> None:
        """把三个每次都要用、但一局里不会变的集合算好存起来。

        **不用 `lru_cache` 装饰方法** —— 那会把 `self` 一起缓存住（地图实例被
        永久持有），而且 `maxsize` 一满就把别的地图的缓存顶掉，是个看着能用、
        实际会漏的写法。地图一共三张、`get_map` 本来就缓存了实例，直接算更省事。
        """
        self.door_cells = frozenset(
            [self.room_door(rid)["door"] for rid in self.room_ids]
            + [g.cell for g in self.gates.values()])
        self.corridor_cells = frozenset(
            (x, y) for y in range(self.h) for x in range(self.w)
            if self.base[y][x] == CORRIDOR)
        pairs = set()
        for rid in self.room_ids:
            d = self.room_door(rid)
            pairs.add(frozenset((d["in"], d["door"])))
            pairs.add(frozenset((d["door"], d["out"])))
        for gid in self.gates:
            pairs.add(frozenset((self.gate_in(gid), self.gate_cell(gid))))
            pairs.add(frozenset((self.gate_cell(gid), self.gate_out(gid))))
        self._door_pairs = frozenset(pairs)
        # 「这一格在哪扇闸门后面」—— 追击判定要 O(1) 问它（见 `gated_owner`）
        self._gated_of = {c: gid for gid, cells in self.gated.items() for c in cells}

    def _validate(self) -> None:
        """三条硬约束。**建图那一刻炸**，不要留到某一局跑到一半。"""
        for rid, r in self.rooms.items():
            iw, ih = r.w - 2, r.h - 2
            pool = SHAPE_POOLS[(iw, ih)]
            d = self.room_door(rid)
            inx, iny = d["in"]
            lx, ly = inx - (r.x + 1), iny - (r.y + 1)
            blx, bly = r.bed
            for sid, mask in pool.items():
                if mask[ly][lx] != "1":
                    raise ValueError(
                        f"地图 {self.id}：房型 {sid} 把 {rid} 的门内侧格挖掉了")
                if mask[bly][blx] != "1":
                    raise ValueError(
                        f"地图 {self.id}：房型 {sid} 把 {rid} 的休眠舱格挖掉了")
            if self.kind(inx, iny, {k: "rect" for k in self.rooms}) != FLOOR:
                raise ValueError(f"地图 {self.id}：{rid} 的门内侧格不是地板")
            # 门必须真的通到通道（外侧格是通道或另一间房的门厅）
            ox, oy = d["out"]
            if not self.in_bounds(ox, oy) or self.base[oy][ox] != CORRIDOR:
                raise ValueError(f"地图 {self.id}：{rid} 的门外面不是通道")
        for gid, cells in self.gated.items():
            g = self.gates[gid]
            ix, iy = g.cell[0] + IN_STEP[g.side][0], g.cell[1] + IN_STEP[g.side][1]
            if (ix, iy) not in cells:
                raise ValueError(f"地图 {self.id}：闸门 {gid} 的内侧格不在门厅里")
            ox, oy = g.cell[0] + OUT_STEP[g.side][0], g.cell[1] + OUT_STEP[g.side][1]
            if not self.in_bounds(ox, oy) or self.base[oy][ox] != CORRIDOR \
                    or (ox, oy) in cells:
                raise ValueError(f"地图 {self.id}：闸门 {gid} 的外侧格不是外通道")
            for rid in g.rooms:
                if self.rooms[rid].gate != gid:
                    raise ValueError(f"地图 {self.id}：{rid} 没挂在闸门 {gid} 上")
        if self.kind(*self.airlock, {k: "rect" for k in self.rooms}) != CORRIDOR:
            raise ValueError(f"地图 {self.id}：气闸不在通道上")
        for s in self.spawns:
            if self.kind(*s, {k: "rect" for k in self.rooms}) != CORRIDOR:
                raise ValueError(f"地图 {self.id}：出生点 {s} 不在通道上")

    # ── 查表 ────────────────────────────────────────────────

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def base_kind(self, x: int, y: int) -> str:
        """不含房型的格子类型（`FLOOR` 表示「房型说了算」）。"""
        if not self.in_bounds(x, y):
            return SPACE
        return self.base[y][x]

    def kind(self, x: int, y: int, shapes: dict[str, str]) -> str:
        """`space` / `corridor` / `wall` / `door` / `floor`。

        `shapes` 是**这一局**的房型分配（`{rid: shape_id}`）—— 房型每局随机，
        所以格子类型不能只看坐标。
        """
        b = self.base_kind(x, y)
        if b != FLOOR:
            return b
        rid = self.room_grid[y][x]
        if rid is None:
            return WALL
        r = self.rooms[rid]
        mask = SHAPE_POOLS[(r.w - 2, r.h - 2)][shapes[rid]]
        return FLOOR if mask[y - r.y - 1][x - r.x - 1] == "1" else WALL

    def room_at(self, x: int, y: int) -> str | None:
        """这一格属于哪间舱室（**含舱壁与门格**）。不在任何舱室里返回 None。"""
        if not self.in_bounds(x, y):
            return None
        return self.room_grid[y][x]

    def is_corridor(self, x: int, y: int) -> bool:
        return self.base_kind(x, y) == CORRIDOR

    def gated_owner(self, cell: tuple[int, int]) -> str | None:
        """这一格属于哪扇闸门后面的**门厅**（不是门厅就返回 None）。

        **追击判定要用它**：门厅在闸门没破时对静默体不可达，而玩家能走进去
        （闸门只拦静默体）—— 不排掉的话它会对一个「隔着门砸不到」的人
        永远重规划、永远追不上（`_chase_candidate` 里那条注释说的就是这个坑）。
        """
        return self._gated_of.get(cell)

    def room_box(self, rid: str) -> tuple[int, int, int, int]:
        r = self.rooms[rid]
        return r.x, r.y, r.w, r.h

    def room_bed(self, rid: str) -> tuple[int, int]:
        r = self.rooms[rid]
        return r.x + 1 + r.bed[0], r.y + 1 + r.bed[1]

    def room_door(self, rid: str) -> dict:
        """舱室的气密门：`in` 舱内格 → `door` 门格（墙上的缺口）→ `out` 通道格。

        **只能从这三个格子进出舱室** —— 包围盒其余边界格是实心舱壁，不可穿越
        （`can_step` 靠这条把「门」变成真的门，而不只是装饰）。

        `door_at` 的语义：门在左右边时是**内部行**下标，在上下边时是**内部列**
        下标；内侧格与门格在同一行/列上（相差一格），别把两者算岔了。
        """
        r = self.rooms[rid]
        side = r.door
        if side in ("left", "right"):
            iy = r.y + 1 + r.door_at
            ix = r.x + (1 if side == "left" else r.w - 2)
            door = (r.x + (0 if side == "left" else r.w - 1), iy)
        else:
            ix = r.x + 1 + r.door_at
            iy = r.y + (1 if side == "top" else r.h - 2)
            door = (ix, r.y + (0 if side == "top" else r.h - 1))
        return {
            "in": (ix, iy),
            "door": door,
            "out": (door[0] + OUT_STEP[side][0], door[1] + OUT_STEP[side][1]),
            "side": side,
        }

    def gate_cell(self, gid: str) -> tuple[int, int]:
        return self.gates[gid].cell

    def gate_door(self, gid: str) -> dict:
        """闸门的门位描述 —— **与 `room_door` 同构**（`in` / `door` / `out` / `side`）。

        共用闸门在引擎里就是「一扇没有主人的门」，**载荷也必须是同一个形状**：
        客户端只有一个 `toDoor()`（按 `door` 取门格），房间门与闸门共用它。
        v9 的第一版给闸门发的是 `cell`，于是 `suite` 图下那个函数读到
        `undefined[0]` 直接抛异常 —— 服务端一切正常（引擎用例全绿、载荷里
        该有的格子一个不少），**只有浏览器里那张图是黑的**。
        """
        g = self.gates[gid]
        return {
            "in": self.gate_in(gid),
            "door": g.cell,
            "out": self.gate_out(gid),
            "side": g.side,
        }

    def gate_in(self, gid: str) -> tuple[int, int]:
        g = self.gates[gid]
        return g.cell[0] + IN_STEP[g.side][0], g.cell[1] + IN_STEP[g.side][1]

    def gate_out(self, gid: str) -> tuple[int, int]:
        g = self.gates[gid]
        return g.cell[0] + OUT_STEP[g.side][0], g.cell[1] + OUT_STEP[g.side][1]

    def room_gate(self, rid: str) -> str | None:
        return self.rooms[rid].gate

    def room_cells(self, rid: str, shape: str) -> list[tuple[int, int]]:
        """该舱室**可建造**的格子（内部地板格），按行优先排序。"""
        r = self.rooms[rid]
        mask = SHAPE_POOLS[(r.w - 2, r.h - 2)][shape]
        return [(r.x + 1 + lx, r.y + 1 + ly)
                for ly in range(r.h - 2) for lx in range(r.w - 2)
                if mask[ly][lx] == "1"]

    def is_floor(self, rid: str, x: int, y: int, shapes: dict[str, str]) -> bool:
        """`(x, y)` 是不是该舱室的一格地板（建造合法性用它）。"""
        return self.room_at(x, y) == rid and self.kind(x, y, shapes) == FLOOR

    def walkable(self, shapes: dict[str, str]) -> set[tuple[int, int]]:
        """玩家能站的格子：全部通道格 + 全部舱室地板格。"""
        out = set(self.corridor_cells)
        for rid in self.room_ids:
            out |= set(self.room_cells(rid, shapes[rid]))
        return out

    def can_step(self, shapes: dict[str, str], a: tuple[int, int],
                 b: tuple[int, int]) -> bool:
        """玩家从格 `a` 走到相邻格 `b` 是否允许。

        通道内、舱室内自由走；**跨舱壁只能经过门那三个格子**（内 → 门 → 通道）。
        **闸门对玩家永远是开的**（门是玩家自己开的，它只拦静默体）。
        """
        ka, kb = self.kind(*a, shapes), self.kind(*b, shapes)
        if kb not in (CORRIDOR, FLOOR, DOOR):
            return False
        if ka != kb:
            return frozenset((a, b)) in self._door_pairs
        return True

    # ── 静默体的可达图 ──────────────────────────────────────

    def ghost_graph(self, open_gates=()) -> set[tuple[int, int]]:
        """静默体**这一时刻**能走的格子。

        平时是「通道格 − 全部门厅」；某扇闸门被砸破之后，它自己和它那间门厅
        一起加进来（`open_gates` 是已破闸门的 id 集合）。
        """
        open_set = set(open_gates)
        graph = set(self.corridor_cells)
        for gid, cells in self.gated.items():
            if gid in open_set:
                graph |= cells
                graph.add(self.gate_cell(gid))
            else:
                graph -= cells
        return graph

    # ── 寻路 ────────────────────────────────────────────────

    def ghost_path(self, start: tuple[int, int], goal: tuple[int, int],
                   open_gates=()) -> list[tuple[int, int]]:
        """静默体在通道网格上的路径（起点会先吸附到最近的通道格）。

        `open_gates` 见 `ghost_graph` —— **闸门没破时它到不了门厅里那扇门**，
        所以攻击目标必须先按可达性筛过一遍（`stasis.choose_target`）。

        ⚠️ **起点会吸附、终点不会**：起点可能落在舱室或门厅里（静默体站在那儿），
        吸附到最近的可走格是必要的兜底；而**终点够不着时必须回空表**，不能吸附
        到附近就算了 —— 那会让「门厅还没开」变成「它已经在门口了」，调用方
        分不出「到了」和「到不了」（`_move_entity` 的 approach 分支正是靠
        「路径空 + 人不在门口」来识别这种局面的）。
        """
        graph = self.ghost_graph(open_gates)
        goal = (int(round(goal[0])), int(round(goal[1])))
        if goal not in graph:
            return []
        return bfs_path(graph, snap_to(graph, start, self.airlock), goal)

    def distance(self, a: tuple[int, int], b: tuple[int, int],
                 open_gates=()) -> int:
        """两格之间在**通道网格**上的步数（人机挑舱室用它）。"""
        return len(self.ghost_path(a, b, open_gates))

    def reachable(self, start: tuple[int, int], open_gates=()) -> set[tuple[int, int]]:
        """从 `start` 出发能走到的格子集合（选目标时一次算完，别逐门跑 BFS）。"""
        graph = self.ghost_graph(open_gates)
        s = snap_to(graph, start, self.airlock)
        seen = {s}
        queue = [s]
        head = 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            for nb in _neighbors(cur):
                if nb in graph and nb not in seen:
                    seen.add(nb)
                    queue.append(nb)
        return seen

    # ── 建局时分配房型 ──────────────────────────────────────

    def assign_shapes(self, rng: random.Random) -> dict[str, str]:
        """把房型随机分配到本图的每一间舱室上。

        刻意**不是**「大房型固定给某几间」—— 每局重掷，「选哪间」才是新决策。
        但保证 `rect`（最大）至少出现两次，免得一局里全是小房。
        """
        out: dict[str, str] = {}
        for pool_key, pool in SHAPE_POOLS.items():
            ids = [rid for rid in self.room_ids
                   if (self.rooms[rid].w - 2, self.rooms[rid].h - 2) == pool_key]
            if not ids:
                continue
            names = list(pool)
            deck = names + ["rect"]
            rng.shuffle(deck)
            picks = (deck * 4)[:len(ids)]
            rng.shuffle(picks)
            for rid, sid in zip(ids, picks):
                out[rid] = sid
        return out

    # ── 下发给客户端的几何 ──────────────────────────────────

    def payload(self, shapes: dict[str, str]) -> dict:
        """**整张图**的几何，随 `game_to_sync` 下发（v9 起客户端不再镜像一份）。

        750 格 = 750 字节的字符画 + 十几间房的格子表，一局只发几次（`game_start`
        与离散动作后的 `stasis_state`），不进 5 Hz 的 tick。换来的是**客户端一行
        几何规则都不用重写** —— 这比省那点带宽值钱得多。
        """
        return {
            "id": self.id,
            "name": self.name,
            "w": self.w,
            "h": self.h,
            "rows": ["".join(KIND_CHAR[self.kind(x, y, shapes)] for x in range(self.w))
                     for y in range(self.h)],
            "airlock": list(self.airlock),
            "spawns": [list(s) for s in self.spawns],
            "rooms": {
                rid: {
                    "box": list(self.room_box(rid)),
                    "cells": [list(c) for c in self.room_cells(rid, shapes[rid])],
                    "bed": list(self.room_bed(rid)),
                    "door": _door_json(self.room_door(rid)),
                    "gate": self.room_gate(rid),
                }
                for rid in self.room_ids
            },
            # **闸门的几何与房间门逐字同构**（同一个 `_door_json`）—— 客户端的
            # `toDoor()` 对两者是同一个函数，形状一旦分叉就是「图是黑的」。
            "gates": {
                gid: {**_door_json(self.gate_door(gid)), "rooms": list(g.rooms)}
                for gid, g in self.gates.items()
            },
        }


def _door_json(d: dict) -> dict:
    return {"in": list(d["in"]), "door": list(d["door"]),
            "out": list(d["out"]), "side": d["side"]}


# ── 寻路原语 ────────────────────────────────────────────────


def _neighbors(cell: tuple[int, int]):
    x, y = cell
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def bfs_path(graph: set[tuple[int, int]], start: tuple[int, int],
             goal: tuple[int, int]) -> list[tuple[int, int]]:
    """`graph` 上的最短路径（不含起点，含终点）。不可达返回空表。

    地图只有几百格，BFS 每格几十微秒 —— 静默体只在**规划时**跑一次，不需要 A*。
    四邻域 + 列表当队列保证同种子下结果稳定（单测可复现）。
    """
    if start == goal:
        return []
    if start not in graph or goal not in graph:
        return []
    prev: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    queue = [start]
    head = 0
    while head < len(queue):
        cur = queue[head]
        head += 1
        for nb in _neighbors(cur):
            if nb in prev or nb not in graph:
                continue
            prev[nb] = cur
            if nb == goal:
                path = [nb]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            queue.append(nb)
    return []


def snap_to(graph, cell: tuple[int, int], fallback: tuple[int, int]
            ) -> tuple[int, int]:
    """把一个可能落在舱室里的格子吸附到 `graph` 里最近的一格（BFS 起点用）。"""
    x, y = int(round(cell[0])), int(round(cell[1]))
    if (x, y) in graph:
        return x, y
    best, best_d = fallback, 1 << 30
    for cx, cy in graph:
        d = abs(cx - x) + abs(cy - y)
        if d < best_d:
            best, best_d = (cx, cy), d
    return best


# ── 三张图 ──────────────────────────────────────────────────
#
# 坐标全部手写。**加新地图只需要在这里加一份 spec** —— 几何、寻路、房型池、
# 客户端渲染都跟着自动走（客户端拿的是 `payload()`）。

_DORM_W, _DORM_H = 25, 30          # 3 列 × 4 行，格状通道（v2 起的老图）


def _dorm_spec() -> MapSpec:
    """标准货舱区：3 列 × 4 行、12 间单人舱，通道交叉成网格。

        气闸
         ▼
    ╔═════╗╔═════╗╔═════╗
    ║  A1 ║║  B1 ║║  C1 ║
    ╚═════╝╚═════╝╚═════╝   包围盒 7×6、内部 5×4（房型池 5×4）
    ╔═════╗╔═════╗╔═════╗   A 列的门朝右开、B/C 列朝左开 ——
    ║  A2 ║║  B2 ║║  C2 ║   门一律开向最近的纵向通道。
    ╚═════╝╚═════╝╚═════╝
    ╔═════╗╔═════╗╔═════╗   **门自己也占一格**（舱壁上的那个缺口）：
    ║  A3 ║║  B3 ║║  C3 ║   舱内格 → 门格 → 通道格，三步出门。
    ╚═════╝╚═════╝╚═════╝
    ╔═════╗╔═════╗╔═════╗
    ║  A4 ║║  B4 ║║  C4 ║
    ╚═════╝╚═════╝╚═════╝
    """
    letters = "ABC"
    door_side = {"A": "right", "B": "left", "C": "left"}
    rooms = tuple(
        RoomSpec(f"{letters[c]}{r + 1}", c * 9, r * 8, 7, 6,
                 door_side[letters[c]], 1, (2, 3))
        for r in range(4) for c in range(3))
    corridors = tuple(
        [(c * 9 + 7, 0, 2, _DORM_H) for c in range(2)]
        + [(0, r * 8 + 6, _DORM_W, 2) for r in range(3)])
    return MapSpec(
        id="dorm", name="标准货舱区", w=_DORM_W, h=_DORM_H,
        rooms=rooms, corridors=corridors,
        airlock=(7, 0),
        spawns=((7, 4), (8, 4), (7, 5), (8, 5), (7, 6), (8, 6)),
        blurb="12 间单人舱，格状通道。最经典的一张。")


def _suite(ox: int, oy: int, a: str, b: str, gid: str
           ) -> tuple[list[RoomSpec], GateSpec, tuple]:
    """一个套间：两间 3×6 的舱室（A 左 B 右）+ 下面一条 7 格门厅 + 一扇外闸门。

    以 `oy` 为原点、`ox` 为左边界，逐行长这样（`.` = 图外、`w` 舱壁、`d` 门格）：

        ly 0     wwwwwwwww    顶舱壁（x = ox .. ox+8）
        ly 1..6  wfffwfffw    **两间房的包围盒都是 5×8**（ly 0..7）：
                              lx=4 那一列是两间房之间的内舱壁，
                              内部各 3×6（房型池 `(3, 6)`）
        ly 7     wwdwwwdww    门厅上面的内舱壁，x=ox+2 / ox+6 两个缺口＝**两扇内门**
        ly 8     .ccccccc.    门厅：一条 7 格宽的通道（x = ox+1 .. ox+7），两侧是图外
        ly 9     ....d....    只有**外闸门自己那一格**（x = ox+4）

    ⚠️ **门厅的两侧与底线之外是 `space`（图外），不是舱壁**：套间是「两间房 + 一条
    门厅」，不是一个四面围死的盒子。于是外闸门是**悬在门厅与外面通道之间的一道门**
    （房间门则是墙上的一个缺口）。这不影响玩法 —— `space` 不可走，进出仍然只有
    三条路：两扇内门 + 外闸门。

    两间房的休眠舱都在**离门最远的那一行**（内部第 0 行），所以躺下之前得真的
    走进房间深处。
    """
    rooms = [
        RoomSpec(a, ox, oy, 5, 8, "bottom", 1, (0, 0), gate=gid),
        RoomSpec(b, ox + 4, oy, 5, 8, "bottom", 1, (0, 0), gate=gid),
    ]
    gate = GateSpec(gid, (a, b), (ox + 4, oy + 9), "bottom")
    vestibule = (gid, ox + 1, oy + 8, 7, 1)
    return rooms, gate, vestibule


def _suite_spec() -> MapSpec:
    """双人套间：3 列 × 2 行 = 6 个套间、12 间舱室，每两间共用一扇外闸门。

    竖通道在 x=9,10 与 x=20,21，横通道在 y=10,11 与 y=22,23；气闸在左上竖通道顶端。
    **套间的闸门一律朝下开**，所以每个套间都从它下面那条横通道进出。
    """
    rooms: list[RoomSpec] = []
    gates: list[GateSpec] = []
    vests: list[tuple] = []
    for r in range(2):
        for c in range(3):
            ox, oy = c * 11, r * 12
            gid = f"G{r * 3 + c + 1}"
            a = f"{'ABC'[c]}{r * 2 + 1}"
            b = f"{'ABC'[c]}{r * 2 + 2}"
            rs, g, v = _suite(ox, oy, a, b, gid)
            rooms += rs
            gates.append(g)
            vests.append(v)
    corridors = tuple(
        [(9, 0, 2, 24), (20, 0, 2, 24), (0, 10, 31, 2), (0, 22, 31, 2)])
    return MapSpec(
        id="suite", name="双人套间", w=31, h=24,
        rooms=tuple(rooms), corridors=corridors,
        gates=tuple(gates), vestibules=tuple(vests),
        airlock=(9, 0),
        spawns=((9, 2), (10, 2), (9, 3), (10, 3), (9, 4), (10, 4)),
        blurb="两间舱室共用一扇外闸门。门不破，里面就是安全的 —— 适合分工。")


_ATRIUM_W, _ATRIUM_H = 30, 27


def _atrium_spec() -> MapSpec:
    """中庭货舱：12 间舱室围着一个 16×13 的大中庭，气闸在中庭正上方的竖井顶端。

        ┌───┬───┬╥┬───┬───┐    上排 4 间、下排 4 间（门朝中庭那两条横通道开），
        ├───┴───┴╨┴───┴───┤    左右各 2 间（门朝中庭的左右两列开）。
        │  ┌────────────┐  │
        │  │   中 庭     │  │    **中庭是共享杀伤区**：四个方向的门都朝它开，
        │  └────────────┘  │    于是几乎所有炮台都能覆盖同一片地 —— 输出容易堆，
        ├───┬───┬──┬───┬───┤    但静默体也能在很短的时间里够到任何一扇门。
        └───┴───┴──┴───┴───┘

    ⚠️ 上下排的门只能用 `door_at=2`（5×4 房型池里**只有第 3 列**在六种房型下
    恒为地板），而休眠舱那一格又必须与它错开，所以上排的床放在内部 `(1, 0)`
    （贴顶、离门最远），下排的床沿用 `(2, 3)`。这些约束由 `Map._validate` 兜底。
    """
    rooms: list[RoomSpec] = []
    xs = (0, 7, 16, 23)
    for i, x in enumerate(xs):                    # 上排：门朝下（开进 y=6 那条横通道）
        rooms.append(RoomSpec(f"T{i + 1}", x, 0, 7, 6, "bottom", 2, (1, 0)))
    for i, x in enumerate(xs):                    # 下排：门朝上（开进 y=20 那条横通道）
        rooms.append(RoomSpec(f"B{i + 1}", x, 21, 7, 6, "top", 1, (2, 3)))
    for i, y in enumerate((7, 14)):               # 左列：门朝右（开进中庭左列 x=7）
        rooms.append(RoomSpec(f"L{i + 1}", 0, y, 7, 6, "right", 1, (2, 3)))
    for i, y in enumerate((7, 14)):               # 右列：门朝左（开进中庭右列 x=22）
        rooms.append(RoomSpec(f"R{i + 1}", 23, y, 7, 6, "left", 1, (2, 3)))
    corridors = (
        (14, 0, 2, 7),                            # 竖井：气闸 → 上横通道
        (0, 6, _ATRIUM_W, 1),                     # 上横通道
        (0, 20, _ATRIUM_W, 1),                    # 下横通道
        (7, 7, 16, 13),                           # 中庭
    )
    return MapSpec(
        id="atrium", name="中庭货舱", w=_ATRIUM_W, h=_ATRIUM_H,
        rooms=tuple(rooms), corridors=corridors,
        airlock=(14, 0),
        spawns=((14, 2), (15, 2), (14, 3), (15, 3), (14, 4), (15, 4)),
        blurb="12 间舱室围着一个大中庭。火力能互相覆盖，门也挨得近。")


SPECS: dict[str, MapSpec] = {
    "dorm": _dorm_spec(),
    "suite": _suite_spec(),
    "atrium": _atrium_spec(),
}
MAP_IDS = list(SPECS)
DEFAULT_MAP_ID = "dorm"


@lru_cache(maxsize=None)
def get_map(map_id: str) -> Map:
    """按 id 取编译好的地图。**未知 id 退回默认图**（老战绩里可能存着别的）。"""
    spec = SPECS.get(map_id) or SPECS[DEFAULT_MAP_ID]
    return Map(spec)


def map_choices() -> list[dict]:
    """大厅里给玩家看的地图列表（id / 名字 / 一句话 / 房间数）。"""
    return [{"id": mid, "name": s.name, "blurb": s.blurb, "rooms": len(s.rooms)}
            for mid, s in SPECS.items()]
