"""深空休眠（stasis）人机队友 AI —— 规则式贪心，无搜索、无 IO。

**这是「人机队友」，不是对手。** 深空休眠是合作塔防：人机扮演的是和真人一起守站的
船员，所以它的目标是「**有用、不添乱、不需要玩家指挥它**」——
资源各自独立（它不花你的钱），动作全部走正常广播路径（引擎仍是唯一权威，AI 只做
决策、不直接改状态），它也不会等你下命令。真人与它各守一间舱室，靠**远程修补**
互相救场（设计文档 §1.8：远程修补是唯一「主动花自己的钱去帮别人」的动作）。

与 `uno_ai.py` 同一套写法：纯函数、无 IO、无 asyncio、不做搜索 —— 一次决策就是
几十次比较，微秒级，**不需要 `asyncio.to_thread`**。

# 性格（v9）

v8 之前只有**一套**决策流程，于是同一局里的三个人机造出来的东西**一模一样**：
同样的开局 4 座塔、同样升到 3 级床、同样的顺序。玩家看到的是三个复制品。

v9 把流程参数化成 **`Profile`（性格）**：开局铺几座塔、经济与防御谁先谁后、
愿不愿意造矿、多急着去救队友、为共用闸门掏多少钱，都是旋钮。`PROFILES` 里有
六种，`assign_profiles()` 保证**同一局里的每个人机拿到不同的性格**。

> 用户的原话：「人机不需要特别聪明，有时候甚至可以故意随机操作，放弃最优解，
> 这样玩家体验会更好」。所以每个性格还带一个 `noise`（噪声比例），到点就
> **故意乱来**（`_random_action`）：把钱花在矿上、升一座位置很差的炮台、
> 给满血的队友修补、或者干脆发呆。一个每轮都按最优解出牌的人机玩起来像在执行
> 脚本，而且会让玩家觉得「我做什么都无所谓」—— 这恰恰是合作游戏最不该有的感觉。
>
> **噪声不等于非法**：每一支随机动作都要先过一遍引擎的判据（够不够钱、能不能升），
> 因为 `_stasis_bot_think` 是**静默吞掉 `ValueError`** 的，非法动作等于白扔一轮决策。

**`balanced`（均衡）是基准性格，`noise = 0`** —— 平衡表与回归测试都以它为准，
别的性格是它的偏移。改任何旋钮之前先跑 `test/balance_stasis.py`。

# 决策流程（`_decide_playing`，按优先级）

1. **先躺下**。躺着才产资源，站着产的是一滴都没有 —— 不先躺下的话后面每条判据
   都因「买不起」而不成立，人机会原地站一整局。`_start_playing` 把人放在床格上，
   正常一步就躺下；位置漂了就先走回床边。
2. **噪声**（随性/莽夫等性格）：到比例就随机挑一个合法动作，放弃最优解。
3. **共用闸门**（v9，只有 `suite` 图有）：闸门快破了先救它 —— 它破了就再也没有了，
   而且暴露的是**两个人**。性格越偏防御（龟缩/修补匠），越愿意为它掏钱。
4. **保命**：自己的门血低于门槛 → 先试升门（升级即满血 + 上限翻倍），升不动才修补。
   门槛拆两档：< `patch_ratio` 无条件修；< `patch_ratio + 0.2` 但**要先有开局炮台**才修。
   单档时它会把每一分生物能都拿去修补（20 生物能换 75 血），永远攒不出炮台。
5. **救队友**：有队友门血 < `ally_ratio` 且自己生物能够 → 远程修那扇门（选**门血
   比例最低**的队友）。远程修补按 1.5 倍加价（30 生物能）。
6. **先铺开局炮台**（`opening_turrets` 座）。8 生物能一座，是单位资源性价比最高的
   一笔，而且它同时是**唯一的反制手段**（打退静默体＝拖慢它进化）。
7. 休眠舱与气密门推到 `early_level` 级（门槛已过且钱够）。
8. 精炼机（第 6 级往上的升级要合金，没有它永远升不动门）。
9. 修复器（合金富余时补，最多 4 台 —— 引擎的 `MAX_REPAIRS` 也是 4）。
10. 铺满剩余空格 —— **但要给下一级休眠舱与精炼机留够钱**。
11. 后期把床与门拉满，炮台从等级最低的开始升。

> **休眠舱的两条升级判据都必须带 `_bed_gate_ok`**（引擎的硬规则：升床需要气密门
> `>=` 当前床等级）。漏掉的话它会永远返回同一个被拒绝的动作、**卡死在这一条**，
> 后面的建造与炮台升级全部轮不到 —— 这是「一次决策一个动作」这个设计的固有陷阱：
> 任何被静默拒绝的动作都会变成死循环。床与门的交替升级也从这条规则里自然长出来。

> **⑥~⑩ 的顺序是 v2 调过来的**（v1 是「先经济与门、再铺炮台」）。v1 那条顺序成立
> 是因为升级费用表有个下标错位的 bug、**第一次升级免费**；费用修好之后同一条顺序
> 会把前 120 生物能全砸进升级、一座塔都没有 → 全灭。

炮台一律**靠门摆**：射程是**逐塔按等级算的欧氏距离**（引擎逐塔判定，1 级炮只有
4 格 —— 对齐原型的「攻击距离是 4」），摆在舱室深处或角落会打不到门口 —— 摆位是
真决策，不再只是「摆得像个懂行的队友」。

**v4 起跑动阶段就躺下并直接转入发育**（倒计时里躺着也产资源、也能建造）。

刻意不做的判断（保持简单，同 `uno_ai.py` 的取舍）：不预判静默体的进化曲线、
不拆建筑、不为「贴着火线摆塔」而重新规划已有建筑（那要评估全图火力覆盖，
收益不确定）。**v9 新增的一条例外**：在 `suite` 图上它会**主动去和队友住同一个
套间**（`_suite_bonus`）—— 那是那张图存在的意义，各住各的会让它退化成
「每间房多一道门」。

> ⚠️ **v7 起它必须造精炼机**（`REFINERY`）。第 6 级往上的升级要**同时**掏生物能与
> 合金（`upgrade_alloy`），没有精炼机的人机一滴合金都没有 —— 它会在「升门」这个
> 动作上被永远拒掉，而 `_stasis_bot_think` 是**静默吞掉 `ValueError`** 的，
> 「一次决策一个动作」下这就是一个死循环（同 `_bed_gate_ok` 那条教训）。
> 所以每一条「够不够钱」的判据都改走 `_can_upgrade()`，两个资源一起看。
"""

