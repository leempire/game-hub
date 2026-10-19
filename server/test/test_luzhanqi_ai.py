"""军棋 AI 单测。

契约测试照抄象棋：不突变输入 / 尊重时间预算 / 只走合法着法 / 自对弈能终止。
另外多两条本游戏特有的：**暗棋下信念集只能由位置与交战结果推出来**
（不能偷偷读对手的棋子身份 —— 它本来就拿不到），以及**暗棋自对弈也必须终止**。
"""

import random
import time

from app.game import luzhanqi as L
from app.game import luzhanqi_ai as AI


def _playing(mode="open", reveal=True, seed=7):
    rng = random.Random(seed)
    gs = L.create_game(mode, reveal)
    gs["player_colors"] = {1: L.RED, 2: L.BLACK}
    for color in (L.RED, L.BLACK):
        L.fill_layout(gs, color, L.random_layout(color, rng))
    gs["ready"] = {L.RED: False, L.BLACK: False}
    gs["flag_revealed"] = {L.RED: False, L.BLACK: False}
    for color in (L.RED, L.BLACK):
        L.confirm_setup(gs, color)
    return gs


class TestContracts:
    def test_returns_none_without_legal_moves(self):
        assert AI.best_move({"legal": [], "your_color": L.RED, "cells": []}) is None
        assert AI.best_move({"legal": None, "your_color": L.RED, "cells": []}) is None

    def test_returns_none_when_color_missing(self):
        gs = _playing()
        view = L.view_for(gs, L.RED)
        view["your_color"] = ""
        assert AI.best_move(view) is None

    def test_does_not_mutate_the_view(self):
        gs = _playing()
        view = L.view_for(gs, L.RED)
        before = repr(view)
        AI.best_move(view)
        assert repr(view) == before

    def test_returns_a_legal_move(self):
        gs = _playing()
        for _ in range(6):
            view = L.view_for(gs, gs["next"])
            legal = {tuple(m) for m in view["legal"]}
            move = AI.best_move(view)
            assert move is not None
            flat = (move[0][0], move[0][1], move[1][0], move[1][1])
            assert flat in legal
            res = L.make_move(gs, move[0], move[1])
            assert res["ok"], res
            if res["game_over"]:
                break

    def test_respects_time_budget(self):
        gs = _playing()
        view = L.view_for(gs, L.RED)
        started = time.monotonic()
        AI.best_move(view, time_budget=0.4)
        assert time.monotonic() - started < 1.5

    def test_takes_the_free_flag_capture(self):
        """对手军旗就摆在眼前（明棋）→ 必须吃。"""
        gs = _playing("open")
        board = gs["board"]
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[11][1] = {"c": L.BLACK, "p": L.JUNQI}
        board[10][1] = {"c": L.RED, "p": L.PAIZHANG}
        board[11][3] = {"c": L.RED, "p": L.JUNQI}
        board[0][0] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["next"] = L.RED
        assert AI.best_move(L.view_for(gs, L.RED)) == ((10, 1), (11, 1))


class TestHiddenInfoDiscipline:
    def test_beliefs_come_from_position_not_from_identities(self):
        """信念集只能由位置约束与交战结果推出。"""
        gs = _playing("hidden")
        view = L.view_for(gs, L.RED)
        board = [list(row) for row in view["cells"]]
        beliefs = AI._beliefs(board, L.RED, view)
        assert beliefs, "应当有未知的敌方棋子"
        for (r, c), options in beliefs.items():
            assert board[r][c]["p"] is None, "信念集只该建立在未知格子上"
            if (r, c) not in L.back_two_rows(L.BLACK):
                assert L.DILEI not in options, "地雷不能动，不该出现在最后两排之外"
            if (r, c) not in L.HQ_CELLS:
                assert L.JUNQI not in options, "军旗只在大本营"

    def test_hidden_view_never_exposes_identities_to_the_ai(self):
        gs = _playing("hidden")
        view = L.view_for(gs, L.RED)
        for r in range(L.ROWS):
            for c in range(L.COLS):
                cell = view["cells"][r][c]
                if cell and cell["c"] == L.BLACK:
                    assert cell["p"] is None

    def test_relation_narrowing(self):
        """打完之后对手那枚比我强 → 候选里不该再出现比它弱的子。"""
        opts = set(L.PIECE_COUNTS)
        narrowed = AI._narrow_by_relation(opts, L.SHIZHANG, +1)
        assert all(L.RANK_VALUE.get(p, 99) > L.RANK_VALUE[L.SHIZHANG]
                   for p in narrowed if p in L.RANK_VALUE)
        assert L.ZHADAN in narrowed and L.DILEI in narrowed
        weaker = AI._narrow_by_relation(opts, L.SHIZHANG, -1)
        assert all(L.RANK_VALUE[p] < L.RANK_VALUE[L.SHIZHANG]
                   for p in weaker if p in L.RANK_VALUE)
        assert L.DILEI not in weaker

    def test_engineer_winning_means_mine_or_weaker(self):
        """工兵挖掉地雷时「对手那枚」是地雷 —— 它没有军衔，不能按大小排。"""
        narrowed = AI._narrow_by_relation(set(L.PIECE_COUNTS), L.GONGBING, -1)
        assert L.DILEI in narrowed
        assert L.PAIZHANG not in narrowed


