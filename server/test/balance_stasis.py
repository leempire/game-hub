"""深空休眠的**平衡模拟台**（不是 pytest 用例，直接 `python test/balance_stasis.py` 跑）。

功能与用法
----------
按 `docs/游戏设计/深空休眠/设计.md` §1.5.1 的规矩，用几种**风格化的打法** × 三个
人数档位 × 一批种子，把整局 6 分钟在毫秒级跑完，统计结局分布：

    python test/balance_stasis.py                 # 默认 16 局/档
    python test/balance_stasis.py --games 32      # 每档 32 局
    python test/balance_stasis.py --style turtle  # 只跑一种打法

**为什么这个脚本要进版本库**：v2 那一版跑平衡的脚本没进版本库，后来重跑时发现
文档里那张表复现不出来（见 `设计.md` §1.5.1 的说明），只能改用「同策略对照」来
代替。改任何战斗常量（`ENTITY_DPS` / `ENTITY_HP` / `DOOR_HP` / `TURRET_DPS` /
`REGEN_UNTIL` / `RETREAT_*` / `PLAYER_COUNT_HP_MULT`）之前，先跑一遍这个脚本，
把新表贴回 `设计.md` §1.5.1。

五种打法（名字与文档里的表一一对应）
------------------------------------
| key | 打法 | 说明 |
|---|---|---|
| `turrets` | 只会造炮台 | 不升床不升门，有钱就铺塔 |
| `turtle`  | 龟缩守门   | 先铺 4 座塔，然后把门与床拉满 |
| `balanced`| 均衡发展   | **直接用人机 `stasis_ai`**（这就是游戏里队友的打法）|
| `econ`    | 经济+输出  | 床拉满（连带把门顶上去），再全力堆塔 |
| `miner`   | 矿工       | 2 座塔 + 1 台精炼机，之后把合金全砸进矿 |

⚠️ 这五种都是**风格化策略**，不是真人。真人的摆位与协调更好，实际 DPS 曲线更陡。
"""

from __future__ import annotations

import argparse
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import stasis as st
from app.game import stasis_ai as ai
from app.game import stasis_map as M

TICK_MS = st.TICK_MS
DECIDE_EVERY = 5                    # 对局阶段 1 秒决策一次（与 room_manager 一致）
# **一局没有时长上限**（v8）：上界取引擎的防卡死兜底 `STALL_CAP_MS`。
# 这会让「僵住不动的对局」在模拟里真的跑满 30 分钟（~9000 tick），比 6 分钟那版
# 慢 5 倍 —— 那是必要的代价：**这些局正是最需要被看见的那些**。
MAX_TICKS = (st.RUN_MS + st.STALL_CAP_MS) // TICK_MS + 600


# ── 把 AI 的动作描述翻译成引擎调用 ─────────────────────────────


def _apply(gs: dict, uid: int, act: dict | None) -> None:
    """执行一个动作。**被拒就咽下去**（风格化策略会不断试到买得起为止）。"""
    if not act:
        return
    try:
        kind = act["type"]
        if kind == "move":
            st.set_move(gs, uid, act["dx"], act["dy"])
        elif kind == "lie":
            st.lie(gs, uid)
        elif kind == "build":
            st.build(gs, uid, act["kind"], act["cell"])
        elif kind == "upgrade":
            st.upgrade(gs, uid, act["target"])
        elif kind == "demolish":
            st.demolish(gs, uid, act["cell"])
        elif kind == "patch":
            st.patch(gs, uid, act.get("target_uid"), gate=act.get("gate"))
    except (ValueError, KeyError):
        pass


def _cell_of(pos) -> tuple[int, int]:
    return int(math.floor(pos[0])), int(math.floor(pos[1]))


