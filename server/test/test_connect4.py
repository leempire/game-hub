"""重力四子棋引擎单测（纯逻辑，无 IO）。

坐标约定：`board[row][col]`，**row 0 在最上面**，row 5 是最下面一行；
棋子都堆在列底，所以落子只需给列号，落点行由重力决定。

Run: pytest test/test_connect4.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import connect4 as c4


class TestBoard:
    def test_empty_board_shape(self):
        board = c4.create_board()
        assert len(board) == c4.ROWS == 6
        assert all(len(row) == c4.COLS == 7 for row in board)
        assert all(cell == c4.EMPTY for row in board for cell in row)

    def test_all_columns_legal_when_empty(self):
        board = c4.create_board()
        assert c4.legal_columns(board) == list(range(7))
        assert all(c4.is_valid_move(board, c) for c in range(7))

    def test_gravity_lands_on_bottom_row(self):
        board = c4.create_board()
        assert c4.landing_row(board, 0) == 5
        assert c4.drop(board, 0, c4.BLACK) == 5
        assert board[5][0] == c4.BLACK

    def test_stack_stacks_upward(self):
        board = c4.create_board()
        rows = [c4.drop(board, 4, c4.BLACK if i % 2 == 0 else c4.WHITE) for i in range(6)]
        assert rows == [5, 4, 3, 2, 1, 0]
        assert [board[r][4] for r in range(6)] == [c4.WHITE, c4.BLACK,
                                                   c4.WHITE, c4.BLACK,
                                                   c4.WHITE, c4.BLACK]

    def test_full_column_is_illegal(self):
        board = c4.create_board()
        for _ in range(c4.ROWS):
            c4.drop(board, 2, c4.BLACK)
        assert not c4.is_valid_move(board, 2)
        assert c4.landing_row(board, 2) == -1
        assert c4.legal_columns(board) == [0, 1, 3, 4, 5, 6]
        with pytest.raises(ValueError):
            c4.drop(board, 2, c4.WHITE)

    def test_out_of_range_column_is_illegal(self):
        board = c4.create_board()
        for col in (-1, 7, 100):
            assert not c4.is_valid_move(board, col)
            with pytest.raises(ValueError):
                c4.drop(board, col, c4.BLACK)

    def test_board_full_only_when_top_row_filled(self):
        board = c4.create_board()
        assert not c4.is_board_full(board)
        for c in range(c4.COLS):
            for _ in range(c4.ROWS):
                c4.drop(board, c, c4.BLACK)
        assert c4.is_board_full(board)
        assert c4.legal_columns(board) == []


class TestWinDetection:
    def test_horizontal_four(self):
        board = c4.create_board()
        for c in range(4):
            c4.drop(board, c, c4.BLACK)
        assert c4.check_win(board, 5, 3)
        assert c4.check_win(board, 5, 0)          # 四颗里任意一颗都能查到
        assert not c4.check_win(board, 5, 4)      # 第五列没有子

    def test_vertical_four(self):
        board = c4.create_board()
        top = None
        for _ in range(4):
            top = c4.drop(board, 6, c4.WHITE)
        assert top == 2
        assert c4.check_win(board, top, 6)

    def test_diagonal_up_right(self):
        board = c4.create_board()
        # 第 i 列先垫 i 颗白子，黑子落在 (5-i, i) → 反对角线
        for i in range(4):
            for _ in range(i):
                c4.drop(board, i, c4.WHITE)
            c4.drop(board, i, c4.BLACK)
        assert c4.check_win(board, 2, 3)
        assert c4.check_win(board, 5, 0)

    def test_diagonal_down_right(self):
        board = c4.create_board()
        # 第 i 列先垫 (3-i) 颗白子，黑子落在 (2+i, i) → 主对角线
        for i in range(4):
            for _ in range(3 - i):
                c4.drop(board, i, c4.WHITE)
            c4.drop(board, i, c4.BLACK)
        assert c4.check_win(board, 5, 3)
        assert c4.check_win(board, 2, 0)

    def test_three_in_a_row_is_not_a_win(self):
        board = c4.create_board()
        for c in range(3):
            c4.drop(board, c, c4.BLACK)
        assert not c4.check_win(board, 5, 2)

    def test_five_in_a_row_still_wins(self):
        """7 列宽，理论上能连出五连——四连判据是 `>= 4`，不能漏。"""
        board = c4.create_board()
        board[5] = [c4.BLACK] * 5 + [c4.EMPTY] * 2
        assert c4.check_win(board, 5, 2)

    def test_empty_cell_never_wins(self):
        board = c4.create_board()
        assert not c4.check_win(board, 5, 3)

    def test_win_does_not_cross_colors(self):
        board = c4.create_board()
        board[5] = [c4.BLACK, c4.BLACK, c4.WHITE, c4.BLACK, c4.BLACK, c4.EMPTY, c4.EMPTY]
        assert not c4.check_win(board, 5, 3)

    def test_would_win_at_matches_check_win(self):
        board = c4.create_board()
        for c in range(3):
            c4.drop(board, c, c4.BLACK)
        assert c4.would_win_at(board, 3, c4.BLACK)
        assert not c4.would_win_at(board, 4, c4.BLACK)
        assert c4.winning_columns(board, c4.BLACK) == [3]
        assert c4.winning_columns(board, c4.WHITE) == []

    def test_would_win_at_full_column_is_false(self):
        board = c4.create_board()
        for _ in range(c4.ROWS):
            c4.drop(board, 0, c4.WHITE)
        assert not c4.would_win_at(board, 0, c4.WHITE)

    def test_winning_columns_does_not_mutate_board(self):
        board = c4.create_board()
        for c in range(3):
            c4.drop(board, c, c4.BLACK)
        before = [row[:] for row in board]
        c4.winning_columns(board, c4.BLACK)
        c4.would_win_at(board, 3, c4.BLACK)
        assert board == before

    def test_winning_columns_finds_both_ends(self):
        board = c4.create_board()
        for c in (1, 2, 3):
            c4.drop(board, c, c4.BLACK)
        assert c4.winning_columns(board, c4.BLACK) == [0, 4]


class TestWinInCornerCells:
    """四连要能贴着边界成立，别因为索引越界少算。"""

    def test_bottom_left_horizontal(self):
        board = c4.create_board()
        for c in range(4):
            c4.drop(board, c, c4.WHITE)
        assert c4.check_win(board, 5, 0)

    def test_bottom_right_horizontal(self):
        board = c4.create_board()
        for c in range(3, 7):
            c4.drop(board, c, c4.WHITE)
        assert c4.check_win(board, 5, 6)

    def test_top_row_horizontal(self):
        board = c4.create_board()
        for c in range(4):
            for i in range(c4.ROWS):
                c4.drop(board, c, c4.BLACK if i == c4.ROWS - 1 else c4.WHITE)
        assert c4.check_win(board, 0, 3)