import math
import random
from dataclasses import dataclass

from app.game.stasis import (
    ACCEL, BED, BINDER, BUILDABLE, BUILD_COST, COPPER, CRYO, DIAMOND, DOOR,
    GOLD, PATCH_COST_BIO, PATCH_COST_MULT, REFINERY, REPAIR, SHIELD, SILVER,
    SHIELD_TRIGGER, TURRET, alive_players, bed_door_req, buildable_cells, can_lie, door_max,
    game_map, max_level, parse_key, player_of, room_cells, room_of,
    upgrade_alloy, upgrade_cost, upgrade_res,
)

# ── 性格 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Profile:
    """一套人机打法。**所有差异都在这几个旋钮上**，决策流程本身是同一份。

    「性格」不是难度：难度是 `skill`（够钱才动手的门槛），性格是**取舍**——
    先经济还是先防御、救不救队友、愿不愿意为共用闸门掏钱。两者正交。
    """

    key: str
    name: str
    blurb: str
    skill: float = 1.0            # 执行力：够钱的门槛是 `cost / skill`
    opening_turrets: int = 4      # 开局先铺几座炮台再谈升级
    early_level: int = 3          # 床与门先推到几级
    turret_cap: int = 99          # 炮台数量的软上限（龟缩型不铺满）
    repair_target: int = 4        # 纳米修复器的目标台数（引擎上限 4）
    refinery: bool = True         # 会不会造精炼机（不造 = 永远升不过 5 级）
    refinery_first: bool = False  # 精炼机是否排在开局炮台之前
    mines: bool = False           # 合金富余时造矿
    ally_ratio: float = 0.25      # 队友门血低于此比例才去救
    patch_ratio: float = 0.35     # 自己门血低于此比例无条件修
    gate_ratio: float = 0.45      # 共用闸门低于此比例就救
    gate_bias: float = 1.0        # >1 更愿意为闸门掏钱，<1 更抠
    # **控场四件的意愿**（v13）：0 = 一件都不造，1 = 正常，>1 = 一有合金就掏。
    # 它是「合金花在哪」这条取舍的旋钮：修补匠/龟缩偏控场，莽夫全砸炮台。
    controls: float = 1.0
    noise: float = 0.0            # 每轮决策有这么大比例**故意乱来**


PROFILES: dict[str, Profile] = {
    "balanced": Profile(
        key="balanced", name="均衡",
        blurb="床、门、炮台一起推，最稳的一种",
        skill=1.0, opening_turrets=4, early_level=3, refinery=True,
        mines=True, controls=1.0, noise=0.0),
    "turtle": Profile(
        key="turtle", name="龟缩",
        blurb="门与床先拉满，炮台只造够用的",
        skill=0.9, opening_turrets=2, early_level=6, turret_cap=8,
        ally_ratio=0.35, patch_ratio=0.5, gate_ratio=0.6, gate_bias=1.4,
        controls=1.4, noise=0.08),
    "rush": Profile(
        key="rush", name="莽夫",
        blurb="钱全砸炮台，经济和门都顾不上",
        skill=1.2, opening_turrets=8, early_level=2, repair_target=1,
        refinery=False, ally_ratio=0.15, patch_ratio=0.25, gate_ratio=0.3,
        gate_bias=0.7, controls=0.0, noise=0.15),
    "miner": Profile(
        key="miner", name="矿工",
        blurb="先铺矿与精炼机，指望后期用钱堆死它",
        # ⚠️ **开局炮台 v13 从 3 座提到 4 座**：静默体的成长现在只来自**砸门
        # 时长**（`EVOLVE_ATTACK_MS`），所以「开局少铺一座塔」的代价不再是
        # 「发育慢一点」，而是**少了唯一能打断它成长的手段** —— 实测三个人数档
        # 从 9/5/5 提到 11/7/7（16 局/档）。这不动任何被用户拍过板的数值，
        # 只把它那条「少造塔」的性格收一收（见 `增强调研.md` §16.5）。
        skill=0.8, opening_turrets=4, early_level=4, repair_target=2,
        refinery=True, refinery_first=True, mines=True, ally_ratio=0.2,
        patch_ratio=0.3, gate_ratio=0.4, gate_bias=0.9, controls=0.6,
        noise=0.1),
    "medic": Profile(
        key="medic", name="修补匠",
        blurb="盯着所有人的门血，自己少造塔",
        skill=1.0, opening_turrets=2, early_level=4, turret_cap=10,
        ally_ratio=0.5, patch_ratio=0.6, gate_ratio=0.7, gate_bias=1.6,
        controls=1.6, noise=0.1),
    "casual": Profile(
        key="casual", name="随性",
        blurb="想到什么造什么，别指望它",
        skill=0.7, opening_turrets=3, early_level=3, repair_target=2,
        ally_ratio=0.2, patch_ratio=0.35, gate_ratio=0.4, mines=True,
        controls=0.5, noise=0.4),
}
DEFAULT_PROFILE = "balanced"
# 分配顺序（`assign_profiles` 会打乱它再依次发）—— 保证同一局里的人机各不相同
PROFILE_KEYS = ["balanced", "turtle", "rush", "miner", "medic", "casual"]


