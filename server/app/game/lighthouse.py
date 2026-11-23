"""Pure rules for the lighthouse economy. Resource stocks store sixtieths."""
from dataclasses import dataclass


HOUR = 3600
DAY = 24 * HOUR
OFFLINE_CAP = DAY
SCALE = 60


@dataclass(frozen=True)
class Route:
    name: str
    hours: int
    parts: int
    tickets: int
    cargo: str


ROUTES = (
    Route("近湾渡口", 12, 900, 300, "把重建后的第一批渡口器材送到对岸。"),
    Route("白礁渔村", 24, 2_400, 800, "带去新浮标，给返航渔船标出暗礁。"),
    Route("雾角邮站", 36, 3_600, 2_100, "恢复雾中邮路，优先凑齐沿途靠泊许可。"),
    Route("蓝湾市场", 48, 8_000, 2_700, "把修船物资送到群岛集市。"),
    Route("风车群岛", 48, 12_000, 4_200, "运送风车轴承和备用帆布。"),
    Route("灰鲸海峡", 60, 13_000, 8_800, "为狭窄水道备齐引航许可。"),
    Route("鸥石造船港", 60, 26_000, 9_000, "运送船坞扩建所需的大批零件。"),
    Route("远潮研究站", 72, 28_000, 17_000, "准备研究站观测设备与远海通行手续。"),
    Route("北针观测台", 72, 48_000, 16_000, "运送北方观测台的基座和锚链。"),
    Route("星湾航标群", 84, 54_000, 30_000, "为夜航补齐各段灯标的通行凭证。"),
    Route("深蓝补给港", 96, 82_000, 28_000, "为主航线建立最后一处大型补给仓。"),
    Route("环岛主航线", 108, 104_000, 50_000, "连接近湾与深蓝港，完成群岛航运网。"),
)

SALVAGE_UPGRADES = ((900, 0, 1, 4), (5000, 0, 2, 6), (18000, 0, 3, 8))
SIGNAL_UPGRADES = ((120, 30, 0, 1), (0, 1800, 1, 2), (0, 8500, 2, 3), (0, 22000, 3, 4))
MODES = ("balanced", "parts", "tickets")


def new_save(now):
    return dict(parts=0, tickets=0, charts=0, updated_at=now, revision=0,
                started_at=None, lit=False, battery=False, dock=False,
                salvage_on=False, signal_on=False, salvage_level=0, signal_level=0,
                completed=0, arrival_at=None, order_window=-1, completed_at=None, mode="balanced")


