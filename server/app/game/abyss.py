"""Authoritative fixed-step simulation for 深渊回声.

武器 / 被动 / 进化 / 经验曲线的数值来自 `abyss_data.py`，按《吸血鬼幸存者》
基础 12 把武器对齐；这里只负责推进世界、索敌、伤害与掉落。

**为什么这个文件超过 500 行**：12 把基础武器 + 12 个进化 + 13 个被动共享同一套
状态机，12 种武器行为（鞭/魔杖/小刀/斧/十字/圣经/火球/光环/水域/反弹/落雷/清屏）
与弹道、区域、环绕、宝箱、升级候选都在这里分发；纯数值表已经拆到
`abyss_data.py`。与 `stasis.py` / `uno.py` 的引擎文件同一量级，暂不为了行数
硬拆成互相 import 的碎片。
"""
import math
import random

from app.game import abyss_data as data

WIDTH, HEIGHT = 2160, 1500
MAP_SIZES = {"small": (1620, 1125), "medium": (WIDTH, HEIGHT),
             "large": (3240, 2250), "endless": (None, None)}
LEGACY_MAP_SIZES = {"small": (1080, 750), "medium": (1440, 1000),
                    "large": (2160, 1500), "endless": (None, None)}
CACHE_PICKUP_RADIUS = 36
CACHE_XP = 12
CACHE_SALVAGE = 100
PURSUIT_RADIUS = 240
PURSUIT_NEAR_RADIUS = 205
PURSUIT_KINDS = {"elite", "warden"}
WALL_INSET = 22
STEP = .05
DEFAULT_DURATION = 720
FULL_MILESTONE_DURATION = 1800
FRAMES_PER_SECOND = 20
UPGRADE_CHOICE_TICKS = 10 * FRAMES_PER_SECOND
RESCUE_RADIUS = 52
RESCUE_TICKS = 2 * FRAMES_PER_SECOND
ORBIT_HIT_TICKS = 34
MAX_LOOT = 160
BASE_MAGNET_RADIUS = 58
PROJECTILE_IMPACT_TAIL = 4
WARDEN_IMPACT_TICKS = 8
WARDEN_RECOVERY_TICKS = 74
WARDEN_ENRAGED_RECOVERY_TICKS = 54
WARDEN_HP_BY_DURATION = {1200: 1.35, 1800: 2.0}

# 刷怪密度：《吸血鬼幸存者》按「最低同屏数 + 补刷」维持怪潮，而不是固定每秒
# 吐固定数量。这里用「基础预算 + 低于同屏下限时补刷 + 同屏上限」近似：玩家清得
# 越快补得越快，即使成型 build 也始终有一片怪可割。数值见
# docs/游戏设计/深渊回声/设计.md。
SPAWN_PHASES = ((1.4, 12, 35), (3.2, 30, 70), (5.5, 55, 100), (10.0, 90, 150))
BOSS_SPAWN = (6.0, 70, 120)
SPAWN_TOPUP_RATE = 24.0
SPAWN_DENSITY_PER_PLAYER = .2
SPAWN_HARD_CAP = 320

# 击退按 VS 的「反推一小段」实现：距离取敌人自身移速 × 武器击退 × 受击者的
# 击退承伤倍率；精英与守卫只吃 20%。荧孢光环每次命中叠加击退承伤（VS 的大蒜）。
STAGGER_TICKS = 3
KNOCKBACK_CAP = 60.0
KNOCKBACK_TAKEN_MAX = 3.0
KNOCKBACK_RESIST = {"elite": .2, "warden": .2}

# 减速是 VS 基础 12 把之外唯一新增的状态：VS 用「冻结/击退抗性削弱」表示控制，
# 本作没有冻结，改由荧孢光环与腐潮水池施加减速。精英与守卫吃一半效果。
SLOW_RESIST = {"elite": .5, "warden": .5}

# 高密度时把 10 Hz 快照降到约 6.7 Hz：带宽和客户端解析量下降三分之一，客户端
# 仍按 frame 插值，画面不会跳。低于 100 只时保持 10 Hz。
DENSE_SNAPSHOT_THRESHOLD = 100
DENSE_SNAPSHOT_INTERVAL = 3
SNAPSHOT_INTERVAL = 2

ENEMIES = {
    # 血量按「VS 前几分钟的杂兵」重定：Magic Wand 10 点伤害能一枪打死漂囊，
    # 裂鳍鳗与锈甲蟹各多挨一枪。旧版血条配的是 0.6 秒冷却的铆钉，换成 VS 的
    # 1.2 秒魔杖后必须同步下调，否则开场 30 秒就会被怪潮淹没。
    "drifter": (10, 56, 11), "eel": (15, 105, 10), "crab": (22, 43, 16),
    "needlefish": (8, 125, 8), "jelly": (26, 58, 19), "angler": (38, 87, 19),
    "elite": (450, 83, 24), "warden": (1350, 84, 28),
}

XP_UNITS = {"warden": 30, "elite": 18, "crab": 2, "jelly": 2, "angler": 3}
# 怪潮密度大幅提高后，接触伤害同步下调：否则「同屏 100+」等价于每秒掉 2~5 点，
# 玩家没有发挥空间。基数 2，高威胁怪仍保留更高值。
CONTACT_DAMAGE = {"needlefish": 1, "jelly": 3, "angler": 5, "warden": 12}
WAVE_KINDS = ("drifter", "eel", "crab", "needlefish", "jelly", "angler")
WAVE_WEIGHTS = (
    (17, 0, 0, 3, 0, 0),
    (10, 4, 4, 4, 2, 0),
    (7, 5, 5, 4, 4, 2),
    (5, 5, 5, 4, 5, 4),
)


def create(players, seed, duration=DEFAULT_DURATION, xp_pace=1.0, map_size="medium"):
    width, height = MAP_SIZES[map_size]
    duration = float(duration)
    milestone_scale = (duration - DEFAULT_DURATION) / (FULL_MILESTONE_DURATION - DEFAULT_DURATION)
    gs = {
        "rng": random.Random(seed), "frame": 0, "elapsed": 0,
        "duration": duration, "xp_pace": float(xp_pace), "map_size": map_size,
        "milestone_scale": max(0.0, min(1.0, milestone_scale)),
        "warden_hp_scale": WARDEN_HP_BY_DURATION.get(duration, 1.0),
        "level": 1, "xp": 0, "growth_boost": 1.0,
        "players": {},
        "count": len(players), "spawn_budget": .85, "spawn_cursor": 0,
        "next_id": 0,
        "enemies": [], "loot": [], "effects": [], "fields": [],
        "shots": [], "strikes": [], "chests": [], "chest_events": [],
        "spawned": set(), "boss": False, "boss_dead": False,
        "pick_until": 0, "kills": 0, "salvage": 0, "result": None,
    }
    for seat, player in enumerate(players):
        # 局外天赋由房间层换算成纯字典注入（引擎不认识数据库）。bot 没有天赋。
        talent = player.get("talent_bonus") or {}
        p = {
            "id": player["id"],
            "name": player.get("nickname") or player["username"],
            "x": (width or WIDTH) / 2 + 36 * (seat - (len(players) - 1) / 2),
            "y": (height or HEIGHT) / 2, "angle": 0, "hp": data.BASE_MAX_HP,
            "max_hp": data.BASE_MAX_HP,
            "invincible": round(float(talent.get("start_invincible", 0)) * FRAMES_PER_SECOND),
            "dx": 0, "dy": 0, "input_until": 0, "down": 0, "rescue": 0,
            "out": False, "departed": False,
            "weapons": {"rivet": min(
                data.WEAPONS["rivet"]["max_level"],
                1 + int(talent.get("start_weapon_level", 0)),
            )}, "passives": {}, "cycles": {},
            "orbits": {}, "orbit_hits": {}, "choices": [],
            "talent": dict(talent),
            "stats": dict(data.BASE_STATS), "kills": 0, "damage_dealt": 0.0, "xp_collected": 0, "rescues": 0, "last_crit_heal": -20,
        }
        _refresh_stats(p)
        # 应急储备是「全队各自的开局打捞分」，直接加到共享打捞池。
        gs["salvage"] += int(talent.get("start_salvage", 0))
        gs["players"][p["id"]] = p
    gs["caches"] = _exploration_caches(gs, seed, width or WIDTH, height or HEIGHT)
    return gs


def move(gs, uid, dx, dy, *, preserve_tap=False):
    p = gs["players"].get(uid)
    if not p or not p["hp"] or gs["result"] or gs["pick_until"]:
        return
    for value in (dx, dy):
        if type(value) not in (int, float) or not -1 <= value <= 1:
            raise ValueError("移动方向无效")
    frame = gs["frame"]
    if not (dx or dy):
        if preserve_tap and p.get("input_started") == frame:
            p["tap_release"] = True
            p["input_until"] = frame + 2
        else:
            stop(gs, uid)
        return

    if p.pop("tap_release", False) or not (p["dx"] or p["dy"]) or p["input_until"] <= frame:
        p["input_started"] = frame
    magnitude = max(1, math.hypot(dx, dy))
    p.update(dx=dx / magnitude, dy=dy / magnitude, input_until=frame + 9)