def assign_profiles(rng: random.Random, uids: list[int]) -> dict[int, str]:
    """给这一局的每个人机发一个**互不相同**的性格。

    「互不相同」是重点：用户报的问题就是「每个人机造的建筑都一模一样」。
    人机数量超过性格种类时（最多 6 个人机、正好 6 种）才会出现重复。
    """
    deck = list(PROFILE_KEYS)
    rng.shuffle(deck)
    return {uid: deck[i % len(deck)] for i, uid in enumerate(uids)}


def profile_of(gs: dict, p: dict) -> Profile:
    return PROFILES.get(p.get("ai_profile") or DEFAULT_PROFILE,
                        PROFILES[DEFAULT_PROFILE])


# ── 调参旋钮 ────────────────────────────────────────────────

# 自己的门掉到这个比例以下就掏钱修。**不能设太高**：静默体持续砸门，阈值一高
# 人机就把每一分生物能都拿去修补（20 生物能换 75 血），永远攒不出炮台 ——
# 而炮台是唯一能把它打退、拖慢它进化的东西。实测阈值 0.5 时 6 人局 190 秒全灭、
# 一座塔都没造出来。所以门槛拆成两档：危险线以下无条件修，危险线以上要先有塔。
SELF_PATCH_SOFT_EXTRA = 0.20       # 「要先有塔才修」的那一档比无条件档宽多少
# 救队友的生物能门槛 = 远程修补价（30）的两倍：救完还剩一次的钱，别把自己搭进去。
SAVE_ALLY_BIO = PATCH_COST_BIO * PATCH_COST_MULT * 2

_TURRET_COST = BUILD_COST[TURRET][1]
# 攒合金的底线：**手上要留够下一次升级要用的那份**，否则它会一路把合金花在修复器上，
# 第 6 级之后的门/床永远升不动。留一倍建造价（64）当缓冲。
_ALLOY_RESERVE = BUILD_COST[REPAIR][1]

# 走位精度：离目标格中心这么近就算到了，停手（否则会在格心附近来回抖）
_ARRIVE = 0.25


def _cell_of(pos) -> tuple[int, int]:
    """浮点格坐标 → 格。**约定：格 `(x,y)` 的中心是 `(x+0.5, y+0.5)`**，
    所以取格是 `floor`（与 `stasis._cell_of` 同一条，见那边的注释）。"""
    return int(math.floor(pos[0])), int(math.floor(pos[1]))


def _need(cost: float, prof: Profile) -> float:
    """「够钱」的门槛：`cost / skill`。

    skill 是「执行力」不是「折扣」，所以是**除**不是乘 —— skill=0.5 的队友要攒到
    两倍的钱才动手（迟钝），1.5 的一有富余就动手（门槛越低越强）。
    """
    return cost / prof.skill


def _rng_of(gs: dict, p: dict) -> random.Random:
    """这个人机自己的随机数流。

    **不共用 `gs["rng"]`**：那条流同时决定静默体挑哪扇门，混进来会让
    「人机的随机操作」和「鬼的随机选门」互相影响 —— 调平衡时说不清是谁的锅。
    按 (种子, uid) 播种，同一局同一座位的人机行为完全可复现（单测靠它）。
    存在玩家字典里，`persistence` 的 `__rng__` 编码器认得它（热重启后序列延续）。
    """
    rng = p.get("ai_rng")
    if rng is None:
        rng = random.Random((gs.get("seed") or 0) * 1000003 + p["uid"] * 7919)
        p["ai_rng"] = rng
    return rng


# ── 对外接口 ────────────────────────────────────────────────


def decide(gs: dict, uid: int) -> dict | None:
    """返回一个动作描述，或 None 表示这轮什么都不做。

    返回形状（room_manager 层会把它翻译成引擎调用）：

        {"type": "move",   "dx": -1.0, "dy": 0.0}   # 跑动阶段，**每 tick 都要发**
        {"type": "build",  "kind": "turret", "cell": "9,7"}
        {"type": "upgrade","target": "bed"}         # 或 "door" / "9,7" / "gate:G1"
        {"type": "demolish","cell": "9,7"}
        {"type": "patch",  "target_uid": 2}         # target_uid 可为自己
        {"type": "patch",  "gate": "G1"}            # 共用闸门

    **每 tick 调用一次，一次只返回一个动作** —— 引擎的每个动作本身就是一次资源
    支出，攒着不发没有意义；而「一次决策一个动作」让这个函数保持成纯函数。
    """
    if gs.get("finished") or gs.get("phase") == "over":
        return None
    p = player_of(gs, uid)
    if p is None or not p["alive"]:
        return None

    if gs["phase"] == "run":
        # **躺下之后立刻转入发育流程**（v4）：倒计时里躺着是产资源的，而且
        # 建筑/升级在跑动阶段也能做（`_require_lying` 不卡 phase）—— 原型的开局
        # 就是「倒计时内躺下、攒钱、赶在鬼出来前把床升上去」。人机要是傻站到
        # 倒计时结束再开工，就等于白送对面 30 秒。
        if p["lying"]:
            return _decide_playing(gs, uid, p)
        return _decide_run(gs, p)

    if gs["phase"] != "playing":
        return None
    return _decide_playing(gs, uid, p)


