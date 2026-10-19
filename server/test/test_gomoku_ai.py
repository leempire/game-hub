"""Unit tests for Gomoku AI. Run:
    cd server && python -m pytest test/test_gomoku_ai.py -q
"""

import sys
import os
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.gomoku import (
    create_board, place_stone, check_win, is_valid_move,
    BOARD_SIZE, EMPTY, BLACK, WHITE,
)
from app.gomoku_ai import best_move


# ── one-step win ──────────────────────────────────────────────────

def test_immediate_win_horizontal():
    """AI (WHITE) must complete 5-in-a-row horizontally."""
    board = create_board()
    for x in range(4):
        place_stone(board, x, 5, WHITE)
    place_stone(board, 7, 7, BLACK)
    move = best_move(board, WHITE)
    assert move == (4, 5), f"Expected (4,5), got {move}"


def test_immediate_win_vertical():
    """AI (BLACK) must complete 5-in-a-row vertically."""
    board = create_board()
    for y in range(4):
        place_stone(board, 3, y, BLACK)
    place_stone(board, 7, 7, WHITE)
    move = best_move(board, BLACK)
    assert move == (3, 4), f"Expected (3,4), got {move}"


def test_immediate_win_diagonal():
    """AI (WHITE) must complete 5-in-a-row diagonally."""
    board = create_board()
    for i in range(4):
        place_stone(board, i + 1, i + 1, WHITE)
    place_stone(board, 10, 0, BLACK)
    move = best_move(board, WHITE)
    assert move in ((0, 0), (5, 5)), f"Expected (0,0) or (5,5), got {move}"


# ── must block ────────────────────────────────────────────────────

def test_must_block_horizontal():
    """AI (WHITE) must block BLACK's 4-in-a-row."""
    board = create_board()
    for x in range(4):
        place_stone(board, x, 5, BLACK)
    place_stone(board, 7, 0, WHITE)
    place_stone(board, 7, 1, WHITE)
    move = best_move(board, WHITE)
    assert move in ((4, 5), (0, 5)), f"Must block at (4,5) or (0,5), got {move}"


def test_must_block_diagonal():
    """AI (BLACK) must block WHITE's diagonal threat."""
    board = create_board()
    # WHITE at (1,1)-(4,4) with BLACK at (5,5) blocking far end.
    # Only (0,0) remains as the single blocking cell.
    for i in range(4):
        place_stone(board, i + 1, i + 1, WHITE)   # (1,1),(2,2),(3,3),(4,4)
    place_stone(board, 5, 5, BLACK)   # blocks far end
    place_stone(board, 0, 7, BLACK)
    place_stone(board, 1, 6, BLACK)
    move = best_move(board, BLACK)
    assert move == (0, 0), f"Must block at (0,0), got {move}"


# ── opening ───────────────────────────────────────────────────────

def test_opening_empty_board():
    """First move on empty board → centre (7,7)."""
    board = create_board()
    move = best_move(board, BLACK)
    assert move == (7, 7), f"Expected (7,7), got {move}"


def test_opening_near_centre():
    """Second move should be near centre."""
    board = create_board()
    place_stone(board, 7, 7, BLACK)
    move = best_move(board, WHITE)
    assert move is not None
    dist = abs(move[0] - 7) + abs(move[1] - 7)
    assert dist <= 3, f"Opening move {move} too far from centre (dist={dist})"


# ── validity ──────────────────────────────────────────────────────

def test_move_validity_various_positions():
    """AI always returns a valid move."""
    random.seed(42)
    for _ in range(10):
        board = create_board()
        n_stones = random.randint(2, 30)
        for _ in range(n_stones):
            empties = [
                (x, y) for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
                if board[y][x] == EMPTY
            ]
            if not empties:
                break
            x, y = random.choice(empties)
            color = BLACK if sum(
                1 for row in board for c in row if c != EMPTY
            ) % 2 == 0 else WHITE
            place_stone(board, x, y, color)
        player = BLACK if sum(
            1 for row in board for c in row if c != EMPTY
        ) % 2 == 0 else WHITE
        move = best_move(board, player)
        if move is not None:
            assert is_valid_move(board, move[0], move[1]), \
                f"Invalid move {move} on board"


def test_full_board_returns_none():
    """Full board → None."""
    board = [[BLACK] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    assert best_move(board, WHITE) is None


# ── AI vs random ─────────────────────────────────────────────────

def _play_random_game(ai_color, max_moves=80):
    """Play one game: AI vs random. Returns 1=AI win, 0=draw, -1=AI loss."""
    board = create_board()
    current = BLACK
    moves_count = 0
    while moves_count < max_moves:
        empties = [
            (x, y) for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
            if board[y][x] == EMPTY
        ]
        if not empties:
            break
        if current == ai_color:
            move = best_move(board, current)
            if move is None:
                break
        else:
            move = random.choice(empties)
        x, y = move
        place_stone(board, x, y, current)
        moves_count += 1
        if check_win(board, x, y):
            return 1 if current == ai_color else -1
        current = WHITE if current == BLACK else BLACK
    return 0


def test_ai_vs_random_black():
    """AI (BLACK) wins ≥80% of 5 games vs random."""
    random.seed(123)
    wins = sum(1 for _ in range(5) if _play_random_game(BLACK) == 1)
    assert wins >= 4, f"AI won only {wins}/5 games as BLACK"


def test_ai_vs_random_white():
    """AI (WHITE) wins ≥80% of 5 games vs random."""
    random.seed(456)
    wins = sum(1 for _ in range(5) if _play_random_game(WHITE) == 1)
    assert wins >= 4, f"AI won only {wins}/5 games as WHITE"


# ── timing ────────────────────────────────────────────────────────

def test_timing_single_move():
    """Single decision must complete within 200 ms."""
    board = create_board()
    # mid-game position with several stones
    for i in range(5):
        place_stone(board, 5 + i, 7, BLACK)
        place_stone(board, 5 + i, 8, WHITE)
    place_stone(board, 3, 3, BLACK)
    place_stone(board, 11, 11, WHITE)

    t0 = time.time()
    move = best_move(board, BLACK)
    elapsed = time.time() - t0
    assert move is not None
    assert elapsed < 0.2, f"Move took {elapsed:.3f}s (>200ms)"


def test_timing_empty_board():
    """Opening move should be near-instant."""
    board = create_board()
    t0 = time.time()
    move = best_move(board, BLACK)
    elapsed = time.time() - t0
    assert move == (7, 7)
    assert elapsed < 0.01, f"Opening took {elapsed:.3f}s"