def rates(state, mode=None):
    salvage = 6 + sum(level[3] for level in SALVAGE_UPGRADES[:state["salvage_level"]])
    signal = 1 + sum(level[3] for level in SIGNAL_UPGRADES[:state["signal_level"]])
    mode = mode or state.get("mode", "balanced")
    if mode == "parts":
        salvage = (salvage * 3 + 1) // 2
        signal = max(1, signal * 3 // 4)
    if mode == "tickets":
        salvage = salvage * 3 // 4
        signal = (signal * 3 + 1) // 2
    return {"parts": salvage if state["salvage_on"] else 0,
            "tickets": signal if state["signal_on"] else 0}


def settle(state, now):
    now = max(now, state["updated_at"])
    gap = now - state["updated_at"]
    elapsed = min(OFFLINE_CAP, gap)
    gains = {key: rate * elapsed for key, rate in rates(state).items()}
    for key, gain in gains.items():
        state[key] += gain
    state["updated_at"] = now
    arrived = None
    if state["arrival_at"] is not None and now >= state["arrival_at"]:
        arrived = ROUTES[state["completed"]].name
        state["completed"] += 1
        state["charts"] += 1
        if state["completed"] == len(ROUTES):
            state["completed_at"] = state["arrival_at"]
        state["arrival_at"] = None
        state["revision"] += 1
    return {"seconds": elapsed, "gains": {key: value / SCALE for key, value in gains.items()},
            "arrived": arrived, "capped": gap > OFFLINE_CAP}


def current_route(state):
    return ROUTES[state["completed"]] if state["completed"] < len(ROUTES) else None


def order_window(state, route):
    if route is None or state["arrival_at"] is None:
        return None
    departed = state["arrival_at"] - route.hours * HOUR
    return (state["updated_at"] - departed) // DAY


def offers(state):
    result = {}

    def offer(action, parts=0, tickets=0, charts=0, rate_gain=0):
        result[action] = dict(parts=parts, tickets=tickets, charts=charts, rate_gain=rate_gain)

    if state["started_at"] is None:
        offer("start")
        return result
    if not state["lit"]:
        offer("light", parts=6)
        return result
    offer("power_salvage")
    offer("power_signal")
    if not state["battery"]:
        offer("battery", parts=48)
        return result
    if not state["dock"]:
        offer("dock", parts=180, tickets=60)
        return result
    for action, key, improvements in (
        ("upgrade_salvage", "salvage_level", SALVAGE_UPGRADES),
        ("upgrade_signal", "signal_level", SIGNAL_UPGRADES),
    ):
        if state[key] < len(improvements):
            offer(action, *improvements[state[key]])
    if state["completed"] >= 3:
        for mode in MODES:
            if mode != state.get("mode", "balanced"):
                offer(f"mode_{mode}")
    route = current_route(state)
    if route and state["arrival_at"] is None:
        offer("dispatch", route.parts, route.tickets)
    window = order_window(state, route)
    if window is not None and state["order_window"] < window:
        offer("order_parts", parts=route.parts // 4)
        offer("order_tickets", tickets=route.tickets // 4)
    return result


def apply_action(state, action):
    cost = offers(state).get(action)
    if cost is None:
        raise ValueError("这项操作当前不可用")
    if any(state[key] < cost[key] * SCALE for key in ("parts", "tickets")) or state["charts"] < cost["charts"]:
        raise ValueError("补给不足")
    for key in ("parts", "tickets"):
        state[key] -= cost[key] * SCALE
    state["charts"] -= cost["charts"]
    if action == "start":
        state["started_at"] = state["updated_at"]
        state["salvage_on"] = True
    elif action == "light":
        state["lit"] = True
    elif action == "battery":
        state["battery"] = True
        state["salvage_on"] = state["signal_on"] = True
    elif action == "dock":
        state["dock"] = True
    elif action.startswith("power_"):
        device = action.removeprefix("power_")
        key = f"{device}_on"
        state[key] = not state[key]
        if state[key] and not state["battery"]:
            other = "salvage" if device == "signal" else "signal"
            state[f"{other}_on"] = False
    elif action.startswith("upgrade_"):
        state[f'{action.removeprefix("upgrade_")}_level'] += 1
    elif action.startswith("mode_"):
        state["mode"] = action.removeprefix("mode_")
    elif action == "dispatch":
        route = current_route(state)
        state["arrival_at"] = state["updated_at"] + route.hours * HOUR
        state["order_window"] = -1
    else:
        state["order_window"] = order_window(state, current_route(state))
        state["charts"] += 1
    state["revision"] += 1


def view(state, summary, now):
    route = current_route(state)
    window = order_window(state, route)
    next_index = state["completed"] + 1
    upcoming = ROUTES[next_index] if next_index < len(ROUTES) and window is not None else None
    next_order_at = None
    if window is not None:
        candidate = state["arrival_at"] - route.hours * HOUR + (window + 1) * DAY
        if candidate < state["arrival_at"]:
            next_order_at = candidate
    return {
        **state,
        "parts": state["parts"] / SCALE,
        "tickets": state["tickets"] / SCALE,
        "rates": rates(state),
        "offers": offers(state),
        "summary": summary,
        "now": max(now, state["updated_at"]),
        "route": vars(route) if route else None,
        "routes": [item.name for item in ROUTES],
        "offline_cap": OFFLINE_CAP,
        "next_order_at": next_order_at,
        "upcoming": vars(upcoming) if upcoming else None,
        "mode": state.get("mode", "balanced"),
        "mode_rates": {mode: rates(state, mode) for mode in MODES} if state["completed"] >= 3 else None,
    }