# ── 跑动阶段：抢舱室 ────────────────────────────────────────


def _decide_run(gs: dict, p: dict) -> dict | None:
    """挑一间空舱室跑过去躺下。**每 tick 都返回一个 move**（引擎按方向积分位置）。"""
    m = game_map(gs)
    # **占到房不等于到了床上**：走进舱室那一刻就占下了，但人还在门口。
    # 直接站住的话 `in_bed` 永远是 False，而床是唯一的收入来源 —— 一局下来
    # 一滴生物能都不产、什么都造不出来（实测 6 人局 190 秒全灭）。
    if p["room"]:
        bx, by = m.room_bed(p["room"])
        dx, dy = bx + 0.5 - p["pos"][0], by + 0.5 - p["pos"][1]
        d = math.hypot(dx, dy)
        if can_lie(gs, p["uid"]):
            # **到了床的九宫格里就躺下，倒计时还没结束也躺**（v4）。躺下不可逆、
            # 而且没躺下的人在场上什么也做不了 —— 人机更得先躺。
            # 判据直接用引擎的 `can_lie`（v6）：人机的动作被拒时 `_stasis_bot_think`
            # 是静默跳过的，自己写一份「离床心 0.25 格」的判据就会在两边口径不一致时
            # 变成死循环（同 `_bed_gate_ok` 那条教训）。
            return {"type": "lie"}
        return {"type": "move", "dx": dx / d, "dy": dy / d}

    target = _my_target_room(gs, p)
    if target is None:
        return {"type": "move", "dx": 0.0, "dy": 0.0}      # 没空房了，原地等自动分配

    # **先走到门口再进房**：直线冲向床心的话，门口不在直线路径上时（A 列的门开在
    # 舱室右边界、人从下方正中过来）它会顶着舱壁站满 30 秒 —— 靠 `_step_players`
    # 的沿墙滑是滑不进去的。到不了房就等自动分配，等于白送一整段发育时间。
    door = m.room_door(target)
    if m.room_at(*_cell_of(p["pos"])) is None:             # 还在通道里
        ox, oy = door["out"]
        if _cell_of(p["pos"]) != (ox, oy):
            dx, dy = ox + 0.5 - p["pos"][0], oy + 0.5 - p["pos"][1]
            d = math.hypot(dx, dy)
            return {"type": "move", "dx": dx / d, "dy": dy / d} if d > _ARRIVE \
                else {"type": "move", "dx": 0.0, "dy": 0.0}

    bx, by = m.room_bed(target)
    dx, dy = bx + 0.5 - p["pos"][0], by + 0.5 - p["pos"][1]
    d = math.hypot(dx, dy)
    if can_lie(gs, p["uid"]):
        return {"type": "lie"}
    # `d` 可能接近 0（正好站在床心却还不能躺，比如舱室刚被别人抢走），
    # 除下去会得到 inf/nan 的方向 —— 那会让 `_step_players` 把人推飞。
    if d < _ARRIVE:
        return {"type": "move", "dx": 0.0, "dy": 0.0}
    return {"type": "move", "dx": dx / d, "dy": dy / d}


def _my_target_room(gs: dict, p: dict) -> str | None:
    """我这一局要抢哪间。**记住并沿用**（存在 `p["ai_room"]`），只在它被别人抢走时才换。

    不记住的话每 tick 重新挑「最好的空房」会让所有人机都扑向同一间 —— 它们同时
    选中、同时跑过去，只有一个能占上，其余的到门口才发现没戏、再掉头，白白浪费
    十几秒跑动时间（跑动阶段一共只有 30 秒）。
    """
    cur = p.get("ai_room")
    if cur and room_of(gs, cur)["owner"] is None:
        return cur
    best = _best_room(gs)
    p["ai_room"] = best
    return best


def _suite_bonus(gs: dict, rid: str) -> int:
    """这间房的套间里**已经有人**了吗（`suite` 图专用，别的图恒为 0）。

    人机会**主动去和队友住同一个套间**。那是那张图存在的意义 —— 两个人共用一个
    门厅、一扇外闸门，一个攒经济一个攒输出。人机各住各的，那张图就退化成
    「每间房多一道门」。
    """
    m = game_map(gs)
    gid = m.room_gate(rid)
    if gid is None:
        return 0
    return 1 if any(room_of(gs, r)["owner"] is not None
                    for r in m.gates[gid].rooms) else 0


def _best_room(gs: dict) -> str | None:
    """空舱室里的最优解：**格子最多** → **套间里已经有人** → **离气闸最远** → 房间号。

    格子多 = 建筑上限高。离气闸远 = 静默体走过来要更久，白送的发育时间
    （原型攻略的「往远离猎梦者出生点的角落跑」就是这条）。最后一项只是为了
    确定性：同一个局面永远给同一间，测试与回放才可复现。

    **套间加分排在距离之前**：在 `suite` 图上所有舱室一样大，这一项直接决定
    「跟不跟队友住一起」；在别的图上它恒为 0，顺序不影响任何东西。
    """
    m = game_map(gs)
    free = [r for r in m.room_ids if room_of(gs, r)["owner"] is None]
    if not free:
        return None
    ax, ay = m.airlock
    # **距离按「闸门全开」算**：这一项问的是「静默体要走多远才够得着这间房」，
    # 而不是「现在能不能走到」—— 套间里的房在闸门没破时是走不到的（`ghost_path`
    # 会回空表），拿它当距离会让所有套间房并列成 0，这一项就废了。
    all_gates = tuple(m.gates)
    return min(free, key=lambda r: (
        -len(room_cells(gs, r)),
        -_suite_bonus(gs, r),
        -m.distance(m.room_door(r)["out"], (ax, ay), all_gates),
        r,
    ))


