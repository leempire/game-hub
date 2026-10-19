"""飞行棋 AI 单测：选子启发式 + 整局自对弈收敛性。

Run: cd server && python -m pytest test/test_ludo_ai.py -q
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import ludo_ai
from app.game.ludo import create_game, roll_dice, move_piece, get_global_position


def _state(seat=0, dice=3, movable=None, pieces=None, opponents=None):
    """构造一个 phase=move 的最小可用状态。"""
    gs = create_game(2)
    gs["current_seat"] = seat
    gs["dice_value"] = dice
    gs["phase"] = "move"
    if pieces is not None:
        gs["players"][seat]["pieces"] = list(pieces)
    if opponents is not None:
        gs["players"][1 - seat]["pieces"] = list(opponents)
    gs["movable_pieces"] = list(movable if movable is not None else range(4))
    return gs


def test_no_move_when_not_in_move_phase():
    gs = _state()
    gs["phase"] = "roll"
    assert ludo_ai.best_move(gs) is None
    gs = _state(movable=[])
    assert ludo_ai.best_move(gs) is None


def test_prefers_capture():
    """能撞掉对手飞机时优先撞机。

    seat0 全局偏移 0，seat1 偏移 13。让 seat0 的 0 号机走 5 步落到全局格 5，
    而 seat1 的飞机 progress=44 → 全局 (44+13)%52 = 5，正好被撞。
    """
    assert get_global_position(1, 44) == 5
    gs = _state(dice=5, pieces=[0, 20, -1, -1], opponents=[44, -1, -1, -1])
    assert ludo_ai.best_move(gs) == 0


def test_prefers_exact_finish():
    """能精确到达终点时优先到达（不进则退，折返浪费）。"""
    gs = _state(dice=6, pieces=[49, 0, -1, -1], opponents=[-1, -1, -1, -1])
    assert ludo_ai.best_move(gs) == 0


def test_prefers_takeoff_on_six():
    """掷 6 且基地有飞机时优先起飞（否则 6 白白浪费）。"""
    gs = _state(dice=6, movable=[0, 1], pieces=[-1, 40, 55, 55],
                opponents=[-1, -1, -1, -1])
    assert ludo_ai.best_move(gs) == 0


def test_avoids_landing_in_front_of_opponent():
    """两个走法收益相同时，避开落在对手 1-6 步可达范围内的那个。"""
    # seat0 两架飞机 progress 6 / 20，掷 2 → 8 / 22。
    # 8 是自己颜色 → 跳 4 格落到 12（不是 8！）；22 不是自己颜色 → 停在 22。
    # seat1 飞机 progress 5 → 全局 (5+13)%52 = 18：
    #   落在 12 的 gap = (12-18)%52 = 46（够不着，安全）
    #   落在 22 的 gap = 4（对手四步就能踩到，危险）
    assert get_global_position(1, 5) == 18
    gs = _state(dice=2, movable=[0, 1], pieces=[6, 20, -1, -1],
                opponents=[5, -1, -1, -1])
    assert ludo_ai.best_move(gs) == 0


def test_selfplay_game_terminates():
    """四家人机自对弈必须收敛：不卡死、不产生非法走子。"""
    gs = create_game(4)
    rng = random.Random(20261010)
    guard = 0
    while not gs["finished"]:
        guard += 1
        assert guard < 20000, "飞行棋人机自对弈未收敛"
        result = roll_dice(gs, rng.randint(1, 6))
        assert result["ok"], result.get("error")
        if result.get("game_over"):
            break
        if gs["phase"] != "move":
            continue
        pid = ludo_ai.best_move(gs)
        assert pid is not None, "有可动飞机却没给出选择"
        moved = move_piece(gs, pid)
        assert moved["ok"], moved.get("error")
        if moved.get("game_over"):
            break
    assert gs["finished"]
    # 4 家全部到达才算 all_finished
    finished_counts = [gs["players"][s]["finished_count"] for s in range(4)]
    assert sum(finished_counts) >= 4
