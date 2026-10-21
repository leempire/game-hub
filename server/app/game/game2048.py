"""2048 pure game logic — no I/O, easy to unit-test.

玩法裁决:
- 4x4 棋盘，经典 2048 规则：滑动合并，每格每次滑动最多合并一次
- 新块: 90% 概率出 2，10% 概率出 4
- 同种子公平竞速：服务端用 seed 生成确定性出块序列，双方收到相同序列
- 结束条件：一方合成 2048 立即获胜；或一方无法移动（game over）时
  对方继续直到也无法移动，然后比分数
"""

import random

BOARD_SIZE = 4
WIN_TILE = 2048


def create_board() -> list[list[int]]:
    """Create an empty 4x4 board."""
    return [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def _empty_cells(board: list[list[int]]) -> list[tuple[int, int]]:
    """Return list of (row, col) for empty cells."""
    cells = []
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            if board[r][c] == 0:
                cells.append((r, c))
    return cells


def spawn_tile(board: list[list[int]], rng: random.Random) -> tuple[int, int, int] | None:
    """Place a new tile (2 or 4) on a random empty cell.

    Returns (row, col, value) or None if board is full.
    """
    cells = _empty_cells(board)
    if not cells:
        return None
    r, c = rng.choice(cells)
    value = 4 if rng.random() < 0.1 else 2
    board[r][c] = value
    return (r, c, value)


def _slide_row_left_traced(
    row: list[int],
) -> tuple[list[int], int, list[tuple[int, int, int, bool]]]:
    """Slide a single row to the left, merging adjacent equal tiles.

    Each tile merges at most once per slide.

    Returns (new_row, score_gained, trace) where each trace entry is
    (from_col, to_col, value, merged) **in this row's frame**. `value` is the
    tile's value after arriving (doubled when merged). A merge emits two
    entries pointing at the same destination, because both tiles slide there.
    """
    # Remove zeros, keeping each tile's original column for the trace
    tiles = [(c, v) for c, v in enumerate(row) if v != 0]
    result: list[int] = []
    trace: list[tuple[int, int, int, bool]] = []
    score = 0
    i = 0
    while i < len(tiles):
        dest = len(result)
        if i + 1 < len(tiles) and tiles[i][1] == tiles[i + 1][1]:
            merged = tiles[i][1] * 2
            result.append(merged)
            score += merged
            trace.append((tiles[i][0], dest, merged, True))
            trace.append((tiles[i + 1][0], dest, merged, True))
            i += 2
        else:
            result.append(tiles[i][1])
            trace.append((tiles[i][0], dest, tiles[i][1], False))
            i += 1
    # Pad with zeros
    while len(result) < BOARD_SIZE:
        result.append(0)
    return result, score, trace


def _slide_row_left(row: list[int]) -> tuple[list[int], int]:
    """Slide a single row to the left. Returns (new_row, score_gained)."""
    new_row, score, _ = _slide_row_left_traced(row)
    return new_row, score


def _rotate_board(board: list[list[int]]) -> list[list[int]]:
    """Rotate board 90 degrees clockwise."""
    n = BOARD_SIZE
    return [[board[n - 1 - c][r] for c in range(n)] for r in range(n)]


def _rotate_board_ccw(board: list[list[int]]) -> list[list[int]]:
    """Rotate board 90 degrees counter-clockwise."""
    n = BOARD_SIZE
    return [[board[c][n - 1 - r] for c in range(n)] for r in range(n)]


def _normalize(board: list[list[int]], direction: str) -> tuple[list[list[int]], int]:
    """Rotate the board so the move becomes a slide-left.

    Returns (rotated_board, rotations); rotating the result back `rotations`
    times with _rotate_board restores the original orientation.
    """
    if direction == "left":
        return [row[:] for row in board], 0
    if direction == "up":
        return _rotate_board_ccw(board), 1
    if direction == "right":
        return _rotate_board(_rotate_board(board)), 2
    if direction == "down":
        return _rotate_board(board), 3
    raise ValueError(f"Invalid direction: {direction}")


def _unrotate_cell(row: int, col: int, rotations: int) -> tuple[int, int]:
    """Map a (row, col) from the normalized frame back to the original board.

    A cell at original (R, C) lands at rotated (C, N-1-R) under _rotate_board,
    so undoing `rotations` clockwise turns is applying that map `rotations`
    times — the inverse of whatever _normalize did.
    """
    n = BOARD_SIZE
    for _ in range(rotations):
        row, col = col, n - 1 - row
    return row, col


def move_with_trace(
    board: list[list[int]], direction: str
) -> tuple[list[list[int]], int, bool, list[dict]]:
    """Apply a move, also returning the per-tile movement trace.

    Returns (new_board, score_gained, moved, trace). Trace entries are
    {"from": [r, c], "to": [r, c], "value": v, "merged": bool} in original
    board coordinates, where `value` is the tile's value after arriving
    (doubled when merged). Drives the client's slide animation.
    """
    rotated, rotations = _normalize(board, direction)

    new_rows = []
    total_score = 0
    trace: list[dict] = []
    for r, row in enumerate(rotated):
        new_row, score, entries = _slide_row_left_traced(row)
        new_rows.append(new_row)
        total_score += score
        for from_c, to_c, value, merged in entries:
            from_r, from_col = _unrotate_cell(r, from_c, rotations)
            to_r, to_col = _unrotate_cell(r, to_c, rotations)
            trace.append({
                "from": [from_r, from_col],
                "to": [to_r, to_col],
                "value": value,
                "merged": merged,
            })

    result = new_rows
    for _ in range(rotations):
        result = _rotate_board(result)

    return result, total_score, result != board, trace


def move(board: list[list[int]], direction: str) -> tuple[list[list[int]], int, bool]:
    """Apply a move in the given direction ('up', 'down', 'left', 'right').

    Returns (new_board, score_gained, moved).
    moved is False if the board didn't change.
    """
    new_board, score, moved, _ = move_with_trace(board, direction)
    return new_board, score, moved


def has_moves(board: list[list[int]]) -> bool:
    """Check if any move is possible."""
    # Any empty cell?
    if _empty_cells(board):
        return True
    # Any adjacent equal tiles?
    for r in range(BOARD_SIZE):
        for c in range(BOARD_SIZE):
            val = board[r][c]
            if c + 1 < BOARD_SIZE and board[r][c + 1] == val:
                return True
            if r + 1 < BOARD_SIZE and board[r + 1][c] == val:
                return True
    return False


def max_tile(board: list[list[int]]) -> int:
    """Return the maximum tile value on the board."""
    return max(max(row) for row in board)


def has_won(board: list[list[int]]) -> bool:
    """Check if the board contains the winning tile (2048)."""
    return any(v >= WIN_TILE for row in board for v in row)


def board_score(board: list[list[int]]) -> int:
    """Return sum of all tiles (simple score proxy)."""
    return sum(v for row in board for v in row)


def create_seeded_rng(seed: int) -> random.Random:
    """Create a deterministic RNG from a seed."""
    return random.Random(seed)


def leaderboard(gs: dict) -> list[dict]:
    """所有玩家的实时分数榜（达成 2048 的排最前，其余按分数降序）。"""
    rows = [
        {
            "user_id": uid,
            "color": gs["player_colors"].get(uid, ""),
            "score": gs["scores"].get(uid, 0),
            "max_tile": max_tile(gs["boards"][uid]),
            "finished": gs["game_over_flags"].get(uid, False),
            "finish_reason": (gs.get("finish_reasons") or {}).get(uid),
        }
        for uid in gs["boards"]
    ]
    rows.sort(key=lambda r: (
        0 if r["finish_reason"] == "reach_2048" else 1,
        -r["score"],
    ))
    return rows


def game_to_sync(gs: dict, user_id: int) -> dict:
    """Build sync-friendly dict for a player's view of a 2048 game.

    只暴露自己的盘面；其余玩家只给分数/最大块（实时排行榜）。
    """
    pid = user_id
    return {
        "board": gs["boards"][pid],
        "score": gs["scores"][pid],
        "max_tile": max_tile(gs["boards"][pid]),
        "game_over": gs["game_over_flags"].get(pid, False),
        "finish_reason": (gs.get("finish_reasons") or {}).get(pid),
        "seed": gs["seed"],
        "move_count": gs["move_counts"].get(pid, 0),
        # 撤销只在单人局开放（多人局各玩各的、同种子竞速，撤销会破坏可比性），
        # 这里把「能不能撤销」直接下发，客户端不必自己推断
        "undo_available": bool((gs.get("undo_stacks") or {}).get(pid)),
        "your_color": gs["player_colors"].get(pid),
        "player_count": len(gs["boards"]),
        "leaderboard": leaderboard(gs),
        "winner": gs["winner"],
        "reason": gs["reason"],
        "ranking": gs.get("ranking", []),
    }


def snapshot(gs: dict) -> dict:
    """Build a final-position snapshot for history."""
    return {
        "game": "2048",
        "boards": gs["boards"],
        "scores": gs["scores"],
        "max_tiles": {uid: max_tile(b) for uid, b in gs["boards"].items()},
        "winner": gs["winner"],
        "reason": gs["reason"],
        "ranking": gs.get("ranking", []),
    }