def stop(gs, uid):
    p = gs["players"].get(uid)
    if p:
        p.update(dx=0, dy=0, input_until=0)
        p.pop("input_started", None)
        p.pop("tap_release", None)


def depart(gs, uid):
    stop(gs, uid)
    p = gs["players"].get(uid)
    if p:
        p.update(hp=0, out=True, departed=True, choices=[], down=0)
        p["orbits"].clear()
        p["orbit_hits"].clear()
        gs["shots"] = [shot for shot in gs["shots"] if shot["owner"] != uid]
        gs["fields"] = [field for field in gs["fields"] if field["owner"] != uid]
        gs["strikes"] = [strike for strike in gs["strikes"] if strike["owner"] != uid]
    if not any(p["hp"] for p in gs["players"].values()):
        gs["result"] = "abandoned"


def _gap(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _gap_xy(a, x, y):
    return math.hypot(a["x"] - x, a["y"] - y)


def _bounds(gs):
    # Persisted games from before exploration keep their original playing area.
    sizes = MAP_SIZES if "caches" in gs else LEGACY_MAP_SIZES
    return sizes[gs.get("map_size", "medium")]


def _clamp(value, limit):
    if limit is None:
        return value
    return max(WALL_INSET, min(limit - WALL_INSET, value))


def _id(gs):
    gs["next_id"] += 1
    return gs["next_id"]


def _scale(gs):
    return 1 + .45 * (gs["count"] - 1)


def threshold(gs):
    # Pre-existing saves continue with the originally advertised threshold.
    base = data.xp_step(gs["level"], milestone_scale=gs.get("milestone_scale", 1.0))
    return max(1, math.ceil(base * _scale(gs) * gs["xp_pace"]))


def snapshot_interval(gs):
    """Snapshot cadence in simulation frames (20 Hz tick)."""
    return DENSE_SNAPSHOT_INTERVAL if len(gs["enemies"]) >= DENSE_SNAPSHOT_THRESHOLD else SNAPSHOT_INTERVAL


def _refresh_stats(p):
    old_max = p.get("max_hp", data.BASE_MAX_HP)
    stats = data.player_stats(p["passives"], p.get("talent"))
    p["stats"] = stats
    p["max_hp"] = stats["max_hp"]
    if p["hp"] > 0:
        p["hp"] = min(p["max_hp"], p["hp"] + max(0.0, p["max_hp"] - old_max))
    else:
        p["hp"] = 0


def _spawn(gs, kind, player):
    width, height = _bounds(gs)
    rng = gs["rng"]
    alive = [member for member in gs["players"].values() if member["hp"]]
    safe_gap = 180 if kind in ("elite", "warden") else 125
    best = None
    best_gap = -1
    for _ in range(6):
        angle = rng.uniform(0, math.tau)
        distance = rng.uniform(235, 415)
        dx, dy = distance * math.cos(angle), distance * math.sin(angle)
        x = player["x"] + dx
        y = player["y"] + dy
        if x != _clamp(x, width):
            x = player["x"] - dx
        if y != _clamp(y, height):
            y = player["y"] - dy
        site = {"x": _clamp(x, width), "y": _clamp(y, height)}
        gap = min((_gap(site, member) for member in alive), default=safe_gap)
        if gap > best_gap:
            best, best_gap = site, gap
        if gap >= safe_gap:
            break
    hp, speed, radius = ENEMIES[kind]
    if kind in ("elite", "warden"):
        team_scale = 1 + .60 * (gs["count"] - 1)
        duration_scale = gs.get("warden_hp_scale", 1.0) if kind == "warden" else 1.0
        hp = round(hp * team_scale * duration_scale)
    else:
        interval = 100 * gs["duration"] / DEFAULT_DURATION
        growth = int(gs["elapsed"] * STEP // interval) if interval > 0 else 0
        hp += 4 * min(6, growth)
    gs["enemies"].append({
        "id": _id(gs), "kind": kind, **best, "hp": hp, "max_hp": hp,
        "angle": 0, "speed": speed, "radius": radius,
        "kb_taken": 1.0, "stagger_until": 0,
        "slow_until": 0, "slow_factor": 1.0,
    })


def _effect(gs, weapon, kind="ring", life=7, **shape):
    # These positions drive only SVG effects. Match the whole-unit precision of
    # the rest of the battlefield snapshot without rounding simulation state.
    for key in ("x", "y", "r"):
        if key in shape:
            shape[key] = round(shape[key])
    if "points" in shape:
        shape["points"] = [[round(x), round(y)] for x, y in shape["points"]]
    gs["effects"].append({
        "id": _id(gs), "weapon": weapon, "kind": kind,
        "start": gs["frame"], "until": gs["frame"] + life, **shape,
    })


def _defeat_effect(gs, enemy):
    boss = enemy["kind"] in ("elite", "warden")
    if not boss:
        frame, count = gs.get("defeat_bursts", (-1, 0))
        count = count if frame == gs["frame"] else 0
        if count >= 8:
            return
        gs["defeat_bursts"] = (gs["frame"], count + 1)
    _effect(gs, "defeat", "defeat", life=12 if boss else 7,
            x=enemy["x"], y=enemy["y"], r=enemy["radius"] + 4, target=enemy["id"])


def _spawn_chest(gs, x, y):
    gs["chests"].append({"id": _id(gs), "x": x, "y": y})


def _exploration_caches(gs, seed, width, height):
    # At least one destination in each quadrant. Layout randomness is independent
    # of combat rolls so a change to the cache layout does not change enemy spawns.
    sites = [(.17, .21), (.82, .20), (.79, .83), (.19, .82)]
    kinds = ["xp", "gear", "repair", "salvage"]
    rng = random.Random(seed)
    rng.shuffle(kinds)
    if gs["map_size"] != "small":
        sites.extend([(.5, .11), (.5, .89)])
        kinds.extend(["xp", "repair"])
    if gs["map_size"] in ("large", "endless"):
        sites.extend([(.10, .5), (.90, .5)])
        kinds.extend(["gear", "salvage"])
    return [{"id": -1 - index, "kind": kind,
             "x": round((x + rng.uniform(-.025, .025)) * width),
             "y": round((y + rng.uniform(-.025, .025)) * height)}
            for index, ((x, y), kind) in enumerate(zip(sites, kinds))]


def _drop_loot(gs, enemy, units):
    if units <= 0:
        return
    loot = gs["loot"]
    if len(loot) >= MAX_LOOT:
        closest = min(loot, key=lambda item: _gap(item, enemy))
        if _gap(closest, enemy) <= BASE_MAGNET_RADIUS:
            closest["xp"] += units
            return
        # Free a slot in the older drops so distant players can collect at the kill site.
        oldest = loot.pop(0)
        neighbor = min(loot, key=lambda item: _gap(item, oldest))
        neighbor["xp"] += oldest["xp"]
    loot.append({"id": _id(gs), "x": enemy["x"], "y": enemy["y"], "xp": units})


def _damage(gs, owner, enemy, damage, *, src=None, knockback=0.0, crit=False,
            heal=0.0, drop_chance=1.0, xp_mult=1, kb_stack=0.0,
            slow=0.0, slow_ticks=0):
    """对单个敌人结算伤害。返回是否击杀。"""
    if not enemy or enemy["hp"] <= 0:
        return False
    if owner and "damage_dealt" in owner:
        owner["damage_dealt"] += min(damage, enemy["hp"])
    was_enraged = enemy["kind"] == "warden" and _warden_enraged(enemy)
    enemy["hp"] -= damage
    if enemy["kind"] == "warden" and not was_enraged and _warden_enraged(enemy) and "next_attack" in enemy:
        remaining = enemy["next_attack"] - (WARDEN_RECOVERY_TICKS - WARDEN_ENRAGED_RECOVERY_TICKS)
        enemy["next_attack"] = max(gs["elapsed"] + 1, remaining)
    if kb_stack:
        enemy["kb_taken"] = min(KNOCKBACK_TAKEN_MAX, enemy.get("kb_taken", 1.0) + kb_stack)
    if slow > 0 and slow_ticks:
        resist = SLOW_RESIST.get(enemy["kind"], 1.0)
        factor = 1.0 - slow * resist
        expired = gs["elapsed"] >= enemy.get("slow_until", 0)
        if expired or factor < enemy.get("slow_factor", 1.0):
            enemy["slow_factor"] = factor
        enemy["slow_until"] = max(enemy.get("slow_until", 0), gs["elapsed"] + slow_ticks)
    if knockback > 0 and src is not None:
        angle = math.atan2(enemy["y"] - src[1], enemy["x"] - src[0])
        push = min(KNOCKBACK_CAP, enemy["speed"] * knockback * .25
                   * enemy.get("kb_taken", 1.0))
        if enemy["kind"] in KNOCKBACK_RESIST:
            push *= KNOCKBACK_RESIST[enemy["kind"]]
        width, height = _bounds(gs)
        enemy["x"] = _clamp(enemy["x"] + push * math.cos(angle), width)
        enemy["y"] = _clamp(enemy["y"] + push * math.sin(angle), height)
        enemy["stagger_until"] = max(enemy.get("stagger_until", 0), gs["elapsed"] + STAGGER_TICKS)
    if crit and heal and owner and owner["hp"] > 0:
        if gs["elapsed"] >= owner["last_crit_heal"] + FRAMES_PER_SECOND:
            owner["hp"] = min(owner["max_hp"], owner["hp"] + heal)
            owner["last_crit_heal"] = gs["elapsed"]
            _effect(gs, "bloody_tear", "heal", life=10, x=owner["x"], y=owner["y"], r=26)
    if enemy["hp"] > 0:
        return False
    _defeat_effect(gs, enemy)
    gs["kills"] += 1
    if owner:
        owner["kills"] += 1
    if gs["rng"].random() < drop_chance:
        _drop_loot(gs, enemy, XP_UNITS.get(enemy["kind"], 1) * xp_mult)
    if enemy["kind"] in ("elite", "warden"):
        _spawn_chest(gs, enemy["x"], enemy["y"])
    if enemy["kind"] == "warden":
        gs["boss_dead"] = True
    return True


def _roll(gs, stats):
    return stats["crit_mult"] if stats["crit"] > 0 and gs["rng"].random() < stats["crit"] else 1.0


def _area_damage(gs, owner, x, y, radius, damage, *, weapon, knockback=0.0,
                 drop_chance=1.0, xp_mult=1, stats=None, crit=True,
                 kb_stack=0.0, slow=0.0, slow_ticks=0):
    stats = stats or {"crit": 0, "crit_mult": 1}
    source = (x, y)
    heal = stats.get("heal_on_crit", 0) if crit else 0
    for enemy in gs["enemies"]:
        if enemy["hp"] <= 0:
            continue
        dx = enemy["x"] - x
        reach = radius + enemy["radius"]
        if dx < -reach or dx > reach:
            continue
        dy = enemy["y"] - y
        if dx * dx + dy * dy <= reach * reach:
            mult = _roll(gs, stats) if crit else 1.0
            _damage(gs, owner, enemy, damage * mult, src=source,
                    knockback=knockback, crit=mult > 1, heal=heal,
                    drop_chance=drop_chance, xp_mult=xp_mult,
                    kb_stack=kb_stack, slow=slow, slow_ticks=slow_ticks)
    _effect(gs, weapon, "burst", life=6, x=x, y=y, r=radius)


def _shot(gs, owner, weapon, stats, x, y, vx, vy, *, kind, life, delay=0,
          pierce=None, radius=12.0, bounce=False, gravity=0.0,
          explode=0.0, return_at=None, retarget=False):
    angle = math.atan2(vy, vx) if vx or vy else 0.0
    gs["shots"].append({
        "id": _id(gs), "owner": owner["id"], "weapon": weapon, "kind": kind,
        "x": x, "y": y, "vx": vx, "vy": vy, "angle": angle,
        "damage": stats["damage"], "pierce": data.INFINITE_PIERCE if pierce is None else pierce,
        "r": radius, "knockback": stats["knockback"],
        "kb_stack": stats.get("kb_stack", 0.0),
        "slow": stats.get("slow", 0.0), "slow_ticks": stats.get("slow_ticks", 0),
        "crit": stats["crit"], "crit_mult": stats["crit_mult"],
        "heal_on_crit": stats.get("heal_on_crit", 0),
        "life": life, "delay": delay, "age": 0, "hit": set(),
        "bounce": bounce, "gravity": gravity, "explode": explode,
        "return_at": return_at, "returning": False, "retarget": retarget,
    })


def _reachable_enemies(enemies, origin, speed, radius, flight_ticks):
    flight_time = flight_ticks * STEP
    return [enemy for enemy in enemies
            if _gap(enemy, origin) <= (speed + enemy["speed"]) * flight_time + radius + enemy["radius"]]


def _retarget(gs, shot):
    """多段发射的后续弹丸在真正出手时重新锁定最近敌人（VS 的行为）。"""
    owner = gs["players"].get(shot["owner"])
    if not owner:
        return False
    enemies = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
    speed = math.hypot(shot["vx"], shot["vy"]) or 320
    shot["x"], shot["y"] = owner["x"], owner["y"]
    if _bounds(gs)[0] is None:
        travel = (shot["return_at"] - 1) if shot["return_at"] is not None else shot["life"]
        enemies = _reachable_enemies(enemies, owner, speed, shot["r"], travel)
        if not enemies:
            return False
    if not enemies:
        return True
    target = min(enemies, key=lambda enemy: _gap(enemy, owner))
    angle = math.atan2(target["y"] - owner["y"], target["x"] - owner["x"])
    shot["vx"], shot["vy"] = math.cos(angle) * speed, math.sin(angle) * speed
    shot["angle"] = angle
    return True


def _frames(seconds):
    return max(1, round(seconds / STEP))


TARGETED_PROJECTILES = {
    "wand": (320, 1.4, 8, None),
    "cross": (280, 1.9, 18, .6),
    "fire": (240, 1.8, 16, None),
}


# ── 武器开火 ────────────────────────────────────────────────


def _fire(gs, p, weapon, level):
    spec = data.WEAPONS[weapon]
    behavior = spec["behavior"]
    enemies = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
    projectile = TARGETED_PROJECTILES.get(behavior)
    if projectile and not enemies:
        return
    width, height = _bounds(gs)
    stats = data.weapon_stats(weapon, level, p["stats"])
    if projectile:
        base_speed, flight_seconds, base_radius, return_seconds = projectile
        speed = base_speed * stats["speed"]
        life = _frames(flight_seconds)
        radius = base_radius * stats["area"]
        return_at = None if return_seconds is None else _frames(return_seconds)
        if width is None:
            flight_ticks = life if return_at is None else return_at - 1
            enemies = _reachable_enemies(enemies, p, speed, radius, flight_ticks)
            if not enemies:
                return
    interval = _frames(stats["interval"])
    amount = max(1, int(stats["amount"]))
    duration = _frames(stats["duration"])
    cooldown = _frames(stats["cooldown"])
    if behavior == "bible" and spec["cycle_add_duration"]:
        p["cycles"][weapon] = gs["elapsed"] + cooldown + duration
    else:
        p["cycles"][weapon] = gs["elapsed"] + cooldown

    if behavior == "whip":
        reach = 150 * stats["area"]
        half = 45 * stats["area"]
        for index in range(amount):
            offset = (index - (amount - 1) / 2) * 55 * stats["area"]
            for enemy in enemies:
                dx = enemy["x"] - p["x"]
                dy = enemy["y"] - p["y"]
                if abs(dy - offset) <= half and abs(dx) <= reach:
                    mult = _roll(gs, stats)
                    _damage(gs, p, enemy, stats["damage"] * mult, src=(p["x"], p["y"]),
                            knockback=stats["knockback"], crit=mult > 1,
                            heal=stats.get("heal_on_crit", 0))
            _effect(gs, weapon, "whip", life=7, x=p["x"], y=p["y"] + offset, r=reach)

    elif behavior == "wand":
        target = min(enemies, key=lambda enemy: _gap(enemy, p))
        angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
        for index in range(amount):
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="bolt", life=life, delay=index * interval,
                  pierce=int(stats["pierce"]), radius=radius,
                  retarget=True)

    elif behavior == "knife":
        for index in range(amount):
            angle = p["angle"]
            speed = 420 * stats["speed"]
            _shot(gs, p, weapon, stats,
                  p["x"] + math.cos(angle) * 18, p["y"] + math.sin(angle) * 18,
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="knife", life=_frames(1.0), delay=index * interval,
                  pierce=int(stats["pierce"]), radius=7 * stats["area"])

    elif behavior == "axe":
        for index in range(amount):
            vx = gs["rng"].uniform(-70, 70) * stats["speed"]
            vy = -300 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"], vx, vy,
                  kind="axe", life=_frames(1.5), delay=index * interval,
                  pierce=int(stats["pierce"]), radius=20 * stats["area"],
                  gravity=420)

    elif behavior == "cross":
        target = min(enemies, key=lambda enemy: _gap(enemy, p))
        angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
        for index in range(amount):
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="cross", life=life, delay=index * interval,
                  pierce=data.INFINITE_PIERCE, radius=radius,
                  return_at=return_at, retarget=True)

    elif behavior == "bible":
        p["orbits"][weapon] = {
            "until": gs["elapsed"] + duration,
            "angle": p["orbits"].get(weapon, {}).get("angle", 0),
            "count": amount, "radius": 80 * stats["area"],
            "hit_r": 17 * stats["area"], "speed": 2.2,
            "damage": stats["damage"], "knockback": stats["knockback"],
            "crit": stats["crit"], "crit_mult": stats["crit_mult"],
            "heal_on_crit": stats.get("heal_on_crit", 0),
        }

    elif behavior == "fire":
        for index in range(amount):
            target = gs["rng"].choice(enemies)
            angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="fire", life=life, delay=index * interval,
                  pierce=int(stats["pierce"]), radius=radius)

    elif behavior == "garlic":
        radius = 65 * stats["area"]
        hits = 0
        for enemy in enemies:
            if _gap_xy(enemy, p["x"], p["y"]) <= radius + enemy["radius"]:
                mult = _roll(gs, stats)
                if _damage(gs, p, enemy, stats["damage"] * mult, src=(p["x"], p["y"]),
                           knockback=stats["knockback"], crit=mult > 1,
                           kb_stack=stats["kb_stack"], slow=stats["slow"],
                           slow_ticks=stats["slow_ticks"]):
                    hits += 1
        if weapon == "soul_eater" and hits:
            p["hp"] = min(p["max_hp"], p["hp"] + min(3.0, hits * .5))
        _effect(gs, weapon, "aura", life=4, x=p["x"], y=p["y"], r=radius)

    elif behavior == "water":
        for index in range(amount):
            angle = gs["rng"].uniform(0, math.tau)
            distance = gs["rng"].uniform(70, 230)
            x = _clamp(p["x"] + math.cos(angle) * distance, width)
            y = _clamp(p["y"] + math.sin(angle) * distance, height)
            radius = 60 * stats["area"]
            gs["fields"].append({
                "id": _id(gs), "owner": p["id"], "weapon": weapon,
                "x": x, "y": y, "r": radius,
                "next": gs["elapsed"] + index * interval + 10,
                "left": max(1, int(stats["duration"] / .5)),
                "tick": 10, "damage": stats["damage"],
                "knockback": stats["knockback"],
                "slow": stats["slow"], "slow_ticks": stats["slow_ticks"],
                "move": False, "grow": 0, "max_r": radius,
            })

    elif behavior in ("rune", "no_future"):
        for index in range(amount):
            angle = gs["rng"].uniform(0, math.tau)
            speed = 260 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="bounce", life=duration, delay=index * interval,
                  pierce=data.INFINITE_PIERCE, radius=14 * stats["area"],
                  bounce=True, explode=30 if behavior == "no_future" else 0)

    elif behavior in ("lightning", "thunder_loop"):
        radius = 55 * stats["area"]
        for index in range(amount):
            if enemies:
                target = gs["rng"].choice(enemies)
                x, y = target["x"], target["y"]
            else:
                angle = gs["rng"].uniform(0, math.tau)
                distance = gs["rng"].uniform(40, 260)
                x = _clamp(p["x"] + math.cos(angle) * distance, width)
                y = _clamp(p["y"] + math.sin(angle) * distance, height)
            gs["strikes"].append({
                "due": gs["elapsed"] + index * interval,
                "owner": p["id"], "weapon": weapon, "x": x, "y": y,
                "r": radius, "damage": stats["damage"],
                "knockback": stats["knockback"],
                "double": behavior == "thunder_loop",
            })

    elif behavior in ("pentagram", "gorgeous_moon"):
        gorgeous = behavior == "gorgeous_moon"
        for enemy in list(gs["enemies"]):
            _damage(gs, p, enemy, stats["damage"],
                    drop_chance=1.0 if gorgeous else stats["preserve"],
                    xp_mult=2 if gorgeous else 1)
        if gorgeous:
            for item in gs["loot"]:
                _grant_xp(gs, p, item["xp"])
            gs["loot"] = []
        _effect(gs, weapon, "nova", life=14, x=p["x"], y=p["y"], r=1400)

    elif behavior == "death_spiral":
        for index in range(amount):
            angle = index * math.tau / amount
            speed = 180 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="spiral", life=_frames(2.0),
                  pierce=data.INFINITE_PIERCE, radius=24 * stats["area"])

    elif behavior == "la_borra":
        for index in range(amount):
            angle = gs["rng"].uniform(0, math.tau)
            distance = gs["rng"].uniform(70, 230)
            x = _clamp(p["x"] + math.cos(angle) * distance, width)
            y = _clamp(p["y"] + math.sin(angle) * distance, height)
            radius = 45 * stats["area"]
            gs["fields"].append({
                "id": _id(gs), "owner": p["id"], "weapon": weapon,
                "x": x, "y": y, "r": radius,
                "next": gs["elapsed"] + index * interval + 10,
                "left": max(1, int(stats["duration"] / .5)),
                "tick": 10, "damage": stats["damage"],
                "knockback": stats["knockback"],
                "slow": stats["slow"], "slow_ticks": stats["slow_ticks"],
                "move": True, "grow": (90 * stats["area"] - radius) / duration,
                "max_r": 90 * stats["area"],
            })


