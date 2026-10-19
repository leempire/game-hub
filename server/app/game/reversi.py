"""Reversi (翻转棋/奥赛罗) pure game logic — no I/O, easy to unit-test.

8×8 board, black goes first. Classic initial layout with 4 center stones.
A move must flip at least one opponent stone in any of 8 directions.
No legal moves → auto-pass. Both pass or board full → game over, most stones wins.
"""

BOARD_SIZE = 8
EMPTY = 0
BLACK = 1
WHITE = 2

DIRECTIONS = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]

# Classic initial layout: white at (3,3)(4,4), black at (3,4)(4,3)
INITIAL_STONES = [
    (3, 3, WHITE), (4, 4, WHITE),
    (3, 4, BLACK), (4, 3, BLACK),
]


def create_board() -> list[list[int]]:
    """Create 8×8 board with initial 4-stone layout."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    for x, y, color in INITIAL_STONES:
        board[y][x] = color
    return board


def _in_bounds(x: int, y: int) -> bool:
    return 0 <= x < BOARD_SIZE and 0 <= y < BOARD_SIZE


def opponent(color: int) -> int:
    """Return the opponent color (BLACK↔WHITE)."""
    return WHITE if color == BLACK else BLACK


def find_flips_in_dir(
    board: list[list[int]], x: int, y: int,
    color: int, dx: int, dy: int,
) -> list[tuple[int, int]]:
    """Find opponent stones flipped in one direction from (x, y)."""
    opp = opponent(color)
    flips: list[tuple[int, int]] = []
    nx, ny = x + dx, y + dy
    while _in_bounds(nx, ny) and board[ny][nx] == opp:
        flips.append((nx, ny))
        nx += dx
        ny += dy
    if flips and _in_bounds(nx, ny) and board[ny][nx] == color:
        return flips
    return []


def find_all_flips(
    board: list[list[int]], x: int, y: int, color: int,
) -> list[tuple[int, int]]:
    """Find all stones flipped by placing color at (x, y). Empty if illegal."""
    if not _in_bounds(x, y) or board[y][x] != EMPTY:
        return []
    flips: list[tuple[int, int]] = []
    for dx, dy in DIRECTIONS:
        flips.extend(find_flips_in_dir(board, x, y, color, dx, dy))
    return flips


def is_valid_move(board: list[list[int]], x: int, y: int, color: int) -> bool:
    """Check if placing color at (x, y) is a legal move."""
    return len(find_all_flips(board, x, y, color)) > 0


def get_legal_moves(board: list[list[int]], color: int) -> list[tuple[int, int]]:
    """Return all legal move positions for color."""
    moves: list[tuple[int, int]] = []
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if is_valid_move(board, x, y, color):
                moves.append((x, y))
    return moves


def apply_move(
    board: list[list[int]], x: int, y: int, color: int,
) -> list[tuple[int, int]]:
    """Place stone and flip captured opponents. Returns flipped positions."""
    flips = find_all_flips(board, x, y, color)
    if not flips:
        raise ValueError(f"Illegal move ({x}, {y}) for color {color}")
    board[y][x] = color
    for fx, fy in flips:
        board[fy][fx] = color
    return flips


def count_stones(board: list[list[int]]) -> dict[str, int]:
    """Count stones per color. Returns {'black': int, 'white': int}."""
    b = sum(cell == BLACK for row in board for cell in row)
    w = sum(cell == WHITE for row in board for cell in row)
    return {"black": b, "white": w}


def game_to_sync(gs: dict, user_id: int) -> dict:
    """Build the game portion of a sync message for reversi."""
    return {
        "board_size": BOARD_SIZE,
        "board": gs["board"],
        "moves": gs["moves"],
        "next": gs["next"],
        "your_color": gs["player_colors"].get(user_id, ""),
        "pass_count": gs.get("pass_count", 0),
        "winner": gs["winner"],
        "reason": gs["reason"],
        "score": count_stones(gs["board"]),
    }
