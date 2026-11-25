"""Authoritative simulation for a shared, non-contact fish race."""

from bisect import bisect_right
import math
import random

HZ = 30
STEP = 1 / HZ
DURATION = 600
MAP_SIZES = {"small": (840, 600), "medium": (1200, 850), "large": (1600, 1100)}
EVENTS = ((55, "school"), (125, "current"), (200, "school"), (275, "current"),
          (350, "school"), (425, "current"), (505, "school"), (560, "current"))
LEGACY_EVENTS = ((25, "school"), (55, "current"), (85, "school"), (105, "current"))
EVENT_LENGTH = {"school": 22, "current": 16}
PHASE_STARTS = (90, 225, 390, 510)
PHASE_PREDATORS = (56, 73, 92, 105)
SIZE_THRESHOLDS = (0, 7, 28, 85, 220, 560, 1200)
SIZE_RADII = (14, 19, 26, 35, 47, 65, 82)
WILD_RADII = (7, 10, 16, 23, 31, 41, 56, 73, 92, 105)
WILD_WEIGHTS = (
    (31, 27, 18, 13, 7, 4, 0, 0, 0, 0),
    (23, 23, 18, 14, 11, 7, 4, 0, 0, 0),
    (18, 18, 16, 14, 13, 10, 7, 4, 0, 0),
    (14, 15, 15, 14, 12, 11, 8, 6, 5, 0),
    (12, 13, 14, 13, 12, 11, 9, 7, 6, 3),
)