def _grant_xp(gs, p, units):
    gain = max(1, round(units * p["stats"]["growth"] * gs["growth_boost"]))
    gs["xp"] += gain
    p["xp_collected"] = p.get("xp_collected", 0) + gain


# ── 每帧推进 ────────────────────────────────────────────────


class _ProjectileTargets:
    """Index horizontal bands during the projectile phase of a crowded tick."""

    band_width = 96

    def __init__(self, enemies):
        self.order = {enemy["id"]: index for index, enemy in enumerate(enemies)}
        self.bands = {}
        for enemy in enemies:
            self.bands.setdefault(self.band(enemy["x"]), {})[enemy["id"]] = enemy

    def band(self, x):
        return math.floor(x / self.band_width)

    def candidates(self, left, right):
        for band in range(self.band(left), self.band(right) + 1):
            occupants = self.bands.get(band)
            if occupants:
                yield from occupants.values()

    def moved(self, enemy, old_x):
        previous = self.band(old_x)
        current = self.band(enemy["x"])
        if previous != current:
            enemy_id = enemy["id"]
            del self.bands[previous][enemy_id]
            self.bands.setdefault(current, {})[enemy_id] = enemy


def _projectile_contacts(enemies, max_enemy_radius, shot, start_x, start_y, targets=None):
    """Return first contact times along the current movement segment."""
    end_x, end_y = shot["x"], shot["y"]
    dx, dy = end_x - start_x, end_y - start_y
    length_sq = dx * dx + dy * dy
    radius = shot["r"]
    padding = radius + max_enemy_radius
    left = min(start_x, end_x) - padding
    right = max(start_x, end_x) + padding
    top = min(start_y, end_y) - padding
    bottom = max(start_y, end_y) + padding
    hit = shot["hit"]
    contacts = []
    candidates = enemies if targets is None else targets.candidates(left, right)
    for enemy in candidates:
        x = enemy["x"]
        if not left <= x <= right:
            continue
        y = enemy["y"]
        if not top <= y <= bottom:
            continue
        if enemy["hp"] <= 0 or enemy["id"] in hit:
            continue
        reach = radius + enemy["radius"]
        offset_x, offset_y = x - start_x, y - start_y
        outside = offset_x * offset_x + offset_y * offset_y - reach * reach
        if outside <= 0:
            contacts.append((0.0, enemy))
            continue
        projection = offset_x * dx + offset_y * dy
        if projection <= 0:
            continue
        discriminant = projection * projection - length_sq * outside
        if discriminant >= 0:
            first = (projection - math.sqrt(discriminant)) / length_sq
            if first <= 1:
                contacts.append((first, enemy))
    if len(contacts) > 1:
        if targets is None:
            contacts.sort(key=lambda contact: contact[0])
        else:
            # Simultaneous collisions keep roster order across bands.
            contacts.sort(key=lambda contact: (contact[0], targets.order[contact[1]["id"]]))
    return contacts


