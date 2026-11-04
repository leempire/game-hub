"""Pure two-sided chess clock; the transport layer supplies elapsed milliseconds.

No wall clock, asyncio, or I/O. A completed move switches the active side. Optional
single-period overtime is supported; the default is a simple total-time clock.
"""


def create(seconds: int, sides: tuple[str, str], turn: str, *, byoyomi_seconds=0) -> dict | None:
    if not seconds:
        return None
    if seconds < 0 or byoyomi_seconds < 0 or turn not in sides or len(set(sides)) != 2:
        raise ValueError("无效棋钟设置")
    period = int(byoyomi_seconds * 1000)
    return {"remaining_ms": {side: int(seconds * 1000) for side in sides},
            "turn": turn, "period_ms": period, "period_left_ms": period,
            "expired": None, "paused": False}


def advance(clock: dict | None, elapsed_ms: int) -> str | None:
    """Charge only the active player; return the side whose flag has fallen."""
    if elapsed_ms < 0:
        raise ValueError("时间差不能为负")
    if not clock or clock.get("paused") or clock.get("expired"):
        return clock.get("expired") if clock else None
    side = clock["turn"]
    left = clock["remaining_ms"][side]
    charged = min(left, int(elapsed_ms))
    clock["remaining_ms"][side] -= charged
    overflow = int(elapsed_ms) - charged
    if clock["remaining_ms"][side] == 0:
        if clock.get("period_ms"):
            clock["period_left_ms"] = max(0, clock["period_left_ms"] - overflow)
            if clock["period_left_ms"] == 0:
                clock["expired"] = side
        else:
            clock["expired"] = side
    return clock["expired"]


def complete_turn(clock: dict | None, next_side: str):
    """Call only after a legal move, including a same-side Reversi auto-pass."""
    if not clock or clock.get("expired"):
        return
    if next_side not in clock["remaining_ms"]:
        raise ValueError("未知执子方")
    clock["turn"] = next_side
    clock["period_left_ms"] = clock.get("period_ms", 0)


def view(clock: dict | None) -> dict | None:
    if clock is None:
        return None
    return {**clock, "remaining_ms": dict(clock["remaining_ms"])}