# ── 对局阶段 ────────────────────────────────────────────────


def _decide_playing(gs: dict, uid: int, p: dict) -> dict | None:
    if not p["room"]:
        return None
    prof = profile_of(gs, p)
    room = room_of(gs, p["room"])

    # ① **先躺下**。躺着才产资源，站着产的是一秒一滴都没有 —— 不先躺下的话
    #    后面每一条判据都因为「买不起」而不成立，人机会站在原地一整局
    #    （v1 的「零收入」事故换了张皮：那时是位置没归位，现在是状态没置上）。
    #    `_start_playing` 会把人放在床格上，所以正常情况一步就躺下了；万一不在
    #    床边（热重启后位置漂了），先走回床边再躺。
    if not p["lying"]:
        if can_lie(gs, p["uid"]):
            return {"type": "lie"}
        bx, by = game_map(gs).room_bed(p["room"])
        dx, dy = bx + 0.5 - p["pos"][0], by + 0.5 - p["pos"][1]
        d = math.hypot(dx, dy)
        return {"type": "move", "dx": dx / d, "dy": dy / d} if d > _ARRIVE \
            else {"type": "move", "dx": 0.0, "dy": 0.0}

    # ①′ **噪声：故意乱来**（见文件头「性格」一节）。放在躺下之后、所有正经判据
    #    之前 —— 它就是用来盖掉最优解的。
    rng = _rng_of(gs, p)
    if prof.noise and rng.random() < prof.noise:
        act = _random_action(gs, p, room, prof, rng)
        if act is not None:
            return act

    # ② **共用闸门**（v9，只有 `suite` 图有）：闸门破了就再也没有了，
    #    而且它暴露的是**两个人** —— 比自己的门更该优先。
    gate = _gate_action(gs, p, room, prof)
    if gate is not None:
        return gate

    # ③ 保命优先：门血见底时什么都别谈。**先试升门，升不动才掏钱修补**。
    #
    # 顺序在 v7 调过来了（原来是无条件先修补）。原因：升级气密门是**直接满血 +
    # 上限翻倍**，而修补是「20 生物能换 25% 上限」—— 门越高级修补越划算，但它
    # 只是回血、不抬上限，静默体的伤害曲线一涨就白修。实测（`test/balance_stasis.py`）
    # 旧顺序下 AI 会陷进「一直修补、永远攒不出一次升门」，整局卡在 3 级门 2 级床，
    # 一整局一滴经济都没滚起来。原型的攻略也是这句：「门是血条，每次升级门会恢复
    # 满血，请多加利用」。
    # 空格与火力位在这里就算出来：③.5 起的几条建造判据都要用
    free = buildable_cells(gs, p["uid"])
    free_slot = free[0] if free else None
    cell = _turret_cell(gs, p, room)
    ratio = _door_ratio(room)
    if ratio < prof.patch_ratio + SELF_PATCH_SOFT_EXTRA:
        if room["door_level"] < max_level(DOOR) \
                and _can_upgrade(p, DOOR, room["door_level"]):
            return {"type": "upgrade", "target": DOOR}
        # 修补还能顺带让门脱离「过载」区间（门血 <20% 时静默体攻速 ×2，早修早止血）。
        if p["bio"] >= PATCH_COST_BIO and (
                ratio < prof.patch_ratio
                or _turret_count(room) >= prof.opening_turrets):
            return {"type": "patch", "target_uid": uid}

    # ③.5 **应急力场**（v13）：门血已经掉进触发线、而升级与修补都还没把血拉回来时，
    #      那 3 秒无敌就是「再撑一口气」的第二层防御 —— 它要的是**已经开始挨打**
    #      的那一刻，所以排在保命判据的正后方，而不是等经济起来。
    #      门槛用 `_need` 走执行力（迟钝的性格要攒得更足），合金富余线也照旧要留。
    if prof.controls > 0 and free_slot is not None \
            and _door_ratio(room) < SHIELD_TRIGGER + prof.patch_ratio * 0.5 \
            and p["alloy"] >= _need(BUILD_COST[SHIELD][1], prof) * prof.controls:
        return {"type": "build", "kind": SHIELD, "cell": free_slot}

    # ④ 救队友：合作游戏里人机必须会救场，且要在门破**之前**动手（门破了就只能分遗产了）。
    ally = _weakest_ally(gs, uid, prof)
    if ally is not None and p["bio"] >= SAVE_ALLY_BIO:
        return {"type": "patch", "target_uid": ally}

    # ⑤ 精炼机（「矿工」性格排在炮台之前）。第 6 级往上的升级要搭合金，
    #    **没有精炼机就永远升不动门**；一台 200 生物能，比一次床升级还贵，
    #    所以对多数性格它排在开局炮台与 3 级门之后 —— 前 3 级升级都只吃生物能。
    if prof.refinery and prof.refinery_first and _count(room, REFINERY) == 0:
        if free and p["bio"] >= _need(BUILD_COST[REFINERY][1], prof):
            return {"type": "build", "kind": REFINERY, "cell": free[0]}

    # ⑥ 先铺几座炮台**再**谈升级。顺序在 v2 调过来了：
    #    v1 时代升级是免费的（费用表下标错位，见 `stasis.py` 的注释），先升床升门
    #    不花钱、纯赚；费用修好之后那条顺序会把前 120 生物能全砸进升级，一座塔都
    #    没有 —— 实测 4 人局 188 秒全灭。炮台 8 生物能一座，是单位资源性价比最高的
    #    一笔，而且它同时是**唯一的反制手段**（打退静默体＝拖慢它进化）。
    if _turret_count(room) < prof.opening_turrets and cell is not None \
            and p["bio"] >= _need(_TURRET_COST, prof):
        return {"type": "build", "kind": TURRET, "cell": cell}

    # ⑦ 经济与门推到 `early_level` 级。休眠舱决定后面所有动作的速度，
    #    而 3 级门（800 HP）是「能扛住一次过载」的底线。
    if p["bed_level"] < prof.early_level and _bed_gate_ok(room, p) \
            and _can_upgrade(p, BED, p["bed_level"]):
        return {"type": "upgrade", "target": BED}
    if room["door_level"] < prof.early_level \
            and _can_upgrade(p, DOOR, room["door_level"]):
        return {"type": "upgrade", "target": DOOR}

    # ⑦.5 精炼机（大多数性格走这一支）。位置用**任意空格**，不占 `_turret_cell`
    #      挑出来的火力位 —— 精炼机与修复器不参与开火，摆在门口最好的格子等于
    #      白扔一个射界。
    if prof.refinery and _count(room, REFINERY) == 0:
        if free and p["bio"] >= _need(BUILD_COST[REFINERY][1], prof):
            return {"type": "build", "kind": REFINERY, "cell": free[0]}
    #      修复器：合金富余时补，最多 4 台（引擎的 `MAX_REPAIRS`）。**必须留储备**，
    #      否则它会把合金一路花在修复器上、第 6 级之后的门再也升不动。
    elif free and _count(room, REPAIR) < prof.repair_target \
            and p["alloy"] >= BUILD_COST[REPAIR][1] + _ALLOY_RESERVE:
        return {"type": "build", "kind": REPAIR, "cell": free[0]}

    # ⑦.7 **升级精炼机**（v10）。满级 10 级、30 合金/秒 —— 而**第 6 级往上的每一次
    #      升级都要搭合金**（`upgrade_alloy`），所以合金产率就是整局的天花板：
    #      光造一台 1 级精炼机（0.5 合金/秒）的话，一次 512 生物能的升门要等 32 秒
    #      才买得起那 16 个合金；升到 2 级（2/秒）就是四倍。
    #      **只在炮台已经铺够、且手上还剩一倍价钱时才动手** —— 否则它会像 v2 那条
    #      「不留储备」的坑一样，把钱全砸进经济、一座塔都没有。
    if prof.refinery:
        ref = _lowest_refinery(room)
        if ref is not None and _turret_count(room) >= prof.opening_turrets:
            lv = room["buildings"][ref]["level"]
            cost = upgrade_cost(REFINERY, lv)
            if p["bio"] >= cost * 2 and _can_upgrade(p, REFINERY, lv):
                return {"type": "upgrade", "target": ref}

    # ⑦.6 矿（「矿工」「均衡」性格）：合金富余时把合金换成生物能产率。
    if prof.mines and free:
        mine = _affordable_mine(p)
        if mine is not None:
            return {"type": "build", "kind": mine, "cell": free[0]}

    # ⑦.8 **控场三件**（v13）。它们的定位是「炮台还没成规模之前，先把鬼按住」：
    #      低温阱（256 合金，减 20% 攻速）与束缚弹射器（64，放技能时定它 3 秒）
    #      对**所有**塔都有效（鬼的攻速就是所有塔的有效 DPS 的倒数），所以它们的
    #      性价比在中期最高；粒子加速器（2048）只加**自己舱室**的塔，是后期件。
    #      **都要留合金储备** —— 第 6 级往上的升级本身就要合金，花光了门就升不动。
    if prof.controls > 0 and free_slot is not None:
        want = 1 + (1 if prof.controls > 1.2 else 0)     # 偏控场的性格铺两台低温阱
        if _count(room, CRYO) < want \
                and p["alloy"] >= BUILD_COST[CRYO][1] + _ALLOY_RESERVE:
            return {"type": "build", "kind": CRYO, "cell": free_slot}
        if _count(room, BINDER) == 0 \
                and p["alloy"] >= BUILD_COST[BINDER][1] + _ALLOY_RESERVE \
                and _turret_count(room) >= prof.opening_turrets:
            return {"type": "build", "kind": BINDER, "cell": free_slot}
        if _count(room, ACCEL) == 0 and _turret_count(room) >= 4 \
                and p["alloy"] >= BUILD_COST[ACCEL][1] + _ALLOY_RESERVE:
            return {"type": "build", "kind": ACCEL, "cell": free_slot}

    # ⑧ 把空格铺满（空格就是建筑上限）——**但要给下一级休眠舱与精炼机留够钱**，
    #    并且不超过这个性格的炮台上限。
    #
    # 不留的话人机会把攒下来的钱一张张 8 块钱花在炮台上，永远凑不齐 25/50 的床
    # 升级，于是卡死在 1 级床（1 生物能/秒）—— 收入 1~6、什么都买不起，10 个格子
    # 铺满之后每一轮决策都返回 None（实测 6 人局就是这样 190 秒全灭的）。
    # 「先攒钱升床」是这一局唯一的复利，优先级必须高于「多摆一座塔」。
    # 门槛没过就别留这笔钱了 —— 留着一笔永远花不出去的钱，等于把炮台饿死。
    next_bed = upgrade_cost(BED, p["bed_level"]) \
        if (p["bed_level"] < max_level(BED) and _bed_gate_ok(room, p)) else 0.0
    # **还没精炼机的话，这笔钱也要一起留**（v8 补）。同 `next_bed` 那条教训，而且
    # 更隐蔽：不预留的话它会把每一分钱按 8 块一张花在炮台上，**永远攒不到精炼机的
    # 200** —— 而第 6 级往上的任何升级都要合金，于是输出与门双双卡在 5 级。
    # 实测（v8 之前，2 人局 seed 20261025）：19 座炮台全 5 级、合金恒为 0、生物能
    # 攒到 1.2 万却什么都买不了，鬼在 10~28% 血之间无限循环 —— 一局跑满 30 分钟的
    # 兜底上限都没分出胜负。**这条是「一局没有时长上限」之后必须修的**：
    # 在那之前 6 分钟到点就结束，卡住也看不出来。
    next_refinery = BUILD_COST[REFINERY][1] \
        if (prof.refinery and _count(room, REFINERY) == 0) else 0.0
    if cell is not None and _turret_count(room) < prof.turret_cap \
            and p["bio"] >= _need(_TURRET_COST, prof) + next_bed + next_refinery:
        return {"type": "build", "kind": TURRET, "cell": cell}

    # ⑨⑩ 后期：把床与门拉满（门升级＝直接满血，危急时刻的一次「倾家荡产换口气」）。
    if p["bed_level"] < max_level(BED) and _bed_gate_ok(room, p) \
            and _can_upgrade(p, BED, p["bed_level"]):
        return {"type": "upgrade", "target": BED}
    if room["door_level"] < max_level(DOOR) \
            and _can_upgrade(p, DOOR, room["door_level"]):
        return {"type": "upgrade", "target": DOOR}

    # ⑪ 炮台升级：从等级最低的开始升，先把低级的补齐（升级的边际收益是递减的，
    #    5 级炮 64 DPS 单门就顶 8 门 1 级炮，但 8→16 那一跳最便宜）。
    cell = _lowest_turret(room)
    if cell is not None:
        lv = room["buildings"][cell]["level"]
        if _can_upgrade(p, TURRET, lv):
            return {"type": "upgrade", "target": cell}

    return None


