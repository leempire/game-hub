"""深渊回声的武器 / 被动道具数据（纯数据，无 IO）。

数值对齐《吸血鬼幸存者》基础 12 把武器与 12 个进化，只换皮肤与名字。
每把武器的等级表存**累计值**，引擎按玩家属性（Might / Area / Cooldown /
Duration / Speed / Amount）在开火时换算。

参考：docs/游戏设计/深渊回声/增强调研.md
"""

from __future__ import annotations

# 玩家属性基准。百分比属性以 1.0 为 100%。
BASE_STATS = {
    "might": 1.0,
    "area": 1.0,
    "cooldown": 1.0,
    "duration": 1.0,
    "speed": 1.0,
    "amount": 0,
    "move_speed": 1.0,
    "magnet": 1.0,
    "luck": 1.0,
    "growth": 1.0,
    "armor": 0,
    "recovery": 0.0,
}

BASE_MAX_HP = 100.0
PLAYER_SPEED = 158.0
MAX_WEAPONS = 6
MAX_PASSIVES = 6
INFINITE_PIERCE = 9999

# 护甲是**百分比减伤**，不是 VS 的固定 −1：固定减伤会把低伤杂兵直接归零
# （等于无敌）。百分比在任何伤害量级下都不会归零，对高伤害同样有效；上限
# 防止后续叠加来源堆穿。5 级局内被动合计 −30%，叠加局外天赋仍留出成长空间。
# 单次伤害保底见 MIN_HIT_DAMAGE（VS 的护甲也不能把伤害减到 0）。
ARMOR_REDUCTION_PER_LEVEL = .06
MAX_ARMOR_REDUCTION = .50
MIN_HIT_DAMAGE = 1.0

# Attractorb 的拾取范围是**乘算**，数值取自官方 wiki。
ATTRACTORB_MULTIPLIERS = (1.5, 1.995, 2.49375, 2.9925, 3.980025)

_DEFAULTS = {
    "damage": 0.0,
    "area": 1.0,
    "amount": 1,
    "cooldown": 1.0,
    "speed": 1.0,
    "duration": 0.0,
    "pierce": 1,
    "interval": 0.0,
    "knockback": 1.0,
    "kb_stack": 0.0,
    "slow": 0.0,
    "slow_ticks": 0,
    "crit": 0.0,
    "crit_mult": 2.0,
    "heal_on_crit": 0.0,
    "preserve": 0.0,
}


def _weapon(wid, name, behavior, base, upgrades, *, evolution=None,
            passive=None, min_cooldown=0.0, cycle_add_duration=True):
    """按「基础值 + 每级增量」生成累计等级表。"""
    levels = []
    current = dict(_DEFAULTS)
    current.update(base)
    levels.append(current)
    for delta in upgrades:
        current = dict(current)
        for key, value in delta.items():
            current[key] = current.get(key, 0) + value
        levels.append(current)
    return {
        "id": wid,
        "name": name,
        "behavior": behavior,
        "levels": tuple(levels),
        "max_level": len(levels),
        "evolution": evolution,
        "passive": passive,
        "min_cooldown": min_cooldown,
        "cycle_add_duration": cycle_add_duration,
    }


def _evolved(wid, name, behavior, stats, *, base, cycle_add_duration=True,
             min_cooldown=0.0):
    current = dict(_DEFAULTS)
    current.update(stats)
    return {
        "id": wid,
        "name": name,
        "behavior": behavior,
        "levels": (current,),
        "max_level": 1,
        "evolution": None,
        "passive": None,
        "min_cooldown": min_cooldown,
        "cycle_add_duration": cycle_add_duration,
        "base": base,
    }


# ── 基础 12 把武器 ───────────────────────────────────────────

