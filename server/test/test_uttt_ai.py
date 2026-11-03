"""AI 走公开盘面，战术和搜索不得修改输入。"""

from copy import deepcopy
import random
import time

import pytest


@pytest.fixture
def modules():
    from app.game import uttt, uttt_ai
    return uttt, uttt_ai


@pytest.mark.parametrize("value,expected", [
    ("easy", "easy"), ("normal", "normal"), ("hard", "hard"),
    (None, "normal"), ("unknown", "normal"), (True, "normal"),
    ([], "normal"), ({}, "normal"),
])
def test_unknown_difficulty_uses_normal(modules, value, expected):
    _, ai = modules
    assert ai.normalize_difficulty(value) == expected


def test_easy_uses_supplied_random_generator(modules):
    u, ai = modules
    view = u.game_to_sync(u.create_game(), 1)
    assert ai.best_move(view, "easy", rng=random.Random(8)) == random.Random(8).choice(
        [(b, c) for b in range(9) for c in range(9)]
    )


@pytest.mark.parametrize("difficulty", ["normal", "hard"])
@pytest.mark.parametrize("color,stone", [("black", 1), ("white", 2)])
def test_takes_immediate_macro_win(modules, difficulty, color, stone):
    u, ai = modules
    gs = u.create_game()
    gs["next"] = color
    gs["local_results"][0:2] = [stone, stone]
    gs["boards"][2][0:2] = [stone, stone]
    gs["active_board"] = 2
    view = u.game_to_sync(gs, 1)
    before = deepcopy(view)
    assert ai.best_move(view, difficulty, rng=random.Random(1)) == (2, 2)
    assert view == before


@pytest.mark.parametrize("difficulty", ["normal", "hard"])
def test_avoids_sending_opponent_to_one_move_macro_win(modules, difficulty):
    u, ai = modules
    gs = u.create_game()
    gs["local_results"][0:2] = [2, 2]
    gs["boards"][2][0:2] = [2, 2]
    gs["active_board"] = 4
    gs["boards"][4] = [2, 1, 0, 1, 2, 0, 1, 2, 1]
    assert u.legal_moves(gs) == [(4, 2), (4, 5)]
    assert ai.best_move(u.game_to_sync(gs, 1), difficulty, rng=random.Random(2)) == (4, 5)


@pytest.mark.parametrize("difficulty", ["normal", "hard"])
def test_blocks_macro_win_when_closed_destinations_give_free_choice(modules, difficulty):
    u, ai = modules
    gs = u.create_game()
    gs["local_results"] = [2, 2, 0, 3, 3, 3, 3, 3, 3]
    gs["boards"][2][0:2] = [2, 2]
    gs["active_board"] = 2
    assert ai.best_move(u.game_to_sync(gs, 1), difficulty, rng=random.Random(3)) == (2, 2)


@pytest.mark.parametrize("difficulty", ["easy", "normal", "hard"])
def test_returns_legal_moves_from_public_view_without_changing_it(modules, difficulty):
    u, ai = modules
    gs = u.create_game()
    for _ in range(12):
        view = u.game_to_sync(gs, 99)
        before = deepcopy(view)
        move = ai.best_move(view, difficulty, time_budget=0.003, rng=random.Random(5))
        assert move in u.legal_moves(gs)
        assert view == before
        assert u.apply_move(gs, *move, gs["next"]) is None


@pytest.mark.parametrize("difficulty", ["easy", "normal", "hard"])
@pytest.mark.parametrize("winner", ["black", "white", "draw"])
def test_terminal_game_has_no_move(modules, difficulty, winner):
    u, ai = modules
    gs = u.create_game()
    gs["winner"] = winner
    assert ai.best_move(u.game_to_sync(gs, 1), difficulty) is None


@pytest.mark.parametrize("difficulty", ["normal", "hard"])
def test_tiny_budget_still_returns_a_move_promptly(modules, difficulty):
    u, ai = modules
    view = u.game_to_sync(u.create_game(), 1)
    start = time.monotonic()
    assert ai.best_move(view, difficulty, time_budget=0, rng=random.Random(9)) in [
        (b, c) for b in range(9) for c in range(9)
    ]
    assert time.monotonic() - start < 0.3
