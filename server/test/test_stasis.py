"""深空休眠（stasis）引擎单测 —— 纯逻辑，无 IO。

v2 把地图从「抽象格子 + 环形通道索引」换成了**整层楼的俯视平面图**，几何全在
`stasis_map.py`（纯函数、无状态），引擎只持游戏状态。所以这里的用例分两层：

- `TestMapGeometry`：直接测几何（12 间舱室、房型遮罩、门、BFS 寻路、`can_step`），
  不用建一局就能跑。
- 其余：建一局再测行为（跑动抢舱室 / 移动 / 经济 / 建造 / 静默体 / 终局）。

**这个引擎最大的可测试性红利**：`advance(gs, dt_ms, rng)` 只认 `dt_ms`、不读墙钟，
所以单测能**毫秒级跑完整局 6 分钟模拟**（`for _ in range(1800): advance(...)`）。
整局行为（进化曲线、会不会被破门、炮台能不能守住）因此全都能断言，
不需要 sleep、不需要起服务。

坐标约定（`stasis._cell_of`）：**格 `(x, y)` 的中心是 `(x + 0.5, y + 0.5)`**，
取格用 `floor`（不是 `round` —— 银行家舍入在边界上左右不一致）。玩家与静默体
都用浮点格子坐标；**建筑键是 `"x,y"` 字符串**（`stasis.cell_key` / `parse_key`）。

Run: pytest test/test_stasis.py -v
"""

import json
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import stasis as st
from app.game import stasis_map as M

# 几何查询全部走**编译好的地图对象**（v9 起 `stasis_map` 不再有模块级常量，
# 因为地图不止一张）。这里的用例绝大多数是「标准货舱区」的行为，所以固定用它；
# 三张图各自的几何由 `TestMapGeometry` 逐张过。
DORM = M.get_map("dorm")
SUITE = M.get_map("suite")
ALL_MAPS = [M.get_map(mid) for mid in M.MAP_IDS]


# ── 测试夹具 ────────────────────────────────────────────────


def _setup(uids=(1, 2), rooms=None, seed=7) -> dict:
    """跳过跑动阶段，直接把舱室派好、进入 playing。

    单测要的是确定状态 —— 不依赖跑动阶段的走位结果（项目有过「测试靠随机性
    变 flaky」的教训）。跑动阶段本身另有专门的用例（`TestRunPhase`）。

    `dir` 显式清零：跑动阶段残留的移动方向会让人继续往前走（`_step_players`
    每 tick 按 dir 积分），站着不动才是测试想要的基线。

    **`lying` 置上**：资源测试的基线是「已经躺好、正在产资源」。要测「站着不产」
    就自己把它清掉（见 `TestBed`）。
    """
    gs = st.create_game(list(uids), seed=seed)
    assign = rooms or DORM.room_ids[: len(uids)]
    for uid, rid in zip(uids, assign):
        st.room_of(gs, rid)["owner"] = uid
        p = gs["players"][uid]
        p["room"] = rid
        bx, by = DORM.room_bed(rid)
        p["pos"] = [bx + 0.5, by + 0.5]        # 直接躺在床上（资源测试的基线）
        p["dir"] = [0.0, 0.0]
        p["lying"] = True
    gs["phase"] = "playing"
    gs["clock_ms"] = 0
    return gs


def _on_foot(gs, *uids) -> None:
    """把这些人放到「还没躺下」的状态。

    `_setup` 的基线是「已经躺好」（资源类用例要的），而**躺着不能移动** ——
    移动/被撞类用例必须先离开躺下态，否则 `_step_players` 会跳过他们、
    `set_move` 也会把方向压成 0。

    **v4 起没有「起身」这条路**（`stasis_stand` 连协议一起删了，躺下不可逆），
    所以这里不再调引擎动作，而是**直接清掉 `lying` / `in_bed`** —— 这种
    「一个还没躺下的人」是纯前置状态，只能在夹具里造。
    """
    for uid in (uids or tuple(gs["players"])):
        p = gs["players"][uid]
        p["lying"] = False
        p["in_bed"] = False


def _run_setup(uids=(1, 2), seed=7) -> dict:
    """停在**跑动阶段**的局（测抢舱室用）。"""
    return st.create_game(list(uids), seed=seed)


def _room(gs, uid) -> dict:
    return st.room_of(gs, gs["players"][uid]["room"])


def _keys(gs, uid) -> list[str]:
    """该玩家可建造的格子键（自己舱室的地板格，减去休眠舱与已占用的）。"""
    return st.buildable_cells(gs, uid)


def _door_keys(gs, uid, n=3) -> list[str]:
    """离门口最近的 n 个可建造格（塔摆在这里才覆盖得住门外）。"""
    rid = gs["players"][uid]["room"]
    ix, iy = DORM.room_door(rid)["in"]
    keys = _keys(gs, uid)
    keys.sort(key=lambda k: (abs(st.parse_key(k)[0] - ix)
                             + abs(st.parse_key(k)[1] - iy), k))
    return keys[:n]


def _set_shapes(gs, mapping=None, default="rect") -> None:
    """把房型改成确定的一组，免得断言依赖每局的随机分配。"""
    mapping = mapping or {}
    gs["map"]["shapes"] = {rid: mapping.get(rid, default) for rid in DORM.room_ids}


def _park_entity(gs) -> None:
    """让静默体一直待在气闸游荡（不挑门、不砸门）。

    测经济/建造时要的是**与战斗无关**的纯净环境 —— 否则它会走过来砸门，
    把「修复器补了多少血」的断言算成净变化（实测：它 2.4 DPS/秒 地啃门，
    而 1 级修复器只回 1.2/秒）。
    """
    e = gs["entity"]
    e["state"] = "idle"
    e["idle_ms"] = 10 ** 9


def _stock(gs, bio=10_000.0, alloy=10_000.0) -> None:
    for p in gs["players"].values():
        p["bio"] = bio
        p["alloy"] = alloy


def _at_door(gs, rid, hp=None) -> dict:
    """把静默体摆到某扇门口砸门（位置固定在门外那一格），并把这一趟的账初始化。

    模块级函数 —— 好几个测试类都要用它，长在某个类里就得复制一份。
    """
    e = gs["entity"]
    e["state"] = "attack"
    e["target_door"] = rid
    e["pos"] = [float(c) for c in st.game_map(gs).room_door(rid)["out"]]
    e["path"] = []
    st._start_trip(gs, e, rid)
    if hp is not None:
        e["hp"] = hp
    return e