WEAPONS = {
    "whip": _weapon(
        "whip", "裂潮鞭", "whip",
        {"damage": 10, "cooldown": 1.35, "interval": .1, "knockback": 1,
         "crit": .20, "crit_mult": 2.0},
        (
            {"amount": 1},
            {"damage": 10, "area": .1},
            {"damage": 10},
            {"damage": 10, "area": .1},
        ),
        evolution="bloody_tear", passive="hollow_heart",
    ),
    "rivet": _weapon(
        "rivet", "铆钉发射器", "wand",
        {"damage": 10, "cooldown": 1.2, "interval": .1, "speed": 1, "pierce": 1},
        (
            {"amount": 1},
            {"amount": 1, "cooldown": -.1},
            {"amount": 1, "damage": 10},
            {"damage": 10, "pierce": 1, "cooldown": -.1},
        ),
        evolution="holy_wand", passive="empty_tome",
    ),
    "harpoon": _weapon(
        "harpoon", "贯流鱼叉", "knife",
        {"damage": 6.5, "cooldown": 1.0, "interval": .1, "speed": 1,
         "pierce": 1, "knockback": .5, "crit": .30, "crit_mult": 3.0},
        (
            {"amount": 1},
            {"amount": 1, "damage": 5},
            {"amount": 1, "interval": -.02, "pierce": 1},
            {"amount": 2, "damage": 5, "interval": -.04, "pierce": 1},
        ),
        evolution="thousand_edge", passive="bracer",
    ),
    "axe": _weapon(
        "axe", "锚刃", "axe",
        {"damage": 20, "cooldown": 4.0, "interval": .2, "speed": 1,
         "pierce": 3, "crit": .30, "crit_mult": 2.0, "area": 1.3},
        (
            {"amount": 1},
            {"damage": 20, "pierce": 2},
            {"amount": 1, "damage": 20},
            {"damage": 20, "pierce": 2},
        ),
        evolution="death_spiral", passive="candelabrador",
    ),
    "cross": _weapon(
        "cross", "回声十字镖", "cross",
        {"damage": 5, "cooldown": 2.0, "interval": .1, "speed": 1,
         "pierce": INFINITE_PIERCE},
        (
            {"damage": 10},
            {"damage": 10, "area": .1, "speed": .25},
            {"amount": 1},
            {"damage": 10, "amount": 1, "area": .1, "speed": .25},
        ),
        evolution="heaven_sword", passive="clover",
    ),
    "bible": _weapon(
        "bible", "环流浮标", "bible",
        {"damage": 10, "cooldown": 3.0, "duration": 3.0, "speed": 1,
         "pierce": INFINITE_PIERCE},
        (
            {"amount": 1},
            {"amount": 1, "area": .25, "speed": .30},
            {"amount": 1, "damage": 10, "area": .25, "speed": .30},
            {"damage": 10, "duration": 1.0},
        ),
        evolution="unholy_vespers", passive="spellbinder",
    ),
    "charge": _weapon(
        "charge", "深压火球", "fire",
        {"damage": 20, "cooldown": 3.0, "interval": .02, "speed": .75,
         "amount": 3, "pierce": 1},
        (
            {"damage": 10},
            {"damage": 20, "speed": .20},
            {"damage": 20, "speed": .20},
            {"damage": 20, "speed": .20},
        ),
        evolution="hellfire", passive="spinach",
    ),
    "ring": _weapon(
        "ring", "荧孢光环", "garlic",
        {"damage": 5, "cooldown": 1.3, "knockback": 0, "kb_stack": .3,
         "slow": .30, "slow_ticks": 30, "pierce": INFINITE_PIERCE},
        (
            {"area": .4, "damage": 2},
            {"cooldown": -.1, "area": .2, "damage": 3},
            {"cooldown": -.1, "area": .2, "damage": 3},
            {"cooldown": -.1, "area": .2, "damage": 2},
        ),
        evolution="soul_eater", passive="pummarola",
    ),
    "spore": _weapon(
        "spore", "腐潮水池", "water",
        {"damage": 10, "cooldown": 4.5, "duration": 2.0, "interval": .3,
         "knockback": 0, "slow": .35, "slow_ticks": 30,
         "pierce": INFINITE_PIERCE},
        (
            {"amount": 1, "area": .2},
            {"amount": 1, "area": .2, "damage": 10, "duration": .5},
            {"amount": 1, "area": .2, "damage": 10, "duration": .25},
            {"area": .2, "damage": 10, "duration": .25},
        ),
        evolution="la_borra", passive="attractorb",
    ),
    "rune": _weapon(
        "rune", "弹跳声呐", "rune",
        {"damage": 10, "cooldown": 3.0, "duration": 2.25, "interval": .2,
         "speed": 1, "pierce": INFINITE_PIERCE},
        (
            {"damage": 5, "speed": .2},
            {"damage": 5, "speed": .2},
            {"amount": 1, "damage": 5, "duration": .25},
            {"amount": 1, "damage": 5, "duration": .75},
        ),
        evolution="no_future", passive="armor",
    ),
    "arc": _weapon(
        "arc", "裂潮电弧", "lightning",
        {"damage": 15, "cooldown": 4.5, "interval": .05, "amount": 2,
         "pierce": INFINITE_PIERCE},
        (
            {"amount": 1},
            {"amount": 1, "damage": 15, "area": 1.0},
            {"amount": 1, "damage": 20, "area": 1.0},
            {"amount": 1, "damage": 15, "area": 1.0},
        ),
        evolution="thunder_loop", passive="duplicator",
    ),
    "pentagram": _weapon(
        "pentagram", "深渊脉冲", "pentagram",
        {"damage": 999999, "cooldown": 90.0, "duration": 1.0,
         "knockback": -2, "preserve": .10, "pierce": INFINITE_PIERCE},
        (
            {"cooldown": -10},
            {"cooldown": -10, "preserve": .15},
            {"cooldown": -5, "preserve": .20},
            {"cooldown": -5, "preserve": .20},
        ),
        evolution="gorgeous_moon", passive="crown", min_cooldown=15.0,
    ),
}