def _run_action(gs: dict, uid: int) -> dict | None:
    """跑动阶段：走到目标舱室、站到床的九宫格里、躺下。

    与 `stasis_ai._decide_run` 同一条路线（先到门口再进房），但**只做走位与躺下**，
    不掺任何建造决策 —— 否则「只会造炮台」这种打法在倒计时里就开始升床了，
    五种打法之间就没有可比性。
    """
    p = st.player_of(gs, uid)
    m = st.game_map(gs)
    if st.can_lie(gs, uid):
        return {"type": "lie"}
    if p["room"]:
        bx, by = m.room_bed(p["room"])
    else:
        rid = ai._my_target_room(gs, p)
        if rid is None:
            return {"type": "move", "dx": 0.0, "dy": 0.0}
        if m.room_at(*_cell_of(p["pos"])) is None:
            ox, oy = m.room_door(rid)["out"]
            if _cell_of(p["pos"]) != (ox, oy):
                bx, by = ox, oy
            else:
                bx, by = m.room_bed(rid)
        else:
            bx, by = m.room_bed(rid)
    dx, dy = bx + 0.5 - p["pos"][0], by + 0.5 - p["pos"][1]
    d = math.hypot(dx, dy)
    if d < 0.05:
        return {"type": "move", "dx": 0.0, "dy": 0.0}
    return {"type": "move", "dx": dx / d, "dy": dy / d}


# ── 五种打法 ───────────────────────────────────────────────────


def _room(gs: dict, uid: int) -> dict:
    return st.room_of(gs, st.player_of(gs, uid)["room"])


def _turret_count(room: dict) -> int:
    return sum(1 for b in room["buildings"].values() if b["kind"] == st.TURRET)


def _upgrade_alloy(kind: str, level: int) -> float:
    """升到 `level + 1` 级要的**合金**。

    `getattr` 兜底是为了让这个脚本能同时跑新旧引擎 —— 「同策略对照」（同一份脚本
    分别跑改前/改后的引擎）是本文档复核平衡的标准做法，而双资源升级是 v6 才加的。
    """
    fn = getattr(st, "upgrade_alloy", None)
    return fn(kind, level) if fn else 0.0


def _affordable(p: dict, kind: str, level: int) -> bool:
    """这一级升得起吗（生物能与合金都要够）。"""
    if p["bio"] < st.upgrade_cost(kind, level):
        return False
    return p["alloy"] >= _upgrade_alloy(kind, level)


def _free_cell(gs: dict, uid: int) -> str | None:
    cells = st.buildable_cells(gs, uid)
    return cells[0] if cells else None


def _build_turret(gs: dict, uid: int) -> dict | None:
    p = st.player_of(gs, uid)
    if p["bio"] < st.BUILD_COST[st.TURRET][1]:
        return None
    cell = ai._turret_cell(gs, p, _room(gs, uid)) or _free_cell(gs, uid)
    if cell is None:
        return None
    return {"type": "build", "kind": st.TURRET, "cell": cell}


def _style_turrets(gs: dict, uid: int, p: dict) -> dict | None:
    """只会造炮台：不升床不升门，有钱就铺塔。"""
    return _build_turret(gs, uid)


def _style_turtle(gs: dict, uid: int, p: dict) -> dict | None:
    """龟缩守门：先 4 座塔，然后门与床拉满，最后才补塔。"""
    room = _room(gs, uid)
    if _turret_count(room) < 4:
        act = _build_turret(gs, uid)
        if act:
            return act
    if room["door_level"] < st.max_level(st.DOOR) and _affordable(p, st.DOOR, room["door_level"]):
        return {"type": "upgrade", "target": st.DOOR}
    nxt = p["bed_level"] + 1
    if nxt <= st.max_level(st.BED) and room["door_level"] >= st.bed_door_req(nxt) \
            and _affordable(p, st.BED, p["bed_level"]):
        return {"type": "upgrade", "target": st.BED}
    return _build_turret(gs, uid)


