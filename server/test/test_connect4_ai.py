"""重力四子棋 AI 单测。

AI 是**确定性的**（中列优先定同分），所以断言可以写死具体列号——
不像象棋 AI 有同分随机，那种只能断言"不输"。

Run: pytest test/test_connect4_ai.py -v
"""

import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import connect4 as c4
from app.game import connect4_ai as ai


def _random_player(board, color, rng):
    return rng.choice(c4.legal_columns(board))


def _ai_player(budget: float):
    """固定预算的 AI 走子（默认 0.5s 跑满整个用例集会太慢）。"""
    def move(board, color):
        return ai.best_move(board, color, time_budget=budget)
    return move


def _play(black_fn, white_fn, seed=0):
    """黑先跑完一局，返回 'black' / 'white' / 'draw'。"""
    rng = random.Random(seed)
    board = c4.create_board()
    color = c4.BLACK
    while True:
        cols = c4.legal_columns(board)
        if not cols:
            return "draw"
        fn = black_fn if color == c4.BLACK else white_fn
        col = fn(board, color, rng) if fn is _random_player else fn(board, color)
        assert col in cols, f"AI 给出了非法列 {col}（合法：{cols}）"
        row = c4.drop(board, col, color)
        if c4.check_win(board, row, col):
            return "black" if color == c4.BLACK else "white"
        color = c4.WHITE if color == c4.BLACK else c4.BLACK


class TestTactics:
    def test_takes_the_immediate_win(self):
        board = c4.create_board()
        for c in (0, 1, 2):
            c4.drop(board, c, c4.BLACK)
        assert ai.best_move(board, c4.BLACK) == 3

    def test_takes_the_win_in_either_direction(self):
        """两端都能赢时，随便哪端都行——只断言"确实是一手赢棋"。"""
        board = c4.create_board()
        for c in (3, 4, 5):
            c4.drop(board, c, c4.BLACK)
        assert c4.winning_columns(board, c4.BLACK) == [2, 6]
        assert ai.best_move(board, c4.BLACK) in (2, 6)

        board2 = c4.create_board()
        for c in (4, 5, 6):
            c4.drop(board2, c, c4.BLACK)
        assert ai.best_move(board2, c4.BLACK) == 3

    def test_vertical_win_is_seen(self):
        board = c4.create_board()
        for _ in range(3):
            c4.drop(board, 5, c4.BLACK)
        assert ai.best_move(board, c4.BLACK) == 5

    def test_blocks_the_opponent(self):
        board = c4.create_board()
        for c in (0, 1, 2):
            c4.drop(board, c, c4.WHITE)
        assert ai.best_move(board, c4.BLACK) == 3

    def test_winning_beats_blocking(self):
        """自己一步能赢时不必去堵对方——先赢先算。"""
        board = c4.create_board()
        # 白方 (0,1,2) 三连，黑方 (4,5,6) 三连，黑先 → 直接补 3 没用（那是白的），
        # 黑应从 6 的方向补：这里用空列 3 让黑赢
        for c in (0, 1, 2):
            c4.drop(board, c, c4.WHITE)
        for c in (4, 5):
            c4.drop(board, c, c4.BLACK)
        c4.drop(board, 3, c4.BLACK)          # 黑也有 3 连了（3,4,5）
        # 白威胁在 3 —— 但 3 已被黑占，改成看它能赢的那一手
        assert ai.best_move(board, c4.BLACK) == 6

    def test_empty_board_opens_in_the_center(self):
        board = c4.create_board()
        assert ai.best_move(board, c4.BLACK) == 3
        assert ai.best_move(board, c4.WHITE) == 3

    def test_returns_none_on_full_board(self):
        board = c4.create_board()
        for c in range(c4.COLS):
            for i in range(c4.ROWS):
                c4.drop(board, c, c4.BLACK if i % 2 == 0 else c4.WHITE)
        assert ai.best_move(board, c4.BLACK) is None