def _step_shots(gs):
    width, height = _bounds(gs)
    alive_players = gs["players"]
    enemies = gs["enemies"]
    max_enemy_radius = max((enemy["radius"] for enemy in enemies), default=0) if gs["shots"] else 0
    targets = None
    indexed = len(enemies) * len(gs["shots"]) >= 2500
    if indexed:
        centers = [enemy["x"] for enemy in enemies]
        indexed = max(centers) - min(centers) >= 3 * _ProjectileTargets.band_width
    remaining = []
    for shot in gs["shots"]:
        if shot["delay"] > 0:
            shot["delay"] -= 1
            if shot["delay"] == 0 and shot.get("retarget") and not _retarget(gs, shot):
                continue
            remaining.append(shot)
            continue
        shot["age"] += 1
        owner = alive_players.get(shot["owner"])
        if shot["return_at"] is not None and not shot["returning"] and shot["age"] >= shot["return_at"]:
            shot["returning"] = True
        homing = shot["returning"] and owner is not None
        if homing:
            angle = math.atan2(owner["y"] - shot["y"], owner["x"] - shot["x"])
            speed = math.hypot(shot["vx"], shot["vy"]) or 280
            shot["vx"], shot["vy"] = math.cos(angle) * speed, math.sin(angle) * speed
        gravity = shot["gravity"]
        if gravity:
            shot["vy"] += gravity * STEP
        start_x, start_y = shot["x"], shot["y"]
        if shot["age"] == 1:
            shot["launch"] = (gs["frame"] - 1, round(start_x), round(start_y))
        shot["x"] += shot["vx"] * STEP
        shot["y"] += shot["vy"] * STEP
        dead = False
        bounced = False
        burst_at = None
        if shot["bounce"] and width is not None:
            if shot["x"] < shot["r"]:
                shot["x"] = shot["r"]
                shot["vx"] = abs(shot["vx"])
                bounced = True
            elif shot["x"] > width - shot["r"]:
                shot["x"] = width - shot["r"]
                shot["vx"] = -abs(shot["vx"])
                bounced = True
            if shot["y"] < shot["r"]:
                shot["y"] = shot["r"]
                shot["vy"] = abs(shot["vy"])
                bounced = True
            elif shot["y"] > height - shot["r"]:
                shot["y"] = height - shot["r"]
                shot["vy"] = -abs(shot["vy"])
                bounced = True
            if bounced and shot["explode"] and owner:
                _area_damage(gs, owner, shot["x"], shot["y"],
                             60 * shot["r"] / max(1, 14), shot["explode"],
                             weapon=shot["weapon"], knockback=1)
                targets = None
        elif width is not None and (shot["x"] < -90 or shot["x"] > width + 90
                                    or shot["y"] < -90 or shot["y"] > height + 90):
            dead = True
        if bounced or homing or gravity:
            shot["angle"] = math.atan2(shot["vy"], shot["vx"]) if shot["vx"] or shot["vy"] else 0
        if shot["returning"] and owner and shot["age"] > shot["return_at"] + 6:
            if math.hypot(owner["x"] - shot["x"], owner["y"] - shot["y"]) < 28:
                dead = True
        if not dead and enemies:
            if indexed and targets is None:
                targets = _ProjectileTargets(enemies)
                # A tightly packed horde is cheaper to scan in roster order.
                if len(targets.bands) < 4:
                    indexed = False
                    targets = None
            for time, enemy in _projectile_contacts(enemies, max_enemy_radius, shot, start_x, start_y, targets):
                x = start_x + (shot["x"] - start_x) * time
                y = start_y + (shot["y"] - start_y) * time
                mult = _roll(gs, shot)
                old_x = enemy["x"] if targets is not None else None
                _damage(gs, owner, enemy, shot["damage"] * mult, src=(x, y),
                        knockback=shot["knockback"], crit=mult > 1, heal=shot["heal_on_crit"],
                        kb_stack=shot.get("kb_stack", 0.0),
                        slow=shot.get("slow", 0.0), slow_ticks=shot.get("slow_ticks", 0))
                if targets is not None and old_x is not None and enemy["x"] != old_x:
                    targets.moved(enemy, old_x)
                first_hit = not shot["hit"]
                if width is None and shot["explode"] and owner and first_hit:
                    burst_at = (x, y)
                shot["hit"].add(enemy["id"])
                terminal = shot["pierce"] <= 1
                contact = {"shot": shot["id"]} if terminal else {}
                if shot["kind"] == "fire":
                    _effect(gs, shot["weapon"], "burst", life=6, x=x, y=y, r=34, **contact)
                elif first_hit or terminal:
                    size = 20 if mult > 1 else 16 if shot["r"] >= 14 else 12
                    _effect(gs, shot["weapon"], "impact", life=5, x=x, y=y, r=size,
                            angle=round(math.atan2(shot["y"] - start_y, shot["x"] - start_x), 3), **contact)
                if terminal:
                    dead = True
                    break
                shot["pierce"] -= 1
        if burst_at is not None:
            _area_damage(gs, owner, *burst_at, 60 * shot["r"] / 14, shot["explode"],
                         weapon=shot["weapon"], knockback=1)
            targets = None
        if shot["age"] == 5 or bounced or shot["returning"]:
            shot.pop("launch", None)
        if bounced:
            # The reflected leg begins on the next tick.
            shot["hit"].clear()
        if shot["age"] > shot["life"]:
            dead = True
        if not dead:
            remaining.append(shot)
    gs["shots"] = remaining