def _run_ticks(gs, n, seed=0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out += st.advance(gs, st.TICK_MS, rng)
        if gs["finished"]:
            break
    return out


# ── 地图几何（stasis_map）───────────────────────────────────


class TestMapGeometry:
    """几何是**权威**（v9 起由服务端整份下发给客户端），所以先锁几何。

    这些用例**逐张图**跑（`ALL_MAPS`）—— 加新地图时不必再补一遍，
    `build()` 里的三条硬约束（房型池覆盖内部尺寸、门内侧格与休眠舱格必须是地板、
    门厅必须与它那扇闸门相邻）会在这里被全部走一遍。
    """

    def test_grid_and_rooms(self):
        assert (DORM.w, DORM.h) == (25, 30)
        assert len(DORM.room_ids) == 12
        assert set(DORM.room_ids) == {f"{c}{r}" for c in "ABC" for r in range(1, 5)}
        for mid, rooms in (("suite", 12), ("atrium", 12)):
            assert len(M.get_map(mid).room_ids) == rooms

    def test_every_shape_keeps_the_door_cell_and_the_bed_cell_floor(self):
        """两条硬约束：门的内侧格与休眠舱那一格**在每个房型下都必须是地板**。

        加新房型时必须满足 —— 门那格要是舱壁，舱室就永远进不去（静默体与玩家
        都只能从门那一对格进出）；床那格要是舱壁，躺不下就永远不产资源。
        `build()` 里也有一份同样的断言（写错图会在**建图那一刻**炸），
        这里再跑一遍是为了让**房型池**也被覆盖到（房型是每局随机分配的）。
        """
        for m in ALL_MAPS:
            for rid in m.room_ids:
                r = m.rooms[rid]
                pool = M.SHAPE_POOLS[(r.w - 2, r.h - 2)]
                for sid in pool:
                    shapes = {k: sid for k in m.room_ids}
                    door = m.room_door(rid)
                    assert m.kind(*door["in"], shapes) == "floor", (m.id, sid, rid)
                    assert m.kind(*door["door"], shapes) == "door", (m.id, sid, rid)
                    assert m.kind(*door["out"], shapes) == "corridor", (m.id, sid, rid)
                    # 舱内 → 门 → 通道：两条边都必须相邻，否则「门」跨不过去
                    for a, b in ((door["in"], door["door"]),
                                 (door["door"], door["out"])):
                        assert (abs(a[0] - b[0]) + abs(a[1] - b[1])) == 1, (sid, rid)
                    assert m.kind(*m.room_bed(rid), shapes) == "floor", (m.id, sid, rid)

    def test_room_cells_match_the_mask(self):
        # 注意：5×4 池里 pillar 的注释写的是 16 格，实际是 18（一根柱子只吃掉
        # 两格）—— 以遮罩为准（见交付报告）。
        for sid, expected in (("rect", 20), ("notch", 16), ("lshape", 16),
                              ("groove", 18), ("pillar", 18), ("tshape", 16)):
            shapes = {rid: sid for rid in DORM.room_ids}
            cells = DORM.room_cells("B1", sid)
            assert len(cells) == expected, sid
            assert len(set(cells)) == expected
            for c in cells:
                assert DORM.kind(*c, shapes) == "floor"
                assert DORM.room_at(*c) == "B1"
            # 反方向：该舱室包围盒里所有 floor 格都在 room_cells 里
            floors = {c for c in DORM.walkable(shapes)
                      if DORM.room_at(*c) == "B1"}
            assert floors == set(cells)

    def test_the_wall_ring_is_exactly_one_cell_thick(self):
        """**舱壁占一格**（用户要求「和猛鬼宿舍一样」）。

        第一版把墙做成「房型遮罩与包围盒的差集」，厚度是 0 —— 舱室与通道直接贴
        在一起，视觉上读不出「这是一间隔开的房间」。这条锁住它：包围盒外环全是
        墙（门格除外），内部一格都不是墙。
        """
        for m in ALL_MAPS:
            shapes = {rid: "rect" for rid in m.room_ids}
            for rid in m.room_ids:
                ox, oy, bw, bh = m.room_box(rid)
                door = m.room_door(rid)
                ring = [(x, y) for y in range(oy, oy + bh) for x in range(ox, ox + bw)
                        if x in (ox, ox + bw - 1) or y in (oy, oy + bh - 1)]
                assert len(ring) == 2 * bw + 2 * bh - 4, rid
                for c in ring:
                    # 门格可能落在**另一间房**的包围盒环上（套间里两间房共用
                    # 一道内舱壁），所以判据是「不是墙就是门」，不写死是哪一种。
                    assert m.kind(*c, shapes) in ("door", "wall"), (m.id, rid, c)
                assert m.kind(*door["door"], shapes) == "door"
                # 内部：rect 房型下全是地板（没有一格是墙）
                inner = [(x, y) for y in range(oy + 1, oy + bh - 1)
                         for x in range(ox + 1, ox + bw - 1)]
                for c in inner:
                    assert m.kind(*c, shapes) == "floor", (m.id, rid, c)

    def test_can_step_only_crosses_through_the_door(self):
        """**门是真的门**：舱室的其他边界格是实心舱壁，跨不过去。"""
        shapes = {rid: "rect" for rid in DORM.room_ids}
        door = DORM.room_door("A1")           # in=(5,2) door=(6,2) out=(7,2)
        assert door["in"] == (5, 2) and door["door"] == (6, 2) and door["out"] == (7, 2)
        assert DORM.can_step(shapes, door["out"], door["door"])
        assert DORM.can_step(shapes, door["door"], door["out"])
        assert DORM.can_step(shapes, door["in"], door["door"])
        assert DORM.can_step(shapes, door["door"], door["in"])
        # 门外那格也进不了门——只有门格自己通
        assert not DORM.can_step(shapes, door["out"], door["in"])
        assert not DORM.can_step(shapes, door["in"], door["out"])
        # 同一间舱室的其他边界：通道格 (7,5) ↔ 舱壁 (6,5) 不许过
        assert DORM.kind(7, 5, shapes) == "corridor"
        assert DORM.kind(6, 5, shapes) == "wall"
        assert not DORM.can_step(shapes, (7, 5), (6, 5))
        # 通道内、舱室内自由走
        assert DORM.can_step(shapes, (7, 5), (7, 6))
        assert DORM.can_step(shapes, (5, 4), (5, 3))
        # 舱壁走不了
        notch = {rid: "notch" for rid in DORM.room_ids}
        assert DORM.kind(2, 4, notch) == "wall"      # A1 内部左下缺角
        assert DORM.kind(3, 4, notch) == "floor"
        assert not DORM.can_step(notch, (3, 4), (2, 4))
        # 越界
        assert DORM.kind(-1, 0, shapes) == "space"
        assert not DORM.can_step(shapes, (0, 0), (-1, 0))

    def test_corridor_path_reaches_every_door(self):
        """静默体只能走通道网格 —— 每张图的每一扇门都必须可达，
        否则它会永远够不着谁（而「够不着」的表现是整局 0 次砸门）。"""
        for m in ALL_MAPS:
            for rid in m.room_ids:
                goal = m.room_door(rid)["out"]
                path = m.ghost_path(m.airlock, goal, m.gates)
                assert path, f"{m.id}: {rid} 的门在通道网格上不可达"
                assert path[-1] == goal
                assert len(path) == len(set(path))
            assert m.ghost_path(m.airlock, m.airlock) == []

    def test_walkable_cells_is_corridors_plus_floors(self):
        shapes = {rid: "notch" for rid in DORM.room_ids}
        walk = DORM.walkable(shapes)
        assert set(DORM.corridor_cells) <= walk
        assert len(walk) == len(DORM.corridor_cells) + 12 * 16
        for c in walk:
            assert DORM.kind(*c, shapes) in ("corridor", "floor")

    def test_snap_to_pulls_room_cells_onto_the_grid(self):
        """BFS 的起点必须落在通道网格上（静默体站在舱室里时用它兜底）。"""
        cells = DORM.corridor_cells
        assert M.snap_to(cells, (7, 3), DORM.airlock) == (7, 3)
        snapped = M.snap_to(cells, DORM.room_bed("A1"), DORM.airlock)   # 舱内
        assert snapped in cells

    def test_assign_shapes_is_reshuffled_every_game_and_keeps_rect(self):
        """房型每局重掷（「选哪间」才是新决策），但保证至少两间大房。"""
        seen = set()
        for seed in range(12):
            shapes = DORM.assign_shapes(random.Random(seed))
            assert set(shapes) == set(DORM.room_ids)
            assert all(v in M.SHAPE_POOLS[(5, 4)] for v in shapes.values())
            assert list(shapes.values()).count("rect") >= 2
            seen.add(tuple(shapes[r] for r in DORM.room_ids))
        assert len(seen) > 1, "房型应当每局随机分配，否则选舱室就没有决策了"

    def test_every_map_assigns_a_valid_shape_to_every_room(self):
        """每张图 × 每个房型池：分到的房型必须真的属于**那间房**的池子。

        `suite` 的舱室内部是 3×6（池子与标准舱不同），把 5×4 的遮罩分给它是
        `IndexError` —— 这条锁住「按内部尺寸分池」这件事。
        """
        for m in ALL_MAPS:
            for seed in range(4):
                shapes = m.assign_shapes(random.Random(seed))
                assert set(shapes) == set(m.room_ids)
                for rid, sid in shapes.items():
                    r = m.rooms[rid]
                    assert sid in M.SHAPE_POOLS[(r.w - 2, r.h - 2)], (m.id, rid, sid)


class TestGateMechanics:
    """**共用闸门**（v9，`suite` 图）：一扇门挡住两间舱室，破了才放静默体进门厅。"""

    def test_suite_has_six_gates_each_guarding_two_rooms(self):
        assert len(SUITE.gates) == 6
        for gid, g in SUITE.gates.items():
            assert len(g.rooms) == 2, gid
            for rid in g.rooms:
                assert SUITE.room_gate(rid) == gid
        assert len(SUITE.room_ids) == 12

    def test_vestibule_is_not_reachable_until_the_gate_breaks(self):
        """**这条是整个机制的核心**：闸门没破时静默体进不了门厅。

        表现是「通往内门的路是空的」——`ghost_path` 返回空表，所以
        `choose_target` 会把那两间房筛掉、只留闸门自己当目标。
        """
        gid = "G1"
        inner = SUITE.gates[gid].rooms[0]
        goal = SUITE.room_door(inner)["out"]          # 门厅里的那一格
        assert goal in SUITE.gated[gid]
        assert SUITE.ghost_path(SUITE.airlock, goal, ()) == []
        # 破了之后路通了
        path = SUITE.ghost_path(SUITE.airlock, goal, (gid,))
        assert path and path[-1] == goal
        # 而闸门自己（门外那格）**始终**可达 —— 否则它永远不会来砸闸门
        out = SUITE.gate_out(gid)
        assert SUITE.ghost_path(SUITE.airlock, out, ())[-1] == out

    def test_gated_cells_are_corridor_but_marked(self):
        """门厅是**通道格**（玩家能走、炮台能覆盖），但被登记在闸门名下。"""
        for gid, cells in SUITE.gated.items():
            for c in cells:
                assert SUITE.base_kind(*c) == "corridor"
                assert SUITE.gated_owner(c) == gid
        assert SUITE.gated_owner(SUITE.airlock) is None

    def test_players_walk_through_a_closed_gate(self):
        """**闸门只拦静默体**：门是玩家自己开的（原型也是这个语义）。

        玩家走不过去的话，跑动阶段根本进不了套间 —— 整张图直接废掉。
        """
        shapes = SUITE.assign_shapes(random.Random(3))
        gid = "G1"
        assert SUITE.can_step(shapes, SUITE.gate_out(gid), SUITE.gate_cell(gid))
        assert SUITE.can_step(shapes, SUITE.gate_cell(gid), SUITE.gate_in(gid))

    def test_engine_starts_with_closed_gates_and_breaks_them(self):
        gs = st.create_game([1, 2, 3, 4], seed=5, map_id="suite")
        assert set(gs["map"]["gates"]) == set(SUITE.gates)
        assert st.open_gates(gs) == []
        for g in gs["map"]["gates"].values():
            assert g["door_hp"] == st.DOOR_HP[0] and not g["broken"]
        gs["map"]["gates"]["G1"]["door_hp"] = 0.0
        st._breach(gs, st.gate_id("G1"), [])
        assert gs["map"]["gates"]["G1"]["broken"] is True
        assert st.open_gates(gs) == ["G1"]
        # **砸破闸门不倒人** —— 它没有主人（与房间门是两种后果）
        assert all(p["alive"] for p in gs["players"].values())

    def test_only_an_occupant_can_upgrade_a_gate(self):
        # 夹具 `_setup` 建的是 dorm 图（它的房间号是写死的 A1/B1…），
        # 所以这里直接建一张真的 suite 局再手工占房。
        gs = st.create_game([1, 2, 3], seed=5, map_id="suite")
        gs["map"]["rooms"]["A1"]["owner"] = 1
        gs["players"][1]["room"] = "A1"
        gs["players"][1]["lying"] = True
        gs["players"][1]["bio"] = 10_000.0
        gs["map"]["rooms"]["B1"]["owner"] = 2
        gs["players"][2]["room"] = "B1"
        gs["players"][2]["lying"] = True
        gs["players"][2]["bio"] = 10_000.0
        gid = st.game_map(gs).room_gate("A1")
        # 住户能升
        out = st.upgrade(gs, 1, st.gate_id(gid))
        assert out["level"] == 2
        assert gs["map"]["gates"][gid]["door_hp"] == st.DOOR_HP[1]
        # 不住在它后面的人不能（B1 在另一个套间）
        assert st.game_map(gs).room_gate("B1") != gid
        with pytest.raises(ValueError):
            st.upgrade(gs, 2, st.gate_id(gid))
        # 砸破之后不能升
        gs["map"]["gates"][gid]["broken"] = True
        with pytest.raises(ValueError):
            st.upgrade(gs, 1, st.gate_id(gid))

    def test_gate_patch_costs_the_remote_price(self):
        gs = st.create_game([1, 2], seed=5, map_id="suite")
        gs["map"]["rooms"]["A1"]["owner"] = 1
        gs["players"][1]["room"] = "A1"
        gs["players"][1]["lying"] = True
        gs["players"][1]["bio"] = 1000.0
        gid = st.game_map(gs).room_gate("A1")
        gate = gs["map"]["gates"][gid]
        gate["door_hp"] = 1.0
        before = gs["players"][1]["bio"]
        st.patch(gs, 1, None, gate=gid)
        assert before - gs["players"][1]["bio"] == st.PATCH_COST_BIO * st.PATCH_COST_MULT
        assert gate["door_hp"] > 1.0

    def test_gate_spending_is_credited_to_whoever_paid(self):
        """**出资账本**（v13）：谁为这扇公共外闸掏过多少钱，一笔笔都记上。

        它对付的是「公共品搭便车」（`增强调研.md` §13.3）里被采纳的那一档 ——
        **可见性**：不做「按出资分收益」，只让「谁在扛这扇门」看得见。
        """
        gs = st.create_game([1, 2], seed=5, map_id="suite")
        gid = st.game_map(gs).room_gate("A1")
        # 两个人**住在这扇闸门后面的两间房**（只有住户能升/修它）
        for uid, rid in zip((1, 2), SUITE.gates[gid].rooms):
            gs["map"]["rooms"][rid]["owner"] = uid
            gs["players"][uid]["room"] = rid
            gs["players"][uid]["bio"] = 10_000.0
            gs["players"][uid]["alloy"] = 10_000.0
            gs["players"][uid]["lying"] = True      # 躺着才能建造/升级/修补（v4）
        assert gs["map"]["gates"][gid]["contrib"] == {}
        # 1 号升两次
        st.upgrade(gs, 1, st.gate_id(gid))
        st.upgrade(gs, 1, st.gate_id(gid))
        # 2 号修一次（闸门是公共品，按「帮别人」的远程价 1.5 倍）
        gs["map"]["gates"][gid]["door_hp"] = 1.0
        st.patch(gs, 2, None, gate=gid)
        contrib = gs["map"]["gates"][gid]["contrib"]
        want_bio = st.upgrade_cost(st.DOOR, 1) + st.upgrade_cost(st.DOOR, 2)
        assert contrib[1]["bio"] == pytest.approx(want_bio)
        assert contrib[1]["alloy"] == pytest.approx(
            st.upgrade_alloy(st.DOOR, 1) + st.upgrade_alloy(st.DOOR, 2))
        assert contrib[1]["acts"] == 2
        assert contrib[2]["bio"] == pytest.approx(
            st.PATCH_COST_BIO * st.PATCH_COST_MULT)
        assert contrib[2]["acts"] == 1
        # 账本随 `map` 下发（键是 uid 的字符串形式 —— JSON 的键只能是字符串）
        payload = st._map_payload(gs)
        assert payload["gates"][gid]["contrib"]["1"]["acts"] == 2
        assert payload["gates"][gid]["contrib"]["2"]["bio"] > 0

    def test_emp_on_a_gate_hits_every_room_behind_it(self):
        gs = st.create_game([1, 2], seed=5, map_id="suite")
        gid = "G1"
        assert st._emp_rooms(gs, st.gate_id(gid)) == list(SUITE.gates[gid].rooms)
        assert st._emp_rooms(gs, "A1") == ["A1"]


class TestMapPayload:
    """下发给客户端的几何（`Map.payload`）—— 客户端一行几何规则都不写，全靠它。"""

    def test_payload_covers_every_cell_and_room(self):
        for m in ALL_MAPS:
            shapes = m.assign_shapes(random.Random(1))
            p = m.payload(shapes)
            assert p["id"] == m.id and (p["w"], p["h"]) == (m.w, m.h)
            assert len(p["rows"]) == m.h and all(len(r) == m.w for r in p["rows"])
            assert set(p["rooms"]) == set(m.room_ids)
            assert set(p["gates"]) == set(m.gates)
            # 字符画与 `kind()` 必须逐格一致 —— 客户端就是照着它画的
            for y in range(m.h):
                for x in range(m.w):
                    assert p["rows"][y][x] == M.KIND_CHAR[m.kind(x, y, shapes)], (m.id, x, y)
            for rid in m.room_ids:
                r = p["rooms"][rid]
                assert [tuple(c) for c in r["cells"]] == m.room_cells(rid, shapes[rid])
                assert tuple(r["bed"]) == m.room_bed(rid)
                assert tuple(r["door"]["in"]) == m.room_door(rid)["in"]
                assert tuple(r["door"]["out"]) == m.room_door(rid)["out"]
                assert r["gate"] == m.room_gate(rid)
            for gid in m.gates:
                g = p["gates"][gid]
                assert tuple(g["door"]) == m.gate_cell(gid)
                assert tuple(g["in"]) == m.gate_in(gid)
                assert tuple(g["out"]) == m.gate_out(gid)
                assert g["side"] == m.gates[gid].side
                assert g["rooms"] == list(m.gates[gid].rooms)

    def test_a_gate_payload_is_the_same_shape_as_a_room_door(self):
        """**房间门与共用闸门在载荷里必须逐字同构**（`in`/`door`/`out`/`side`）。

        回归的是用户报的「**双人套间打开全是黑的**」：v9 第一版给闸门发的是
        `cell`，而客户端的 `buildMap` 只有一个 `toDoor()`，它按 `door` 取门格
        —— 读到 `undefined[0]` 直接抛异常。服务端这边一切正常（引擎用例全绿、
        载荷里的格子一个不少），`socket.ts` 的 `catch` 又是**静默的**，
        于是唯一的症状就是浏览器里那张黑图。

        **只要两边对同一个东西的叫法不一致，这条先红** —— 它比「载荷里有
        `cell`」这种断言值钱，因为后者只锁住了一个名字。
        """
        for m in ALL_MAPS:
            p = m.payload(m.assign_shapes(random.Random(3)))
            keys = {"in", "door", "out", "side"}
            for rid, r in p["rooms"].items():
                assert keys <= set(r["door"]), (m.id, rid)
            for gid, g in p["gates"].items():
                assert keys <= set(g), (m.id, gid)
                assert "cell" not in g, \
                    f'{m.id} 的闸门 {gid} 又用回了 cell —— 客户端的 toDoor() 读的是 door'
            if m.gates:      # 两边真的**逐字**一样：闸门那一份只多一个 `rooms`
                assert set(p["gates"]["G1"]) - keys == {"rooms"}, \
                    f'{m.id} 的闸门几何长出了额外的键：{set(p["gates"]["G1"]) - keys - {"rooms"}}'
                assert set(p["rooms"][m.gates["G1"].rooms[0]]["door"]) == keys, \
                    f'{m.id} 的房间门几何多了/少了键'

    def test_payload_rows_round_trip_through_the_char_map(self):
        """字符表是双向的（服务端编码、客户端解码），写错一位就是「墙画成地板」。"""
        for ch, kind in M.CHAR_KIND.items():
            assert M.KIND_CHAR[kind] == ch
        assert set(M.KIND_CHAR) == {"space", "corridor", "wall", "door", "floor"}


# ── 建局 ────────────────────────────────────────────────────


class TestCreateGame:
    def test_player_count_is_validated(self):
        """2-6 人（v2 从 4 人扩到 6 人，`PLAYER_COUNT_HP_MULT` 也补齐了 5/6 档）。"""
        for n in (1, 7):
            with pytest.raises(ValueError):
                st.create_game(list(range(1, n + 1)))
        for n in (2, 6):
            assert st.create_game(list(range(1, n + 1)), seed=1)["num_players"] == n

    def test_initial_state(self):
        gs = st.create_game([1, 2, 3], seed=1)
        assert gs["phase"] == "run"            # 跑动抢舱室，不再是 "pick"
        assert gs["run_ms"] == st.RUN_MS
        assert gs["clock_ms"] == 0
        assert gs["num_players"] == 3
        for uid in (1, 2, 3):
            p = gs["players"][uid]
            assert p["room"] is None
            assert p["bio"] == st.START_BIO
            assert p["bed_level"] == 1
            assert p["alive"] and not p["surrendered"]
            # v4：撞倒机制（`down_until_ms` / `immune_until_ms`）连同字段一起删了
            # —— 走廊里被静默体碰到就是直接出局（见 TestEntityMovement）。
            assert "down_until_ms" not in p and "immune_until_ms" not in p
            assert p["lying"] is False
            assert p["in_bed"] is False
            assert p["dir"] == [0.0, 0.0]
            # 出生在气闸下方那段通道的**格心**
            assert st._cell_of(p["pos"]) in DORM.spawns
            assert p["pos"][0] % 1 == 0.5 and p["pos"][1] % 1 == 0.5
        e = gs["entity"]
        assert e["state"] == "idle"
        assert st._cell_of(e["pos"]) == DORM.airlock
        assert e["path"] == [] and e["target_door"] is None

    def test_map_carries_shapes_rooms_and_airlock(self):
        gs = st.create_game([1, 2], seed=3)
        assert set(gs["map"]["shapes"]) == set(DORM.room_ids)
        assert set(gs["map"]["rooms"]) == set(DORM.room_ids)
        assert gs["map"]["airlock"] == list(DORM.airlock)
        for rid, room in gs["map"]["rooms"].items():
            assert room["shape"] == gs["map"]["shapes"][rid]
            assert room["owner"] is None
            assert room["door_level"] == 1
            assert room["door_hp"] == st.DOOR_HP[0]
            assert room["emp_until_ms"] == 0
            assert room["buildings"] == {}

    def test_entity_hp_scales_with_player_count(self):
        """人多了静默体更肉（伤害不缩放）—— 5/6 人档不能缺，否则 KeyError。"""
        for n, mult in st.PLAYER_COUNT_HP_MULT.items():
            gs = st.create_game(list(range(1, n + 1)), seed=1)
            assert gs["entity"]["max_hp"] == st.ENTITY_HP[0] * mult
        assert set(st.PLAYER_COUNT_HP_MULT) == {2, 3, 4, 5, 6}

    def test_player_colors_present(self):
        """ws.py 多处通用循环把 player_colors 当「玩家 uid 集合」用，
        留空会让所有广播静默地一个都不发。"""
        gs = st.create_game([7, 8], seed=1)
        assert set(gs["player_colors"]) == {7, 8}


# ── 跑动阶段（抢舱室）───────────────────────────────────────


class TestRunPhase:
    def test_walking_into_a_free_room_claims_it(self):
        """跑动阶段的核心动作：**走进空舱室就占下它**（不再是投票 + 随机裁决）。"""
        gs = _run_setup(uids=(1, 2), seed=1)
        p1 = gs["players"][1]
        p1["pos"] = [8.5, 2.5]                 # B1 门外那格通道
        assert DORM.room_door("B1")["out"] == (8, 2)
        st.set_move(gs, 1, 1.0, 0.0)           # 朝门里走
        events = _run_ticks(gs, 1)

        claimed = [e for e in events if e["type"] == "room_claimed"]
        assert claimed == [{"type": "room_claimed", "uid": 1, "room": "B1"}]
        assert st.room_of(gs, "B1")["owner"] == 1
        assert p1["room"] == "B1"
        # 再走一步不该重复占房
        assert not [e for e in _run_ticks(gs, 1) if e["type"] == "room_claimed"]

    def test_an_owned_room_cannot_be_stolen(self):
        """**主人在里面**时，别人进不来（占房是「舱室跟着人走」，不是「先到先得」）。"""
        gs = _run_setup(uids=(1, 2), seed=1)
        st.room_of(gs, "B1")["owner"] = 2
        p2 = gs["players"][2]
        p2["room"] = "B1"
        bx, by = DORM.room_bed("B1")           # **人得真的站在里面**：主人一走开
        p2["pos"] = [bx + 0.5, by + 0.5]       # 就会让出舱室（见下一条用例）
        p1 = gs["players"][1]
        p1["pos"] = [8.5, 2.5]
        st.set_move(gs, 1, 1.0, 0.0)
        events = _run_ticks(gs, 3)

        assert not [e for e in events if e["type"] == "room_claimed"]
        assert st.room_of(gs, "B1")["owner"] == 2
        assert p1["room"] is None

    def test_walking_out_of_a_room_releases_it(self):
        """**走出去就放掉它** —— 舱室是「跟着人走」的，主人一走开就该空出来。"""
        gs = _run_setup(uids=(1, 2), seed=1)
        p1 = gs["players"][1]
        p1["pos"] = [8.5, 2.5]
        st.set_move(gs, 1, 1.0, 0.0)           # 走进 B1
        _run_ticks(gs, 1)
        assert st.room_of(gs, "B1")["owner"] == 1

        st.set_move(gs, 1, -1.0, 0.0)          # 再走回通道
        events = _run_ticks(gs, 3)
        released = [e for e in events if e["type"] == "room_released"]
        assert released == [{"type": "room_released", "uid": 1, "room": "B1"}]
        assert st.room_of(gs, "B1")["owner"] is None
        assert p1["room"] is None

    def test_can_switch_rooms_before_lying_down(self):
        """用户实测报的那条：**进 A 房 → 出来 → 跑进 B 房，就该能在 B 躺下**。

        原来只在 `p["room"]` 为空时才认领，于是人进了 B 房手里还攥着 A ——
        站在 B 的床边 `_can_lie` 判的却是 A 的九宫格，**躺下按钮永远不出现**。
        """
        gs = _run_setup(uids=(1, 2), seed=1)
        p1 = gs["players"][1]
        p1["pos"] = [8.5, 2.5]                 # B1 门外
        st.set_move(gs, 1, 1.0, 0.0)
        _run_ticks(gs, 1)
        assert p1["room"] == "B1"

        p1["pos"] = [17.5, 2.5]                # C1 门外那格通道（直接挪过去）
        assert DORM.room_door("C1")["out"] == (17, 2)
        st.set_move(gs, 1, 1.0, 0.0)
        _run_ticks(gs, 3)
        assert p1["room"] == "C1", "换了一间空舱室就该换过去"
        assert st.room_of(gs, "B1")["owner"] is None
        assert st.room_of(gs, "C1")["owner"] == 1

        bx, by = DORM.room_bed("C1")           # 走到新舱室的床边
        p1["pos"] = [bx + 0.5, by + 0.5]
        assert st.can_lie(gs, 1) is True, "换房之后必须能躺下（用户报的就是这条）"
        assert st.lie(gs, 1)["ok"] is True

    def test_a_lying_player_keeps_their_room(self):
        """**躺下即锁死**：躺下的人即使被挪出舱室，也不该被换房或释放。

        躺下之后位置被吸在床心、本来就动不了，这条是显式表达规则（v11）——
        舱室从躺下那一刻起才真的是「你的」。
        """
        gs = _run_setup(uids=(1, 2), seed=1)
        p1 = gs["players"][1]
        p1["pos"] = [8.5, 2.5]
        st.set_move(gs, 1, 1.0, 0.0)
        _run_ticks(gs, 1)
        bx, by = DORM.room_bed("B1")
        p1["pos"] = [bx + 0.5, by + 0.5]
        st.lie(gs, 1)
        assert p1["lying"] is True

        p1["pos"] = [8.5, 2.5]                 # 硬把人挪回通道（模拟任何异常位移）
        events = _run_ticks(gs, 3)
        assert not [e for e in events if e["type"] == "room_released"]
        assert p1["room"] == "B1"
        assert st.room_of(gs, "B1")["owner"] == 1

    def test_two_players_can_swap_rooms(self):
        """两趟走（先全放、再全占）的理由：**两个人对调舱室时一趟走会互相挡住**。"""
        gs = _run_setup(uids=(1, 2), seed=1)
        p1, p2 = gs["players"][1], gs["players"][2]
        st.room_of(gs, "A1")["owner"] = 1
        p1["room"] = "A1"
        p1["pos"] = [x + 0.5 for x in DORM.room_bed("A1")]
        st.room_of(gs, "B1")["owner"] = 2
        p2["room"] = "B1"
        p2["pos"] = [x + 0.5 for x in DORM.room_bed("B1")]

        # 直接把两人瞬移到对方舱室的床上（走位与本题无关）
        p1["pos"], p2["pos"] = list(p2["pos"]), list(p1["pos"])
        _run_ticks(gs, 1)
        assert p1["room"] == "B1" and p2["room"] == "A1"
        assert st.room_of(gs, "B1")["owner"] == 1
        assert st.room_of(gs, "A1")["owner"] == 2

    def test_countdown_assigns_the_homeless_and_starts_playing(self):
        """倒计时归零：**没占到房的自动补一间最大的空房并送进去**，
        否则开局就有人在通道里裸奔，被静默体一路追着打。"""
        gs = _run_setup(uids=(1, 2, 3), seed=5)
        gs["run_ms"] = 1                       # 不用真等 30 秒
        events = _run_ticks(gs, 1)

        assert gs["phase"] == "playing"
        assert gs["clock_ms"] == 0             # 跑动阶段不吃对局时间
        assert any(e["type"] == "airlock_open" for e in events)
        auto = [e for e in events if e["type"] == "room_claimed"]
        assert len(auto) == 3 and all(e["auto"] is True for e in auto)

        rooms = {e["room"] for e in auto}
        assert len(rooms) == 3, "两个人不能挤进同一间"
        for uid in (1, 2, 3):
            p = gs["players"][uid]
            assert p["room"] in rooms
            assert st.room_of(gs, p["room"])["owner"] == uid
            bx, by = DORM.room_bed(p["room"])
            assert p["pos"] == [bx + 0.5, by + 0.5], "应当被直接送进床上"

    def test_start_playing_keeps_a_lying_player_lying(self):
        """v4：**跑动阶段就能躺下**（规则 A1），而且 `_start_playing` 不再无条件
        清掉 `lying`（规则 A6）—— 已经躺好的人要保持躺着。

        原来 `_start_playing` 无条件 `p["lying"] = False`，在新规则下等于
        「倒计时一到就把所有人的床拆了」，玩家会发现自己又被拎起来站着。
        顺带验「跑动阶段躺着也产资源」（规则 A6）。
        """
        gs = _run_setup(uids=(1, 2), seed=1)
        st.room_of(gs, "A1")["owner"] = 1
        p1 = gs["players"][1]
        p1["room"] = "A1"
        bx, by = DORM.room_bed("A1")
        p1["pos"] = [bx + 0.5, by + 0.5]
        assert st.lie(gs, 1)["ok"] is True         # 跑动阶段就躺下
        assert p1["lying"] is True and p1["in_bed"] is True

        p1["bio"] = 0.0
        _run_ticks(gs, 5)                          # 躺 1 秒
        assert p1["bio"] == pytest.approx(st.BED_RATE[0], abs=1e-6), \
            "跑动阶段躺着也应当产资源"

        gs["run_ms"] = 1
        _run_ticks(gs, 1)                          # 倒计时归零 → playing
        assert gs["phase"] == "playing"
        assert p1["lying"] is True and p1["in_bed"] is True, \
            "进对局时不该把已经躺好的人弄起来"

    def test_auto_assignment_prefers_the_biggest_rooms(self):
        gs = _run_setup(uids=(1, 2), seed=5)
        # A1 20 格 / B1 18 格 / 其余 16 格
        _set_shapes(gs, {"A1": "rect", "B1": "groove"}, default="notch")
        gs["run_ms"] = 1
        _run_ticks(gs, 1)
        assert gs["players"][1]["room"] == "A1"
        assert gs["players"][2]["room"] == "B1"

    def test_clock_does_not_run_during_run(self):
        """跑动倒计时不能吃掉对局时间 —— 否则玩家实际只有 5 分 30 秒。"""
        gs = _run_setup(uids=(1, 2), seed=3)
        for _ in range(st.RUN_MS // st.TICK_MS):
            st.advance(gs, st.TICK_MS, random.Random(3))
        assert gs["phase"] == "playing"
        assert gs["clock_ms"] == 0

    def test_no_claiming_after_the_run_phase(self):
        """进 playing 后不再自动占房 —— 否则玩家在通道里乱走也能白捡一间。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        p1 = gs["players"][1]
        bx, by = DORM.room_bed("C1")
        p1["pos"] = [bx + 0.5, by + 0.5]
        _run_ticks(gs, 3)
        assert st.room_of(gs, "C1")["owner"] is None
        assert p1["room"] == "A1"


# ── 移动 ────────────────────────────────────────────────────


class TestMovement:
    def test_set_move_then_advance_moves_the_player(self):
        """服务端是位置的权威：客户端只发方向，位置由 tick 积分。"""
        gs = _setup(uids=(1, 2))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [7.5, 12.5]                # 纵向通道（x=7 那一列）
        assert st.set_move(gs, 1, 0.0, 1.0)["ok"] is True
        _run_ticks(gs, 5)                      # 1 秒
        assert p1["pos"][1] == pytest.approx(12.5 + st.PLAYER_SPEED, abs=1e-6)
        assert p1["pos"][0] == pytest.approx(7.5, abs=1e-6)

    def test_move_input_is_normalized(self):
        """超长输入夹成单位向量 —— 否则一 tick 就能瞬移（客户端可乱发）。"""
        gs = _setup(uids=(1, 2))
        _on_foot(gs)
        st.set_move(gs, 1, 3.0, 4.0)
        assert gs["players"][1]["dir"] == pytest.approx([0.6, 0.8])
        st.set_move(gs, 1, 0.5, 0.0)           # 短输入原样保留（手柄的模拟量）
        assert gs["players"][1]["dir"] == pytest.approx([0.5, 0.0])

    def test_wall_slides_instead_of_sticking(self):
        """分轴积分：撞墙时沿墙滑，手感不粘。

        站位刻意选在**离气闸很远**的通道格：静默体出生在气闸，站在那儿会被它
        抓到直接出局（v4），就不是在测滑墙了。
        """
        gs = _setup(uids=(1, 2))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [3.5, 14.5]                # A2 下方那条横向通道（y=14）
        st.set_move(gs, 1, -1.0, -1.0)         # 左上：x 是通道、y 是舱壁
        _run_ticks(gs, 1)
        assert p1["pos"][1] == pytest.approx(14.5, abs=1e-6), "y 方向被舱壁挡住"
        assert p1["pos"][0] < 3.5, "x 方向应当照走（沿墙滑）"

    def test_cannot_walk_through_a_wall_into_a_room(self):
        """舱室只能从门那一对格进出（`can_step` 的硬约束在移动路径上也成立）。"""
        gs = _setup(uids=(1, 2))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [3.5, 14.5]                # A2 的正下方（门在右边界，不在这里）
        assert DORM.room_door("A2")["out"] != (3, 14)
        st.set_move(gs, 1, 0.0, -1.0)          # 一头顶进舱壁
        _run_ticks(gs, 20)
        assert p1["pos"][1] == pytest.approx(14.5, abs=1e-6)
        assert DORM.room_at(*st._cell_of(p1["pos"])) is None

    def test_player_caught_in_the_corridor_is_eliminated(self):
        """v4：走廊里被静默体碰到＝**直接出局**，不是「撞倒 1.5 秒」。

        原来那条「撞倒期间不能动」的行为整个删掉了（`down_until_ms` /
        `immune_until_ms` / `_knock_down` 都不存在），所以这条改成断言新的
        正确行为：碰到 → `entity_catch` → 本人出局、此后不能再动。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [7.5, 2.5]                     # 通道里
        e = gs["entity"]
        e["pos"] = [7.0, 2.0]                      # 距离 0.71 ≤ ENTITY_CATCH_DIST
        e["state"] = "chase"
        e["target_uid"] = 1
        events = _run_ticks(gs, 1, seed=1)

        assert any(x["type"] == "entity_catch" and x["uid"] == 1 for x in events)
        assert p1["alive"] is False, "碰到静默体就该出局"
        # 出局之后不能再动（死人不吃移动输入）
        assert st.set_move(gs, 1, 0.0, 1.0) == {"ok": False, "reason": "dead"}
        frozen = list(p1["pos"])
        _run_ticks(gs, 5, seed=1)
        assert p1["pos"] == frozen

    def test_dead_player_gets_no_move(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 1)
        p1 = gs["players"][1]
        p1["pos"] = [5.5, 8.5]
        assert st.set_move(gs, 1, 0.0, 1.0) == {"ok": False, "reason": "dead"}
        _run_ticks(gs, 5)
        assert p1["pos"] == [5.5, 8.5]

    def test_outsider_cannot_move(self):
        gs = _setup(uids=(1, 2))
        with pytest.raises(ValueError):
            st.set_move(gs, 999, 1.0, 0.0)

    def test_cell_of_uses_floor_not_round(self):
        """**格 `(x,y)` 的中心是 `(x+0.5, y+0.5)`**，取格用 `floor`。

        这条踩过坑：用 `floor(x + 0.5)`（或 `round`）会把站在格心的 `(9.5, 9.5)`
        判成第 10 格，`room_at` 于是返回 None —— 静默体跑去追一个躺在舱室里的
        人，追不到又永不放弃（整局 0 次砸门）。
        """
        assert st._cell_of([9.5, 9.5]) == (9, 9)
        assert DORM.room_at(*st._cell_of([9.5, 9.5])) == "B2"
        assert st._cell_of([4.5, 4.5]) == (4, 4)     # round(4.5) 是 4、round(5.5) 是 6
        assert st._cell_of([5.5, 5.5]) == (5, 5)
        for rid in DORM.room_ids:
            bx, by = DORM.room_bed(rid)
            assert DORM.room_at(*st._cell_of([bx + 0.5, by + 0.5])) == rid


# ── 资源 ────────────────────────────────────────────────────


class TestEconomy:
    def test_bed_produces_bio_while_in_bed(self):
        gs = _setup()
        _stock(gs, bio=0.0, alloy=0.0)
        _run_ticks(gs, 10)                       # 2 秒
        assert gs["players"][1]["in_bed"] is True
        assert gs["players"][1]["bio"] == pytest.approx(2.0, abs=1e-6)

    def test_not_lying_stops_production(self):
        """**没躺下就没有收入**。

        v4 删掉了「起身」这个动作（躺下不可逆），所以这条从「起身 = 断收入」
        改成「还没躺下 = 零收入」：站着的期间一滴不产，`lie` 之后才恢复。
        """
        gs = _setup()
        _stock(gs, bio=0.0)
        p1 = gs["players"][1]
        p1["lying"] = False                        # 站着（v4 没有 stand，直接置状态）
        p1["in_bed"] = False
        _run_ticks(gs, 10)
        assert p1["in_bed"] is False
        assert p1["bio"] == pytest.approx(0.0, abs=1e-6)
        # 躺下（不可逆）→ 恢复产出
        st.lie(gs, 1)
        _run_ticks(gs, 5)
        assert p1["in_bed"] is True
        assert p1["bio"] == pytest.approx(st.BED_RATE[0], abs=1e-6)

    def test_bed_rate_doubles_per_level(self):
        gs = _setup()
        _stock(gs, bio=0.0)
        gs["players"][1]["bed_level"] = 3        # 4/秒
        _run_ticks(gs, 5)                        # 1 秒
        assert gs["players"][1]["bio"] == pytest.approx(4.0, abs=1e-6)

    def test_refinery_and_mine_produce(self):
        gs = _setup()
        _stock(gs)                               # 先给足建造费
        ref, cop = _keys(gs, 1)[0], _keys(gs, 1)[1]
        st.build(gs, 1, st.REFINERY, ref)
        st.build(gs, 1, st.COPPER, cop)
        _stock(gs, bio=0.0, alloy=0.0)           # 扣掉建造费后再归零
        _run_ticks(gs, 5)                        # 1 秒
        assert gs["players"][1]["alloy"] == pytest.approx(st.REFINERY_RATE[0],
                                                          abs=1e-6)
        assert gs["players"][1]["bio"] == pytest.approx(
            st.MINE_RATE[st.COPPER][0] + st.BED_RATE[0], abs=1e-6)

    def test_each_mine_produces_its_own_rate(self):
        """v10：**四座矿是四个独立的建筑**，产率按 kind（+ 等级）取。

        现值：铜 8 / 银 32 / 金 64 / 钻石 256，钻石矿 2 级（氪金矿）512
        —— 用户 v10 拍板的一组数，与原作口径的对照见 `增强调研.md` §13.1。
        这里逐个建、逐个断言 —— 表格一旦串行就会红。
        """
        for kind, rate in ((st.COPPER, 8.0), (st.SILVER, 32.0),
                           (st.GOLD, 64.0), (st.DIAMOND, 256.0)):
            assert st.MINE_RATE[kind][0] == rate
            gs = _setup()
            _stock(gs)
            st.build(gs, 1, kind, _keys(gs, 1)[0])
            _stock(gs, bio=0.0, alloy=0.0)
            _run_ticks(gs, 5)                    # 1 秒
            assert gs["players"][1]["bio"] == pytest.approx(
                rate + st.BED_RATE[0], abs=1e-6), kind

    def test_diamond_mine_upgrades_into_a_krypton_mine(self):
        """v10：**钻石矿花 4096 合金升成氪金矿**，产率翻倍（256 → 512）。

        它是四座矿里唯一有升级档的一个，而且**掏的是合金不是生物能**
        （`UPGRADE_RES`：与建造费同一个池子）。
        """
        gs = _setup()
        _stock(gs)
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.DIAMOND, key)
        assert st.max_level(st.DIAMOND) == 2
        assert st.upgrade_res(st.DIAMOND) == "alloy"
        gs["players"][1]["bio"] = 9999.0        # 生物能再多也不该被扣
        st.upgrade(gs, 1, key)
        assert gs["players"][1]["bio"] == 9999.0, "这一级掏的是合金"
        assert st.building_name(st.DIAMOND, 2) == "氪金矿"
        _stock(gs, bio=0.0, alloy=0.0)
        _run_ticks(gs, 5)                       # 1 秒
        assert gs["players"][1]["bio"] == pytest.approx(
            st.mine_rate(st.DIAMOND, 2) + st.BED_RATE[0], abs=1e-6)

    def test_copper_mine_has_no_upgrade_path(self):
        """v5 起、v10 仍成立：**矿没有升级链**（钻石矿那次升级是 v10 新加的唯一例外）。

        想换更快的矿就拆了重造。原作的「升级版」说法让两种口径都成立，但两条路
        并存时升级链恒为劣选（128+1024 > 1024），留着就是一份永远不该点的 UI。
        见 `增强调研.md` §10.1。
        """
        gs = _setup()
        _stock(gs)
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.COPPER, key)
        with pytest.raises(ValueError, match="这种建筑不能升级"):
            st.upgrade(gs, 1, key)

    def test_refinery_produces_by_its_own_level(self):
        """v4：精炼机**现在也能升级**（原来只有炮台能升），
        `_produce` 逐个建筑按自己的等级产（不再「数个数 × 固定产率」）。"""
        gs = _setup()
        _stock(gs)
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.REFINERY, key)
        st.upgrade(gs, 1, key)                   # 精炼机 1→2 级（400 生物能）
        _stock(gs, bio=0.0, alloy=0.0)
        _run_ticks(gs, 5)                        # 1 秒
        assert gs["players"][1]["alloy"] == pytest.approx(st.REFINERY_RATE[1],
                                                          abs=1e-6)

    def test_repair_station_heals_door(self):
        """纳米修复器自动补门（建筑跟人在不在无关）。"""
        gs = _setup()
        _stock(gs)
        _park_entity(gs)
        st.build(gs, 1, st.REPAIR, _keys(gs, 1)[0])
        room = _room(gs, 1)
        room["door_hp"] = 10.0
        _run_ticks(gs, 25)                       # 5 秒 → 2%/秒 × 5 = 10%
        assert room["door_hp"] == pytest.approx(
            10.0 + st.REPAIR_RATE * st.door_max(room) * 5.0, rel=1e-3)

    def test_repair_never_exceeds_max(self):
        gs = _setup()
        _stock(gs)
        _park_entity(gs)
        st.build(gs, 1, st.REPAIR, _keys(gs, 1)[0])
        room = _room(gs, 1)
        room["door_hp"] = st.door_max(room) - 0.5
        _run_ticks(gs, 25)
        assert room["door_hp"] == st.door_max(room)

    def test_dead_players_stop_producing(self):
        gs = _setup(uids=(1, 2, 3))
        _stock(gs, bio=0.0)
        st.surrender(gs, 2)
        gs["players"][2]["bio"] = 0.0
        _run_ticks(gs, 5)
        assert gs["players"][2]["bio"] == 0.0