def _style_econ(gs: dict, uid: int, p: dict) -> dict | None:
    """经济+输出：先把床拉满（连带把门顶到门槛），再全力堆塔。"""
    room = _room(gs, uid)
    nxt = p["bed_level"] + 1
    if nxt <= st.max_level(st.BED):
        need = st.bed_door_req(nxt)
        if room["door_level"] < need:
            if _affordable(p, st.DOOR, room["door_level"]):
                return {"type": "upgrade", "target": st.DOOR}
            return _build_turret(gs, uid)
        if _affordable(p, st.BED, p["bed_level"]):
            return {"type": "upgrade", "target": st.BED}
    # 床满了（或暂时升不动）：塔优先，其次把塔升级
    act = _build_turret(gs, uid)
    if act:
        return act
    low = ai._lowest_turret(room)
    if low is not None:
        lv = room["buildings"][low]["level"]
        if lv < st.max_level(st.TURRET) and _affordable(p, st.TURRET, lv):
            return {"type": "upgrade", "target": low}
    return None


def _style_miner(gs: dict, uid: int, p: dict) -> dict | None:
    """矿工：**4 座塔 + 精炼机**起步，之后合金全砸进矿、生物能优先顶床。

    ⚠️ **v13 这一行的定义改过两次，两次都是「它测的不是矿工」**：

    - 原来是「2 座塔 + 1 台精炼机，合金全砸矿」。v13 之后静默体的成长只来自
      **砸门时长**（`EVOLVE_ATTACK_MS`），「不设防」不再是「发育慢一点」而是
      **直接失去唯一的打断手段** —— 那一版在三个人数档上 0/24 全灭；
    - 改成 4 座塔之后仍然 0/24：因为这一条**除了造矿什么升级都不做**（床停在
      1 级 = 1 生物能/秒、门停在 1 级、塔停在 1 级）—— 那还是「自杀」，不是矿工。

    现在它是一条**真的经济流**：防御铺到「守得住」的底线，然后把资源压在
    经济上（合金→矿、生物能→床，床的钱被门等级卡着就先升门），塔最后才升。
    与「经济+输出」的区别是**先矿后床**、而且不主动升塔。
    """
    room = _room(gs, uid)
    if _turret_count(room) < 4:
        act = _build_turret(gs, uid)
        if act:
            return act
    if not any(b["kind"] == st.REFINERY for b in room["buildings"].values()):
        if p["bio"] >= st.BUILD_COST[st.REFINERY][1]:
            cell = _free_cell(gs, uid)
            if cell:
                return {"type": "build", "kind": st.REFINERY, "cell": cell}
    # 买得起的最贵的那座矿（直接建高档矿比铺铜矿划算）
    for kind in (st.DIAMOND, st.GOLD, st.SILVER, st.COPPER):
        if p["alloy"] >= st.BUILD_COST[kind][1]:
            cell = _free_cell(gs, uid)
            if cell:
                return {"type": "build", "kind": kind, "cell": cell}
    # 精炼机也往上顶（合金产量是矿的上游）
    for key, b in sorted(room["buildings"].items()):
        if b["kind"] == st.REFINERY and b["level"] < st.max_level(st.REFINERY) \
                and _affordable(p, st.REFINERY, b["level"]):
            return {"type": "upgrade", "target": key}
    # 经济：床（门槛卡在门等级上就先升门）
    nxt = p["bed_level"] + 1
    if nxt <= st.max_level(st.BED):
        need = st.bed_door_req(nxt)
        if room["door_level"] < need:
            if _affordable(p, st.DOOR, room["door_level"]):
                return {"type": "upgrade", "target": st.DOOR}
        elif _affordable(p, st.BED, p["bed_level"]):
            return {"type": "upgrade", "target": st.BED}
    # 门别破（低于 25% 就补一次）
    if st._ratio(room["door_hp"], st.door_max(room)) < 0.25 \
            and p["bio"] >= st.PATCH_COST_BIO:
        return {"type": "patch", "target_uid": uid}
    # 最后才轮到塔
    low = ai._lowest_turret(room)
    if low is not None:
        lv = room["buildings"][low]["level"]
        if lv < st.max_level(st.TURRET) and _affordable(p, st.TURRET, lv):
            return {"type": "upgrade", "target": low}
    return None