# ── 共用闸门（v9）──────────────────────────────────────────


def _gate_action(gs: dict, p: dict, room: dict, prof: Profile) -> dict | None:
    """照看自己那扇**共用闸门**（只有 `suite` 图有，别的图第一步就返回 None）。

    闸门与房间门有三处不同，所以值得单开一条判据：
    ① **破了就再也没有了**（房间门破了房间会被清空重置，闸门是永久敞开）；
    ② 它挡的是**两个人**，不是一个人；
    ③ 它是**共用的**，所以「该谁掏钱」本身就是这两个玩家要商量的事 ——
       人机不商量，它按自己的性格决定愿不愿意当那个出钱的人（`gate_bias`）。
    """
    m = game_map(gs)
    gid = m.room_gate(p["room"])
    if gid is None:
        return None
    gate = gs["map"]["gates"].get(gid)
    if gate is None or gate["broken"]:
        return None
    lv = gate["door_level"]
    ratio = gate["door_hp"] / door_max(gate)
    # ① 闸门见血了：能升就升（升级即满血），升不动就掏钱修
    if ratio < prof.gate_ratio * prof.gate_bias:
        if lv < max_level(DOOR) and _can_upgrade(p, DOOR, lv):
            return {"type": "upgrade", "target": "gate:" + gid}
        if p["bio"] >= PATCH_COST_BIO * PATCH_COST_MULT:
            return {"type": "patch", "gate": gid}
    # ② 有余力时把闸门拉到自己门同级（它挡在前面，先投它比先投自己划算）
    if lv < room["door_level"] and ratio < 0.95 and _can_upgrade(p, DOOR, lv):
        return {"type": "upgrade", "target": "gate:" + gid}
    return None