# ── 躺下 / 起身 ─────────────────────────────────────────────


class TestBed:
    """`stasis_lie`（**v4 起没有对应的 `stasis_stand`**）。

    `lie` 是补「进房间之后要干嘛」的：原来 `in_bed` 由**位置**推出，玩家
    走进舱室站在床边，界面上没有任何东西告诉他还要再走一步，而站着产的资源是零
    （用户报的「进房间没法躺下、生物能没有增加」）。

    v4 的两处变化：**跑动阶段也能躺**（原来是「还没开始」就拒绝），并且
    **躺下不可逆**（没有起身），所以 `lying` 只能由 `lie` 置上、之后再也清不掉。
    """

    def test_lie_at_the_bedside_starts_production(self):
        gs = _setup()
        _stock(gs, bio=0.0)
        p1 = gs["players"][1]
        p1["lying"] = False
        bx, by = DORM.room_bed("A1")
        p1["pos"] = [bx + 1 + 0.5, by + 0.5]        # 床边那一格
        _run_ticks(gs, 5)
        assert p1["bio"] == pytest.approx(0.0), "站着不该产资源"
        st.lie(gs, 1)
        assert p1["lying"] is True and p1["in_bed"] is True
        assert st._cell_of(p1["pos"]) == DORM.room_bed("A1"), "躺下要被吸到床心"
        _run_ticks(gs, 5)
        assert p1["bio"] == pytest.approx(st.BED_RATE[0], abs=1e-6)

    def test_lie_inside_the_bed_neighbourhood_is_accepted(self):
        """v6：**站在休眠舱的九宫格之内就能躺**（用户指定）。

        九宫格里真正能站的只有舱室内部那几格（房型缺角会吃掉一两格），所以这里
        按房型现算一遍，逐格验过去 —— 而不是挑一个「大概没问题」的偏移量。
        """
        gs = _setup()
        bx, by = DORM.room_bed("A1")
        cells = [c for c in DORM.room_cells("A1", gs["map"]["shapes"]["A1"])
                 if abs(c[0] - bx) <= 1 and abs(c[1] - by) <= 1]
        # 床在最里面那一行，所以九宫格只截出「床那一行 + 上面一行」共 3~6 格。
        # 少于 3 格说明房型把床边挖空了（房型约束里没有这一条，先在这里拦住）。
        assert 3 <= len(cells) <= 6, f"床的九宫格里只有 {len(cells)} 格可站"
        for c in cells:
            gs = _setup()
            p1 = gs["players"][1]
            p1["lying"] = False
            p1["pos"] = [c[0] + 0.5, c[1] + 0.5]
            st.lie(gs, 1)
            assert p1["lying"] is True, f"床的九宫格 {c} 应该能躺"
            assert st._cell_of(p1["pos"]) == DORM.room_bed("A1"), "躺下要被吸到床心"

    def test_lie_two_cells_from_the_bed_is_rejected(self):
        """**离床两格就躺不下了**，哪怕人还在自己舱室里 —— 这就是「走到床边」
        这一步本身（v6 收紧；v5 曾经放宽成整间舱室）。"""
        gs = _setup()
        p1 = gs["players"][1]
        p1["lying"] = False
        bx, by = DORM.room_bed("A1")
        p1["pos"] = [bx + 2 + 0.5, by + 0.5]        # 离床两格，但还在舱室里
        assert DORM.room_at(bx + 2, by) == "A1", "这一格得先真的在自己舱室里"
        with pytest.raises(ValueError, match="要走到休眠舱旁边"):
            st.lie(gs, 1)

    def test_lie_outside_the_room_is_rejected(self):
        """在走廊里躺不下 —— 这正是「倒计时结束前必须进屋」那条规则的执法者。"""
        gs = _setup()
        p1 = gs["players"][1]
        p1["lying"] = False
        p1["pos"] = [7.5, 10.5]                     # 纵向主通道（x=7/8 那两条）
        with pytest.raises(ValueError, match="要走到休眠舱旁边"):
            st.lie(gs, 1)

    def test_lie_without_a_room_is_rejected(self):
        gs = _setup()
        gs["players"][1]["lying"] = False
        gs["players"][1]["room"] = None
        with pytest.raises(ValueError, match="还没有舱室"):
            st.lie(gs, 1)

    def test_lying_players_cannot_move(self):
        """躺下＝不能移动（用户明确要求）。**输入也要被压成 0** —— 只让
        `_step_players` 跳过的话，起身那一刻手上还攥着一个陈旧方向。"""
        gs = _setup()
        p1 = gs["players"][1]
        before = list(p1["pos"])
        st.set_move(gs, 1, 1.0, 0.0)
        assert p1["dir"] == [0.0, 0.0], "躺着的移动输入必须被丢掉"
        _run_ticks(gs, 10)
        assert p1["pos"] == before

    def test_a_player_who_has_not_lied_can_move(self):
        """没躺下的人走得动（v4 删了「起身」，所以直接造一个没躺下的状态）。"""
        gs = _setup()
        _on_foot(gs, 1)
        p1 = gs["players"][1]
        assert p1["lying"] is False and p1["in_bed"] is False
        st.set_move(gs, 1, 1.0, 0.0)
        _run_ticks(gs, 5)
        assert p1["pos"][0] > DORM.room_bed("A1")[0], "没躺下就该走得动"

    def test_lie_is_idempotent(self):
        gs = _setup()
        assert st.lie(gs, 1)["ok"] is True
        assert st.lie(gs, 1)["ok"] is True          # 已经躺着，不算错

    def test_there_is_no_stand_action(self):
        """v4：**躺下不可逆** —— `stand` 整个删了，协议 `stasis_stand` 也从
        ws.py / room_manager.py 删了。所以没有任何东西能把 `lying` 清掉，
        测试里要造「没躺下」只能直接置状态（见 `_on_foot`）。"""
        assert not hasattr(st, "stand")

    def test_lie_is_allowed_during_the_run_phase(self):
        """v4：**跑动阶段也能躺**（原来这条是反过来的：「还没开始」就被拒）。

        原型就是倒计时内上床 —— 躺下是**开局动作**，不是对局动作；而且规则改后
        「倒计时结束前必须躺下」是硬要求（没躺下的人在走廊里碰到静默体直接出局）。
        """
        gs = _run_setup(uids=(1, 2), seed=1)
        st.room_of(gs, "A1")["owner"] = 1
        p1 = gs["players"][1]
        p1["room"] = "A1"
        bx, by = DORM.room_bed("A1")
        p1["pos"] = [bx + 0.5, by + 0.5]
        assert st.lie(gs, 1)["ok"] is True
        assert p1["lying"] is True and p1["in_bed"] is True

    def test_eliminated_player_is_marked_not_lying(self):
        gs = _setup()
        assert gs["players"][1]["lying"] is True
        st.surrender(gs, 1)
        assert gs["players"][1]["lying"] is False


