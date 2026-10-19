"""Unit tests for gomoku game logic. Run: python test/test_gomoku.py"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.gomoku import (
    create_board,
    is_valid_move,
    place_stone,
    check_win,
    is_board_full,
    BOARD_SIZE,
    BLACK,
    WHITE,
    EMPTY,
)


def test_create_board():
    board = create_board()
    assert len(board) == BOARD_SIZE
    assert all(len(row) == BOARD_SIZE for row in board)
    assert all(board[y][x] == EMPTY for y in range(BOARD_SIZE) for x in range(BOARD_SIZE))
    print("  ✓ test_create_board")


def test_valid_move():
    board = create_board()
    assert is_valid_move(board, 0, 0) is True
    assert is_valid_move(board, 14, 14) is True
    assert is_valid_move(board, -1, 0) is False
    assert is_valid_move(board, 0, 15) is False
    place_stone(board, 5, 5, BLACK)
    assert is_valid_move(board, 5, 5) is False
    print("  ✓ test_valid_move")


def test_place_stone():
    board = create_board()
    place_stone(board, 3, 4, BLACK)
    assert board[4][3] == BLACK
    place_stone(board, 7, 7, WHITE)
    assert board[7][7] == WHITE
    print("  ✓ test_place_stone")


def test_horizontal_win():
    board = create_board()
    for x in range(5):
        place_stone(board, x, 0, BLACK)
    assert check_win(board, 4, 0) is True
    # Only 4 in a row
    board2 = create_board()
    for x in range(4):
        place_stone(board2, x, 0, BLACK)
    assert check_win(board2, 3, 0) is False
    print("  ✓ test_horizontal_win")


def test_vertical_win():
    board = create_board()
    for y in range(5):
        place_stone(board, 3, y, WHITE)
    assert check_win(board, 3, 4) is True
    print("  ✓ test_vertical_win")


def test_diagonal_win():
    board = create_board()
    for i in range(5):
        place_stone(board, i, i, BLACK)
    assert check_win(board, 4, 4) is True
    # Anti-diagonal
    board2 = create_board()
    for i in range(5):
        place_stone(board2, i, 4 - i, WHITE)
    assert check_win(board2, 4, 0) is True
    print("  ✓ test_diagonal_win")


def test_no_win():
    board = create_board()
    # 4 in a row with gap
    place_stone(board, 0, 0, BLACK)
    place_stone(board, 1, 0, BLACK)
    place_stone(board, 2, 0, BLACK)
    place_stone(board, 4, 0, BLACK)
    assert check_win(board, 4, 0) is False
    print("  ✓ test_no_win")


def test_six_in_a_row():
    """Six in a row should also count as win (≥5)."""
    board = create_board()
    for x in range(6):
        place_stone(board, x, 0, BLACK)
    assert check_win(board, 5, 0) is True
    assert check_win(board, 0, 0) is True
    print("  ✓ test_six_in_a_row")


def test_board_full():
    assert is_board_full(224) is False
    assert is_board_full(225) is True
    assert is_board_full(226) is True
    print("  ✓ test_board_full")


def test_center_win():
    """Win in the middle of the board."""
    board = create_board()
    for x in range(5, 10):
        place_stone(board, x, 7, BLACK)
    assert check_win(board, 9, 7) is True
    print("  ✓ test_center_win")


if __name__ == "__main__":
    print("Running gomoku unit tests...")
    test_create_board()
    test_valid_move()
    test_place_stone()
    test_horizontal_win()
    test_vertical_win()
    test_diagonal_win()
    test_no_win()
    test_six_in_a_row()
    test_board_full()
    test_center_win()
    print("\nAll gomoku tests passed! ✓")