# ── 故意乱来（v9）──────────────────────────────────────────


def _random_action(gs: dict, p: dict, room: dict, prof: Profile,
                   rng: random.Random) -> dict | None:
    """**放弃最优解**，从「现在做得了的事」里随便挑一件。

    这是用户明确要的（「有时候甚至可以故意随机操作」）。它会：把钱花在没用的矿上、
    升一座位置很差的炮台、给满血的队友修补、或者干脆发呆 —— 具体挑到什么不重要，
    重要的是**它不再总是那套最优顺序**。

    **但绝不返回非法动作**：每一支都先过引擎的判据（够不够钱、能不能升、门槛过没过），
    因为 `_stasis_bot_think` 是静默吞 `ValueError` 的，一个非法动作等于白扔一轮决策
    （在「一次决策一个动作」下甚至会变成死循环）。
    """
    options: list[dict] = []
    cells = buildable_cells(gs, p["uid"])
    if cells:
        for kind in BUILDABLE:
            res, amount = BUILD_COST[kind]
            if p[res] >= amount:
                options.append({"type": "build", "kind": kind,
                                "cell": rng.choice(cells)})
    for kind, lv in ((BED, p["bed_level"]), (DOOR, room["door_level"])):
        if kind == BED and not _bed_gate_ok(room, p):
            continue
        if lv < max_level(kind) and _can_upgrade(p, kind, lv):
            options.append({"type": "upgrade", "target": kind})
    for key, b in room["buildings"].items():
        if b["level"] < max_level(b["kind"]) \
                and b["kind"] in (TURRET, REFINERY) \
                and _can_upgrade(p, b["kind"], b["level"]):
            options.append({"type": "upgrade", "target": key})
    gid = game_map(gs).room_gate(p["room"])
    if gid and not gs["map"]["gates"][gid]["broken"]:
        lv = gs["map"]["gates"][gid]["door_level"]
        if lv < max_level(DOOR) and _can_upgrade(p, DOOR, lv):
            options.append({"type": "upgrade", "target": "gate:" + gid})
    # 修补：**只列真的掉了血的门**。给满血的门塞钱是最典型的「人机会干的蠢事」，
    # 但引擎会直接拒掉它（「这扇门是满血的」），而 `_stasis_bot_think` 静默吞
    # `ValueError` —— 那不是「打得菜」，是白扔一轮决策。**次优要选合法的那种。**
    if p["bio"] >= PATCH_COST_BIO and _door_ratio(room) < 1.0:
        options.append({"type": "patch", "target_uid": p["uid"]})
    for q in alive_players(gs):
        if q["uid"] != p["uid"] and q["room"] and p["bio"] >= SAVE_ALLY_BIO \
                and _door_ratio(room_of(gs, q["room"])) < 1.0:
            options.append({"type": "patch", "target_uid": q["uid"]})
    # 发呆也是一支：满血的门、买不起的东西、无事可做的局面本来就会返回 None，
    # 让「什么都不做」在随机里占一席，人机才不会显得一直在忙。
    options.append({"type": "idle"})
    return rng.choice(options) if options else None