# ── 升级的前置条件 ──────────────────────────────────────────


class TestBedGate:
    """休眠舱升级需要气密门跟上（原型的「升级小床必须要门的等级对应」）。

    搜到的原型攻略就是这条规则的产物：「前期有钱优先升级床，**只需要在升级小床
    必须要门的等级对应的时候才升级门**」。它把「先经济还是先防御」从一个自由选择
    变成强制的一步一 check —— 想更快产资源，就必须先把门升上去保住它。
    """

    def test_bed_upgrade_needs_the_door_to_keep_up(self):
        gs = _setup()
        _stock(gs)
        p1 = gs["players"][1]
        p1["lying"] = True
        assert st.upgrade(gs, 1, st.BED)["ok"] is True       # 1→2：门 1 ≥ req(2)=1
        assert p1["bed_level"] == 2
        # v4：门槛改成间断表（`bed_door_req`），床 2→3 需要门 2（现在只有 1）。
        # 报错文案也从「气密门 Lv2」改成「需要铁门 2」。
        with pytest.raises(ValueError, match="需要铁门 2"):
            st.upgrade(gs, 1, st.BED)

    def test_upgrading_the_door_unlocks_the_bed(self):
        gs = _setup()
        _stock(gs)
        st.upgrade(gs, 1, st.BED)                            # 床 2
        st.upgrade(gs, 1, st.DOOR)                           # 门 2
        assert st.upgrade(gs, 1, st.BED)["ok"] is True       # 床 3 放行
        assert gs["players"][1]["bed_level"] == 3

    def test_the_first_bed_upgrade_is_not_gated(self):
        """开局 床1/门1 就该能升 —— 否则「前期优先升床」这条原型节奏走不动。"""
        gs = _setup()
        _stock(gs)
        assert gs["players"][1]["bed_level"] == 1
        assert st.room_of(gs, "A1")["door_level"] == 1
        assert st.upgrade(gs, 1, st.BED)["ok"] is True

    def test_the_gate_is_reported_and_nothing_is_spent(self):
        gs = _setup()
        _stock(gs, bio=500.0)
        st.upgrade(gs, 1, st.BED)
        bio = gs["players"][1]["bio"]
        with pytest.raises(ValueError, match="休眠舱升到 Lv3"):
            st.upgrade(gs, 1, st.BED)
        assert gs["players"][1]["bio"] == bio, "被拒绝的升级不能扣钱"
        assert gs["players"][1]["bed_level"] == 2

    def test_the_bed_gate_is_intermittent(self):
        """v4：门槛是**间断的**（`BED_DOOR_REQ`），不是「床每升一级门就要 +1」。

        锚点来自原型攻略：**门 2 → 床可到 4**，**门 5 → 床可到 6**。所以门升到 2
        之后能一口气连升到床 4（这正对应原型「攒一波一次性连升几级床」的打法），
        再往上被门卡住；门补到 5 之后才能继续到 6。
        """
        gs = _setup()
        _stock(gs)
        p1 = gs["players"][1]
        st.room_of(gs, "A1")["door_level"] = 2
        for _ in range(3):                                   # 床 1→2→3→4
            st.upgrade(gs, 1, st.BED)
        assert p1["bed_level"] == 4
        with pytest.raises(ValueError, match="需要铁门 5"):   # 床 4→5 要门 5
            st.upgrade(gs, 1, st.BED)
        st.room_of(gs, "A1")["door_level"] = 5
        st.upgrade(gs, 1, st.BED)                            # 床 →5
        st.upgrade(gs, 1, st.BED)                            # 床 →6
        assert p1["bed_level"] == 6
        with pytest.raises(ValueError, match="需要钢门 3"):   # 床 6→7 要门 8
            st.upgrade(gs, 1, st.BED)

    def test_door_upgrade_is_never_gated_by_the_bed(self):
        """门槛是单向的：门不要求床跟上（否则两条升级线互锁、谁也动不了）。"""
        gs = _setup()
        _stock(gs)
        st.room_of(gs, "A1")["door_level"] = 4
        assert st.upgrade(gs, 1, st.DOOR)["ok"] is True