def phase(frame):
    return bisect_right(PHASE_STARTS, frame // HZ)


def growth(score):
    return bisect_right(SIZE_THRESHOLDS, score) - 1


def duration(gs):
    # Preserve the timer of in-flight rounds saved before the ten-minute rules.
    return gs.get("duration", 120)


def swim_speed(r, boosted):
    return (174 - .40 * (r - 14)) * (1.40 if boosted else 1)



def radius(score):
    return SIZE_RADII[growth(score)]


def _point(gs):
    return (gs["rng"].uniform(55, gs["width"] - 55),
            gs["rng"].uniform(55, gs["height"] - 55))


def _spawn(gs, player):
    r = radius(player["score"])

    def clearance(point):
        return min(
            (math.hypot(point[0] - fish["x"], point[1] - fish["y"]) - fish["r"] - r
             for fish in gs["fish"] if fish["r"] > r * 1.08),
            default=gs["width"],
        )

    def opportunity(point):
        nearest_prey = min((math.hypot(point[0] - f["x"], point[1] - f["y"])
                            for f in gs["fish"] if f["r"] < r * .92), default=250)
        return min(180, clearance(point)) - min(nearest_prey, 250) * .4

    margin = max(55, r + 12)
    rng = gs["rng"]
    candidates = ((rng.uniform(margin, gs["width"] - margin),
                   rng.uniform(margin, gs["height"] - margin)) for _ in range(18))
    player["x"], player["y"] = max(candidates, key=opportunity)
    player.update(dx=0, dy=0, input_until=0, immune=gs["frame"] + 90)


def _wild(gs, size=None, expires=None):
    gs["serial"] += 1
    x, y = _point(gs)
    angle = gs["rng"].uniform(0, math.tau)
    r = size or gs["rng"].choices(WILD_RADII, WILD_WEIGHTS[phase(gs["frame"])])[0]
    return dict(id=gs["serial"], x=x, y=y, r=r, vx=math.cos(angle), vy=math.sin(angle),
                expires=expires)


def create(players, seed, map_size, items, events):
    width, height = MAP_SIZES[map_size]
    gs = dict(rng=random.Random(seed), width=width, height=height, frame=0, serial=0, duration=DURATION,
              players={}, fish=[], items=[], items_enabled=items, events_enabled=events,
              event=None, event_until=0, finished=False, reason=None)
    gs["fish"] = [_wild(gs) for _ in range({"small": 28, "medium": 42, "large": 58}[map_size])]
    gs["population"] = len(gs["fish"])
    for p in players:
        player = dict(id=p["id"], name=p.get("nickname") or p["username"], score=0,
                      lives=3, boost=0, shield=False, facing=1, departed=False)
        _spawn(gs, player)
        gs["players"][p["id"]] = player
    return gs


def move(gs, uid, dx, dy):
    if not all(type(value) in (int, float) and -1 <= value <= 1 for value in (dx, dy)):
        return
    p = gs["players"].get(uid)
    if p and p["lives"] and not p["departed"] and not gs["finished"]:
        length = max(1, math.hypot(dx, dy))
        p.update(dx=dx / length, dy=dy / length, input_until=gs["frame"] + 18)


def stop(gs, uid):
    if uid in gs["players"]:
        gs["players"][uid].update(dx=0, dy=0)


def depart(gs, uid):
    gs["players"][uid]["departed"] = True
    _check_end(gs)


def _check_end(gs):
    if not any(p["lives"] and not p["departed"] for p in gs["players"].values()):
        gs["reason"] = "no_survivors"
    elif gs["frame"] >= duration(gs) * HZ:
        gs["reason"] = "time"
    gs["finished"] = gs["reason"] is not None


def _touches(a, b, distance):
    return (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 <= distance ** 2


def _event_schedule(gs):
    return EVENTS if "duration" in gs else LEGACY_EVENTS


def _update_ecosystem(gs):
    frame = gs["frame"]
    if gs["event_until"] <= frame and gs["event"]:
        gs["event"] = None
        gs["fish"] = [f for f in gs["fish"] if not f.get("expires")]
    if gs["events_enabled"] and frame % HZ == 0:
        for second, kind in _event_schedule(gs):
            if frame != second * HZ:
                continue
            end = frame + EVENT_LENGTH[kind] * HZ
            gs.update(event=kind, event_until=end)
            if kind == "school":
                gs["fish"].extend(_wild(gs, gs["rng"].choice((7, 10, 16)), end)
                                  for _ in range(max(8, gs["population"] // 4)))
            break
    if gs["items_enabled"] and frame % (6 * HZ) == 0:
        gs["items"] = [item for item in gs["items"] if item.get("expires", frame) > frame]
        if len(gs["items"]) < max(3, gs["population"] // 10):
            gs["serial"] += 1
            x, y = _point(gs)
            gs["items"].append(dict(id=gs["serial"], x=x, y=y, expires=frame + 30 * HZ,
                                    kind=gs["rng"].choice(("boost", "boost", "shield"))))
    # Slowly cycle the background population even in areas players rarely visit.
    if frame % HZ == 0 and frame // HZ in PHASE_STARTS:
        candidate = _wild(gs, PHASE_PREDATORS[phase(frame) - 1])
        living = [p for p in gs["players"].values() if p["lives"] and not p["departed"]]
        points = [_point(gs) for _ in range(10)]
        candidate["x"], candidate["y"] = max(
            points, key=lambda point: min(((point[0] - p["x"]) ** 2 + (point[1] - p["y"]) ** 2
                                           for p in living), default=0))
        ambient = next((f for f in gs["fish"] if not f.get("expires")), None)
        if ambient is not None:
            gs["fish"].remove(ambient)
        gs["fish"].append(candidate)
    elif frame % (4 * HZ) == 0:
        ambient = next((f for f in gs["fish"] if not f.get("expires")), None)
        if ambient is not None:
            gs["fish"].remove(ambient)


def advance(gs, online):
    if gs["finished"]:
        return
    gs["frame"] += 1
    frame = gs["frame"]
    _update_ecosystem(gs)
    for fish in gs["fish"]:
        speed = (42 + min(47, fish["r"] * .74)) * (1.35 if gs["event"] == "current" else 1)
        for axis, velocity, extent in (("x", "vx", "width"), ("y", "vy", "height")):
            pos = fish[axis] + fish[velocity] * speed * STEP
            edge = fish["r"]
            if pos < edge or pos > gs[extent] - edge:
                fish[velocity] *= -1
            fish[axis] = max(edge, min(gs[extent] - edge, pos))
    # Rotate processing order so equally placed players alternate pickup priority.
    players = list(gs["players"].values())
    offset = frame % len(players)
    for p in players[offset:] + players[:offset]:
        if p["departed"] or not p["lives"] or p["id"] not in online:
            continue
        if frame > p["input_until"]:
            stop(gs, p["id"])
        r = radius(p["score"])
        speed = swim_speed(r, p["boost"] > frame)
        p["x"] = max(r, min(gs["width"] - r, p["x"] + p["dx"] * speed * STEP))
        p["y"] = max(r, min(gs["height"] - r, p["y"] + p["dy"] * speed * STEP))
        if abs(p["dx"]) > .05:
            p["facing"] = 1 if p["dx"] > 0 else -1
        for item in gs["items"][:]:
            if _touches(p, item, r + 11):
                gs["items"].remove(item)
                if item["kind"] == "boost":
                    p["boost"] = frame + 150
                else:
                    p["shield"] = True
        if p["immune"] <= frame and any(
            f["r"] > r * 1.08 and _touches(p, f, (r + f["r"]) * .59)
            for f in gs["fish"]
        ):
            if p["shield"]:
                p.update(shield=False, immune=frame + 30)
            else:
                p["lives"] -= 1
                if p["lives"]:
                    p["boost"] = 0
                    _spawn(gs, p)
                continue
        for f in gs["fish"][:]:
            if f["r"] < r * .92 and _touches(p, f, (r + f["r"]) * .82):
                p["score"] += max(1, round(f["r"] / 6))
                gs["fish"].remove(f)
    if frame % 6 == 0 and len(gs["fish"]) < gs["population"]:
        gs["fish"].append(_wild(gs))
    _check_end(gs)


def ranking(gs):
    players = sorted(gs["players"].values(), key=lambda p: (p["departed"], -p["score"], p["id"]))
    place, previous = 0, None
    rows = []
    for index, p in enumerate(players, 1):
        if p["score"] != previous:
            place = index
        previous = p["score"]
        rows.append(dict(id=p["id"], name=p["name"], score=p["score"],
                         rank=None if p["departed"] else place, lives=p["lives"]))
    return rows


def snapshot(gs):
    frame = gs["frame"]
    upcoming = None
    if gs["events_enabled"] and not gs["event"]:
        upcoming = next(((kind, second * HZ - frame) for second, kind in _event_schedule(gs)
                         if 0 < second * HZ - frame <= 5 * HZ), None)
    return dict(
        game="fish", frame=frame, width=gs["width"], height=gs["height"],
        duration=duration(gs), remaining=max(0, duration(gs) - frame / HZ),
        event=gs["event"], event_remaining=max(0, gs["event_until"] - frame) / HZ,
        upcoming=upcoming, finished=gs["finished"],
        players=[dict(id=p["id"], name=p["name"], x=round(p["x"]), y=round(p["y"]),
                      score=p["score"], r=radius(p["score"]), lives=p["lives"], tier=growth(p["score"]) + 1,
                      growth_from=SIZE_THRESHOLDS[growth(p["score"])],
                      growth_to=SIZE_THRESHOLDS[growth(p["score"]) + 1] if growth(p["score"]) + 1 < len(SIZE_THRESHOLDS) else None,
                      speed=swim_speed(radius(p["score"]), p["boost"] > frame),
                      facing=p["facing"], immune=max(0, p["immune"] - frame),
                      boost=p["boost"] > frame, shield=p["shield"])
                 for p in gs["players"].values() if not p["departed"]],
        fish=[[f["id"], round(f["x"]), round(f["y"]), f["r"], 1 if f["vx"] > 0 else -1]
              for f in gs["fish"]],
        items=[[i["id"], round(i["x"]), round(i["y"]), i["kind"]] for i in gs["items"]],
        ranking=ranking(gs) if gs["finished"] else [],
    )
