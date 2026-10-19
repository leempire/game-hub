"""Reversi AI — positional weights + mobility + minimax with alpha-beta.

Interface: best_move(board, player) -> (x, y) | None
Board: list[list[int]] (8×8), board[y][x], EMPTY=0 / BLACK=1 / WHITE=2.
"""

import copy
import time
from app.reversi import (
    BOARD_SIZE, EMPTY, BLACK, WHITE,
    get_legal_moves, apply_move, opponent, count_stones,
)

_TIME_BUDGET = 0.15

# Position weight table: corners highly valued, X/C squares penalised.
_WEIGHTS = [
    [ 100, -20,  10,   5,   5,  10, -20,  100],
    [ -20, -50,  -2,  -2,  -2,  -2, -50,  -20],
    [  10,  -2,   1,   1,   1,   1,  -2,   10],
    [   5,  -2,   1,   0,   0,   1,  -2,    5],
    [   5,  -2,   1,   0,   0,   1,  -2,    5],
    [  10,  -2,   1,   1,   1,   1,  -2,   10],
    [ -20, -50,  -2,  -2,  -2,  -2, -50,  -20],
    [ 100, -20,  10,   5,   5,  10, -20,  100],
]


# ── evaluation ─────────────────────────────────────────────────────

def _pos_score(board, player):
    """Sum of position weights for *player*'s stones minus opponent's."""
    opp = opponent(player)
    score = 0
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] == player:
                score += _WEIGHTS[y][x]
            elif board[y][x] == opp:
                score -= _WEIGHTS[y][x]
    return score


def _mobility_score(board, player):
    """Mobility advantage: my_moves − opp_moves."""
    opp = opponent(player)
    return len(get_legal_moves(board, player)) - len(get_legal_moves(board, opp))


def _stone_score(board, player):
    """Stone count difference (important in endgame)."""
    stones = count_stones(board)
    my_count = stones["black"] if player == BLACK else stones["white"]
    opp_count = stones["white"] if player == BLACK else stones["black"]
    return my_count - opp_count


def _evaluate(board, player):
    """Combined evaluation for *player*."""
    pos = _pos_score(board, player)
    mob = _mobility_score(board, player) * 5
    empty = sum(1 for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
                if board[y][x] == EMPTY)
    stn = _stone_score(board, player) * (2 if empty < 12 else 0)
    return pos + mob + stn


# ── minimax (NegaMax) ─────────────────────────────────────────────

def _minimax(board, player, depth, alpha, beta, deadline):
    """NegaMax with alpha-beta. Score is from *player*'s perspective."""
    if time.time() > deadline:
        return 0, None

    moves = get_legal_moves(board, player)
    opp = opponent(player)
    opp_moves = get_legal_moves(board, opp)

    # terminal: neither player can move
    if not moves and not opp_moves:
        diff = _stone_score(board, player)
        if diff > 0:
            return 10000 + diff, None
        if diff < 0:
            return -10000 + diff, None
        return 0, None

    # leaf or time-based cutoff
    if depth == 0:
        return _evaluate(board, player), None

    # pass: current player has no moves
    if not moves:
        sc, _ = _minimax(board, opp, depth - 1, -beta, -alpha, deadline)
        return -sc, None

    # move ordering: corners first for better pruning
    moves.sort(key=lambda m: _WEIGHTS[m[1]][m[0]], reverse=True)

    best_score, best_move = -100001, None
    for mx, my in moves:
        nb = copy.deepcopy(board)
        apply_move(nb, mx, my, player)
        sc, _ = _minimax(nb, opp, depth - 1, -beta, -alpha, deadline)
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
        board: list[list[int]] 8×8, board[y][x], EMPTY/BLACK/WHITE.
        player: BLACK (1) or WHITE (2).
    Returns:
        (x, y) or None (no legal moves → must pass).
    """
    moves = get_legal_moves(board, player)
    if not moves:
        return None
    if len(moves) == 1:
        return moves[0]

    empty = sum(1 for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
                if board[y][x] == EMPTY)
    # adaptive depth: deeper in endgame (fewer moves per position)
    if empty <= 10:
        max_depth = 8
    elif empty <= 16:
        max_depth = 6
    else:
        max_depth = 4

    deadline = time.time() + _TIME_BUDGET
    result = moves[0]
    for depth in range(1, max_depth + 1):
        _, m = _minimax(board, player, depth,
                        -100001, 100001, deadline)
        if m is not None:
            result = m
        if time.time() > deadline:
            break
    return result