# ── 12 个进化 ───────────────────────────────────────────────

WEAPONS.update({
    "bloody_tear": _evolved(
        "bloody_tear", "血潮鞭", "whip",
        {"damage": 40, "area": 1.3, "amount": 2, "cooldown": 1.35,
         "interval": .1, "knockback": 1, "crit": .10, "crit_mult": 2.0,
         "heal_on_crit": 6},
        base="whip",
    ),
    "holy_wand": _evolved(
        "holy_wand", "圣铆炮", "wand",
        {"damage": 30, "amount": 4, "cooldown": .5, "speed": 2.0,
         "pierce": 2, "interval": .1, "knockback": 1},
        base="rivet",
    ),
    "thousand_edge": _evolved(
        "thousand_edge", "千刃鱼群", "knife",
        {"damage": 16.5, "amount": 6, "cooldown": .35, "speed": 1.5,
         "pierce": 3, "interval": .05, "knockback": .5,
         "crit": .30, "crit_mult": 3.0},
        base="harpoon",
    ),
    "death_spiral": _evolved(
        "death_spiral", "死神漩涡", "death_spiral",
        {"damage": 60, "area": 1.2, "amount": 9, "cooldown": 4.0,
         "speed": .8, "pierce": INFINITE_PIERCE, "interval": .05,
         "knockback": 1, "crit": .30, "crit_mult": 2.0},
        base="axe",
    ),
    "heaven_sword": _evolved(
        "heaven_sword", "天穹剑", "cross",
        {"damage": 77, "area": 1.2, "amount": 1, "cooldown": 3.3,
         "speed": 2.0, "pierce": INFINITE_PIERCE, "interval": .5,
         "knockback": 6, "crit": .10, "crit_mult": 2.5},
        base="cross",
    ),
    "unholy_vespers": _evolved(
        "unholy_vespers", "邪潮圣咏", "bible",
        {"damage": 30, "area": 1.75, "speed": 1.5, "amount": 4,
         "duration": 3.0, "cooldown": 3.0, "knockback": 4,
         "pierce": INFINITE_PIERCE},
        base="bible", cycle_add_duration=False,
    ),
    "hellfire": _evolved(
        "hellfire", "狱火", "fire",
        {"damage": 100, "amount": 2, "cooldown": 3.0, "speed": 1.0,
         "pierce": INFINITE_PIERCE, "interval": .2, "knockback": 1},
        base="charge",
    ),
    "soul_eater": _evolved(
        "soul_eater", "噬魂光环", "garlic",
        {"damage": 20, "area": 3.0, "cooldown": 1.0, "duration": 1.3,
         "knockback": 0, "kb_stack": .3, "slow": .40, "slow_ticks": 30,
         "pierce": INFINITE_PIERCE},
        base="ring",
    ),
    "la_borra": _evolved(
        "la_borra", "拉波拉潮", "la_borra",
        {"damage": 40, "area": 2.0, "amount": 4, "cooldown": 4.0,
         "duration": 4.0, "interval": .3, "knockback": 0,
         "slow": .45, "slow_ticks": 30, "pierce": INFINITE_PIERCE},
        base="spore",
    ),
    "no_future": _evolved(
        "no_future", "未来隔绝", "no_future",
        {"damage": 30, "amount": 1, "cooldown": 1.0, "speed": 2.8,
         "duration": 3.0, "pierce": INFINITE_PIERCE, "interval": 0,
         "knockback": 1},
        base="rune",
    ),
    "thunder_loop": _evolved(
        "thunder_loop", "雷环", "thunder_loop",
        {"damage": 65, "area": 4.0, "amount": 6, "cooldown": 4.5,
         "interval": 0, "knockback": 1, "pierce": INFINITE_PIERCE},
        base="arc",
    ),
    "gorgeous_moon": _evolved(
        "gorgeous_moon", "华月", "gorgeous_moon",
        {"damage": 999999, "area": 1.0, "amount": 1, "cooldown": 60.0,
         "duration": 1.0, "knockback": 1, "preserve": 1.0,
         "pierce": INFINITE_PIERCE},
        base="pentagram", min_cooldown=15.0,
    ),
})