def _step_strikes(gs):
    remaining = []
    for strike in gs["strikes"]:
        if gs["elapsed"] < strike["due"]:
            remaining.append(strike)
            continue
        owner = gs["players"].get(strike["owner"])
        if owner:
            for _ in range(2 if strike["double"] else 1):
                _area_damage(gs, owner, strike["x"], strike["y"], strike["r"],
                             strike["damage"], weapon=strike["weapon"],
                             knockback=strike["knockback"], crit=False)
            _effect(gs, strike["weapon"], "bolt", life=7,
                    x=strike["x"], y=strike["y"], r=strike["r"])
    gs["strikes"] = remaining


def _step_fields(gs):
    remaining = []
    for field in gs["fields"]:
        owner = gs["players"].get(field["owner"])
        if not owner:
            continue
        if field["move"]:
            dx, dy = owner["x"] - field["x"], owner["y"] - field["y"]
            distance = math.hypot(dx, dy)
            step = 40 * STEP
            if distance <= step:
                field["x"], field["y"] = owner["x"], owner["y"]
            else:
                field["x"] += dx * step / distance
                field["y"] += dy * step / distance
            field["r"] = min(field["max_r"], field["r"] + field["grow"])
        if gs["elapsed"] >= field["next"] and field["left"] > 0:
            _area_damage(gs, owner, field["x"], field["y"], field["r"],
                         field["damage"], weapon=field["weapon"],
                         knockback=field["knockback"],
                         slow=field.get("slow", 0.0),
                         slow_ticks=field.get("slow_ticks", 0))
            field["next"] += field["tick"]
            field["left"] -= 1
        if field["left"] > 0:
            remaining.append(field)
    gs["fields"] = remaining


def _step_orbits(gs):
    elapsed = gs["elapsed"]
    for p in gs["players"].values():
        if not p["hp"]:
            continue
        if elapsed % ORBIT_HIT_TICKS == 0:
            # Keep recent cooldowns across recasts without retaining a match's dead enemies.
            for hits in p["orbit_hits"].values():
                for enemy_id, until in tuple(hits.items()):
                    if until <= elapsed:
                        del hits[enemy_id]

        expired = []
        for weapon, orbit in p["orbits"].items():
            if elapsed >= orbit["until"]:
                expired.append(weapon)
                continue
            orbit["angle"] += orbit["speed"] * STEP
            hits = p["orbit_hits"].setdefault(weapon, {})
            for index in range(orbit["count"]):
                angle = orbit["angle"] + index * math.tau / orbit["count"]
                x = p["x"] + math.cos(angle) * orbit["radius"]
                y = p["y"] + math.sin(angle) * orbit["radius"]
                for enemy in gs["enemies"]:
                    if enemy["hp"] <= 0 or hits.get(enemy["id"], 0) > elapsed:
                        continue
                    dx, dy = enemy["x"] - x, enemy["y"] - y
                    reach = orbit["hit_r"] + enemy["radius"]
                    if dx * dx + dy * dy <= reach * reach:
                        mult = _roll(gs, orbit)
                        _damage(gs, p, enemy, orbit["damage"] * mult,
                                src=(x, y), knockback=orbit["knockback"],
                                crit=mult > 1, heal=orbit["heal_on_crit"])
                        hits[enemy["id"]] = elapsed + ORBIT_HIT_TICKS
        for weapon in expired:
            p["orbits"].pop(weapon, None)


def _collect(gs, alive, *, online_ids=None):
    magnets = [(p, p["x"], p["y"], (BASE_MAGNET_RADIUS * p["stats"]["magnet"]) ** 2) for p in alive]
    remaining = []
    for item in gs["loot"]:
        target = None
        nearest_sq = math.inf
        x, y = item["x"], item["y"]
        for player, px, py, range_sq in magnets:
            dx, dy = px - x, py - y
            distance_sq = dx * dx + dy * dy
            if distance_sq <= range_sq and distance_sq < nearest_sq:
                target, nearest_sq = player, distance_sq
        if target is None:
            remaining.append(item)
        elif nearest_sq <= 14 * 14:
            _grant_xp(gs, target, item["xp"])
        else:
            fraction = 220 * STEP / math.sqrt(nearest_sq)
            item["x"] += (target["x"] - x) * fraction
            item["y"] += (target["y"] - y) * fraction
            remaining.append(item)
    gs["loot"] = remaining

    chests = []
    collectors = alive if online_ids is None else [p for p in alive if p["id"] in online_ids]
    for chest in gs["chests"]:
        target = min(collectors, key=lambda p: _gap(chest, p), default=None)
        if target is not None and _gap(chest, target) <= 36:
            _open_chest(gs, chest, target)
        else:
            chests.append(chest)
    gs["chests"] = chests
    _collect_caches(gs, collectors)


