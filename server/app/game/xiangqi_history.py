"""Pure repetition adjudication and paired takebacks for Xiangqi."""


def position_key(gs: dict) -> str:
    return gs["next"] + ":" + "".join(piece or "." for row in gs["board"] for piece in row)


def ensure_history(gs: dict):
    key = position_key(gs)
    positions = gs.get("_repetition_positions") or []
    if not positions or positions[-1] != key:
        gs["_repetition_positions"] = [key]
        gs["_repetition_checks"] = []


def record_position(gs: dict, mover: str, gave_check: bool):
    gs.setdefault("_repetition_positions", []).append(position_key(gs))
    gs.setdefault("_repetition_checks", []).append({"by": mover, "check": gave_check})


def adjudicate(gs: dict) -> tuple[str, str] | None:
    positions = gs.get("_repetition_positions") or []
    if not positions:
        return None
    matches = [index for index, key in enumerate(positions) if key == positions[-1]]
    if len(matches) < 3:
        return None
    checks = (gs.get("_repetition_checks") or [])[matches[-3]:matches[-1]]
    offenders = []
    for side in ("red", "black"):
        turns = [move for move in checks if move["by"] == side]
        if len(turns) >= 2 and all(move["check"] for move in turns):
            offenders.append(side)
    if len(offenders) == 1:
        return ("black" if offenders[0] == "red" else "red", "perpetual_check")
    if len(offenders) == 2:
        return "draw", "mutual_perpetual_check"
    return None


def rewind_pair(gs: dict, color: str) -> dict:
    """Undo one move by each side; room permissions are checked by the caller."""
    if gs.get("finished") or gs.get("winner"):
        return {"ok": False, "error": "对局已结束，无法悔棋"}
    moves = gs.get("moves") or []
    if gs.get("next") != color:
        return {"ok": False, "error": "请等人机走完后再悔棋"}
    if len(moves) < 2:
        return {"ok": False, "error": "双方各走一步后才能悔棋"}
    first = moves[-2]
    for move in reversed(moves[-2:]):
        fr, fc = move["from"]
        tr, tc = move["to"]
        gs["board"][fr][fc] = move["piece"]
        gs["board"][tr][tc] = move.get("captured")
    del moves[-2:]
    gs["next"] = color
    gs["last_move"] = moves[-1] if moves else None
    trailing = 0
    for move in reversed(moves):
        if move.get("captured"):
            break
        trailing += 1
    gs["no_capture"] = first.get("_no_capture_before", trailing)
    for key in ("_repetition_positions", "_repetition_checks"):
        if key in gs:
            del gs[key][-2:]
    gs["revision"] = gs.get("revision", 0) + 1
    return {"ok": True, "removed": 2}