BASE_WEAPON_ORDER = (
    "whip", "rivet", "harpoon", "axe", "cross", "bible",
    "charge", "ring", "spore", "rune", "arc", "pentagram",
)
EVOLVED_WEAPONS = tuple(w for w, spec in WEAPONS.items() if spec.get("base"))

# 12 个基础武器按 VS 的进化配方一一对应。
EVOLUTIONS = {
    spec["evolution"]: (wid, spec["passive"])
    for wid, spec in WEAPONS.items()
    if spec.get("evolution")
}


# ── 13 个战斗向被动道具 ─────────────────────────────────────
#
# 每项用 stat + per_level 描述；Attractorb 是乘算表，单独处理。
# 延后的 3 个（提拉吉苏 / 石面具 / 骷髅狂热）不在本表的候选池里。

PASSIVES = {
    "spinach": {"name": "浓缩燃素", "max_level": 5, "stat": "might",
                "per_level": .10, "detail": "所有武器伤害 +10%/级"},
    "hollow_heart": {"name": "高压气瓶", "max_level": 5, "stat": "max_hp",
                     "per_level": .20, "detail": "最大生命 +20%/级"},
    "empty_tome": {"name": "循环泵", "max_level": 5, "stat": "cooldown",
                   "per_level": -.08, "detail": "武器冷却 -8%/级"},
    "candelabrador": {"name": "扩散声呐", "max_level": 5, "stat": "area",
                      "per_level": .10, "detail": "武器范围 +10%/级"},
    "bracer": {"name": "流体加速器", "max_level": 5, "stat": "speed",
               "per_level": .10, "detail": "投射物速度 +10%/级"},
    "spellbinder": {"name": "延时引信", "max_level": 5, "stat": "duration",
                    "per_level": .10, "detail": "效果持续时间 +10%/级"},
    "duplicator": {"name": "分流阀", "max_level": 2, "stat": "amount",
                   "per_level": 1, "detail": "投射物数量 +1/级"},
    "wings": {"name": "推进鳍", "max_level": 5, "stat": "move_speed",
              "per_level": .10, "detail": "移动速度 +10%/级"},
    "attractorb": {"name": "信标磁锚", "max_level": 5, "stat": "magnet",
                   "per_level": 0, "detail": "拾取范围大幅提升（乘算）"},
    "clover": {"name": "幸运锈币", "max_level": 5, "stat": "luck",
               "per_level": .10, "detail": "幸运 +10%/级"},
    "crown": {"name": "打捞执照", "max_level": 5, "stat": "growth",
              "per_level": .08, "detail": "经验获取 +8%/级"},
    "armor": {"name": "抗压壳", "max_level": 5, "stat": "armor",
              "per_level": ARMOR_REDUCTION_PER_LEVEL,
              "detail": "受到伤害 -6%/级（最多 -30%）"},
    "pummarola": {"name": "自愈膜", "max_level": 5, "stat": "recovery",
                  "per_level": .1, "detail": "每秒回复 +0.1/级"},
}