# ── 建造 / 升级 / 拆除 ──────────────────────────────────────


class TestBuild:
    def test_build_deducts_cost_and_occupies_the_cell(self):
        """建筑挂在**房间**上、键是 `"x,y"` 字符串（不再是玩家身上的整数格号）。"""
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        key = _keys(gs, 1)[0]
        result = st.build(gs, 1, st.TURRET, key)
        assert result["ok"] and result["cell"] == key
        assert p["bio"] == pytest.approx(100.0 - st.BUILD_COST[st.TURRET][1])
        assert _room(gs, 1)["buildings"][key] == {"kind": st.TURRET, "level": 1}
        assert "buildings" not in p, "建筑不该再挂在玩家身上"

    def test_cannot_build_on_bed_or_occupied_cell(self):
        gs = _setup()
        _stock(gs)
        bed_key = st.cell_key(*DORM.room_bed("A1"))
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, bed_key)
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.TURRET, key)
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, key)

    def test_cannot_build_outside_your_room(self):
        """只能在自己舱室的地板上造 —— 通道格、别人的房间、自家舱壁都不行。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, st.cell_key(7, 3))          # 通道格
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_bed("B1")))   # 别人的房
        gs["map"]["shapes"]["A1"] = "notch"                        # (1,3) 是舱壁
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, st.cell_key(1, 3))

    def test_cannot_build_without_resources(self):
        gs = _setup()
        gs["players"][1]["alloy"] = 0.0
        with pytest.raises(ValueError):
            st.build(gs, 1, st.COPPER, _keys(gs, 1)[0])

    def test_cannot_build_unknown_kind(self):
        gs = _setup()
        _stock(gs)
        with pytest.raises(ValueError):
            st.build(gs, 1, "death_star", _keys(gs, 1)[0])

    def test_build_during_the_run_phase_requires_lying(self):
        """v4：跑动阶段**躺着就能造**（`_require_lying` 不卡 phase），没躺下不行。

        原来这条是「跑动阶段一律不能建造」；规则改了 —— 现在只有「有没有躺下」
        这一道闸门，跑动阶段躺着也能把倒计时里的钱花掉（原型开局）。
        所以这里改成断言新的正确行为：没躺下报「要先躺进休眠舱才能建造与升级」，
        躺下后同一句请求成立。
        """
        gs = _run_setup(uids=(1, 2))
        st.room_of(gs, "A1")["owner"] = 1
        p1 = gs["players"][1]
        p1["room"] = "A1"
        p1["bio"] = 100.0
        with pytest.raises(ValueError, match="要先躺进休眠舱才能建造与升级"):
            st.build(gs, 1, st.TURRET, st.cell_key(1, 1))
        bx, by = DORM.room_bed("A1")
        p1["pos"] = [bx + 0.5, by + 0.5]
        assert st.lie(gs, 1)["ok"] is True
        assert st.build(gs, 1, st.TURRET, st.cell_key(1, 1))["ok"] is True

    def test_build_requires_lying(self):
        """v4：**躺着才做得成事** —— 对局阶段没躺下同样不能建造。"""
        gs = _setup()
        _stock(gs)
        p = gs["players"][1]
        p["lying"] = False
        p["in_bed"] = False
        with pytest.raises(ValueError, match="要先躺进休眠舱才能建造与升级"):
            st.build(gs, 1, st.TURRET, _keys(gs, 1)[0])

    def test_demolish_refunds_half_of_build_cost(self):
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.TURRET, key)
        result = st.demolish(gs, 1, key)
        assert p["bio"] == pytest.approx(100.0 - 8.0 + 4.0)
        assert result["refund"] == {st.BUILD_COST[st.TURRET][0]: 4.0}
        assert key not in _room(gs, 1)["buildings"]

    def test_buildable_cells_excludes_bed_and_buildings(self):
        gs = _setup()
        _stock(gs)
        keys = _keys(gs, 1)
        st.build(gs, 1, st.TURRET, keys[0])
        cells = _keys(gs, 1)
        bed_key = st.cell_key(*DORM.room_bed("A1"))
        assert bed_key not in cells and keys[0] not in cells
        assert len(cells) == len(st.room_cells(gs, "A1")) - 2
        assert len(set(cells)) == len(cells)

    def test_buildable_cells_is_empty_without_a_room(self):
        gs = _setup(uids=(1, 2))
        gs["players"][1]["room"] = None
        assert st.buildable_cells(gs, 1) == []


class TestUpgrade:
    def test_bed_upgrade_levels(self):
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        result = st.upgrade(gs, 1, st.BED)
        assert p["bed_level"] == 2 and result["target"] == st.BED
        assert p["bio"] <= 100.0

    def test_door_upgrade_refills_hp_completely(self):
        """**升级即满血**是原型最爽的设计，实现时不要「优化」成只加上限。"""
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        room = _room(gs, 1)
        room["door_hp"] = 10.0
        st.upgrade(gs, 1, st.DOOR)
        assert room["door_level"] == 2
        assert room["door_hp"] == st.DOOR_HP[1]

    def test_door_belongs_to_the_room_not_the_player(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        gs["players"][1]["bio"] = 1000.0
        st.upgrade(gs, 1, st.DOOR)
        assert st.room_of(gs, "A1")["door_level"] == 2
        assert st.room_of(gs, "B1")["door_level"] == 1
        assert "door_hp" not in gs["players"][1]

    def test_turret_upgrade_raises_dps(self):
        gs = _setup()
        _stock(gs)
        key = _door_keys(gs, 1, 1)[0]
        st.build(gs, 1, st.TURRET, key)
        assert st.room_dps(gs, gs["players"][1]["room"]) == st.TURRET_DPS[0]
        st.upgrade(gs, 1, key)
        assert st.room_dps(gs, gs["players"][1]["room"]) == st.TURRET_DPS[1]

    def test_max_level_is_rejected(self):
        gs = _setup()
        p = gs["players"][1]
        _stock(gs)
        # v4：`MAX_LEVEL` 常量删了 —— 满级从费用表推（`max_level(kind)`）。
        p["bed_level"] = st.max_level(st.BED)
        with pytest.raises(ValueError, match="满级"):
            st.upgrade(gs, 1, st.BED)
        _room(gs, 1)["door_level"] = st.max_level(st.DOOR)
        with pytest.raises(ValueError, match="满级"):
            st.upgrade(gs, 1, st.DOOR)

    def test_upgrade_without_resources_is_rejected(self):
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 0.0
        p["bed_level"] = 2                     # 2 级以后的升级要钱
        _room(gs, 1)["door_level"] = 2         # 门槛先过掉，否则报的是门槛不是「资源不够」
        with pytest.raises(ValueError, match="生物能不够"):
            st.upgrade(gs, 1, st.BED)
        with pytest.raises(ValueError, match="生物能不够"):
            st.upgrade(gs, 1, st.DOOR)

    def test_upgrade_from_level_six_also_needs_alloy(self):
        """v6：**第 6 级起升级要同时掏生物能与合金**。

        两种资源**分别报错** —— 升级弹窗要把缺的那样说出来，合成一句「资源不够」
        玩家就只看到「点了没反应」（原型的「有前置但看不出来」是同一类事故）。
        """
        # 分界线是**「升到第 6 级」**这一跳（原型：升钢门时要备够电），不是第 7 级
        assert st.upgrade_alloy(st.BED, 4) == 0.0        # 4 → 5 只要生物能
        assert st.upgrade_alloy(st.BED, 5) > 0.0         # 5 → 6 起要合金
        assert st.upgrade_alloy(st.BED, 6) > 0.0
        assert st.upgrade_alloy(st.DOOR, 6) == st.upgrade_cost(st.DOOR, 6) / st.ALLOY_RATIO

        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 1e6
        p["alloy"] = 0.0
        p["bed_level"] = 5
        _room(gs, 1)["door_level"] = 5                   # 门槛先过掉
        with pytest.raises(ValueError, match="合金不够"):
            st.upgrade(gs, 1, st.BED)

        p["alloy"] = st.upgrade_alloy(st.BED, 5)
        st.upgrade(gs, 1, st.BED)
        assert p["bed_level"] == 6
        assert p["alloy"] == 0.0, "合金要被真的扣掉"

    def test_repair_station_is_capped_at_four_per_room(self):
        """v6：纳米修复器每间舱室最多 4 台（原型规则）。

        不加这条，「门的血比原作多」修不掉 —— 修复是按门血**上限的百分比**回的，
        20 格的舱室能摆十几台，静默体的伤害再怎么调都追不上。
        """
        gs = _setup()
        _stock(gs)
        for key in _keys(gs, 1)[:st.MAX_REPAIRS]:
            st.build(gs, 1, st.REPAIR, key)
        with pytest.raises(ValueError, match=f"最多 {st.MAX_REPAIRS} 台"):
            st.build(gs, 1, st.REPAIR, _keys(gs, 1)[st.MAX_REPAIRS])

    def test_repair_station_cannot_upgrade(self):
        """v4：精炼机 / 培养槽**现在能升级了**，但**纳米修复器仍然不能**（原型也没有）。

        原来这条用培养槽（当时只有炮台能升），规则改后要换一个真的不能升的建筑。
        """
        gs = _setup()
        _stock(gs)
        key = _keys(gs, 1)[0]
        st.build(gs, 1, st.REPAIR, key)
        with pytest.raises(ValueError, match="这种建筑不能升级"):
            st.upgrade(gs, 1, key)

    def test_upgrade_requires_lying(self):
        """v4：**躺着才做得成事** —— 没躺下时升级报同一句「要先躺进休眠舱…」。"""
        gs = _setup()
        _stock(gs)
        p = gs["players"][1]
        p["lying"] = False
        p["in_bed"] = False
        with pytest.raises(ValueError, match="要先躺进休眠舱才能建造与升级"):
            st.upgrade(gs, 1, st.BED)

    def test_upgrade_unknown_cell_is_rejected(self):
        gs = _setup()
        _stock(gs)
        with pytest.raises(ValueError):
            st.upgrade(gs, 1, st.cell_key(5, 3))       # 通道格：没有建筑

    def test_upgrade_without_a_room_is_rejected(self):
        gs = _setup(uids=(1, 2))
        _stock(gs)
        gs["players"][1]["room"] = None
        with pytest.raises(ValueError):
            st.upgrade(gs, 1, st.BED)


class TestPatch:
    def test_self_patch_restores_a_chunk(self):
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        room = _room(gs, 1)
        room["door_hp"] = 50.0
        st.patch(gs, 1)
        assert p["bio"] == 100.0 - st.PATCH_COST_BIO
        assert room["door_hp"] == pytest.approx(
            50.0 + st.door_max(room) * st.PATCH_FRACTION)

    def test_remote_patch_costs_1_5x(self):
        """远程修补是唯一「主动花自己的钱帮别人」的动作；1.5 倍让「救不救」
        成为一个真取舍 —— 这是最容易调的平衡旋钮。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        a, b = gs["players"][1], gs["players"][2]
        a["bio"] = 100.0
        room_b = _room(gs, 2)
        room_b["door_hp"] = 50.0
        result = st.patch(gs, 1, 2)
        assert result["remote"] is True
        assert a["bio"] == pytest.approx(100.0 - st.PATCH_COST_BIO * st.PATCH_COST_MULT)
        assert room_b["door_hp"] > 50.0
        assert b["bio"] == st.START_BIO, "被修的人不掏钱"

    def test_patch_full_door_is_rejected(self):
        gs = _setup()
        gs["players"][1]["bio"] = 100.0
        with pytest.raises(ValueError):
            st.patch(gs, 1)

    def test_patch_needs_resources(self):
        gs = _setup()
        gs["players"][1]["bio"] = 0.0
        _room(gs, 1)["door_hp"] = 10.0
        with pytest.raises(ValueError):
            st.patch(gs, 1)

    def test_patch_a_dead_ally_is_rejected(self):
        gs = _setup(uids=(1, 2, 3), rooms=("A1", "B1", "C1"))
        gs["players"][1]["bio"] = 100.0
        st.surrender(gs, 2)
        with pytest.raises(ValueError):
            st.patch(gs, 1, 2)

    def test_patch_requires_lying(self):
        """v4：修补也走 `_require_lying` —— 没躺下不能修补。"""
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        _room(gs, 1)["door_hp"] = 10.0
        p["lying"] = False
        p["in_bed"] = False
        with pytest.raises(ValueError, match="要先躺进休眠舱才能建造与升级"):
            st.patch(gs, 1)


# ── 静默体 ──────────────────────────────────────────────────


