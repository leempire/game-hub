"""Gomoku (五子棋) pure game logic — no I/O, easy to unit-test."""

from app.game.gomoku_rules import (BOARD_SIZE, EMPTY, BLACK, WHITE, DIRECTIONS,
                                   forbidden_reason, legal_moves)


def create_board(size: int = BOARD_SIZE) -> list[list[int]]:
    """Create an empty 15x15 board."""
    if isinstance(size, bool) or size not in (9, 13, 15, 19):
        raise ValueError("五子棋棋盘支持 9/13/15/19 路")
    return [[EMPTY] * size for _ in range(size)]


def is_valid_move(board: list[list[int]], x: int, y: int) -> bool:
    """Check if (x, y) is a legal move."""
    if x < 0 or x >= len(board) or y < 0 or y >= len(board):
        return False
    return board[y][x] == EMPTY


def place_stone(board: list[list[int]], x: int, y: int, color: int) -> None:
    """Place a stone on the board (mutates in place)."""
    board[y][x] = color


def check_win(board: list[list[int]], x: int, y: int, forbidden: bool = False) -> bool:
    """Check if the stone at (x, y) creates five-in-a-row."""
    color = board[y][x]
    if color == EMPTY:
        return False
    for dx, dy in DIRECTIONS:
        count = 1
        for sign in (1, -1):
            nx, ny = x + dx * sign, y + dy * sign
            while (
                0 <= nx < len(board)
                and 0 <= ny < len(board)
                and board[ny][nx] == color
            ):
                count += 1
                nx += dx * sign
                ny += dy * sign
        if count == 5 or (count > 5 and (not forbidden or color == WHITE)):
            return True
    return False


def is_board_full(moves_count: int, size: int = BOARD_SIZE) -> bool:
    """Check if the board is full (draw condition)."""
    return moves_count >= size * size
