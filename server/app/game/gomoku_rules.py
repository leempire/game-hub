"""Renju forbidden-point adjudication, including recursively false open threes.

Only the black forbidden-move rules are used; this is not a tournament opening
protocol. Black exact five takes precedence, as specified by RIF rule 9.2.
"""

from functools import lru_cache

BOARD_SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))


def _inside(board, x, y):
    return 0 <= x < len(board) and 0 <= y < len(board)


def _run(board, x, y, dx, dy):
    total = 1
    for sign in (-1, 1):
        nx, ny = x + sign * dx, y + sign * dy
        while _inside(board, nx, ny) and board[ny][nx] == BLACK:
            total += 1
            nx, ny = nx + sign * dx, ny + sign * dy
    return total


def _fours(board, x, y):
    """Map each distinct four's stones and direction to its winning endpoints."""
    groups = {}
    for dx, dy in DIRECTIONS:
        for offset in range(-4, 1):
            cells = [(x + (offset + i) * dx, y + (offset + i) * dy) for i in range(5)]
            if any(not _inside(board, cx, cy) for cx, cy in cells):
                continue
            stones = tuple((cx, cy) for cx, cy in cells if board[cy][cx] == BLACK)
            empty = [(cx, cy) for cx, cy in cells if board[cy][cx] == EMPTY]
            if len(stones) != 4 or len(empty) != 1 or (x, y) not in stones:
                continue
            ex, ey = empty[0]
            board[ey][ex] = BLACK
            exact = _run(board, ex, ey, dx, dy) == 5
            board[ey][ex] = EMPTY
            if exact:
                groups.setdefault((dx, dy, stones), set()).add((ex, ey))
    return groups


def forbidden_reason(board, x, y):
    """Reason for a hypothetical black move; input is restored even on failure."""
    if not _inside(board, x, y) or board[y][x] != EMPTY:
        return 'occupied'
    working = [row[:] for row in board]

    @lru_cache(maxsize=8192)
    def reason(position, px, py):
        state = [list(row) for row in position]
        state[py][px] = BLACK
        lengths = [_run(state, px, py, dx, dy) for dx, dy in DIRECTIONS]
        if 5 in lengths:
            return None
        if any(length > 5 for length in lengths):
            return 'overline'
        if len(_fours(state, px, py)) > 1:
            return 'double_four'
        threes = set()
        for dx, dy in DIRECTIONS:
            for offset in range(-3, 4):
                ex, ey = px + offset * dx, py + offset * dy
                if not _inside(state, ex, ey) or state[ey][ex] != EMPTY:
                    continue
                state[ey][ex] = BLACK
                # A three must extend to a straight four without making a five.
                if any(_run(state, ex, ey, ax, ay) == 5 for ax, ay in DIRECTIONS):
                    state[ey][ex] = EMPTY
                    continue
                groups = _fours(state, px, py)
                candidates = [key[2] for key, ends in groups.items()
                              if key[:2] == (dx, dy) and len(ends) == 2 and (ex, ey) in key[2]]
                state[ey][ex] = EMPTY
                if candidates and reason(tuple(tuple(row) for row in state), ex, ey) is None:
                    for stones in candidates:
                        threes.add((dx, dy, tuple(point for point in stones if point != (ex, ey))))
                    if len(threes) > 1:
                        return 'double_three'
        return None

    return reason(tuple(tuple(row) for row in working), x, y)


def legal_moves(board, player=BLACK, forbidden=False):
    result = []
    for y, row in enumerate(board):
        for x, cell in enumerate(row):
            if cell == EMPTY and (not forbidden or player != BLACK or forbidden_reason(board, x, y) is None):
                result.append((x, y))
    return result
