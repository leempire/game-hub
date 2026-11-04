"""Public, rank-independent piece tracking for hidden military chess."""


def token(cell: dict, r: int, c: int) -> str:
    # The starting square is already public. Never include the hidden rank.
    return cell.get("token") or f"{cell['c']}:{r}:{c}"


def ensure_tokens(board: list):
    for r, row in enumerate(board):
        for c, cell in enumerate(row):
            if cell is not None:
                cell.setdefault("token", token(cell, r, c))


def mark_moved(gs: dict, piece_token: str):
    moved = gs.setdefault("_moved_tokens", [])
    if piece_token not in moved:
        moved.append(piece_token)


def public_identity(gs: dict, cell: dict, r: int, c: int) -> dict:
    value = token(cell, r, c)
    return {"token": value, "moved": value in gs.get("_moved_tokens", [])}


def advance_setup(gs: dict, elapsed_ms: int) -> bool:
    """At an optional deadline, auto-confirm the existing legal setup."""
    if elapsed_ms < 0:
        raise ValueError("时间差不能为负")
    left = gs.get("setup_remaining_ms")
    if left is None or gs.get("phase") != "setup" or gs.get("finished") or gs.get("winner"):
        return False
    gs["setup_remaining_ms"] = max(0, left - int(elapsed_ms))
    if gs["setup_remaining_ms"]:
        return False
    gs["ready"] = {"red": True, "black": True}
    gs["phase"] = "playing"
    return True