# ── 小工具 ──────────────────────────────────────────────────


def _door_ratio(room: dict) -> float:
    return room["door_hp"] / door_max(room)


def _can_upgrade(p: dict, kind: str, level: int) -> bool:
    """升得起这一级吗 —— **主资源与合金都要看**。

    只看生物能的话，第 6 级往上的每一次升级都会被 `_spend_upgrade` 以「合金不够」
    拒掉，而 `_stasis_bot_think` 是静默吞 `ValueError` 的 —— 「一次决策一个动作」
    下它就会卡在这个动作上一动不动（同 `_bed_gate_ok` 那条教训）。
    主资源走 `upgrade_res`：**矿那一族升级掏的是合金**（v10 的氪金矿）。
    """
    return p[upgrade_res(kind)] >= upgrade_cost(kind, level) \
        and p["alloy"] >= upgrade_alloy(kind, level)


def _bed_gate_ok(room: dict, p: dict) -> bool:
    """休眠舱能不能升（引擎的硬规则：**间断门槛**，见 `stasis.BED_DOOR_REQ`）。

    **人机的每一条升级判据都必须带上它** —— 漏掉的话它会一直返回
    `{"type":"upgrade","target":"bed"}`，被引擎拒绝、下一轮再返回同一个动作，
    于是**永远卡在这一条**、后面的建造与炮台升级全部轮不到（人机站在那里
    一辈子不铺炮台）。这是「一次决策一个动作」这个设计的固有陷阱：任何
    被静默拒绝的动作都会变成死循环。
    """
    return room["door_level"] >= bed_door_req(p["bed_level"] + 1)


def _weakest_ally(gs: dict, uid: int, prof: Profile) -> int | None:
    """门血比例最低、且已经低到危险线（< `prof.ally_ratio`）的队友；没有就返回 None。

    「谁最危险救谁」而不是「谁先喊救谁」—— 静默体的仇恨值也是「门最弱的优先」，
    所以门血比例最低的那位就是下一个最可能被破门的人。
    """
    weakest: int | None = None
    lowest = prof.ally_ratio
    for q in alive_players(gs):
        if q["uid"] == uid or not q["room"]:
            continue
        r = _door_ratio(room_of(gs, q["room"]))
        if r < lowest:
            weakest, lowest = q["uid"], r
    return weakest


def _turret_cell(gs: dict, p: dict, room: dict) -> str | None:
    """挑一个空格造炮台，**离门口越近越好**。

    炮台射程是**逐塔按等级算的欧氏距离**（`dps_in_range` 逐塔判定），1 级只有 4 格
    —— 摆在舱室深处或角落会打不到门口，所以「靠门摆」不再是摆样子，是实打实的
    火力覆盖。同距离时按格子键排序，保证确定性。
    """
    cells = buildable_cells(gs, p["uid"])
    if not cells:
        return None
    di = game_map(gs).room_door(p["room"])["in"]
    return min(cells, key=lambda k: (
        math.hypot(parse_key(k)[0] - di[0], parse_key(k)[1] - di[1]), k))


def _lowest_turret(room: dict) -> str | None:
    """等级最低的那门哨戒炮所在格；没有炮台或全都满级则返回 None。

    按 (等级, 格键) 取最小 —— 同等级时取格键小的，保证行为确定。
    """
    cands = [(b["level"], k) for k, b in room["buildings"].items() if b["kind"] == TURRET]
    if not cands:
        return None
    lv, cell = min(cands)
    return cell if lv < max_level(TURRET) else None


def _lowest_refinery(room: dict) -> str | None:
    """等级最低的那台精炼机所在格；没有精炼机或全都满级则返回 None。"""
    cands = [(b["level"], k) for k, b in room["buildings"].items()
             if b["kind"] == REFINERY]
    if not cands:
        return None
    lv, cell = min(cands)
    return cell if lv < max_level(REFINERY) else None


def _affordable_mine(p: dict) -> str | None:
    """买得起、且产率最高的那种矿（四座矿是四个独立建筑，没有升级链）。

    **留一倍修复器价当缓冲**：矿花的是合金，而合金是第 6 级之后每一次升级都要
    搭的那份钱 —— 把合金全换成矿，门就永远停在 5 级了（v8 踩过的那个坑的变体）。
    """
    for kind in (DIAMOND, GOLD, SILVER, COPPER):
        res, amount = BUILD_COST[kind]
        if p[res] >= amount + _ALLOY_RESERVE:
            return kind
    return None


def _count(room: dict, kind: str) -> int:
    return sum(1 for b in room["buildings"].values() if b["kind"] == kind)


def _turret_count(room: dict) -> int:
    return _count(room, TURRET)