def _collect_caches(gs, collectors):
    if not gs.get("caches"):
        return
    remaining = []
    for cache in gs["caches"]:
        kind = cache["kind"]
        eligible = (player for player in collectors
                    if kind != "repair" or player["hp"] < player["max_hp"])
        target = min(eligible, key=lambda p: _gap(cache, p), default=None)
        if target is None or _gap(cache, target) > CACHE_PICKUP_RADIUS:
            remaining.append(cache)
            continue
        if kind == "xp":
            before = gs["xp"]
            _grant_xp(gs, target, CACHE_XP)
            text = f"信标储备 · 全队经验 +{gs['xp'] - before}"
        elif kind == "repair":
            before = target["hp"]
            target["hp"] = _hp_after_repair(target, "repair")
            text = f"紧急补片 · 耐久 +{round(target['hp'] - before, 1):g}"
        elif kind == "gear":
            text = f"装备补给 · {_chest_reward(gs, target, allow_evolution=False)}"
        else:
            gs["salvage"] += CACHE_SALVAGE
            text = f"打捞储备 · 全队打捞分 +{CACHE_SALVAGE}"
        gs["chest_events"].append({
            "frame": gs["frame"], "player": target["id"], "text": f"{target['name']}：{text}",
        })
    gs["chest_events"] = gs["chest_events"][-12:]
    gs["caches"] = remaining


def _upgrade_weapon(p, weapon, gs=None):
    spec = data.WEAPONS.get(weapon)
    if not spec:
        return False
    level = p["weapons"].get(weapon)
    if level is None:
        if len(p["weapons"]) >= data.MAX_WEAPONS:
            return False
        p["weapons"][weapon] = 1
    elif level < spec["max_level"]:
        p["weapons"][weapon] = level + 1
    else:
        return False
    p["cycles"][weapon] = 0
    return True


def _upgrade_passive(p, passive):
    spec = data.PASSIVES.get(passive)
    if not spec:
        return False
    level = p["passives"].get(passive)
    if level is None:
        if len(p["passives"]) >= data.MAX_PASSIVES:
            return False
        p["passives"][passive] = 1
    elif level < spec["max_level"]:
        p["passives"][passive] = level + 1
    else:
        return False
    _refresh_stats(p)
    return True


def _try_evolve(p):
    for weapon, level in list(p["weapons"].items()):
        spec = data.WEAPONS.get(weapon)
        if not spec or level < spec["max_level"]:
            continue
        if not spec["evolution"] or spec["passive"] not in p["passives"]:
            continue
        evolved = spec["evolution"]
        if evolved in p["weapons"]:
            continue
        del p["weapons"][weapon]
        p["orbits"].pop(weapon, None)
        p["orbit_hits"].pop(weapon, None)
        p["cycles"].pop(weapon, None)
        p["weapons"][evolved] = 1
        p["cycles"][evolved] = 0
        return weapon, evolved
    return None


def _chest_reward(gs, p, *, allow_evolution=True):
    evolved = _try_evolve(p) if allow_evolution else None
    if evolved:
        base, target = evolved
        return f"{data.WEAPONS[base]['name']} 进化为 {data.WEAPONS[target]['name']}"
    options = []
    for weapon, level in p["weapons"].items():
        if level < data.WEAPONS[weapon]["max_level"]:
            options.append(("w", weapon))
    for passive, level in p["passives"].items():
        if level < data.PASSIVES[passive]["max_level"]:
            options.append(("p", passive))
    if options:
        kind, item = gs["rng"].choice(options)
        if kind == "w":
            before = p["weapons"][item]
            _upgrade_weapon(p, item, gs)
            return f"{data.WEAPONS[item]['name']} Lv.{before} → {before + 1}"
        before = p["passives"][item]
        _upgrade_passive(p, item)
        return f"{data.PASSIVES[item]['name']} Lv.{before} → {before + 1}"
    if 0 < p["hp"] < p["max_hp"]:
        before = p["hp"]
        p["hp"] = _hp_after_repair(p, "repair")
        return f"修复 {round(p['hp'] - before, 1):g} 点耐久"
    gs["salvage"] += 25
    return "获得 25 打捞分"


def _open_chest(gs, chest, opener):
    _effect(gs, "chest", "chest", life=14, x=chest["x"], y=chest["y"], r=44)
    for p in gs["players"].values():
        if not p["hp"]:
            continue
        text = _chest_reward(gs, p)
        gs["chest_events"].append({
            "frame": gs["frame"], "player": p["id"],
            "text": f"{p['name']}：{text}",
        })
    gs["chest_events"] = gs["chest_events"][-12:]
    if opener:
        gs["salvage"] += 25


def _available_choices(p):
    upgrades = []
    for weapon, level in p["weapons"].items():
        spec = data.WEAPONS[weapon]
        if level < spec["max_level"]:
            upgrades.append(weapon)
    for passive, level in p["passives"].items():
        if level < data.PASSIVES[passive]["max_level"]:
            upgrades.append(passive)
    news = []
    if len(p["weapons"]) < data.MAX_WEAPONS:
        news.extend(weapon for weapon in data.BASE_WEAPON_ORDER
                    if weapon not in p["weapons"])
    if len(p["passives"]) < data.MAX_PASSIVES:
        news.extend(passive for passive in data.PASSIVE_ORDER
                    if passive not in p["passives"])
    return upgrades, news


def _choices_for(gs, p):
    upgrades, news = _available_choices(p)
    choices = []
    if upgrades:
        choices.append(gs["rng"].choice(upgrades))
    if news:
        choices.append(gs["rng"].choice(news))
    pool = [item for item in (*upgrades, *news) if item not in choices]
    gs["rng"].shuffle(pool)
    choices.extend(pool[:max(0, 3 - len(choices))])
    luck = p["stats"]["luck"]
    if len(choices) >= 3 and luck > 1 and gs["rng"].random() < 1 - 1 / luck:
        extra = [item for item in (*upgrades, *news) if item not in choices]
        if extra:
            choices.append(gs["rng"].choice(extra))
    fillers = ("repair", "salvage") if 0 < p["hp"] < p["max_hp"] else ("salvage",)
    for filler in fillers:
        if len(choices) >= 3:
            break
        choices.append(filler)
    gs["rng"].shuffle(choices)
    return choices[:4]


def _offer(gs, *, online_ids=None):
    gs["xp"] -= threshold(gs)
    gs["level"] += 1
    gs["growth_boost"] = 2.0 if gs["level"] in (20, 40) else 1.0
    for p in gs["players"].values():
        if p["hp"] > 0:
            p["choices"] = _choices_for(gs, p)
            offline = online_ids is not None and p["id"] not in online_ids
            if offline or p["choices"] == ["salvage"]:
                pick(gs, p["id"], gs["level"], p["choices"][0])
    if any(p["choices"] for p in gs["players"].values()):
        gs["pick_until"] = gs["frame"] + UPGRADE_CHOICE_TICKS
        for uid in gs["players"]:
            stop(gs, uid)
    else:
        gs["pick_until"] = 0


REPAIR_AMOUNTS = {"repair": 30, "rest": 15}


def _hp_after_repair(player, choice):
    return min(player["max_hp"], player["hp"] + REPAIR_AMOUNTS[choice])


def pick(gs, uid, level, choice):
    p = gs["players"].get(uid)
    if not p or type(level) is not int or level != gs["level"] or choice not in p["choices"]:
        return False
    applied = False
    if choice in data.WEAPONS:
        applied = _upgrade_weapon(p, choice, gs)
    elif choice in data.PASSIVES:
        applied = _upgrade_passive(p, choice)
    elif choice in REPAIR_AMOUNTS:
        if p["hp"]:
            p["hp"] = _hp_after_repair(p, choice)
        elif choice == "repair":
            p["down"] += 100
        applied = True
    elif choice == "salvage":
        gs["salvage"] += 100
        applied = True
    p["choices"] = []
    return applied


def _hurt_diver(gs, p, damage):
    if not p["hp"] or p["invincible"] > gs["elapsed"]:
        return
    reduction = min(data.MAX_ARMOR_REDUCTION, p["stats"]["armor"])
    p["hp"] = max(0, p["hp"] - max(data.MIN_HIT_DAMAGE,
                                   damage * (1.0 - reduction)))
    p["invincible"] = gs["elapsed"] + 20
    if not p["hp"]:
        stop(gs, p["id"])
        p.update(down=gs["elapsed"] + 300, rescue=0)
        p["orbits"].clear()
        p["orbit_hits"].clear()
        gs["shots"] = [shot for shot in gs["shots"] if shot["owner"] != p["id"]]
        gs["fields"] = [field for field in gs["fields"] if field["owner"] != p["id"]]
        gs["strikes"] = [strike for strike in gs["strikes"] if strike["owner"] != p["id"]]