def _style_personality(key: str):
    """**某个性格的人机**（v9）。跑平衡表时把 `ai_profile` 钉死在每个人身上，
    否则 `_start_stasis` 那套「同一局发不同性格」的分配在这里不生效。"""
    def run(gs: dict, uid: int, p: dict) -> dict | None:
        p["ai_profile"] = key
        return ai.decide(gs, uid)
    return run


# 「均衡」就是 `stasis_ai` 的基准性格（`noise = 0`），所以它同时是
# 「游戏里队友的打法」与「平衡表的基准行」。
STYLES = {
    "turrets": _style_turrets,
    "turtle": _style_turtle,
    "balanced": None,               # 直接用 stasis_ai.decide
    "econ": _style_econ,
    "miner": _style_miner,
}
# 六种人机性格各跑一行 —— 这张表是「性格真的不一样」的证据（v9）。
for _k in ai.PROFILE_KEYS:
    STYLES[f"ai-{_k}"] = _style_personality(_k)

# 默认跑这几行：与 `设计.md` §1.5.1 的老表一一对应，方便逐格对照。
BASE_STYLES = ["turrets", "turtle", "balanced", "econ", "miner"]


# ── 跑一局 ─────────────────────────────────────────────────────


def decide(gs: dict, uid: int, style: str) -> dict | None:
    p = st.player_of(gs, uid)
    if p is None or not p["alive"]:
        return None
    if gs["phase"] == "run" and not p["lying"]:
        return _run_action(gs, uid)
    if not p["lying"]:
        # 对局阶段还没躺下（开局被自动分配进房）→ 走位过去、躺下
        return _run_action(gs, uid)
    if style == "balanced":
        return ai.decide(gs, uid)
    return STYLES[style](gs, uid, p)


def play(style: str, n_players: int, seed: int, map_id: str = "dorm") -> dict:
    uids = list(range(1, n_players + 1))
    gs = st.create_game(uids, seed=seed, map_id=map_id)
    rng = gs["rng"]
    tick = 0
    while not gs["finished"] and tick < MAX_TICKS:
        st.advance(gs, TICK_MS, rng)
        tick += 1
        if gs["phase"] == "run" or tick % DECIDE_EVERY == 0:
            for uid in uids:
                _apply(gs, uid, decide(gs, uid, style))
    return {
        "reason": gs["reason"] or "timeout",
        "ms": gs["clock_ms"],
        "level": gs["entity"]["level"],
        "alive": len(st.alive_players(gs)),
    }


# ── 汇总 ───────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=16, help="每个档位跑多少局")
    ap.add_argument("--players", type=str, default="2,4,6")
    ap.add_argument("--style", type=str, default=",".join(BASE_STYLES))
    ap.add_argument("--map", dest="map_id", type=str, default="dorm",
                    help="地图 id（dorm / suite / atrium）")
    args = ap.parse_args()

    counts = [int(x) for x in args.players.split(",")]
    styles = [s.strip() for s in args.style.split(",") if s.strip()]
    base_seed = 20261013

    print(f"地图 {args.map_id}（{M.get_map(args.map_id).name}）"
          f" · 每档 {args.games} 局")
    for style in styles:
        row = []
        for n in counts:
            tally = collections.Counter()
            levels = []
            for i in range(args.games):
                out = play(style, n, base_seed + i, args.map_id)
                tally[out["reason"]] += 1
                levels.append(out["level"])
            killed = tally[st.REASON_KILLED]
            timeout = tally[st.REASON_TIMEOUT]
            wiped = tally[st.REASON_WIPED]
            row.append(f"{n}人 击杀{killed}/超时{timeout}/全灭{wiped}"
                       f"（均 {sum(levels)/len(levels):.1f} 级）")
        print(f"{style:11s} | " + " | ".join(row))


if __name__ == "__main__":
    main()
