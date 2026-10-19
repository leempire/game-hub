"""中国象棋 AI 单测。运行：
    cd server && python -m pytest test/test_xiangqi_ai.py -q
"""

import sys
import os
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.xiangqi import (
    RED,
    BLACK,
    create_game,
    create_board,
    board_from_rows,
    all_legal_moves,
    make_move,
    is_in_check,
    game_status,
    apply_move,
    undo_move,
    rows_of,
)
from app.xiangqi_ai import best_move


def _red_moves(board, budget=0.3):
    return best_move(board, RED, budget)


# ── 基本契约 ──────────────────────────────────────────

def test_returns_none_when_no_moves():
    """无子可动（困毙）时返回 None，不抛异常。"""
    board = board_from_rows([
        "....k....",
        ".........",
        "..N...N..",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "...K.....",
    ])
    assert best_move(board, BLACK, 0.2) is None
    print("  ✓ test_returns_none_when_no_moves")


def test_single_move_is_returned_directly():
    """只剩一个合法着法时直接返回（不走搜索）。"""
    # 红帅被黑车沿第 9 行将军，(9,3)/(9,5) 仍在第 9 行 → 唯一的活路是 (8,4)
    board = board_from_rows([
        "...k.....",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "r...K....",
    ])
    assert all_legal_moves(board, RED) == [((9, 4), (8, 4))]
    t = time.time()
    assert best_move(board, RED) == ((9, 4), (8, 4))
    assert time.time() - t < 0.05, "单着不该进搜索"
    print("  ✓ test_single_move_is_returned_directly")


def test_does_not_mutate_board():
    """AI 不能改动调用方手里的盘面（调用方拿着房间里的 board）。"""
    board = create_board()
    before = rows_of(board)
    best_move(board, RED, 0.2)
    assert rows_of(board) == before
    print("  ✓ test_does_not_mutate_board")


def test_respects_time_budget():
    """时间预算必须兜住——不设限的话中局搜索会卡死事件循环。"""
    board = create_board()
    t = time.time()
    best_move(board, RED, 0.3)
    elapsed = time.time() - t
    assert elapsed < 2.0, f"0.3s 预算却跑了 {elapsed:.2f}s"
    print("  ✓ test_respects_time_budget")


def test_returns_legal_move_from_opening():
    board = create_board()
    mv = _red_moves(board)
    assert mv in all_legal_moves(board, RED)
    print("  ✓ test_returns_legal_move_from_opening")


# ── 棋力 ──────────────────────────────────────────────

def test_finds_forced_win_in_one():
    """一步制胜：走出来的那步必须让对方无着可走。

    这个残局有 3 步都能赢（红帅在 (9,3) 顺带封死了 (0,3)——黑将往那儿走会和红帅
    照面），其中既有将死也有**困毙**（象棋里困毙同样判负）。所以断言「对方无着」
    而不是某一步具体着法、也不是非要 checkmate。
    """
    board = board_from_rows([
        "....k....",
        ".........",
        "R........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "...K.....",
    ])
    board[1][1] = "R"                      # 封住第 1 行
    assert is_in_check(board, BLACK) is False
    assert game_status(board, BLACK) == "playing"
    mv = _red_moves(board)
    assert mv is not None
    captured = apply_move(board, mv[0], mv[1])
    status = game_status(board, BLACK)
    undo_move(board, mv[0], mv[1], captured)
    assert status in ("checkmate", "stalemate"), f"AI 走了 {mv}，却没能取胜（{status}）"
    print("  ✓ test_finds_forced_win_in_one")


def test_takes_the_free_rook():
    """白送的车必须吃（不吃的唯一理由是有更大的收益）。"""
    board = board_from_rows([
        "..k......",
        ".........",
        ".........",
        ".........",
        ".........",
        "....r....",
        ".........",
        ".........",
        ".........",
        "K...R....",
    ])
    assert is_in_check(board, BLACK) is False
    assert _red_moves(board) == ((9, 4), (5, 4))
    print("  ✓ test_takes_the_free_rook")


def test_escapes_check():
    """被将军时必须应将（有多个解时也不能挑一个解不掉的）。"""
    # 黑车 (8,0) 沿第 8 行将军红帅 (8,4)；(8,3)/(8,5) 仍在第 8 行 → 只有 (7,4)/(9,4) 两条活路
    board = board_from_rows([
        "...k.....",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "r...K....",
        ".........",
    ])
    assert is_in_check(board, RED) is True
    assert len(all_legal_moves(board, RED)) == 2
    mv = _red_moves(board)
    assert mv is not None
    captured = apply_move(board, mv[0], mv[1])
    still_in_check = is_in_check(board, RED)
    undo_move(board, mv[0], mv[1], captured)
    assert still_in_check is False, f"AI 走了 {mv} 却没解将"
    print("  ✓ test_escapes_check")


def test_never_loses_to_random_player():
    """AI 对随机走子不能输——棋力低于随机的 AI 等于没有 AI。

    断言的是「不输」而不是「全胜」：随机对手偶尔能靠躲子拖到 60 回合无吃子判和，
    硬要求全胜会让这条用例偶发失败（**测试不要依赖随机性**）。真正的棋力不变式是
    「随机赢不了 AI」，胜局数只作下界。
    """
    wins = draws = losses = 0
    for seed in (7, 11, 23):
        random.seed(seed)
        gs = create_game()
        for _ in range(300):
            color = gs["next"]
            mv = (best_move(gs["board"], color, 0.15) if color == RED
                  else random.choice(all_legal_moves(gs["board"], color)))
            if mv is None:
                break
            result = make_move(gs, mv[0], mv[1])
            assert result["ok"], f"产生了非法着法 {mv}: {result}"
            if result["game_over"]:
                break
        if gs["winner"] == RED:
            wins += 1
        elif gs["winner"] == "draw":
            draws += 1
        else:
            losses += 1
    assert losses == 0, f"AI 输给了随机走子 {losses} 局"
    assert wins >= 2, f"AI 只赢了 {wins}/3 局（和 {draws} 局）"
    print("  ✓ test_never_loses_to_random_player")


def test_self_play_produces_only_legal_moves_and_terminates():
    """AI 自对弈：每一步都合法，且棋局必然终止（无吃子判和兜底）。"""
    random.seed(3)
    gs = create_game()
    for _ in range(400):
        mv = best_move(gs["board"], gs["next"], 0.05)
        if mv is None:
            break
        result = make_move(gs, mv[0], mv[1])
        assert result["ok"], f"产生了非法着法 {mv}: {result}"
        if result["game_over"]:
            break
    assert gs["finished"], "自对弈没有终局——无吃子判和没兜住"
    assert gs["winner"] in (RED, BLACK, "draw")
    print("  ✓ test_self_play_produces_only_legal_moves_and_terminates")


if __name__ == "__main__":
    print("Running xiangqi AI tests...")
    test_returns_none_when_no_moves()
    test_single_move_is_returned_directly()
    test_does_not_mutate_board()
    test_respects_time_budget()
    test_returns_legal_move_from_opening()
    test_finds_forced_win_in_one()
    test_takes_the_free_rook()
    test_escapes_check()
    test_never_loses_to_random_player()
    test_self_play_produces_only_legal_moves_and_terminates()
    print("\nAll xiangqi AI tests passed! ✓")