class TestSelfPlay:
    def test_full_board_self_play_only_makes_legal_moves(self):
        """满盘自对弈下若干步：只走合法着法、不崩。

        **不在这里断言「一定终局」**：暗棋双方都回避拿大子撞未知，吃子极稀疏，
        而引擎的判和条件是「连续 120 半回合无吃子」，于是 49 次吃子理论上能把一局
        拖到几千步。要断言终局就用下面那个稀疏局。
        """
        for seed in (0, 1):
            _run_bounded(_playing("open", seed=seed), plies=200)
            _run_bounded(_playing("hidden", seed=100 + seed), plies=200)

    def test_sparse_games_terminate_in_both_modes(self):
        """子力很少的残局必须能下完（明棋与暗棋各来几局）。"""
        for mode in ("open", "hidden"):
            for seed in range(3):
                _run(_sparse(mode, seed), max_plies=600)

    def test_ai_beats_a_random_player_most_of_the_time(self):
        """明棋下 AI 对随机走子至少不能输多赢少。

        ⚠️ **预算要显式给小的**（v13）：明棋现在是迭代加深搜索，用默认的 0.8 秒
        会让这一条跑上几分钟（它要下 8 局、每局几百步）—— 而这条用例量的是
        「会不会被随机下法打败」，不是「给足时间能有多强」。
        """
        wins = 0
        for seed in range(8):
            rng = random.Random(500 + seed)
            gs = _playing("open", seed=seed)
            for _ in range(600):
                if gs["finished"]:
                    break
                if gs["next"] == L.RED:
                    move = AI.best_move(L.view_for(gs, L.RED), time_budget=0.05)
                    if move is None:
                        break
                else:
                    moves = L.all_moves(gs["board"], L.BLACK)
                    if not moves:
                        break
                    move = rng.choice(moves)
                res = L.make_move(gs, move[0], move[1])
                if res.get("game_over"):
                    break
            if gs["winner"] == L.RED:
                wins += 1
        assert wins >= 5, f"AI 只赢了 {wins}/8 局"


def _sparse(mode: str, seed: int) -> dict:
    """子力很少的残局：双方各一旗、一地雷、两枚能动的子 —— 几分钟就能下完。"""
    gs = _playing(mode, seed=seed)
    board = gs["board"]
    for r in range(L.ROWS):
        for c in range(L.COLS):
            board[r][c] = None
    board[11][1] = {"c": L.RED, "p": L.JUNQI}
    board[10][0] = {"c": L.RED, "p": L.DILEI}
    board[6][0] = {"c": L.RED, "p": L.PAIZHANG}
    board[6][4] = {"c": L.RED, "p": L.LIANZHANG}
    board[0][1] = {"c": L.BLACK, "p": L.JUNQI}
    board[1][4] = {"c": L.BLACK, "p": L.DILEI}
    board[5][0] = {"c": L.BLACK, "p": L.PAIZHANG}
    board[5][4] = {"c": L.BLACK, "p": L.LIANZHANG}
    gs["next"] = L.RED
    return gs


def _run(gs, max_plies=1500):
    for _ in range(max_plies):
        if gs["finished"]:
            break
        view = L.view_for(gs, gs["next"])
        move = AI.best_move(view, time_budget=0.05)
        if move is None:
            break
        res = L.make_move(gs, move[0], move[1])
        assert res["ok"], res
    assert gs["finished"], "自对弈没能在上限内结束"


def _run_bounded(gs, plies=200):
    """只跑固定步数，断言每一步都合法。"""
    for _ in range(plies):
        if gs["finished"]:
            return
        view = L.view_for(gs, gs["next"])
        move = AI.best_move(view, time_budget=0.05)
        assert move is not None, "有合法着法时不该返回 None"
        flat = (move[0][0], move[0][1], move[1][0], move[1][1])
        assert flat in {tuple(m) for m in view["legal"]}, "AI 走了不合法的一步"
        res = L.make_move(gs, move[0], move[1])
        assert res["ok"], res
