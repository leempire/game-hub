"""Gomoku AI — heuristic pattern evaluation + minimax with alpha-beta pruning.

Interface: best_move(board, player) -> (x, y) | None
Board: list[list[int]] (15×15), board[y][x], EMPTY=0 / BLACK=1 / WHITE=2.
"""

import copy
import time
from app.game.gomoku import (
    BOARD_SIZE, EMPTY, BLACK, WHITE, DIRECTIONS,
    place_stone, check_win,
)

_SCORES = {5: 100000, 42: 9000, 41: 8000, 32: 900, 31: 100, 22: 50, 21: 5}
_TIME_BUDGET = 0.15
_CENTER = BOARD_SIZE // 2


# ── helpers ────────────────────────────────────────────────────────

def _in_bounds(x: int, y: int) -> bool:
    return 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE


def _opp(color: int) -> int:
    return WHITE if color == BLACK else BLACK


def _center_dist(x: int, y: int) -> int:
    return abs(x - _CENTER) + abs(y - _CENTER)


# ── pattern evaluation ─────────────────────────────────────────────

def _score_pattern(count: int, open_ends: int) -> int:
    """Score a line pattern by consecutive count and open-end count."""
    if count >= 5:
        return 100000
    if open_ends == 0:
        return 0
    return _SCORES.get(count * 10 + open_ends, 0)


def _count_dir(board, x, y, dx, dy, color):
    """Count consecutive *color* stones from (x,y) stepping (dx,dy)."""
    n = 0
    nx, ny = x + dx, y + dy
    while _in_bounds(nx, ny) and board[ny][nx] == color:
        n += 1
        nx += dx
        ny += dy
    return n


def _score_pos(board, x, y, color):
    """Sum pattern scores through (x, y) for *color*."""
    total = 0
    for dx, dy in DIRECTIONS:
        pos = _count_dir(board, x, y, dx, dy, color)
        neg = _count_dir(board, x, y, -dx, -dy, color)
        count = 1 + pos + neg
        oe = 0
        ax, ay = x + dx * (pos + 1), y + dy * (pos + 1)
        if _in_bounds(ax, ay) and board[ay][ax] == EMPTY:
            oe += 1
        bx, by = x - dx * (neg + 1), y - dy * (neg + 1)
        if _in_bounds(bx, by) and board[by][bx] == EMPTY:
            oe += 1
        total += _score_pattern(count, oe)
    return total


def _evaluate(board, player):
    """Positional eval: positive favours *player*."""
    return _evaluate_side(board, player) - _evaluate_side(board, _opp(player))


def _evaluate_side(board, color):
    total = 0
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] == color:
                total += _score_pos(board, x, y, color)
    return total


# ── candidate generation ──────────────────────────────────────────

def _candidates(board, max_moves=20):
    """Empty cells within radius of any stone, best first."""
    stone_count = sum(
        1 for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
        if board[y][x] != EMPTY
    )
    if stone_count == 0:
        return [(_CENTER, _CENTER)]

    cands, seen = [], set()
    radius = 2 if stone_count >= 4 else 3
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] != EMPTY:
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        nx, ny = x + dx, y + dy
                        if (_in_bounds(nx, ny) and board[ny][nx] == EMPTY
                                and (nx, ny) not in seen):
                            seen.add((nx, ny))
                            cands.append((nx, ny))

    cands.sort(key=lambda m: _center_dist(m[0], m[1]))
    if stone_count < 4:
        return cands[:max_moves]

    scored = []
    for mx, my in cands:
        board[my][mx] = BLACK
        s = _score_pos(board, mx, my, BLACK)
        board[my][mx] = EMPTY
        scored.append((s, mx, my))
    scored.sort(reverse=True)
    return [(x, y) for _, x, y in scored[:max_moves]]


# ── threat detection ──────────────────────────────────────────────

def _find_immediate_win(board, player):
    """Return (x,y) that wins in one move, or None."""
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] == EMPTY:
                board[y][x] = player
                won = check_win(board, x, y)
                board[y][x] = EMPTY
                if won:
                    return (x, y)
    return None


def _find_threats(board, opp_color):
    """Find empty cells where opp_color could complete five in one move.

    Uses 5-cell sliding window: exactly 4 opponent stones + 1 empty cell.
    Covers consecutive (XXXX_) and gap (XX_XX, X_XXX) patterns.
    """
    threats = set()
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] != EMPTY:
                continue
            if _is_threat_cell(board, x, y, opp_color):
                threats.add((x, y))
    return threats


def _is_threat_cell(board, x, y, color):
    """Would placing *color* at empty (x,y) complete five-in-a-row?"""
    for dx, dy in DIRECTIONS:
        for start in range(-4, 1):
            sx, sy = x + start * dx, y + start * dy
            ex, ey = sx + 4 * dx, sy + 4 * dy
            if not (_in_bounds(sx, sy) and _in_bounds(ex, ey)):
                continue
            cnt = 0
            valid = True
            for i in range(5):
                cx, cy = sx + i * dx, sy + i * dy
                cell = board[cy][cx]
                if cell == color:
                    cnt += 1
                elif not (cx == x and cy == y):
                    valid = False
                    break
            if valid and cnt == 4:
                return True
    return False


# ── minimax (NegaMax) ─────────────────────────────────────────────

def _minimax(board, last_x, last_y, last_player,
             depth, alpha, beta, deadline):
    """NegaMax: returns (score, move) from the about-to-move player's view.

    *last_player* just placed at (last_x, last_y); we check win there.
    """
    if last_player is not None and check_win(board, last_x, last_y):
        return -100000, None          # prev player won → current loses
    if time.time() > deadline:
        return 0, None

    current = _opp(last_player) if last_player else BLACK
    if depth == 0:
        return _evaluate(board, current), None

    cands = _candidates(board)
    if not cands:
        return 0, None

    best_score, best_move = -100001, None
    for mx, my in cands:
        nb = copy.deepcopy(board)
        place_stone(nb, mx, my, current)
        sc, _ = _minimax(nb, mx, my, current,
                         depth - 1, -beta, -alpha, deadline)
        sc = -sc
        if sc > best_score:
            best_score = sc
            best_move = (mx, my)
        alpha = max(alpha, sc)
        if alpha >= beta:
            break
    return best_score, best_move


# ── public API ────────────────────────────────────────────────────

def best_move(board, player):
    """Choose best move for *player*.

    Args:
        board: list[list[int]] 15×15, board[y][x], EMPTY/BLACK/WHITE.
        player: BLACK (1) or WHITE (2).
    Returns:
        (x, y) or None (board full).
    """
    if all(board[y][x] != EMPTY
           for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)):
        return None

    # empty board → centre
    if all(board[y][x] == EMPTY
           for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)):
        return (_CENTER, _CENTER)

    # 1-step win
    win = _find_immediate_win(board, player)
    if win:
        return win

    # must-block: opponent has exactly one winning cell
    threats = _find_threats(board, _opp(player))
    if len(threats) == 1:
        return threats.pop()

    # iterative deepening minimax
    deadline = time.time() + _TIME_BUDGET
    move = None
    for depth in range(2, 5):
        _, m = _minimax(board, 0, 0, None, depth,
                        -100001, 100001, deadline)
        if m is not None:
            move = m
        if time.time() > deadline:
            break
    if move is None:
        cands = _candidates(board)
        move = cands[0] if cands else None
    return move
