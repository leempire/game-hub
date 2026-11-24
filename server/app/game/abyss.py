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

WIDTH, HEIGHT = 1440, 1000
STEP = .05
DEFAULT_DURATION = 720
FRAMES_PER_SECOND = 20

ENEMIES = {
    # 血量按「VS 前几分钟的杂兵」重定：Magic Wand 10 点伤害能一枪打死漂囊，
    # 裂鳍鳗与锈甲蟹各多挨一枪。旧版血条配的是 0.6 秒冷却的铆钉，换成 VS 的
    # 1.2 秒魔杖后必须同步下调，否则开场 30 秒就会被怪潮淹没。
    "drifter": (10, 56, 11), "eel": (15, 105, 10), "crab": (22, 43, 16),
    "elite": (450, 83, 24), "warden": (1350, 84, 28),
}

XP_UNITS = {"warden": 30, "elite": 18, "crab": 2}


def create(players, seed, duration=DEFAULT_DURATION, xp_pace=1.0):
    gs = {
        "rng": random.Random(seed), "frame": 0, "elapsed": 0,
        "duration": float(duration), "xp_pace": float(xp_pace),
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
        p = {
            "id": player["id"],
            "name": player.get("nickname") or player["username"],
            "x": WIDTH / 2 + 36 * (seat - (len(players) - 1) / 2),
            "y": HEIGHT / 2, "angle": 0, "hp": data.BASE_MAX_HP,
            "max_hp": data.BASE_MAX_HP, "invincible": 0,
            "dx": 0, "dy": 0, "input_until": 0, "down": 0, "rescue": 0,
            "out": False, "departed": False,
            "weapons": {"rivet": 1}, "passives": {}, "cycles": {},
            "orbits": {}, "orbit_hits": {}, "choices": [],
            "stats": dict(data.BASE_STATS), "kills": 0, "last_crit_heal": -20,
        }
        _refresh_stats(p)
        gs["players"][p["id"]] = p
    return gs


def move(gs, uid, dx, dy):
    p = gs["players"].get(uid)
    if not p or not p["hp"] or gs["result"]:
        return
    for value in (dx, dy):
        if type(value) not in (int, float) or not -1 <= value <= 1:
            raise ValueError("移动方向无效")
    magnitude = max(1, math.hypot(dx, dy))
    p.update(dx=dx / magnitude, dy=dy / magnitude, input_until=gs["frame"] + 9)


def stop(gs, uid):
    p = gs["players"].get(uid)
    if p:
        p.update(dx=0, dy=0, input_until=0)


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


def _clamp(value, limit):
    return max(22, min(limit - 22, value))


def _id(gs):
    gs["next_id"] += 1
    return gs["next_id"]


def _scale(gs):
    return 1 + .45 * (gs["count"] - 1)


def threshold(gs):
    base = data.xp_step(gs["level"])
    return max(1, math.ceil(base * _scale(gs) * gs["xp_pace"]))


def _refresh_stats(p):
    old_max = p.get("max_hp", data.BASE_MAX_HP)
    stats = data.player_stats(p["passives"])
    p["stats"] = stats
    p["max_hp"] = stats["max_hp"]
    if p["hp"] > 0:
        p["hp"] = min(p["max_hp"], p["hp"] + max(0.0, p["max_hp"] - old_max))
    else:
        p["hp"] = 0


def _spawn(gs, kind, player):
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
        if x != _clamp(x, WIDTH):
            x = player["x"] - dx
        if y != _clamp(y, HEIGHT):
            y = player["y"] - dy
        site = {"x": _clamp(x, WIDTH), "y": _clamp(y, HEIGHT)}
        gap = min((_gap(site, member) for member in alive), default=safe_gap)
        if gap > best_gap:
            best, best_gap = site, gap
        if gap >= safe_gap:
            break
    hp, speed, radius = ENEMIES[kind]
    if kind in ("elite", "warden"):
        hp = round(hp * (1 + .60 * (gs["count"] - 1)))
    else:
        interval = 100 * gs["duration"] / DEFAULT_DURATION
        growth = int(gs["elapsed"] * STEP // interval) if interval > 0 else 0
        hp += 4 * min(6, growth)
    gs["enemies"].append({
        "id": _id(gs), "kind": kind, **best, "hp": hp, "max_hp": hp,
        "angle": 0, "speed": speed, "radius": radius,
    })


def _effect(gs, weapon, kind="ring", life=7, **shape):
    gs["effects"].append({
        "id": _id(gs), "weapon": weapon, "kind": kind,
        "until": gs["frame"] + life, **shape,
    })


def _spawn_chest(gs, x, y):
    gs["chests"].append({"id": _id(gs), "x": x, "y": y})


def _drop_loot(gs, enemy, units):
    if units <= 0:
        return
    if len(gs["loot"]) >= 160:
        closest = min(gs["loot"], key=lambda item: _gap(item, enemy))
        closest["xp"] += units
    else:
        gs["loot"].append({"id": _id(gs), "x": enemy["x"], "y": enemy["y"], "xp": units})


def _damage(gs, owner, enemy, damage, *, src=None, knockback=0.0, crit=False,
            heal=0.0, drop_chance=1.0, xp_mult=1):
    """对单个敌人结算伤害。返回是否击杀。"""
    if not enemy or enemy["hp"] <= 0:
        return False
    enemy["hp"] -= damage
    if knockback and src is not None:
        angle = math.atan2(enemy["y"] - src[1], enemy["x"] - src[0])
        push = min(42.0, 6.0 * knockback)
        if enemy["kind"] in ("elite", "warden"):
            push *= .2
        enemy["x"] = _clamp(enemy["x"] + push * math.cos(angle), WIDTH)
        enemy["y"] = _clamp(enemy["y"] + push * math.sin(angle), HEIGHT)
    if crit and heal and owner and owner["hp"] > 0:
        if gs["elapsed"] >= owner["last_crit_heal"] + FRAMES_PER_SECOND:
            owner["hp"] = min(owner["max_hp"], owner["hp"] + heal)
            owner["last_crit_heal"] = gs["elapsed"]
            _effect(gs, "bloody_tear", "heal", life=10, x=owner["x"], y=owner["y"], r=26)
    if enemy["hp"] > 0:
        return False
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
                 drop_chance=1.0, xp_mult=1, stats=None, crit=True):
    stats = stats or {"crit": 0, "crit_mult": 1}
    for enemy in list(gs["enemies"]):
        if enemy["hp"] <= 0:
            continue
        if _gap_xy(enemy, x, y) <= radius + enemy["radius"]:
            mult = _roll(gs, stats) if crit else 1.0
            _damage(gs, owner, enemy, damage * mult, src=(x, y),
                    knockback=knockback, crit=mult > 1,
                    heal=stats.get("heal_on_crit", 0) if crit else 0,
                    drop_chance=drop_chance, xp_mult=xp_mult)
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
        "crit": stats["crit"], "crit_mult": stats["crit_mult"],
        "heal_on_crit": stats.get("heal_on_crit", 0),
        "life": life, "delay": delay, "age": 0, "hit": set(),
        "bounce": bounce, "gravity": gravity, "explode": explode,
        "return_at": return_at, "returning": False, "retarget": retarget,
    })


