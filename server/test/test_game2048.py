"""Unit tests for 2048 game engine."""

import pytest
import random
from app.game.game2048 import (
    create_board,
    spawn_tile,
    move,
    has_moves,
    max_tile,
    has_won,
    board_score,
    create_seeded_rng,
    _slide_row_left,
    BOARD_SIZE,
    WIN_TILE,
)


class TestCreateBoard:
    def test_empty_board(self):
        board = create_board()
        assert len(board) == BOARD_SIZE
        assert all(len(row) == BOARD_SIZE for row in board)
        assert all(v == 0 for row in board for v in row)


class TestSpawnTile:
    def test_spawn_on_empty(self):
        board = create_board()
        rng = random.Random(42)
        result = spawn_tile(board, rng)
        assert result is not None
        r, c, v = result
        assert board[r][c] == v
        assert v in (2, 4)

    def test_spawn_full_board(self):
        board = [[2] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        rng = random.Random(42)
        result = spawn_tile(board, rng)
        assert result is None

    def test_spawn_probability(self):
        """With seed, we get deterministic 2/4 distribution."""
        twos = 0
        fours = 0
        for seed in range(100):
            board = create_board()
            rng = random.Random(seed)
            _, _, v = spawn_tile(board, rng)
            if v == 2:
                twos += 1
            else:
                fours += 1
        # Should be roughly 90% twos
        assert twos > fours


class TestSlideRowLeft:
    def test_empty_row(self):
        row, score = _slide_row_left([0, 0, 0, 0])
        assert row == [0, 0, 0, 0]
        assert score == 0

    def test_no_merge(self):
        row, score = _slide_row_left([2, 4, 8, 0])
        assert row == [2, 4, 8, 0]
        assert score == 0

    def test_simple_merge(self):
        row, score = _slide_row_left([2, 2, 0, 0])
        assert row == [4, 0, 0, 0]
        assert score == 4

    def test_merge_once_per_tile(self):
        """Three 2s: only rightmost pair merges, not chain."""
        row, score = _slide_row_left([2, 2, 2, 0])
        assert row == [4, 2, 0, 0]
        assert score == 4

    def test_four_in_row(self):
        """Four 2s: two pairs merge independently."""
        row, score = _slide_row_left([2, 2, 2, 2])
        assert row == [4, 4, 0, 0]
        assert score == 8

    def test_full_push(self):
        """Tiles slide to fill gaps."""
        row, score = _slide_row_left([0, 0, 2, 4])
        assert row == [2, 4, 0, 0]
        assert score == 0

    def test_no_change(self):
        """Already compacted left, no merges."""
        row, score = _slide_row_left([2, 4, 8, 16])
        assert row == [2, 4, 8, 16]
        assert score == 0

    def test_gap_merge(self):
        """Tiles with gap merge when slid."""
        row, score = _slide_row_left([4, 0, 0, 4])
        assert row == [8, 0, 0, 0]
        assert score == 8


class TestMove:
    def test_move_left(self):
        board = [
            [0, 0, 2, 2],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, moved = move(board, "left")
        assert new_board[0] == [4, 0, 0, 0]
        assert score == 4
        assert moved is True

    def test_move_right(self):
        board = [
            [2, 2, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, moved = move(board, "right")
        assert new_board[0] == [0, 0, 0, 4]
        assert score == 4
        assert moved is True

    def test_move_up(self):
        board = [
            [2, 0, 0, 0],
            [2, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, moved = move(board, "up")
        assert new_board[0][0] == 4
        assert new_board[1][0] == 0
        assert score == 4
        assert moved is True

    def test_move_down(self):
        board = [
            [2, 0, 0, 0],
            [2, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, moved = move(board, "down")
        assert new_board[3][0] == 4
        assert new_board[0][0] == 0
        assert score == 4
        assert moved is True

    def test_no_op_move(self):
        """Already compacted, no merges possible → not moved."""
        board = [
            [2, 4, 8, 16],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        _, _, moved = move(board, "left")
        assert moved is False

    def test_invalid_direction(self):
        board = create_board()
        with pytest.raises(ValueError):
            move(board, "diagonal")

    def test_merge_once_per_slide(self):
        """Three 2s in a row: sliding left merges only the leftmost pair."""
        board = [
            [0, 2, 2, 2],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, _ = move(board, "left")
        assert new_board[0] == [4, 2, 0, 0]
        assert score == 4

    def test_column_merge(self):
        """Three 4s in a column sliding up."""
        board = [
            [0, 4, 0, 0],
            [0, 4, 0, 0],
            [0, 4, 0, 0],
            [0, 0, 0, 0],
        ]
        new_board, score, _ = move(board, "up")
        assert new_board[0][1] == 8
        assert new_board[1][1] == 4
        assert score == 8


class TestHasMoves:
    def test_empty_board(self):
        assert has_moves(create_board()) is True

    def test_full_no_merge(self):
        board = [
            [2, 4, 2, 4],
            [4, 2, 4, 2],
            [2, 4, 2, 4],
            [4, 2, 4, 2],
        ]
        assert has_moves(board) is False

    def test_full_with_merge(self):
        board = [
            [2, 4, 2, 4],
            [4, 2, 4, 2],
            [2, 4, 2, 4],
            [4, 2, 4, 4],  # adjacent 4s
        ]
        assert has_moves(board) is True


class TestMaxTile:
    def test_empty(self):
        assert max_tile(create_board()) == 0

    def test_with_tiles(self):
        board = [[2, 4, 8, 16], [32, 64, 128, 256], [512, 1024, 2048, 0], [0, 0, 0, 0]]
        assert max_tile(board) == 2048


class TestHasWon:
    def test_not_won(self):
        board = [[2, 4, 8, 16], [32, 64, 128, 256], [512, 1024, 0, 0], [0, 0, 0, 0]]
        assert has_won(board) is False

    def test_won(self):
        board = [[2, 4, 8, 16], [32, 64, 128, 256], [512, 1024, 2048, 0], [0, 0, 0, 0]]
        assert has_won(board) is True


class TestSeededRng:
    def test_deterministic(self):
        rng1 = create_seeded_rng(42)
        rng2 = create_seeded_rng(42)
        board1 = create_board()
        board2 = create_board()
        for _ in range(10):
            r1 = spawn_tile(board1, rng1)
            r2 = spawn_tile(board2, rng2)
            assert r1 == r2

    def test_different_seeds(self):
        rng1 = create_seeded_rng(42)
        rng2 = create_seeded_rng(99)
        board1 = create_board()
        board2 = create_board()
        results1 = [spawn_tile(board1, rng1) for _ in range(5)]
        results2 = [spawn_tile(board2, rng2) for _ in range(5)]
        # Very unlikely to be identical
        assert results1 != results2


class TestBoardScore:
    def test_empty(self):
        assert board_score(create_board()) == 0

    def test_with_tiles(self):
        board = [[2, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        assert board_score(board) == 6