def _contact_damage(gs, alive):
    enemies = gs["enemies"]
    elapsed = gs["elapsed"]
    for p in alive:
        if not p["hp"] or p["invincible"] > elapsed:
            continue
        x, y = p["x"], p["y"]
        for enemy in enemies:
            dx = enemy["x"] - x
            reach = enemy["radius"] + 10
            if dx <= -reach or dx >= reach:
                continue
            dy = enemy["y"] - y
            if dx * dx + dy * dy < reach * reach:
                _hurt_diver(gs, p, CONTACT_DAMAGE.get(enemy["kind"], 2))
                break


def _warden_enraged(warden):
    return 0 < warden["hp"] <= .45 * warden["max_hp"]


def _step_warden(gs, warden, alive):
    elapsed = gs["elapsed"]
    attack = warden.get("attack")
    if attack:
        if elapsed >= attack["resolve_at"] and not attack["resolved"]:
            attack["resolved"] = True
            for p in alive:
                if any(_gap_xy(p, x, y) <= r for x, y, r in attack["zones"]):
                    _hurt_diver(gs, p, attack["damage"])
        if elapsed >= attack["resolve_at"] + WARDEN_IMPACT_TICKS:
            warden.pop("attack")
        return

    if elapsed < warden.setdefault("next_attack", elapsed + 30):
        return

    enraged = _warden_enraged(warden)
    kind = warden.get("next_attack_kind", "strike")
    if kind == "pulse":
        warning = 28
        zones = [[warden["x"], warden["y"], 180 if enraged else 160]]
        damage = 24 if enraged else 20
    else:
        warning = 22
        zones = []
        for p in alive:
            if not any(_gap_xy(p, x, y) <= 28 for x, y, _ in zones):
                zones.append([p["x"], p["y"], 105 if enraged else 90])
        damage = 20 if enraged else 16

    resolve_at = elapsed + warning
    warden["attack"] = {
        "id": _id(gs), "kind": kind, "zones": zones, "resolve_at": resolve_at,
        "warning": warning, "damage": damage, "resolved": False,
    }
    warden["next_attack"] = resolve_at + (WARDEN_ENRAGED_RECOVERY_TICKS if enraged else WARDEN_RECOVERY_TICKS)
    warden["next_attack_kind"] = "strike" if kind == "pulse" else "pulse"


def _warden_attack_view(gs):
    for enemy in gs["enemies"]:
        if enemy["kind"] == "warden" and (attack := enemy.get("attack")):
            remaining = attack["resolve_at"] - gs["elapsed"]
            duration = WARDEN_IMPACT_TICKS if attack["resolved"] else attack["warning"]
            progress = -remaining / duration if attack["resolved"] else 1 - remaining / duration
            return {
                "id": attack["id"], "kind": attack["kind"], "impact": attack["resolved"],
                "progress": max(0, min(1, progress)), "duration_ms": round(duration * STEP * 1000),
                # Keep the drawn edge at the same coordinates as the hit test.
                "zones": [list(zone) for zone in attack["zones"]],
            }
    return None


def _spawn_events(gs, seconds, alive):
    duration = gs["duration"]
    events = (
        ("elite0", duration / 3, "elite"),
        ("elite1", duration * 2 / 3, "elite"),
        ("boss", duration * 11 / 12, "warden"),
    )
    for key, at, kind in events:
        if seconds >= at and key not in gs["spawned"]:
            gs["spawned"].add(key)
            if kind == "warden":
                gs["boss"] = True
            _spawn(gs, kind, alive[0])


def advance(gs, *, online_ids=None):
    width, height = _bounds(gs)
    if gs["result"]:
        return
    gs["frame"] += 1
    # Keep short projectile hits available to the client's delayed render timeline.
    gs["effects"] = [
        effect for effect in gs["effects"]
        if effect["until"] + (PROJECTILE_IMPACT_TAIL if effect["kind"] == "impact" else 0) > gs["frame"]
    ]
    members = list(gs["players"].values())
    if gs["pick_until"]:
        expired = gs["frame"] >= gs["pick_until"]
        for p in members:
            offline = online_ids is not None and p["id"] not in online_ids
            if p["choices"] and (expired or offline):
                pick(gs, p["id"], gs["level"], p["choices"][0])
        if any(p["choices"] for p in members):
            return
        gs["pick_until"] = 0
        # Restored games may already contain input buffered during an upgrade.
        for p in members:
            stop(gs, p["id"])
    gs["elapsed"] += 1
    elapsed = gs["elapsed"]
    seconds = elapsed * STEP
    gs["count"] = max(1, sum(not p["departed"] for p in members))
    alive = [p for p in members if p["hp"] > 0]
    if not alive:
        gs["result"] = "lost"
        return

    for p in alive:
        if p["input_until"] > gs["frame"] and (p["dx"] or p["dy"]):
            amount = data.PLAYER_SPEED * p["stats"]["move_speed"] * STEP
            p["x"] = _clamp(p["x"] + p["dx"] * amount, width)
            p["y"] = _clamp(p["y"] + p["dy"] * amount, height)
            p["angle"] = math.atan2(p["dy"], p["dx"])
        if p.get("tap_release"):
            stop(gs, p["id"])
        if p["stats"]["recovery"] > 0:
            p["hp"] = min(p["max_hp"], p["hp"] + p["stats"]["recovery"] * STEP)

    # Use the same updated positions for every rescue, independent of seat order.
    rescuers = alive if online_ids is None else [p for p in alive if p["id"] in online_ids]
    for p in members:
        if p["hp"] or p["out"]:
            continue
        if elapsed >= p["down"]:
            p["out"] = True
            continue
        closest = min(rescuers, key=lambda diver: _gap(p, diver), default=None)
        if closest is None or _gap(p, closest) >= RESCUE_RADIUS:
            p["rescue"] = 0
            continue
        p["rescue"] += 1
        p["rescuer"] = closest["id"]
        if p["rescue"] >= RESCUE_TICKS:
            p.update(hp=35, down=0, rescue=0, invincible=elapsed + 30)
            _effect(gs, "rescue", "rescue", life=14, x=p["x"], y=p["y"], r=54)
            closest["rescues"] = closest.get("rescues", 0) + 1

    phase = sum(seconds >= gs["duration"] * limit for limit in (1 / 6, 5 / 12, 2 / 3))
    scale = _scale(gs)
    density = 1 + SPAWN_DENSITY_PER_PLAYER * (gs["count"] - 1)
    if seconds >= gs["duration"] * 11 / 12 and not gs["boss_dead"]:
        rate, min_alive, base_cap = BOSS_SPAWN
    else:
        rate, min_alive, base_cap = SPAWN_PHASES[phase]
    min_alive = round(min_alive * density)
    cap = min(SPAWN_HARD_CAP, round(base_cap * density))
    missing = max(0, min_alive - len(gs["enemies"]))
    topup = min(SPAWN_TOPUP_RATE * density, missing * 2.5)
    gs["spawn_budget"] += (rate * scale + topup) * STEP
    while gs["spawn_budget"] >= 1 and len(gs["enemies"]) < cap:
        gs["spawn_budget"] -= 1
        p = alive[gs["spawn_cursor"] % len(alive)]
        gs["spawn_cursor"] += 1
        kind = gs["rng"].choices(WAVE_KINDS, WAVE_WEIGHTS[phase])[0]
        _spawn(gs, kind, p)
    gs["spawn_budget"] = min(2, gs["spawn_budget"])
    _spawn_events(gs, seconds, alive)

    diver_positions = [(p["x"], p["y"]) for p in alive]
    pursuit_speed = max(data.PLAYER_SPEED * p["stats"]["move_speed"] for p in alive)
    for enemy in gs["enemies"]:
        attack = enemy.get("attack")
        if attack and attack["kind"] == "pulse" and not attack["resolved"]:
            continue
        x, y = enemy["x"], enemy["y"]
        nearest_sq = math.inf
        dx = dy = 0.0
        for px, py in diver_positions:
            ox, oy = px - x, py - y
            distance_sq = ox * ox + oy * oy
            if distance_sq < nearest_sq:
                nearest_sq, dx, dy = distance_sq, ox, oy
        enemy["angle"] = math.atan2(dy, dx)
        if not nearest_sq:
            continue
        speed = enemy["speed"]
        pursuit = False
        if speed > 0 and enemy["kind"] in PURSUIT_KINDS:
            radius = PURSUIT_NEAR_RADIUS if enemy.get("pursuing") else PURSUIT_RADIUS
            pursuit = nearest_sq > radius * radius
            enemy["pursuing"] = pursuit
        if not pursuit and elapsed < enemy.get("stagger_until", 0):
            continue
        distance = math.sqrt(nearest_sq)
        if pursuit:
            # Catch up without teleporting, then return to normal movement and
            # crowd control inside the shorter radius.
            speed = max(speed, pursuit_speed + min(160, 55 + distance - PURSUIT_NEAR_RADIUS))
        elif elapsed < enemy.get("slow_until", 0):
            speed *= enemy.get("slow_factor", 1.0)
        fraction = min(1, speed * STEP / distance)
        enemy["x"] += dx * fraction
        enemy["y"] += dy * fraction

    for p in alive:
        for weapon, level in list(p["weapons"].items()):
            if elapsed >= p["cycles"].get(weapon, 0):
                _fire(gs, p, weapon, level)
    _step_strikes(gs)
    _step_fields(gs)
    _step_shots(gs)
    _step_orbits(gs)

    gs["enemies"] = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
    for enemy in gs["enemies"]:
        if enemy["kind"] == "warden":
            _step_warden(gs, enemy, alive)
    _contact_damage(gs, alive)
    alive = [p for p in members if p["hp"] > 0]
    _collect(gs, alive, online_ids=online_ids)
    if not any(p["hp"] for p in members):
        gs["result"] = "lost"
    elif seconds >= gs["duration"]:
        gs["result"] = "won" if gs["boss_dead"] else "timeout"
    elif gs["xp"] >= threshold(gs):
        _offer(gs, online_ids=online_ids)