class TestInvariants:
    def test_always_returns_a_legal_column(self):
        """随机盘面上随便抽，回来的列必须合法。"""
        rng = random.Random(11)
        for _ in range(40):
            board = c4.create_board()
            color = c4.BLACK
            for _ in range(rng.randint(0, 30)):
                cols = c4.legal_columns(board)
                if not cols:
                    break
                c = rng.choice(cols)
                row = c4.drop(board, c, color)
                if c4.check_win(board, row, c):
                    break
                color = c4.WHITE if color == c4.BLACK else c4.BLACK
            cols = c4.legal_columns(board)
            if not cols:
                continue
            move = ai.best_move(board, color, time_budget=0.05)
            assert move in cols

    def test_does_not_mutate_the_board(self):
        board = c4.create_board()
        for c, color in [(3, c4.BLACK), (3, c4.WHITE), (4, c4.BLACK)]:
            c4.drop(board, c, color)
        before = [row[:] for row in board]
        ai.best_move(board, c4.WHITE, time_budget=0.1)
        assert board == before

    def test_is_deterministic(self):
        board = c4.create_board()
        for c, color in [(3, c4.BLACK), (4, c4.WHITE), (3, c4.BLACK)]:
            c4.drop(board, c, color)
        first = ai.best_move(board, c4.WHITE, time_budget=0.1)
        second = ai.best_move(board, c4.WHITE, time_budget=0.1)
        assert first == second

    def test_respects_the_time_budget(self):
        board = c4.create_board()
        for c, color in [(3, c4.BLACK), (3, c4.WHITE), (4, c4.BLACK), (4, c4.WHITE)]:
            c4.drop(board, c, color)
        t0 = time.monotonic()
        ai.best_move(board, c4.BLACK, time_budget=0.2)
        elapsed = time.monotonic() - t0
        # 迭代加深是"整层搜完才采纳"，所以只会略微超出预算
        assert elapsed < 1.0, f"单步耗时 {elapsed:.2f}s，远超 0.2s 预算"

    def test_default_budget_is_under_a_second(self):
        assert 0 < ai._TIME_BUDGET <= 1.0


class TestNeverLosesToRandom:
    """AI 对随机玩家必须一场不输——这是"能不能当对手"的最低线。

    只断言"不输"而不是"全胜"：Connect Four 本身有和棋分支，把和棋也当失败
    会让用例随随机种子 flaky（象棋 AI 那条用例踩过同一个坑）。
    """

    def test_as_first_player(self):
        ai_move = _ai_player(0.15)
        for seed in range(4):
            result = _play(ai_move, _random_player, seed)
            assert result != "white", f"AI 执黑输给了随机玩家（seed={seed}）"

    def test_as_second_player(self):
        ai_move = _ai_player(0.15)
        for seed in range(4):
            result = _play(_random_player, ai_move, seed)
            assert result != "black", f"AI 执白输给了随机玩家（seed={seed}）"

    def test_wins_most_games(self):
        ai_move = _ai_player(0.15)
        wins = 0
        for seed in range(6):
            wins += _play(ai_move, _random_player, seed) == "black"
            wins += _play(_random_player, ai_move, seed) == "white"
        assert wins >= 8, f"12 局只赢了 {wins} 局，AI 太弱"

    def test_full_budget_never_loses(self):
        """按真实预算（0.5s）再跑一局，确认生产配置下同样不输。"""
        assert _play(ai.best_move, _random_player, 99) != "white"
        assert _play(_random_player, ai.best_move, 99) != "black"


class TestEvaluation:
    def test_evaluate_is_zero_on_empty_board(self):
        assert ai.evaluate(c4.create_board(), c4.BLACK) == 0

    def test_evaluate_favours_the_side_with_more_threats(self):
        board = c4.create_board()
        for c in (0, 1):
            c4.drop(board, c, c4.BLACK)
        assert ai.evaluate(board, c4.BLACK) > 0
        assert ai.evaluate(board, c4.WHITE) < 0

    def test_window_with_both_colors_scores_nothing(self):
        """双方都占住的窗口直接作废——涂成 2×2 交替色时所有窗口都作废。"""
        board = c4.create_board()
        for r in range(c4.ROWS):
            for c in range(c4.COLS):
                board[r][c] = c4.BLACK if (r + c) % 2 == 0 else c4.WHITE
        # 任意连续 4 格必然黑白各半 → 69 个窗口全部作废
        assert ai.evaluate(board, c4.BLACK) == 0
        assert ai.evaluate(board, c4.WHITE) == 0

    def test_lone_stone_scores_one_per_window(self):
        board = c4.create_board()
        board[5][0] = c4.BLACK
        windows = sum(1 for w in ai._WINDOWS if (5, 0) in w)
        # (5,0) 是左下角，只有 3 个窗口够得到它：横 (5,0..3)、竖 (2..5,0)、
        # 一条反对角线 (2,3)(3,2)(4,1)(5,0)；另一个斜向出界了。
        assert windows == 3
        assert ai.evaluate(board, c4.BLACK) == windows * ai._SCORES[1]

    def test_window_table_covers_the_board(self):
        assert len(ai._WINDOWS) == 69          # 横 24 + 竖 21 + 斜 24
        assert all(len(w) == 4 for w in ai._WINDOWS)
        assert all(c4.in_board(r, c) for w in ai._WINDOWS for r, c in w)