class TestEntityMovement:
    def test_plan_to_uses_the_corridor_grid(self):
        gs = _setup(uids=(1, 2))
        e = gs["entity"]
        st._plan_to(gs, e, DORM.room_door("C4")["out"])
        assert e["path"], "气闸到最远的门必须有路"
        assert all(tuple(c) in DORM.corridor_cells for c in e["path"])
        assert tuple(e["path"][-1]) == DORM.room_door("C4")["out"]

    def test_advance_along_consumes_the_path(self):
        gs = _setup(uids=(1, 2))
        e = gs["entity"]
        e["path"] = [[5, 1], [5, 2]]
        e["pos"] = [5.5, 0.5]
        st._advance_along(e, 1000, st.ENTITY_SPEED)      # 2 秒 → 走 4 格
        assert e["path"] == []
        assert e["pos"] == [5.0, 2.0]
        # 半秒只走到第一格
        e["path"] = [[5, 3]]
        e["pos"] = [5.0, 2.0]
        st._advance_along(e, 250, st.ENTITY_SPEED)       # 0.5 格 → 差半格到 (5,3)
        assert e["path"] == [[5, 3]]
        assert e["pos"][1] == pytest.approx(2.5, abs=1e-6)

    def test_arrives_then_attacks(self):
        gs = _setup(uids=(1, 2), rooms=("B1", "C1"))
        e = gs["entity"]
        e["state"] = "approach"
        e["target_door"] = "B1"
        st._plan_to(gs, e, DORM.room_door("B1")["out"])
        events = _run_ticks(gs, 60, seed=1)
        assert any(x["type"] == "entity_attack_start" for x in events)
        assert e["state"] in ("attack", "chase", "idle", "retreat")

    def test_retreat_then_regenerate_then_return(self):
        gs = _setup(uids=(1, 2), rooms=("B1", "C1"))
        e = gs["entity"]
        e["state"] = "retreat"
        e["hp"] = e["max_hp"] * 0.1
        st._plan_to(gs, e, DORM.airlock)
        events = _run_ticks(gs, 400, seed=1)
        types = [x["type"] for x in events]
        assert "entity_regenerate" in types
        assert e["hp"] > 0
        assert e["state"] in ("idle", "approach", "attack", "chase")

    def test_regenerate_heals_all_the_way_to_full(self):
        """**回满血才出来**（v13，用户明确要求 —— 原来是回到 40% 就出来）。

        老注释写着「必须 < 1，回满血就再也打不死了」，那条推理的两个前提
        （再生期间无敌 + 撤退没有门槛）现在都不成立了：**它在气闸里也是挨打的**，
        而每出来一趟必须先啃够门血上限的 25% —— 所以「回满」不是免死金牌，
        而是把「要不要让它缓过来」这件事交给玩家的输出。"""
        gs = _setup(uids=(1, 2), rooms=("B1", "C1"))
        e = gs["entity"]
        e["state"] = "regenerate"
        e["path"] = []
        e["hp"] = e["max_hp"] - 0.05               # 再回一点点就到满
        _run_ticks(gs, 3)
        assert e["hp"] == pytest.approx(e["max_hp"], rel=1e-9)
        assert st.REGEN_UNTIL == 1.0
        assert e["state"] == "idle"

    def test_regenerate_heals_over_time(self):
        gs = _setup(uids=(1, 2), rooms=("B1", "C1"))
        e = gs["entity"]
        e["state"] = "regenerate"
        e["path"] = []
        e["hp"] = 0.0
        _run_ticks(gs, 5)                          # 1 秒 → 回最大 HP 的 5%
        assert e["hp"] == pytest.approx(e["max_hp"] * st.REGEN_RATE, rel=1e-6)
        assert e["state"] == "regenerate"

    def test_chase_gives_up_when_the_target_hides_in_a_room(self):
        """**目标躲进舱室就放弃追击** —— 静默体进不了舱室，继续追只会贴着墙
        原地打转（`_plan_to` 把舱内目标吸附到最近的通道格，到了那儿距离永远
        > 1，于是每一帧重规划、永远不结束）。

        踩坑的根因是 `_cell_of` 用了舍入：站在格心的玩家被判成隔壁格 →
        `room_at` 返回 None → 它以为人还在走廊里 → 整局 0 次砸门。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [7.5, 2.5]                     # 站在通道里（危险区）
        e = gs["entity"]
        e["pos"] = [7.0, 0.5]
        e["state"] = "idle"
        e["idle_ms"] = 10 ** 9
        events = _run_ticks(gs, 2, seed=1)
        assert any(x["type"] == "entity_chase" for x in events)
        assert e["target_uid"] == 1

        bx, by = DORM.room_bed("A1")                  # 躲进舱室
        p1["pos"] = [bx + 0.5, by + 0.5]
        _run_ticks(gs, 5, seed=1)
        assert e["state"] != "chase", "躲进舱室后不该继续追"
        assert e["target_uid"] is None
        # 回去干活：游荡一会儿之后重新挑一扇门
        events = _run_ticks(gs, 40, seed=1)
        assert e["state"] in ("idle", "approach", "attack")

    def test_entity_catch_eliminates_and_splits_legacy(self):
        """v4：走廊里被静默体碰到 = **直接出局**（原型就是这样），走遗产转移。

        原实现是「撞倒 + 掉生物能 + 击退」（`_knock_down`：发 `player_knocked`、
        写 `down_until_ms` / `immune_until_ms`、`KNOCK_*` 常量），v4 整个删掉：
        躺下不可逆之后，对局阶段走廊里本来就不该有人。所以这里断言新的正确行为：
        发 `entity_catch`、本人出局、`player_eliminated` 带 `cause="entity"`、
        遗产分给队友。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["pos"] = [7.5, 2.5]
        p1["bio"] = 100.0
        e = gs["entity"]
        e["pos"] = [7.0, 2.0]                      # 距离 0.71 ≤ ENTITY_CATCH_DIST
        e["state"] = "chase"
        e["target_uid"] = 1
        events = _run_ticks(gs, 1, seed=1)

        assert any(x["type"] == "entity_catch" and x["uid"] == 1 for x in events)
        elim = [x for x in events if x["type"] == "player_eliminated"]
        assert elim and elim[-1]["uid"] == 1 and elim[-1]["cause"] == "entity"
        assert p1["alive"] is False
        assert p1["bio"] == 0.0, "遗产被分掉了"
        assert gs["players"][2]["bio"] > st.START_BIO, "队友应当拿到遗产"

    def test_entity_catch_eliminates_even_when_rich(self):
        """反过来验：钱多也照样出局 —— **接触即死，与生物能无关**。

        （原来的 `test_knock_down_never_eliminates` 断言的是「撞倒永不淘汰」，
        规则改成「碰到直接出局」之后，这个断言必须反向。）
        """
        gs = _setup(uids=(1, 2, 3), rooms=("A1", "B1", "C1"))
        _on_foot(gs)
        p1 = gs["players"][1]
        p1["bio"] = 5000.0
        p1["pos"] = [7.5, 2.5]
        e = gs["entity"]
        e["pos"] = [7.0, 2.0]
        e["state"] = "chase"
        e["target_uid"] = 1
        _run_ticks(gs, 1, seed=1)
        assert p1["alive"] is False