def _choice_changes(player):
    changes = {}
    for choice in player["choices"]:
        if choice in player["weapons"]:
            rank = player["weapons"][choice]
            before = data.weapon_stats(choice, rank, player["stats"])
            after = data.weapon_stats(choice, rank + 1, player["stats"])
            keys = ("damage", "amount", "cooldown", "area", "pierce", "duration",
                    "speed", "interval", "knockback", "crit", "preserve")
        elif choice in data.PASSIVES:
            passives = {**player["passives"], choice: player["passives"].get(choice, 0) + 1}
            before = player["stats"]
            # 局外天赋也要带上，否则带天赋的玩家会看到「升级后属性反而下降」。
            after = data.player_stats(passives, player.get("talent"))
            keys = (data.PASSIVES[choice]["stat"],)
        else:
            continue

        changes[choice] = [
            [f"{stat}_scale" if choice in data.PASSIVES and stat in ("cooldown", "duration") else stat,
             round(before[stat], 3), round(after[stat], 3)]
            for stat in keys if round(before[stat], 3) != round(after[stat], 3)
        ]
    return changes


def _new_weapon_stats(player):
    previews = {}
    for choice in player["choices"]:
        if choice not in data.WEAPONS or choice in player["weapons"]:
            continue
        stats = data.weapon_stats(choice, 1, player["stats"])
        keys = ("cooldown",) if choice == "pentagram" else ("damage", "cooldown")
        previews[choice] = [[key, round(stats[key], 3)] for key in keys]
    return previews


def _evolution_requirements(player):
    return {
        weapon: {"target": spec["evolution"], "passive": spec["passive"], "rank": spec["max_level"]}
        for weapon in player["weapons"]
        if (spec := data.WEAPONS[weapon])["evolution"]
    }


def record_summary(gs):
    return {
        "game": "abyss", "seconds": int(gs["elapsed"] * STEP), "map_size": gs.get("map_size", "medium"),
        **{key: gs[key] for key in ("level", "kills", "salvage", "result", "boss", "boss_dead")},
        "players": [
            {**{key: p[key] for key in ("id", "name", "kills", "hp", "out")},
             **{key: p[key] for key in ("xp_collected", "rescues") if key in p},
             **({"damage_dealt": round(p["damage_dealt"], 1)} if "damage_dealt" in p else {}),
             "weapons": dict(p["weapons"]), "passives": dict(p["passives"])}
            for p in gs["players"].values()
        ],
    }


def snapshot(gs):
    def point(a):
        return [round(a["x"]), round(a["y"])]

    orbits = []
    auras = []
    for p in gs["players"].values():
        for weapon, orbit in p["orbits"].items():
            for index in range(orbit["count"]):
                angle = orbit["angle"] + index * math.tau / orbit["count"]
                orbits.append([
                    p["id"], weapon,
                    round(p["x"] + math.cos(angle) * orbit["radius"]),
                    round(p["y"] + math.sin(angle) * orbit["radius"]),
                    round(orbit["hit_r"]), index,
                ])
        if p["hp"]:
            for weapon in ("ring", "soul_eater"):
                level = p["weapons"].get(weapon)
                if not level:
                    continue
                stats = data.weapon_stats(weapon, level, p["stats"])
                auras.append([p["id"], weapon, round(p["x"]), round(p["y"]),
                              round(65 * stats["area"])])

    return {
        "frame": gs["frame"], "seconds": int(gs["elapsed"] * STEP),
        "duration": int(gs["duration"]),
        "map_size": gs.get("map_size", "medium"),
        "bounds": list(_bounds(gs)),
        "level": gs["level"], "xp": gs["xp"], "next_xp": threshold(gs),
        "growth_boost": gs["growth_boost"],
        "kills": gs["kills"], "salvage": gs["salvage"],
        "choice_seconds": max(0, math.ceil((gs["pick_until"] - gs["frame"]) * STEP)),
        "result": gs["result"], "boss": gs["boss"], "boss_dead": gs["boss_dead"],
        "warden_enraged": gs["boss"] and not gs["boss_dead"] and any(
            enemy["kind"] == "warden" and _warden_enraged(enemy) for enemy in gs["enemies"]
        ),
        "warden_attack": None if gs["result"] else _warden_attack_view(gs),
        "players": [{
            "id": p["id"], "name": p["name"], "position": point(p),
            "angle": round(p["angle"], 2), "hp": round(p["hp"], 1),
            "max_hp": round(p["max_hp"], 1), "out": p["out"],
            **({"protected": True, "protection_ms": round((p["invincible"] - gs["elapsed"]) * STEP * 1000)}
               if p["hp"] and p["invincible"] > gs["elapsed"] and not gs["result"] else {}),
            "down": max(0, math.ceil((p["down"] - gs["elapsed"]) * STEP)),
            "rescue": p["rescue"], "weapons": dict(p["weapons"]),
            **({"rescuer": p["rescuer"]} if p["rescue"] and not p["out"] and "rescuer" in p else {}),
            "passives": dict(p["passives"]), "choices": list(p["choices"]),
            "choice_changes": _choice_changes(p),
            "new_weapon_stats": _new_weapon_stats(p),
            **({"repair_health": {
                choice: [round(p["hp"], 1), round(_hp_after_repair(p, choice), 1)]
                for choice in p["choices"] if choice in REPAIR_AMOUNTS
            }} if p["hp"] and any(choice in REPAIR_AMOUNTS for choice in p["choices"]) else {}),
            "evolution_requirements": _evolution_requirements(p),
            **({"damage_dealt": round(p["damage_dealt"], 1)} if "damage_dealt" in p else {}),
            **({"talent_award": p["talent_award"]} if p.get("talent_award") else {}),
            "kills": p["kills"], "xp_collected": p.get("xp_collected", 0), "rescues": p.get("rescues", 0),
            "move_speed": round(data.PLAYER_SPEED * p["stats"]["move_speed"]),
            "stats": {
                key: round(p["stats"][key], 3)
                for key in ("might", "area", "cooldown", "duration", "speed",
                            "amount", "magnet", "luck", "growth", "armor",
                            "recovery")
            },
        } for p in gs["players"].values()],
        "enemies": [[e["id"], e["kind"], *point(e), round(e["angle"], 2),
                     round(e["hp"], 1), e["max_hp"],
                     round(1 - e.get("slow_factor", 1.0), 2)
                     if e.get("slow_until", 0) > gs["elapsed"] else 0]
                    for e in gs["enemies"]],
        "loot": [[item["id"], *point(item)] for item in gs["loot"]],
        "chests": [[chest["id"], *point(chest)] for chest in gs["chests"]],
        "caches": [[cache["id"], cache["kind"], *point(cache)] for cache in gs.get("caches", ())],
        "chest_events": [
            event for event in gs["chest_events"]
            if event["frame"] >= gs["frame"] - 100
        ],
        "effects": [dict(effect) for effect in gs["effects"]],
        "fields": [[field["id"], field["weapon"], *point(field), round(field["r"])]
                   for field in gs["fields"]],
        "shots": [[shot["id"], shot["weapon"], *point(shot),
                   round(shot["angle"], 2), round(shot["r"]),
                   *([list(shot["launch"])] if "launch" in shot else ())]
                  for shot in gs["shots"] if shot["delay"] <= 0],
        "orbits": orbits,
        "auras": auras,
    }
