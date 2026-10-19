"""Gomoku (五子棋) pure game logic — no I/O, easy to unit-test."""

BOARD_SIZE = 15
EMPTY = 0
BLACK = 1
WHITE = 2

DIRECTIONS = [(1, 0), (0, 1), (1, 1), (1, -1)]


def create_board() -> list[list[int]]:
    """Create an empty 15x15 board."""
    return [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]


def is_valid_move(board: list[list[int]], x: int, y: int) -> bool:
    """Check if (x, y) is a legal move."""
    if x < 0 or x >= BOARD_SIZE or y < 0 or y >= BOARD_SIZE:
        return False
    return board[y][x] == EMPTY


def place_stone(board: list[list[int]], x: int, y: int, color: int) -> None:
    """Place a stone on the board (mutates in place)."""
    board[y][x] = color


def check_win(board: list[list[int]], x: int, y: int) -> bool:
    """Check if the stone at (x, y) creates five-in-a-row."""
    color = board[y][x]
    if color == EMPTY:
        return False
    for dx, dy in DIRECTIONS:
        count = 1
        for sign in (1, -1):
            nx, ny = x + dx * sign, y + dy * sign
            while (
                0 <= nx < BOARD_SIZE
                and 0 <= ny < BOARD_SIZE
                and board[ny][nx] == color
            ):
                count += 1
                nx += dx * sign
                ny += dy * sign
        if count >= 5:
            return True
    return False


def is_board_full(moves_count: int) -> bool:
    """Check if the board is full (draw condition)."""
    return moves_count >= BOARD_SIZE * BOARD_SIZE