PASSIVE_ORDER = tuple(PASSIVES)


def passive_bonuses(passives: dict) -> dict:
    """把被动等级换算成玩家属性。返回的是**增量**，不是最终值。"""
    bonuses = {key: 0 for key in ("might", "area", "cooldown", "duration",
                                  "speed", "amount", "move_speed", "luck",
                                  "growth", "armor", "recovery", "max_hp")}
    magnet = 1.0
    for pid, level in passives.items():
        spec = PASSIVES.get(pid)
        if not spec or not level:
            continue
        if pid == "attractorb":
            magnet = ATTRACTORB_MULTIPLIERS[min(level, len(ATTRACTORB_MULTIPLIERS)) - 1]
            continue
        if pid == "hollow_heart":
            bonuses["max_hp"] += BASE_MAX_HP * spec["per_level"] * level
            continue
        bonuses[spec["stat"]] += spec["per_level"] * level
    bonuses["magnet"] = magnet
    return bonuses


def player_stats(passives: dict, talent: dict | None = None) -> dict:
    """最终玩家属性。百分比属性 1.0 = 100%。

    `talent` 是局外天赋的**增量**字典（见 `app/talents.py` 的 `bonuses`）：
    百分比项直接加在 1.0 上，护甲/回复相加，生命百分比只放大基础生命
    （不与局内「高压气瓶」的固定加成连乘），避免局外成长滚雪球。
    """
    bonus = passive_bonuses(passives)
    talent = talent or {}
    return {
        "might": 1.0 + bonus["might"] + talent.get("might", 0.0),
        "area": 1.0 + bonus["area"] + talent.get("area", 0.0),
        "cooldown": max(.10, 1.0 + bonus["cooldown"] + talent.get("cooldown", 0.0)),
        "duration": 1.0 + bonus["duration"] + talent.get("duration", 0.0),
        "speed": 1.0 + bonus["speed"] + talent.get("speed", 0.0),
        "amount": int(bonus["amount"] + talent.get("amount", 0)),
        "move_speed": 1.0 + bonus["move_speed"] + talent.get("move_speed", 0.0),
        "magnet": bonus["magnet"] * (1.0 + talent.get("magnet", 0.0)),
        "luck": 1.0 + bonus["luck"] + talent.get("luck", 0.0),
        "growth": 1.0 + bonus["growth"] + talent.get("growth", 0.0),
        "armor": min(MAX_ARMOR_REDUCTION,
                     bonus["armor"] + talent.get("armor", 0.0)),
        "recovery": bonus["recovery"] + talent.get("recovery", 0.0),
        "max_hp": (BASE_MAX_HP * (1.0 + talent.get("max_hp", 0.0))
                   + bonus["max_hp"]),
    }


def weapon_stats(wid: str, level: int, stats: dict) -> dict:
    """把等级表里的基础值乘上玩家属性，返回本次开火的最终数值。"""
    spec = WEAPONS[wid]
    index = max(0, min(level, spec["max_level"]) - 1)
    out = dict(spec["levels"][index])
    out["damage"] *= stats["might"]
    out["area"] *= stats["area"]
    out["duration"] *= stats["duration"]
    out["speed"] *= stats["speed"]
    out["amount"] = int(out["amount"]) + int(stats["amount"])
    cooldown = out["cooldown"] * stats["cooldown"]
    if spec["min_cooldown"]:
        cooldown = max(spec["min_cooldown"], cooldown)
    out["cooldown"] = cooldown
    out["crit"] = min(1.0, out["crit"] * stats["luck"])
    return out


def xp_step(level: int, *, milestone_scale: float = 1.0) -> int | float:
    """单级经验门槛，milestone_scale 仅缩放 20、40 级的额外门槛。"""
    nxt = level + 1
    if nxt <= 19:
        return 10 * nxt - 15
    if nxt == 20:
        return 185 + 600 * milestone_scale
    if nxt <= 39:
        return 13 * nxt - 75
    if nxt == 40:
        return 445 + 2400 * milestone_scale
    return 16 * nxt - 195


def xp_total(level: int) -> int | float:
    """从 1 级累计到 `level` 级所需经验（测试与文档用）。"""
    return sum(xp_step(level) for level in range(1, level))