def _retarget(gs, shot):
    """多段发射的后续弹丸在真正出手时重新锁定最近敌人（VS 的行为）。"""
    owner = gs["players"].get(shot["owner"])
    if not owner:
        return
    enemies = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
    if not enemies:
        return
    target = min(enemies, key=lambda enemy: _gap(enemy, owner))
    angle = math.atan2(target["y"] - owner["y"], target["x"] - owner["x"])
    speed = math.hypot(shot["vx"], shot["vy"]) or 320
    shot["vx"], shot["vy"] = math.cos(angle) * speed, math.sin(angle) * speed


def _frames(seconds):
    return max(1, round(seconds / STEP))


# ── 武器开火 ────────────────────────────────────────────────


def _fire(gs, p, weapon, level):
    spec = data.WEAPONS[weapon]
    stats = data.weapon_stats(weapon, level, p["stats"])
    behavior = spec["behavior"]
    enemies = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
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
        for index in range(amount):
            if not enemies:
                break
            target = min(enemies, key=lambda enemy: _gap(enemy, p))
            angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
            speed = 320 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="bolt", life=_frames(1.4), delay=index * interval,
                  pierce=int(stats["pierce"]), radius=8 * stats["area"],
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
        for index in range(amount):
            if not enemies:
                break
            target = min(enemies, key=lambda enemy: _gap(enemy, p))
            angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
            speed = 280 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="cross", life=_frames(1.9), delay=index * interval,
                  pierce=data.INFINITE_PIERCE, radius=18 * stats["area"],
                  return_at=_frames(.6), retarget=True)

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
            if not enemies:
                break
            target = gs["rng"].choice(enemies)
            angle = math.atan2(target["y"] - p["y"], target["x"] - p["x"])
            speed = 240 * stats["speed"]
            _shot(gs, p, weapon, stats, p["x"], p["y"],
                  math.cos(angle) * speed, math.sin(angle) * speed,
                  kind="fire", life=_frames(1.8), delay=index * interval,
                  pierce=int(stats["pierce"]), radius=16 * stats["area"])

    elif behavior == "garlic":
        radius = 65 * stats["area"]
        hits = 0
        for enemy in enemies:
            if _gap_xy(enemy, p["x"], p["y"]) <= radius + enemy["radius"]:
                mult = _roll(gs, stats)
                if _damage(gs, p, enemy, stats["damage"] * mult, src=(p["x"], p["y"]),
                           knockback=stats["knockback"], crit=mult > 1):
                    hits += 1
        if weapon == "soul_eater" and hits:
            p["hp"] = min(p["max_hp"], p["hp"] + min(3.0, hits * .5))
        _effect(gs, weapon, "aura", life=4, x=p["x"], y=p["y"], r=radius)

    elif behavior == "water":
        for index in range(amount):
            angle = gs["rng"].uniform(0, math.tau)
            distance = gs["rng"].uniform(70, 230)
            x = _clamp(p["x"] + math.cos(angle) * distance, WIDTH)
            y = _clamp(p["y"] + math.sin(angle) * distance, HEIGHT)
            radius = 60 * stats["area"]
            gs["fields"].append({
                "id": _id(gs), "owner": p["id"], "weapon": weapon,
                "x": x, "y": y, "r": radius,
                "next": gs["elapsed"] + index * interval + 10,
                "left": max(1, int(stats["duration"] / .5)),
                "tick": 10, "damage": stats["damage"],
                "knockback": stats["knockback"],
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
                x = _clamp(p["x"] + math.cos(angle) * distance, WIDTH)
                y = _clamp(p["y"] + math.sin(angle) * distance, HEIGHT)
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
                gs["xp"] += _xp_gain(gs, p, item["xp"])
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
            x = _clamp(p["x"] + math.cos(angle) * distance, WIDTH)
            y = _clamp(p["y"] + math.sin(angle) * distance, HEIGHT)
            radius = 45 * stats["area"]
            gs["fields"].append({
                "id": _id(gs), "owner": p["id"], "weapon": weapon,
                "x": x, "y": y, "r": radius,
                "next": gs["elapsed"] + index * interval + 10,
                "left": max(1, int(stats["duration"] / .5)),
                "tick": 10, "damage": stats["damage"],
                "knockback": stats["knockback"],
                "move": True, "grow": (90 * stats["area"] - radius) / duration,
                "max_r": 90 * stats["area"],
            })


def _xp_gain(gs, p, units):
    return max(1, round(units * p["stats"]["growth"] * gs["growth_boost"]))


# ── 每帧推进 ────────────────────────────────────────────────


def _step_shots(gs):
    alive_players = gs["players"]
    remaining = []
    for shot in gs["shots"]:
        if shot["delay"] > 0:
            shot["delay"] -= 1
            if shot["delay"] == 0 and shot.get("retarget"):
                _retarget(gs, shot)
            remaining.append(shot)
            continue
        shot["age"] += 1
        owner = alive_players.get(shot["owner"])
        if shot["return_at"] is not None and not shot["returning"] and shot["age"] >= shot["return_at"]:
            shot["returning"] = True
        if shot["returning"] and owner:
            angle = math.atan2(owner["y"] - shot["y"], owner["x"] - shot["x"])
            speed = math.hypot(shot["vx"], shot["vy"]) or 280
            shot["vx"], shot["vy"] = math.cos(angle) * speed, math.sin(angle) * speed
        if shot["gravity"]:
            shot["vy"] += shot["gravity"] * STEP
        shot["x"] += shot["vx"] * STEP
        shot["y"] += shot["vy"] * STEP
        shot["angle"] = math.atan2(shot["vy"], shot["vx"]) if shot["vx"] or shot["vy"] else 0
        dead = False
        if shot["bounce"]:
            bounced = False
            if shot["x"] < shot["r"]:
                shot["x"] = shot["r"]
                shot["vx"] = abs(shot["vx"])
                bounced = True
            elif shot["x"] > WIDTH - shot["r"]:
                shot["x"] = WIDTH - shot["r"]
                shot["vx"] = -abs(shot["vx"])
                bounced = True
            if shot["y"] < shot["r"]:
                shot["y"] = shot["r"]
                shot["vy"] = abs(shot["vy"])
                bounced = True
            elif shot["y"] > HEIGHT - shot["r"]:
                shot["y"] = HEIGHT - shot["r"]
                shot["vy"] = -abs(shot["vy"])
                bounced = True
            if bounced:
                shot["hit"].clear()
                if shot["explode"] and owner:
                    _area_damage(gs, owner, shot["x"], shot["y"],
                                 60 * shot["r"] / max(1, 14), shot["explode"],
                                 weapon=shot["weapon"], knockback=1)
        elif shot["x"] < -90 or shot["x"] > WIDTH + 90 or shot["y"] < -90 or shot["y"] > HEIGHT + 90:
            dead = True
        if shot["returning"] and owner and shot["age"] > shot["return_at"] + 6:
            if math.hypot(owner["x"] - shot["x"], owner["y"] - shot["y"]) < 28:
                dead = True
        if not dead:
            for enemy in gs["enemies"]:
                if enemy["hp"] <= 0 or enemy["id"] in shot["hit"]:
                    continue
                if _gap_xy(enemy, shot["x"], shot["y"]) <= shot["r"] + enemy["radius"]:
                    stats = {"crit": shot["crit"], "crit_mult": shot["crit_mult"],
                             "heal_on_crit": shot["heal_on_crit"]}
                    mult = _roll(gs, stats)
                    _damage(gs, owner, enemy, shot["damage"] * mult,
                            src=(shot["x"], shot["y"]), knockback=shot["knockback"],
                            crit=mult > 1, heal=shot["heal_on_crit"])
                    shot["hit"].add(enemy["id"])
                    if shot["kind"] == "fire":
                        _effect(gs, shot["weapon"], "burst", life=6,
                                x=shot["x"], y=shot["y"], r=34)
                    if shot["pierce"] <= 1:
                        dead = True
                        break
                    shot["pierce"] -= 1
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
            angle = math.atan2(owner["y"] - field["y"], owner["x"] - field["x"])
            field["x"] += math.cos(angle) * 40 * STEP
            field["y"] += math.sin(angle) * 40 * STEP
            field["r"] = min(field["max_r"], field["r"] + field["grow"])
        if gs["elapsed"] >= field["next"] and field["left"] > 0:
            _area_damage(gs, owner, field["x"], field["y"], field["r"],
                         field["damage"], weapon=field["weapon"],
                         knockback=field["knockback"])
            field["next"] += field["tick"]
            field["left"] -= 1
        if field["left"] > 0:
            remaining.append(field)
    gs["fields"] = remaining


def _step_orbits(gs):
    for p in gs["players"].values():
        if not p["hp"]:
            continue
        expired = []
        for weapon, orbit in p["orbits"].items():
            if gs["elapsed"] >= orbit["until"]:
                expired.append(weapon)
                continue
            orbit["angle"] += orbit["speed"] * STEP
            hits = p["orbit_hits"].setdefault(weapon, {})
            for index in range(orbit["count"]):
                angle = orbit["angle"] + index * math.tau / orbit["count"]
                x = p["x"] + math.cos(angle) * orbit["radius"]
                y = p["y"] + math.sin(angle) * orbit["radius"]
                for enemy in gs["enemies"]:
                    if enemy["hp"] <= 0:
                        continue
                    if hits.get(enemy["id"], 0) > gs["elapsed"]:
                        continue
                    if _gap_xy(enemy, x, y) <= orbit["hit_r"] + enemy["radius"]:
                        stats = {"crit": orbit["crit"], "crit_mult": orbit["crit_mult"],
                                 "heal_on_crit": orbit["heal_on_crit"]}
                        mult = _roll(gs, stats)
                        _damage(gs, p, enemy, orbit["damage"] * mult,
                                src=(x, y), knockback=orbit["knockback"],
                                crit=mult > 1, heal=orbit["heal_on_crit"])
                        hits[enemy["id"]] = gs["elapsed"] + 34
        for weapon in expired:
            p["orbits"].pop(weapon, None)


def _collect(gs, alive):
    remaining = []
    for item in gs["loot"]:
        nearby = [p for p in alive if _gap(item, p) <= 58 * p["stats"]["magnet"]]
        if not nearby:
            remaining.append(item)
            continue
        p = min(nearby, key=lambda member: _gap(item, member))
        distance = _gap(item, p)
        if distance <= 14:
            gs["xp"] += _xp_gain(gs, p, item["xp"])
        else:
            fraction = min(1, 220 * STEP / distance)
            for axis in ("x", "y"):
                item[axis] += (p[axis] - item[axis]) * fraction
            remaining.append(item)
    gs["loot"] = remaining

    chests = []
    for chest in gs["chests"]:
        target = next((p for p in alive if _gap(chest, p) <= 36), None)
        if target:
            _open_chest(gs, chest, target)
        else:
            chests.append(chest)
    gs["chests"] = chests


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


def _chest_reward(gs, p):
    evolved = _try_evolve(p)
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
    if p["hp"] > 0:
        p["hp"] = min(p["max_hp"], p["hp"] + 30)
        return "修复 30 点耐久"
    return "打捞到一枚锈币"


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
    for filler in ("repair", "salvage", "rest"):
        if len(choices) >= 3:
            break
        if filler not in choices:
            choices.append(filler)
    gs["rng"].shuffle(choices)
    return choices[:4]


def _offer(gs):
    gs["xp"] -= threshold(gs)
    gs["level"] += 1
    gs["growth_boost"] = 2.0 if gs["level"] in (20, 40) else 1.0
    gs["pick_until"] = gs["frame"] + 120
    for p in gs["players"].values():
        if p["hp"] > 0:
            p["choices"] = _choices_for(gs, p)


def pick(gs, uid, level, choice):
    p = gs["players"].get(uid)
    if not p or type(level) is not int or level != gs["level"] or choice not in p["choices"]:
        return False
    applied = False
    if choice in data.WEAPONS:
        applied = _upgrade_weapon(p, choice, gs)
    elif choice in data.PASSIVES:
        applied = _upgrade_passive(p, choice)
    elif choice == "repair":
        if p["hp"]:
            p["hp"] = min(p["max_hp"], p["hp"] + 30)
        else:
            p["down"] += 100
        applied = True
    elif choice == "rest":
        if p["hp"]:
            p["hp"] = min(p["max_hp"], p["hp"] + 15)
        applied = True
    elif choice == "salvage":
        gs["salvage"] += 100
        applied = True
    p["choices"] = []
    return applied


def _contact_damage(gs, alive):
    for p in alive:
        if p["invincible"] > gs["elapsed"]:
            continue
        hit = next((enemy for enemy in gs["enemies"]
                    if _gap(enemy, p) < enemy["radius"] + 10), None)
        if not hit:
            continue
        damage = 12 if hit["kind"] == "warden" else 6
        damage = max(0, damage - p["stats"]["armor"])
        p["hp"] = max(0, p["hp"] - damage)
        # 受击后 1 秒无敌，对齐 VS 的 i-frame；0.8 秒在怪潮里几乎等于没有。
        p["invincible"] = gs["elapsed"] + 20
        if not p["hp"]:
            stop(gs, p["id"])
            p.update(down=gs["elapsed"] + 300, rescue=0)
            p["orbits"].clear()
            p["orbit_hits"].clear()
            gs["shots"] = [shot for shot in gs["shots"] if shot["owner"] != p["id"]]
            gs["fields"] = [field for field in gs["fields"] if field["owner"] != p["id"]]
            gs["strikes"] = [strike for strike in gs["strikes"] if strike["owner"] != p["id"]]


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


def advance(gs):
    if gs["result"]:
        return
    gs["frame"] += 1
    gs["effects"] = [effect for effect in gs["effects"] if effect["until"] > gs["frame"]]
    members = list(gs["players"].values())
    if gs["pick_until"]:
        if gs["frame"] >= gs["pick_until"]:
            for p in members:
                if p["choices"]:
                    pick(gs, p["id"], gs["level"], p["choices"][0])
        if any(p["choices"] for p in members):
            return
        gs["pick_until"] = 0
    gs["elapsed"] += 1
    elapsed = gs["elapsed"]
    seconds = elapsed * STEP
    gs["count"] = max(1, sum(not p["departed"] for p in members))
    alive = [p for p in members if p["hp"] > 0]
    if not alive:
        gs["result"] = "lost"
        return

    for p in members:
        if p["hp"]:
            if p["input_until"] > gs["frame"] and (p["dx"] or p["dy"]):
                amount = data.PLAYER_SPEED * p["stats"]["move_speed"] * STEP
                p["x"] = _clamp(p["x"] + p["dx"] * amount, WIDTH)
                p["y"] = _clamp(p["y"] + p["dy"] * amount, HEIGHT)
                p["angle"] = math.atan2(p["dy"], p["dx"])
            if p["stats"]["recovery"] > 0:
                p["hp"] = min(p["max_hp"], p["hp"] + p["stats"]["recovery"] * STEP)
        elif not p["out"]:
            if elapsed >= p["down"]:
                p["out"] = True
            else:
                p["rescue"] = p["rescue"] + 1 if any(
                    _gap(p, other) < 52 for other in alive) else 0
                if p["rescue"] >= 40:
                    p.update(hp=35, down=0, rescue=0, invincible=elapsed + 30)

    phase = sum(seconds >= gs["duration"] * limit for limit in (1 / 6, 5 / 12, 2 / 3))
    rate = 1.9 if seconds >= gs["duration"] * 11 / 12 and not gs["boss_dead"] else (0.85, 1.3, 2.0, 2.7)[phase]
    gs["spawn_budget"] += rate * _scale(gs) * STEP
    cap = min(160, 50 + gs["count"] * 20)
    while gs["spawn_budget"] >= 1 and len(gs["enemies"]) < cap:
        gs["spawn_budget"] -= 1
        p = alive[gs["spawn_cursor"] % len(alive)]
        gs["spawn_cursor"] += 1
        kind = gs["rng"].choices(
            ("drifter", "eel", "crab"),
            ((8, 0, 0), (6, 2, 2), (5, 3, 3), (4, 4, 4))[phase],
        )[0]
        _spawn(gs, kind, p)
    gs["spawn_budget"] = min(2, gs["spawn_budget"])
    _spawn_events(gs, seconds, alive)

    for enemy in gs["enemies"]:
        target = min(alive, key=lambda p: _gap(p, enemy))
        angle = math.atan2(target["y"] - enemy["y"], target["x"] - enemy["x"])
        step = min(enemy["speed"] * STEP, _gap(enemy, target))
        enemy["x"] += step * math.cos(angle)
        enemy["y"] += step * math.sin(angle)
        enemy["angle"] = angle

    for p in alive:
        for weapon, level in list(p["weapons"].items()):
            if elapsed >= p["cycles"].get(weapon, 0):
                _fire(gs, p, weapon, level)
    _step_strikes(gs)
    _step_fields(gs)
    _step_shots(gs)
    _step_orbits(gs)

    gs["enemies"] = [enemy for enemy in gs["enemies"] if enemy["hp"] > 0]
    _contact_damage(gs, alive)
    alive = [p for p in members if p["hp"] > 0]
    _collect(gs, alive)
    if not any(p["hp"] for p in members):
        gs["result"] = "lost"
    elif seconds >= gs["duration"]:
        gs["result"] = "won" if gs["boss_dead"] else "timeout"
    elif gs["xp"] >= threshold(gs):
        _offer(gs)


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
        "level": gs["level"], "xp": gs["xp"], "next_xp": threshold(gs),
        "kills": gs["kills"], "salvage": gs["salvage"],
        "choice_seconds": max(0, math.ceil((gs["pick_until"] - gs["frame"]) * STEP)),
        "result": gs["result"], "boss": gs["boss"], "boss_dead": gs["boss_dead"],
        "players": [{
            "id": p["id"], "name": p["name"], "position": point(p),
            "angle": round(p["angle"], 2), "hp": round(p["hp"], 1),
            "max_hp": round(p["max_hp"], 1), "out": p["out"],
            "down": max(0, math.ceil((p["down"] - gs["elapsed"]) * STEP)),
            "rescue": p["rescue"], "weapons": dict(p["weapons"]),
            "passives": dict(p["passives"]), "choices": list(p["choices"]),
            "kills": p["kills"],
            "move_speed": round(data.PLAYER_SPEED * p["stats"]["move_speed"]),
            "stats": {
                key: round(p["stats"][key], 3)
                for key in ("might", "area", "cooldown", "duration", "speed",
                            "amount", "magnet", "luck", "growth", "armor",
                            "recovery")
            },
        } for p in gs["players"].values()],
        "enemies": [[e["id"], e["kind"], *point(e), round(e["angle"], 2),
                     round(e["hp"], 1), e["max_hp"]] for e in gs["enemies"]],
        "loot": [[item["id"], *point(item)] for item in gs["loot"]],
        "chests": [[chest["id"], *point(chest)] for chest in gs["chests"]],
        "chest_events": [
            event for event in gs["chest_events"]
            if event["frame"] >= gs["frame"] - 100
        ][-6:],
        "effects": [dict(effect) for effect in gs["effects"]],
        "fields": [[field["id"], field["weapon"], *point(field), round(field["r"])]
                   for field in gs["fields"]],
        "shots": [[shot["id"], shot["weapon"], *point(shot),
                   round(shot["angle"], 2), round(shot["r"])]
                  for shot in gs["shots"] if shot["delay"] <= 0],
        "orbits": orbits,
        "auras": auras,
    }