class TestTargeting:
    def test_choose_target_prefers_the_weakest_door(self):
        """「门最弱的人最危险」是协作的信息基础 —— 纯随机的话玩家无法预判。
        `choose_target` 现在返回**房间号**（门挂在房间上）。"""
        gs = _setup(uids=(1, 2, 3, 4))
        weak = _room(gs, 3)
        weak["door_hp"] = st.door_max(weak) * 0.1
        rng = random.Random(0)
        picks = [st.choose_target(gs, rng) for _ in range(400)]
        target = gs["players"][3]["room"]
        assert picks.count(target) / len(picks) > 0.5, "残血门应当被显著更频繁地盯上"

    def test_choose_target_ignores_rooms_whose_owner_is_gone(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 2)
        rng = random.Random(0)
        picks = {st.choose_target(gs, rng) for _ in range(100)}
        assert gs["players"][2]["room"] is None or all(
            st.room_of(gs, r)["owner"] is not None for r in picks)

    def test_choose_target_returns_none_when_nobody_left(self):
        gs = _setup(uids=(1, 2))
        for uid in (1, 2):
            gs["players"][uid]["alive"] = False
        assert st.choose_target(gs, random.Random(0)) is None

    def test_dps_in_range_covers_the_door_but_not_the_far_corridor(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        key = st.cell_key(*DORM.room_door("A1")["in"])
        st.build(gs, 1, st.TURRET, key)
        assert st.dps_in_range(gs, [5.5, 1.5]) > 0        # 门口那一格
        assert st.dps_in_range(gs, [5.5, 12.5]) == 0.0    # 走廊深处

    def test_turret_placement_decides_the_corridor_coverage(self):
        """射程是 5 格**欧氏**距离 → 摆在舱室深处就打不到门外（放角落的塔打不到
        门口），所以「靠门摆」是真决策。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(1, 4))     # 远离门的角落
        assert st.dps_in_range(gs, [7.5, 2.5]) == 0.0

        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_door("A1")["in"]))
        assert st.dps_in_range(gs, [7.5, 2.5]) > 0

    def test_emp_zeroes_the_coverage(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_door("A1")["in"]))
        _room(gs, 1)["emp_until_ms"] = gs["clock_ms"] + st.EMP_DURATION_MS
        assert st.dps_in_range(gs, [7.5, 2.5]) == 0.0
        gs["clock_ms"] += st.EMP_DURATION_MS
        assert st.dps_in_range(gs, [7.5, 2.5]) > 0


class TestCombat:
    def _entity_at_door(self, gs, rid, hp=None) -> dict:
        """把静默体摆到某扇门口砸门（位置固定在门外那一格）。"""
        return _at_door(gs, rid, hp)

    def test_door_breach_eliminates_the_owner_and_resets_the_room(self):
        """门破 = **舱室主人淘汰 + 房间清空重置**（别人可以再来占）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        victim = gs["players"][1]
        victim["bio"] = 100.0
        victim["alloy"] = 40.0
        room = _room(gs, 1)
        room["door_level"] = 2
        room["buildings"][st.cell_key(0, 0)] = {"kind": st.TURRET, "level": 3}
        room["door_hp"] = 0.3                     # 一 tick（6 DPS × 0.2s = 1.2）内必破
        self._entity_at_door(gs, "A1")
        victim_bio = victim["bio"]

        events = _run_ticks(gs, 1, seed=1)
        types = [x["type"] for x in events]

        assert "door_breached" in types
        assert "player_eliminated" in types
        assert room["owner"] is None
        assert room["door_level"] == 1 and room["door_hp"] == st.DOOR_HP[0]
        assert room["buildings"] == {}
        assert victim["alive"] is False
        assert victim["bio"] == 0.0 and victim["alloy"] == 0.0
        # 继承人拿到了全部遗产（外加这一 tick 自己产的生物能）
        heir = gs["players"][2]
        assert heir["bio"] >= victim_bio + st.START_BIO
        assert heir["alloy"] >= 40.0

    def test_legacy_is_split_evenly_among_survivors(self):
        gs = _setup(uids=(1, 2, 3), rooms=("A1", "B1", "C1"))
        victim = gs["players"][1]
        victim["bio"] = 90.0
        for uid in (2, 3):
            gs["players"][uid]["bio"] = 0.0
        st.surrender(gs, 1)
        assert gs["players"][2]["bio"] == pytest.approx(45.0)
        assert gs["players"][3]["bio"] == pytest.approx(45.0)

    def test_turrets_damage_the_entity_at_the_door(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        for key in _door_keys(gs, 1, 3):
            st.build(gs, 1, st.TURRET, key)
            st.upgrade(gs, 1, key)                # 升到 2 级 = 8 DPS
        e = self._entity_at_door(gs, "A1")
        total = st.dps_in_range(gs, e["pos"])
        assert total == pytest.approx(3 * st.TURRET_DPS[1])
        hp0 = e["hp"]
        _run_ticks(gs, 5, seed=1)                 # 1 秒
        assert e["hp"] == pytest.approx(hp0 - total * 1.0, rel=1e-3)

    def test_turrets_force_the_entity_to_retreat(self):
        """有炮台就该能把它打退 —— 否则「输出有战略价值」这条不成立。

        **v8 起撤退目标恒为气闸**（`stasis._retreat_goal` 直接返回 `DORM.airlock`）：
        击杀是唯一的胜利条件之后，「退到 6 格外就停」那条 v2 的规则反而不服务
        目标了 —— 它必须走完整条走廊才回得了血，来回两趟都在炮台射程里。
        见 `stasis.py` 里 `RETREAT_SPEED_MULT` 上面那段注释。

        ⚠️ **v10 起还要求「砸够一截」**（`retreat_commit`）：血掉到线下还不够，
        本次出击对当前那扇门的累计伤害也要够**出发时那扇门**上限的
        `RETREAT_COMMIT`（门槛在出发那一刻冻住）。
        所以这里的起点取半血（而不是刚好压线）—— 它得先在门口挨够时间把这一条
        满足，实测约 8 秒。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        for key in _door_keys(gs, 1, 4):
            st.build(gs, 1, st.TURRET, key)
            st.upgrade(gs, 1, key)
        e = self._entity_at_door(gs, "A1", hp=st.ENTITY_HP[0] * 1.3 * 0.5)
        # 一 tick 一 tick 地跑到「它决定撤退」那一刻为止 —— 再往后它就开始往回
        # 走了（A1 离气闸只有两格，一口气跑满 12 秒的话它已经在气闸里再生了，
        # 断言 `state == "retreat"` 会假红）。
        rng = random.Random(3)
        events: list[dict] = []
        for _ in range(60):
            events += st.advance(gs, st.TICK_MS, rng)
            if e["state"] == "retreat":
                break
        assert any(x["type"] == "entity_retreat" for x in events)
        assert e["state"] == "retreat"
        assert tuple(e["path"][-1]) == DORM.airlock, \
            f"撤退目标应是气闸 {DORM.airlock}，实际 {tuple(e['path'][-1])}"

    def test_entity_must_commit_to_a_door_before_it_may_retreat(self):
        """**「打一下就跑」被掐掉了**（v10，用户实测反馈）。

        血掉到线下、但这一趟还没砸掉门血上限的 25% 时，它**只能继续砸** ——
        否则它会在门外挨一两下就撤回气闸、回血到 40% 再来，一个来回净赚
        （回的比掉的多），玩家永远打不死它。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        for key in _door_keys(gs, 1, 4):
            st.build(gs, 1, st.TURRET, key)
            st.upgrade(gs, 1, key)
        e = self._entity_at_door(gs, "A1", hp=st.ENTITY_HP[0] * 1.3 * 0.2)
        events = _run_ticks(gs, 20, seed=3)       # 4 秒：血早就过线了，门还没砸够
        assert not any(x["type"] == "entity_retreat" for x in events), \
            "还没砸够就撤了"
        assert e["state"] == "attack"
        door = st.room_of(gs, "A1")
        assert e["trip_damage"] < e["trip_commit"], "这一趟确实还没砸够"

    def test_commit_threshold_is_frozen_when_the_trip_starts(self):
        """门槛是**出发那一刻冻住**的，判据是**累计伤害**（用户明确指出的）。

        反例（老写法）：拿「此刻门的血量是不是 < 75%」当判据 —— 门一边挨打一边
        被修补（每秒回上限的百分比）或**升级即满血**，那条判据就永远不成立，
        它会被卡在门口一直挨打。这里把门补回满血，门槛不许跟着变。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = self._entity_at_door(gs, "A1")
        st._start_trip(gs, e, "A1")
        e["trip_damage"] = st.RETREAT_COMMIT * st.door_max(st.room_of(gs, "A1"))
        assert st._may_retreat(gs, e), "砸够了就该放行"
        # 门被补回满血（甚至升级、上限翻倍）—— 这一趟的账**不许**被改写
        room = st.room_of(gs, "A1")
        room["door_hp"] = st.door_max(room)
        assert st._may_retreat(gs, e), "补血不该把这一趟的账抹掉"
        room["door_level"] = 5
        room["door_hp"] = st.door_max(room)
        assert st._may_retreat(gs, e), "升级（上限翻倍）也不追认"
        e["trip_damage"] -= 1.0
        assert not st._may_retreat(gs, e), "差一点就是不准走"

    def test_retreat_has_no_per_game_limit(self):
        """**撤退次数不设上限**（用户拍板）：拦住「打一下就跑」的是承诺门槛，
        不是次数。这条直接钉住「撤很多次也不被拦」。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = gs["entity"]
        for _ in range(5):
            e = self._entity_at_door(gs, "A1")
            st._start_trip(gs, e, "A1")
            e["trip_damage"] = st.door_max(st.room_of(gs, "A1"))
            e["hp"] = st.ENTITY_HP[0] * 1.3 * 0.1
            assert st._may_retreat(gs, e)



    def test_regenerating_entity_can_still_be_shot(self):
        """**再生期间不再无敌**（v10，用户实测反馈：「鬼即使在恢复过程中也要
        允许被打」）。

        老代码写的是「再生时它在气闸外、打不到」，于是那条守卫变成了一条**凭空
        的无敌**：射程够得着的炮台也打不进去，玩家只看到它当着面回血。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_door("A1")["in"]))
        e = gs["entity"]
        e["state"] = "regenerate"
        e["path"] = []
        e["hp"] = e["max_hp"] * 0.05
        e["pos"] = [float(c) + 0.5 for c in DORM.room_door("A1")["out"]]
        assert st.dps_in_range(gs, e["pos"]) > 0, "这一格得真在射程里才测得出"
        hp0, max_hp = e["hp"], e["max_hp"]
        # 只看「净掉血」会被回血盖掉（再生是每秒 5% 上限，比一座 1 级塔快得多），
        # 所以判据是「比**纯回血**该到的位置更低」。
        _run_ticks(gs, 1, seed=3)
        heal_only = min(max_hp * st.REGEN_UNTIL,
                        hp0 + max_hp * st.REGEN_RATE * st.TICK_MS / 1000.0)
        assert e["hp"] < heal_only, "再生中的静默体也必须能被打"

    def test_entity_only_regenerates_at_the_airlock(self):
        """**回血只发生在气闸那一格**（v8，用户实测报的）。

        老代码的判据是「路走完了」，而 `path` 为空还可能是因为被 `_go_idle`
        之类的路径清空 —— 那时它人还在走廊中间，于是就地回血。现在按**位置**判。
        """
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        e = gs["entity"]
        # 手动把它摆成「撤退中、路径被清空、人却在走廊中间」
        mid = (8, 10)
        assert mid in DORM.corridor_cells
        e["pos"] = [float(mid[0]), float(mid[1])]
        e["state"] = "retreat"
        e["path"] = []
        e["hp"] = st.ENTITY_HP[0] * 1.3 * 0.1
        _run_ticks(gs, 1, seed=3)
        assert e["state"] == "retreat", "不在气闸就不该进再生"
        assert e["path"], "应该重新规划回气闸的路线"
        assert tuple(e["path"][-1]) == DORM.airlock

    def test_entity_regenerates_once_it_reaches_the_airlock(self):
        """走到气闸那一格才开始回血。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        e = gs["entity"]
        e["pos"] = [float(DORM.airlock[0]), float(DORM.airlock[1])]
        e["state"] = "retreat"
        e["path"] = []
        e["hp"] = st.ENTITY_HP[0] * 1.3 * 0.1
        events = _run_ticks(gs, 2, seed=3)
        assert any(x["type"] == "entity_regenerate" for x in events)
        assert e["state"] in ("regenerate", "idle")
        assert e["hp"] > st.ENTITY_HP[0] * 1.3 * 0.1, "该开始回血了"

    def test_retreat_is_not_replanned_every_tick(self):
        """**撤退只触发一次** —— 每 tick 重触发会把路径反复重置，它于是原地磨蹭
        着走不回去（只有跑模拟才看得出来的坑）。

        ⚠️ **v10 差点把它带回来**：删掉「再生期间无敌」之后，撤退判断开始能在
        `regenerate` 态下跑到，而 `_may_retreat` 在非 `attack` 态一律放行 ——
        于是它每 tick 被从再生拽回撤退、又被推回再生，12 秒刷了 7 次撤退事件、
        血一格都没回上去。回归就靠这条用例（它现在也覆盖再生态）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        for key in _door_keys(gs, 1, 4):
            st.build(gs, 1, st.TURRET, key)
            st.upgrade(gs, 1, key)
        e = self._entity_at_door(gs, "A1", hp=st.ENTITY_HP[0] * 1.3 * 0.5)
        events = _run_ticks(gs, 60, seed=3)
        retreats = [x for x in events if x["type"] == "entity_retreat"]
        assert len(retreats) == 1, f"撤退事件发了 {len(retreats)} 次"
        assert e["pos"] != [float(c) for c in DORM.room_door("A1")["out"]], \
            "它该往回走了"

    def test_entity_does_not_evolve_on_the_clock(self):
        """**只有砸门才升级**（v13）—— 计时进化已删。

        这条钉的正是 `增强调研.md` §11.4/§12.6/§14.8 连记三轮的那条：老实现挂着
        一条 50 秒的计时器，**按游戏时钟走**，于是「把它打得只能在走廊里游荡」
        对它毫无影响 —— 6 分钟白送 7~8 级、×25.6 的血。现在把它的游荡计时器
        钉死（`idle_ms` 巨大 ⇒ 整局都在通道里晃，不会去砸任何一扇门），
        跑满 6 分钟它必须**一级都不升**。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = gs["entity"]
        e["state"] = "idle"
        e["idle_ms"] = 10 ** 9
        events = _run_ticks(gs, st.GAME_MS // st.TICK_MS, seed=4)
        evolves = [x for x in events if x["type"] == "entity_evolve"]
        assert not evolves, f"没在砸门就不该进化，实际 {len(evolves)} 次"
        assert e["level"] == 1
        assert e["attack_ms"] == 0.0

    def test_evolution_only_accrues_while_it_is_chewing_a_door(self):
        """进度＝**砸门时长**，别的状态一律不计（这是「打跑它就能掐断它成长」
        的机制来源，v13 之后它是**唯一**的成长来源）。

        ① 站在门口啃 1 秒 ⇒ 记满 1 秒；② 在通道里游荡 1 秒 ⇒ 一秒都不记。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = self._entity_at_door(gs, "A1")
        _run_ticks(gs, 5, seed=4)
        assert abs(e["attack_ms"] - 5 * st.TICK_MS) < 1e-6, e["attack_ms"]

        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = gs["entity"]
        e["state"] = "idle"
        e["idle_ms"] = 10 ** 9
        _run_ticks(gs, 5, seed=4)
        assert e["attack_ms"] == 0.0, e["attack_ms"]

    def test_entity_does_not_evolve_while_regenerating(self):
        """**再生期间不进化**：那会让 max_hp 一路上涨、「回血到 40%」的目标跟着
        往后跑，它就会卡在气闸外回血回一整局（实测踩过）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = gs["entity"]
        e["state"] = "regenerate"
        e["path"] = []
        e["hp"] = 100.0                        # 远低于 40%，确保它一直待在再生态
        e["attack_ms"] = st.EVOLVE_ATTACK_MS
        level = e["level"]
        _run_ticks(gs, 20, seed=4)             # 4 秒（还没回到 40%）
        assert e["state"] == "regenerate"
        assert e["level"] == level

    def test_entity_evolves_when_it_has_chewed_a_door_long_enough(self):
        """啃够 `EVOLVE_ATTACK_MS` 就进化一级，随即清零（不累积超过一级）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        e = self._entity_at_door(gs, "A1")
        e["attack_ms"] = st.EVOLVE_ATTACK_MS - 100
        events = _run_ticks(gs, 10, seed=4)
        assert any(x["type"] == "entity_evolve" for x in events)
        assert e["attack_ms"] < st.EVOLVE_ATTACK_MS, "进化后进度清零"

    def test_breaking_a_door_does_not_by_itself_max_the_entity(self):
        """**砸破一扇门不该把它顶到满级**（`增强调研.md` §11.8 那条的直接回归）。

        老口径是「累计对门造成 1500 伤害就进化」的**绝对**判据，而门血每级翻倍
        ⇒ 砸破一扇 13 级门（153.6 万血）那一刻就把等级一路顶到 `MAX_ENTITY_LEVEL`。
        换成「砸门时长」之后，**门血厚不厚由「砸穿它要花多久」自己表达**，
        破门这件事本身不再跳级。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs, bio=0.0)                            # 让 B1 没塔、A1 的塔也造不起
        room = _room(gs, 1)
        room["door_level"] = st.max_level(st.DOOR)
        room["door_hp"] = 1.0                          # 一碰就破
        e = self._entity_at_door(gs, "A1")
        _run_ticks(gs, 5, seed=4)
        assert room["owner"] is None, "这扇门该被砸破了"
        assert e["level"] == 1, e["level"]

    def test_entity_hp_growth_on_evolve_is_scaled_by_player_count(self):
        gs = _setup(uids=(1, 2, 3, 4, 5, 6), rooms=tuple(DORM.room_ids[:6]))
        e = self._entity_at_door(gs, "A1")
        e["attack_ms"] = st.EVOLVE_ATTACK_MS
        _run_ticks(gs, 1, seed=4)
        assert e["level"] == 2
        assert e["max_hp"] == st.ENTITY_HP[1] * st.PLAYER_COUNT_HP_MULT[6]

    def test_emp_event_paralyses_the_target_door(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_door("A1")["in"]))
        e = self._entity_at_door(gs, "A1")
        e["emp_cd_ms"] = 1
        events = _run_ticks(gs, 2, seed=4)
        assert any(x["type"] == "entity_emp" and x["door"] == "A1" for x in events)
        assert st.room_dps(gs, "A1") == 0.0
        assert st.room_dps(gs, "B1") == 0.0


# ── 终局 ────────────────────────────────────────────────────


class TestControls:
    """**控场四件**（v13）：应急力场 / 束缚弹射器 / 低温阱 / 粒子加速器。

    四条效果的数值照抄原作（`增强调研.md` §16.1），名字是本作自己的；差别只在
    「挂在哪一瞬间」—— 原作挂「猛鬼放技能」，本作里可观测的技能就是 **EMP** 与
    **进入过载**这两个离散瞬间。
    """

    def test_emergency_field_absorbs_damage_and_has_a_cooldown(self):
        """应急力场：门血掉进 30% 就开 3 秒无敌，**并且有冷却**。

        没有冷却的话它就是一扇永远打不破的门（无敌一结束血还在线下，立刻再开）
        —— 那是本作自己加的旋钮（原作没提冷却，见 `SHIELD_CD_MS`）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.SHIELD, _keys(gs, 1)[0])
        room = _room(gs, 1)
        room["door_hp"] = st.door_max(room) * (st.SHIELD_TRIGGER - 0.05)
        _at_door(gs, "A1")
        events = _run_ticks(gs, 5, seed=4)                 # 1 秒
        assert any(x["type"] == "shield_up" and x["room"] == "A1" for x in events)
        assert st.shield_active(gs, room)
        hp = room["door_hp"]
        _run_ticks(gs, 10, seed=4)                         # 再过 2 秒（还在无敌里）
        assert room["door_hp"] == pytest.approx(hp), "无敌期间门血不该掉"
        assert room["door_hp"] > 0
        _run_ticks(gs, 20, seed=4)                         # 无敌结束（冷却还在）
        assert room["door_hp"] < hp, "无敌结束该继续挨打了"
        assert not st.shield_ready(gs, room), "冷却期间不许再开"
        room["door_hp"] = st.door_max(room) * 0.1          # 血更低了也不行
        _run_ticks(gs, 20, seed=4)
        assert not st.shield_active(gs, room), "冷却没好就不许再开"

    def test_emergency_field_is_capped_at_one_per_room(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        keys = _keys(gs, 1)
        st.build(gs, 1, st.SHIELD, keys[0])
        with pytest.raises(ValueError, match="最多 1 台"):
            st.build(gs, 1, st.SHIELD, keys[1])

    def test_cryo_trap_slows_the_entity_attack(self):
        """低温阱：每台 −20% 攻速（三台叠到 ×0.512）。"""
        def dmg_over(ticks: int, cryos: int) -> float:
            gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
            _stock(gs)
            keys = _keys(gs, 1)
            for i in range(cryos):
                st.build(gs, 1, st.CRYO, keys[i])
            room = _room(gs, 1)
            before = room["door_hp"]
            _at_door(gs, "A1")
            _run_ticks(gs, ticks, seed=4)
            return before - room["door_hp"]

        plain = dmg_over(10, 0)
        one = dmg_over(10, 1)
        three = dmg_over(10, 3)
        assert one == pytest.approx(plain * (1 - st.CRYO_SLOW), rel=1e-6)
        assert three == pytest.approx(plain * (1 - st.CRYO_SLOW) ** 3, rel=1e-6)

    def test_binder_binds_the_entity_when_it_casts(self):
        """束缚弹射器：它**放技能**的那一刻掷骰子 —— 本作里的技能是 EMP 与
        「刚进入过载」这两个离散瞬间。被定住期间不动、不砸门、也放不出技能。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.BINDER, _keys(gs, 1)[0])
        e = _at_door(gs, "A1")
        e["emp_cd_ms"] = 1                                 # 下一 tick 就放 EMP
        st.BINDER_CHANCE = 1.0                             # 必定命中（去掉随机性）
        try:
            events = _run_ticks(gs, 3, seed=4)
        finally:
            st.BINDER_CHANCE = 0.30
        assert any(x["type"] == "entity_bound" and x["room"] == "A1" for x in events)
        room = _room(gs, 1)
        hp = room["door_hp"]
        pos = list(e["pos"])
        _run_ticks(gs, 10, seed=4)                         # 2 秒（定身 3 秒内）
        assert room["door_hp"] == pytest.approx(hp), "被定住还砸得动门"
        assert e["pos"] == pos, "被定住还能走"

    def test_particle_accelerator_boosts_the_rooms_turrets(self):
        """粒子加速器：**本舱室**的炮台攻速 +50%（不是全局 buff）。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        keys = _keys(gs, 1)
        st.build(gs, 1, st.TURRET, keys[0])
        base = st.room_dps(gs, "A1")
        st.build(gs, 1, st.ACCEL, keys[1])
        assert st.room_dps(gs, "A1") == pytest.approx(base * (1 + st.ACCEL_BOOST))
        assert st.room_dps(gs, "B1") == 0.0, "别加成到别人舱室"

    def test_control_items_need_alloy(self):
        """四件都掏**合金**（与纳米修复器同一个池子）—— 没有精炼机就造不出来。"""
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs, bio=10_000.0, alloy=0.0)
        for kind in st.CONTROLS:
            with pytest.raises(ValueError, match="资源不够"):
                st.build(gs, 1, kind, _keys(gs, 1)[0])


class TestEndGame:
    def test_a_game_can_run_past_six_minutes(self):
        """**一局没有时长上限**（v8，用户要求「不要求 6 分钟内打死，时间不限」）。

        这条钉的是「`GAME_MS` 不再是判据」：把时钟推过 6 分钟，对局必须**照常
        继续**（更早的那版 v8 会在这里 `REASON_TIMEOUT` 终局）。终局只剩
        「击杀静默体」与「全员失压」两条。
        """
        gs = _setup()
        gs["clock_ms"] = st.GAME_MS + 1000
        _run_ticks(gs, 2, seed=1)
        assert not gs["finished"], "6 分钟不该再结束对局"
        assert gs["phase"] != "over"

    def test_stall_cap_still_ends_a_deadlocked_game(self):
        """**防卡死的兜底还在**（`STALL_CAP_MS`，30 分钟）。

        它不是玩法规则 —— 一局打到分出胜负为止；但没有这条兜底，一个「门强到
        它砸不破、炮台又弱到打不死它」的死局会让本项目唯一的服务端 tick
        **永远跑下去**。
        """
        gs = _setup()
        gs["clock_ms"] = st.STALL_CAP_MS - 100
        events = _run_ticks(gs, 2, seed=1)
        assert gs["finished"]
        assert gs["phase"] == "over"
        assert gs["reason"] == st.REASON_TIMEOUT
        assert gs["winner"] == "entity"
        assert any(x["type"] == "game_over" for x in events)

    def test_killing_the_entity_wins_early(self):
        gs = _setup(uids=(1, 2), rooms=("A1", "B1"))
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(*DORM.room_door("A1")["in"]))
        e = gs["entity"]
        e["state"] = "attack"
        e["target_door"] = "A1"
        e["pos"] = [float(c) for c in DORM.room_door("A1")["out"]]
        e["hp"] = 1.0
        _run_ticks(gs, 2, seed=1)
        assert gs["finished"]
        assert gs["reason"] == st.REASON_KILLED
        assert gs["winner"] == "players"
        assert gs["clock_ms"] < st.GAME_MS

    def test_wipe_loses(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 1)
        st.surrender(gs, 2)
        result = st.surrender(gs, 3)
        assert gs["finished"]
        assert gs["reason"] == st.REASON_WIPED
        assert gs["winner"] == "entity"
        assert any(x["type"] == "game_over" for x in result["events"])

    def test_surrender_only_ends_you(self):
        """**认输只结束本人、队友继续守**（合作塔防不是「对手获胜」的 2 人语义）。"""
        gs = _setup(uids=(1, 2, 3), rooms=("A1", "B1", "C1"))
        st.surrender(gs, 1)
        assert not gs["finished"]
        assert not gs["players"][1]["alive"]
        assert gs["players"][1]["surrendered"] is True
        # 注意：`p["room"]` 仍然留着（战绩/排名要记「他守的是哪一间」），
        # 但房间已经**易主**（owner 清空、门重置），别人可以再来占。
        assert st.room_of(gs, "A1")["owner"] is None
        assert st.room_of(gs, "A1")["door_hp"] == 0.0
        assert gs["players"][2]["alive"] and gs["players"][3]["alive"]

    def test_advance_after_finish_is_a_noop(self):
        gs = _setup()
        gs["clock_ms"] = st.STALL_CAP_MS
        _run_ticks(gs, 1, seed=1)
        assert gs["finished"]
        frozen = json.dumps(st.dynamic_snapshot(gs), sort_keys=True)
        assert st.advance(gs, st.TICK_MS, random.Random(0)) == []
        assert json.dumps(st.dynamic_snapshot(gs), sort_keys=True) == frozen

    def test_actions_are_rejected_after_finish(self):
        gs = _setup()
        gs["clock_ms"] = st.STALL_CAP_MS
        _run_ticks(gs, 1, seed=1)
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, _keys(gs, 1)[0])
        with pytest.raises(ValueError):
            st.upgrade(gs, 1, st.BED)

    def test_dead_player_cannot_act(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 1)
        with pytest.raises(ValueError):
            st.build(gs, 1, st.TURRET, st.cell_key(1, 1))
        with pytest.raises(ValueError):
            st.patch(gs, 1)


# ── 确定性 / 整局模拟 ───────────────────────────────────────


class TestSimulation:
    def test_same_seed_and_inputs_give_same_result(self):
        def run(seed):
            gs = st.create_game([1, 2, 3], seed=seed)
            rng = random.Random(seed)
            for _ in range(st.RUN_MS // st.TICK_MS + 2):     # 跑完跑动阶段
                st.advance(gs, st.TICK_MS, rng)
            for uid in (1, 2, 3):
                st.lie(gs, uid)      # v4：躺着才建得成（跑动结束时人已在床边）
                for key in st.buildable_cells(gs, uid)[:3]:
                    st.build(gs, uid, st.TURRET, key)
            # 跑到终局为止 —— **一局没有时长上限**（v8），所以上界取防卡死的兜底
            for _ in range(st.STALL_CAP_MS // st.TICK_MS):
                st.advance(gs, st.TICK_MS, rng)
                if gs["finished"]:
                    break
            return json.dumps(st.dynamic_snapshot(gs), sort_keys=True)

        assert run(42) == run(42)
        assert run(42) != run(43)

    def test_full_game_simulates_in_milliseconds(self):
        """引擎不认识时间只认识 dt_ms 的红利：整局能毫秒级跑完。"""
        gs = st.create_game([1, 2, 3, 4], seed=5)
        rng = random.Random(5)
        started = time.perf_counter()
        for _ in range((st.STALL_CAP_MS + st.RUN_MS) // st.TICK_MS + 5):
            st.advance(gs, st.TICK_MS, rng)
            if gs["finished"]:
                break
        elapsed = time.perf_counter() - started
        assert gs["finished"]
        assert elapsed < 2.0, f"整局模拟耗时 {elapsed:.2f}s，不该这么慢"

    def test_idle_players_get_wiped_without_building_anything(self):
        """什么都不造就该输 —— 这是「必须做决策」的底线保证。

        上界取防卡死的兜底（v8 起一局没有时长上限）。实际上它远早于此就全灭了。
        """
        gs = _setup(uids=(1, 2, 3, 4))
        _run_ticks(gs, st.STALL_CAP_MS // st.TICK_MS, seed=6)
        assert gs["finished"]
        assert gs["reason"] == st.REASON_WIPED


# ── 下发 / 结算 ─────────────────────────────────────────────


class TestSyncAndRanking:
    def test_game_to_sync_shape(self):
        gs = _setup(uids=(1, 2))
        sync = st.game_to_sync(gs, 1)
        assert sync["game"] == "stasis"
        assert sync["your_seat"] == 0
        assert sync["your_uid"] == 1
        assert sync["game_ms"] == st.GAME_MS
        assert sync["phase"] == "playing"
        assert len(sync["seat_colors"]) == 2
        # **整张图的几何**（v9 起由服务端下发）+ 每间房的状态，走这一层，
        # 不进 5 Hz 的 tick
        assert sync["map"]["id"] == "dorm"
        assert len(sync["map"]["rows"]) == DORM.h
        assert set(sync["map"]["rooms"]) == set(DORM.room_ids)
        assert sync["map"]["airlock"] == list(DORM.airlock)
        assert sync["map"]["gates"] == {}
        assert sync["map"]["rooms"]["A1"]["owner"] == 1
        assert "buildings" in sync["map"]["rooms"]["A1"]
        # 玩家带位置（v2 才有）
        me = sync["players"]["1"]
        assert me["room"] == "A1"
        assert me["pos"] == [3.5, 4.5]
        # v4：sync 里的 `down` 字段（撞倒态）删了，改成 `lying`
        assert me["bed_level"] == 1 and me["lying"] is True
        assert sync["entity"]["pos"] == pytest.approx([7.5, 0.5])

    def test_sync_works_for_an_outsider(self):
        gs = _setup()
        sync = st.game_to_sync(gs, 999)
        assert sync["your_seat"] is None
        assert sync["game"] == "stasis"

    def test_dynamic_snapshot_is_compact_and_has_no_layout(self):
        gs = _setup()
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(1, 1))
        snap = st.dynamic_snapshot(gs)
        assert "clock_ms" in snap and "entity" in snap and "players" in snap
        # 布局走低频层，不进每 tick 的快照
        assert "buildings" not in snap["players"]["1"]
        assert "map" not in snap
        assert "door_level" not in snap["players"]["1"]
        assert "pos" in snap["players"]["1"] and "in_bed" in snap["players"]["1"]
        assert "pos" in snap["entity"] and "path" in snap["entity"]
        assert snap["doors"]["A1"]["max"] == st.DOOR_HP[0]

    def test_dynamic_snapshot_carries_the_lie_flag(self):
        """`lie` 随 tick 下发 —— **客户端不复刻这个判据**，按钮的显隐直接读它
        （同象棋 `legal` / UNO `playable` 的约定，理由见 `stasis.can_lie`）。"""
        gs = _setup()
        p1 = gs["players"][1]
        p1["lying"] = False
        bx, by = DORM.room_bed("A1")

        p1["pos"] = [bx + 0.5, by + 0.5]                  # 床边
        assert st.dynamic_snapshot(gs)["players"]["1"]["lie"] is True
        assert st.can_lie(gs, 1) is True

        p1["pos"] = [bx + 2 + 0.5, by + 0.5]              # 舱室里、但离床两格
        assert st.dynamic_snapshot(gs)["players"]["1"]["lie"] is False
        assert st.can_lie(gs, 1) is False

        p1["pos"] = [7.5, 10.5]                           # 走廊
        assert st.dynamic_snapshot(gs)["players"]["1"]["lie"] is False

        # 躺下之后（以及出局之后）当然也不能再躺
        p1["pos"] = [bx + 0.5, by + 0.5]
        st.lie(gs, 1)
        assert st.dynamic_snapshot(gs)["players"]["1"]["lie"] is False
        assert st.can_lie(gs, 999) is False, "不在这一局里的人不能躺"

    def test_dynamic_snapshot_is_json_serializable(self):
        gs = _setup()
        _stock(gs)
        st.build(gs, 1, st.TURRET, st.cell_key(1, 1))
        json.dumps(st.dynamic_snapshot(gs))
        json.dumps(st.game_to_sync(gs, 1))
        json.dumps(st.snapshot(gs))

    def test_ranking_puts_survivors_first(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 1)
        info = [{"seat": i, "uid": u, "username": f"p{u}"} for i, u in enumerate((1, 2, 3))]
        ranking = st.compute_ranking(gs, info)
        assert [r["uid"] for r in ranking][:2] != [1]
        assert ranking[0]["alive"] is True
        assert ranking[-1]["uid"] == 1
        assert ranking[-1]["rank"] == 3

    def test_ranking_survivors_share_a_rank(self):
        gs = _setup(uids=(1, 2, 3))
        st.surrender(gs, 3)
        info = [{"seat": i, "uid": u, "username": f"p{u}"} for i, u in enumerate((1, 2, 3))]
        ranking = st.compute_ranking(gs, info)
        alive = [r for r in ranking if r["alive"]]
        assert len({r["rank"] for r in alive}) == 1, "存活者并列"

    def test_snapshot_records_rooms_buildings_and_map(self):
        gs = _setup()
        _stock(gs)
        key = st.cell_key(*DORM.room_door("A1")["in"])
        st.build(gs, 1, st.TURRET, key)
        st.upgrade(gs, 1, st.DOOR)
        snap = st.snapshot(gs)
        assert snap["game"] == "stasis"
        assert set(snap["map"]["shapes"]) == set(DORM.room_ids)
        entry = snap["players"]["1"]
        assert entry["room"] == gs["players"][1]["room"]
        assert entry["buildings"] == [{"cell": key, "kind": st.TURRET, "level": 1}]
        assert entry["door_level"] == 2


# ── 已知缺陷（引擎侧，写在这里是为了「修好时会亮」）──────────


class TestRegressions:
    """**v2 重写引入、跑模拟/跑浏览器才暴露的缺陷的回归**。

    这四条原本是 `xfail`（「应该成立但没成立」），缺陷修好之后改成普通断言 ——
    它们锁的都是**纸面推理看不出来**的东西：只有把整局跑起来、或者真的在浏览器里
    点一遍，才会发现「静默体整局 0 次砸门」「人机一座塔都没造」「建造请求被静默拒掉」。
    """

    def test_the_first_upgrade_is_not_free(self):
        """升级费用表**下标 = 当前等级 - 1**：`[0]` 是 1→2 级的价，不是占位 0。

        踩过的坑：表首留了个 0.0 当占位，而取值写 `[lv-1]` —— 于是**每种建筑的
        第一次升级都免费**，最后一项永远用不到。这个 bug 静默地作废了此前**所有**
        平衡模拟的结论（先升经济在模拟里是不花钱的）。
        v4 把三张表并成 `UPGRADE_COST` 并改用 `upgrade_cost(kind, level)` 取值
        （`BED_UPGRADE` 常量删了），这条回归跟着改取值方式。
        """
        gs = _setup()
        p = gs["players"][1]
        p["bio"] = 100.0
        st.upgrade(gs, 1, st.BED)
        assert p["bio"] == pytest.approx(100.0 - st.upgrade_cost(st.BED, 1)), \
            "休眠舱 1→2 级应当收 25"

    def test_build_accepts_a_tuple_cell(self):
        """`build/demolish/upgrade` 的元组格键分支要真的能用。

        踩过的坑：那三个函数里写的是 `M.cell_key(*cell)`，而 `cell_key` 定义在
        `stasis`、`stasis_map` 里根本没有 —— 抛 `AttributeError`（**逃得过
        `except ValueError`**）。ws 层当时还在传整数格号，于是每一次建造都把连接
        静默搞掉，界面上只表现为「点了没反应」。
        """
        gs = _setup()
        _stock(gs)
        x, y = st.parse_key(_keys(gs, 1)[0])
        assert st.build(gs, 1, st.TURRET, (x, y))["ok"]

    def test_auto_assigned_player_is_placed_on_the_bed(self):
        """跑动阶段结束时**所有人**都要被归位到自己的床上，且清掉残留方向。

        踩过的坑（两条叠加）：
        ① 只传送「没占到房的人」—— 而「占到房」发生在踏进舱室那一刻，人这时还在
           门口，于是这批人整局卡在门口、`in_bed` 永远 False；
        ② 传送时不清 `dir`，残留方向会立刻把人推离床格。
        床是**唯一的收入来源**，所以这两条合起来＝零收入、什么都造不出来
        （实测 4 人局 188 秒全灭、一座塔都没有）。

        **归位不等于躺下**：归位只负责「站到床边」，躺下是玩家自己的动作
        （`stasis_lie`）—— 替他躺下，这个机制就永远不会被发现。
        """
        gs = st.create_game([1, 2], seed=1)
        _set_shapes(gs)                        # 全 rect → 保证床隔壁那格是地板
        gs["run_ms"] = 1
        gs["players"][1]["dir"] = [1.0, 0.0]   # 跑动阶段残留的方向
        rng = random.Random(0)
        st.advance(gs, st.TICK_MS, rng)        # 结算跑动阶段 → 归位上床
        assert gs["phase"] == "playing"
        p1 = gs["players"][1]
        assert st._cell_of(p1["pos"]) == DORM.room_bed(p1["room"]), "没有被归位到床边"
        assert p1["lying"] is False, "不该替玩家自动躺下"
        for _ in range(15):                    # 3 秒
            st.advance(gs, st.TICK_MS, rng)
        assert st._cell_of(p1["pos"]) == DORM.room_bed(p1["room"]), \
            f"被推离了床（pos={p1['pos']}）"
        # 站到床边了 + 点「躺下」 → 开始产资源
        assert p1["bio"] == pytest.approx(st.START_BIO, abs=1e-6), "没躺下却在产资源"
        st.lie(gs, 1)
        for _ in range(15):
            st.advance(gs, st.TICK_MS, rng)
        assert p1["in_bed"] is True
        assert p1["bio"] > st.START_BIO

    def test_claimed_room_player_also_gets_placed_on_the_bed(self):
        """**占到房但没走到床**的人同样要被归位 —— 这是上面那条漏掉的一半。

        只覆盖「没占到房」的话，占到了房、却在门口撞上倒计时的人会被留下，
        症状与上面那条一模一样（零收入）。
        """
        gs = st.create_game([1, 2], seed=1)
        _set_shapes(gs)
        # 手动让 1 号占下 A1，但把他按在**门口那一格**（还没走到床上）
        rid = "A1"
        st.room_of(gs, rid)["owner"] = 1
        gs["players"][1]["room"] = rid
        gs["players"][1]["pos"] = [float(DORM.room_door(rid)["in"][0]) + 0.5,
                                   float(DORM.room_door(rid)["in"][1]) + 0.5]
        gs["run_ms"] = 1
        rng = random.Random(0)
        st.advance(gs, st.TICK_MS, rng)
        p1 = gs["players"][1]
        assert st._cell_of(p1["pos"]) == DORM.room_bed(rid), \
            f"占到房的人没有被归位到床上（pos={p1['pos']}）"
        st.lie(gs, 1)
        for _ in range(15):
            st.advance(gs, st.TICK_MS, rng)
        assert p1["in_bed"] is True
        assert p1["bio"] > st.START_BIO


class TestDifficulty:
    """v1.18.1 三档难度：只乘静默体血量/门伤/进化速度，默认标准。"""

    def test_entity_hp_follows_the_difficulty_multiplier(self):
        base = st.ENTITY_HP[0] * st.PLAYER_COUNT_HP_MULT[2]
        for key, spec in st.DIFFICULTY_SPECS.items():
            gs = st.create_game([1, 2], seed=1, difficulty=key)
            assert gs["difficulty"] == key
            assert gs["entity"]["max_hp"] == pytest.approx(base * spec["hp"])

    def test_unknown_difficulty_falls_back_to_normal(self):
        gs = st.create_game([1, 2], seed=1, difficulty="nightmare")
        assert gs["difficulty"] == st.DEFAULT_DIFFICULTY
        assert st.difficulty_spec(gs) == st.DIFFICULTY_SPECS[st.DEFAULT_DIFFICULTY]

    def test_sync_carries_difficulty(self):
        gs = st.create_game([1, 2], seed=1, difficulty="hard")
        sync = st.game_to_sync(gs, 1)
        assert sync["difficulty"] == "hard"
        assert sync["difficulty_label"] == st.DIFFICULTY_SPECS["hard"]["label"]
