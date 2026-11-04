"""Room-level deadlines: monotonic anchors and one sleeping task per timed room.

Only remaining milliseconds are persisted in the game state. A restored game
gets a fresh anchor, so server downtime is never charged. Player disconnects do
not pause clocks or setup deadlines; the existing disconnect-forfeit also runs.
"""

import asyncio
import time

from app.game import chess_clock, luzhanqi
from app import persistence

_anchors: dict[str, tuple[dict, float]] = {}
_tasks: dict[str, asyncio.Task] = {}
_resolve = None
_on_deadline = None


def _now() -> float:
    return time.monotonic()


def configure(resolve, on_deadline):
    global _resolve, _on_deadline
    _resolve, _on_deadline = resolve, on_deadline


def _anchor(room: dict, now: float) -> float:
    state = room.get("game_state")
    previous = _anchors.get(room["code"])
    if previous is None or previous[0] is not state:
        _anchors[room["code"]] = (state, now)
        return now
    return previous[1]


def initialize(room: dict):
    """Call once after constructing a new game; no time reads in pure engines."""
    gs = room["game_state"]
    if room["game"] in ("gomoku", "reversi", "xiangqi"):
        sides = ("red", "black") if room["game"] == "xiangqi" else ("black", "white")
        gs["clock"] = chess_clock.create(
            room.get("chess_clock_seconds", 0), sides, gs["next"],
            byoyomi_seconds=room.get("xiangqi_byoyomi_seconds", 0) if room["game"] == "xiangqi" else 0,
        )
    _anchors[room["code"]] = (gs, _now())
    schedule(room)


def settle(room: dict, *, now: float | None = None) -> str | None:
    """Charge elapsed service uptime. Caller handles a 'clock' or 'setup' event."""
    if room.get("status") != "playing" or not room.get("game_state"):
        return None
    now = _now() if now is None else now
    last = _anchor(room, now)
    elapsed = max(0, int((now - last) * 1000))
    # Preserve sub-millisecond remainders, including under repeated bad input.
    _anchors[room["code"]] = (room["game_state"], last + elapsed / 1000)
    gs = room["game_state"]
    if room["game"] == "luzhanqi" and luzhanqi.advance_setup(gs, elapsed):
        return "setup"
    if chess_clock.advance(gs.get("clock"), elapsed):
        return "clock"
    return None


def after_move(room: dict):
    gs = room["game_state"]
    if room.get("status") == "playing":
        chess_clock.complete_turn(gs.get("clock"), gs["next"])
        schedule(room)
    else:
        if gs.get("clock"):
            gs["clock"]["paused"] = True
        cancel(room["code"])


def decorate(room: dict, view: dict) -> dict:
    """Project a fresh clock view without mutating authoritative state."""
    gs = room.get("game_state") or {}
    now = _now()
    last = _anchor(room, now)
    elapsed = max(0, int((now - last) * 1000)) if room.get("status") == "playing" else 0
    if gs.get("clock"):
        projected = chess_clock.view(gs["clock"])
        chess_clock.advance(projected, elapsed)
        view["clock"] = projected
    if room["game"] == "luzhanqi" and gs.get("phase") == "setup":
        left = gs.get("setup_remaining_ms")
        view["setup_remaining_ms"] = max(0, left - elapsed) if left is not None else None
    return view


def _delay_ms(room: dict) -> int | None:
    gs = room.get("game_state") or {}
    if room["game"] == "luzhanqi" and gs.get("phase") == "setup":
        return gs.get("setup_remaining_ms")
    clock = gs.get("clock")
    if not clock or clock.get("paused") or clock.get("expired"):
        return None
    return clock["remaining_ms"][clock["turn"]] + clock.get("period_left_ms", 0)


def schedule(room: dict):
    if _on_deadline is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # Direct engine/room tests need no background event loop.
    cancel(room["code"])
    if room.get("status") != "playing" or _delay_ms(room) is None:
        return
    state = room.get("game_state")
    _anchor(room, _now())

    async def wait_deadline():
        current = asyncio.current_task()
        try:
            while (_resolve(room["code"]) is room and room.get("status") == "playing"
                   and room.get("game_state") is state):
                delay = _delay_ms(room)
                if delay is None:
                    break
                await asyncio.sleep(max(0.001, min(5.0, delay / 1000)))
                if (_resolve(room["code"]) is not room or room.get("status") != "playing"
                        or room.get("game_state") is not state):
                    break
                event = settle(room)
                if event:
                    await _on_deadline(room["code"], room, event)
                    break
                persistence.save_room(room)
                await _on_deadline(room["code"], room, "checkpoint")
        finally:
            if _tasks.get(room["code"]) is current:
                _tasks.pop(room["code"], None)
    _tasks[room["code"]] = loop.create_task(wait_deadline())


def cancel(code: str):
    task = _tasks.pop(code, None)
    if task is not None:
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not current:
            task.cancel()


def forget(code: str):
    cancel(code)
    _anchors.pop(code, None)


async def shutdown(rooms: list[dict]):
    """Checkpoint exact remaining service time during a graceful server stop."""
    for room in rooms:
        if room.get("status") == "playing" and _delay_ms(room) is not None:
            event = settle(room)
            if event and _on_deadline is not None:
                await _on_deadline(room["code"], room, event)
            persistence.save_room(room)
        forget(room["code"])
