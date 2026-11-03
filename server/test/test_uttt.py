"""封盘版终极井字棋：规则、输入边界和权威预览。"""

from copy import deepcopy
import random

import pytest


LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
    (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6),
)


@pytest.fixture
def u():
    from app.game import uttt
    return uttt


def test_first_move_has_81_independent_empty_cells(u):
    gs = u.create_game()
    assert u.legal_moves(gs) == [(b, c) for b in range(9) for c in range(9)]
    assert gs["next"] == "black"
    assert gs["revision"] == 0
    assert gs["active_board"] is None
    gs["boards"][0][0] = 1
    assert gs["boards"][1][0] == u.create_game()["boards"][0][0] == 0


@pytest.mark.parametrize("cell", range(9))
def test_relative_cell_assigns_next_board(u, cell):
    gs = u.create_game()
    assert u.apply_move(gs, 4, cell, "black") is None
    assert gs["active_board"] == cell
    assert gs["next"] == "white"
    assert gs["revision"] == 1
    assert gs["moves"] == [{"board": 4, "cell": cell, "color": "black"}]
    assert {b for b, _ in u.legal_moves(gs)} == {cell}


@pytest.mark.parametrize("color,stone", [("black", 1), ("white", 2)])
@pytest.mark.parametrize("line", LINES)
def test_every_local_line_closes_board_with_remaining_empty_cells(u, color, stone, line):
    gs = u.create_game()
    gs["next"] = color
    gs["boards"][4][line[0]] = gs["boards"][4][line[1]] = stone
    assert u.apply_move(gs, 4, line[2], color) is None
    assert gs["local_results"][4] == stone
    assert all(b != 4 for b, _ in u.legal_moves(gs))


@pytest.mark.parametrize("color,stone", [("black", 1), ("white", 2)])
@pytest.mark.parametrize("line", LINES)
def test_every_macro_line_wins_and_ends_legal_moves(u, color, stone, line):
    gs = u.create_game()
    gs["next"] = color
    gs["local_results"][line[0]] = gs["local_results"][line[1]] = stone
    gs["boards"][line[2]][0:2] = [stone, stone]
    assert u.apply_move(gs, line[2], 2, color) is None
    assert gs["winner"] == color
    assert gs["reason"]
    assert gs["active_board"] is None
    assert u.legal_moves(gs) == []
    before = deepcopy(gs)
    assert u.apply_move(gs, 8, 8, gs["next"])
    assert gs == before


@pytest.mark.parametrize("result", [1, 2, 3])
def test_sending_to_closed_board_releases_constraint(u, result):
    gs = u.create_game()
    gs["local_results"][2] = result
    assert u.apply_move(gs, 0, 2, "black") is None
    assert gs["active_board"] is None
    assert {b for b, _ in u.legal_moves(gs)} == {0, 1, 3, 4, 5, 6, 7, 8}


def test_win_and_send_back_to_same_board_is_free(u):
    gs = u.create_game()
    gs["active_board"] = 2
    gs["boards"][2][0:2] = [1, 1]
    assert u.apply_move(gs, 2, 2, "black") is None
    assert gs["local_results"][2] == 1
    assert gs["active_board"] is None
    assert len(u.legal_moves(gs)) == 72


def test_full_local_draw_closes_board_without_giving_either_player_ownership(u):
    gs = u.create_game()
    gs["boards"][8] = [1, 2, 1, 1, 2, 2, 2, 1, 0]
    assert u.apply_move(gs, 8, 8, "black") is None
    assert gs["local_results"][8] == 3
    assert gs["active_board"] is None
    assert gs["winner"] is None


def test_all_closed_without_macro_line_draws_even_with_empty_cells(u):
    gs = u.create_game()
    gs["local_results"] = [1, 2, 1, 1, 2, 2, 2, 1, 0]
    gs["boards"][8] = [1, 2, 1, 1, 2, 2, 2, 1, 0]
    assert u.apply_move(gs, 8, 8, "black") is None
    assert gs["winner"] == "draw"
    assert gs["reason"]
    assert gs["active_board"] is None
    assert u.legal_moves(gs) == []


def test_drawn_macro_line_never_counts_as_win_and_no_early_draw(u):
    gs = u.create_game()
    gs["local_results"] = [3, 3, 3, 1, 2, 1, 2, 1, 0]
    assert u.apply_move(gs, 8, 0, "black") is None
    assert gs["winner"] is None
    assert u.legal_moves(gs)


@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, [], {}, -1, 9, 100])
@pytest.mark.parametrize("field", ["board", "cell"])
def test_invalid_coordinate_does_not_change_any_state(u, field, value):
    gs = u.create_game()
    before = deepcopy(gs)
    args = {"board": 0, "cell": 0, "color": "black", field: value}
    assert isinstance(u.apply_move(gs, **args), str)
    assert gs == before


@pytest.mark.parametrize("color", ["white", "X", "", None, 1, True, [], {}])
def test_invalid_color_or_wrong_turn_does_not_change_state(u, color):
    gs = u.create_game()
    before = deepcopy(gs)
    assert u.apply_move(gs, 0, 0, color)
    assert gs == before


@pytest.mark.parametrize("board,cell", [(1, 0), (0, 0), (2, 1)])
def test_wrong_board_occupied_cell_and_closed_board_do_not_change_state(u, board, cell):
    gs = u.create_game()
    gs["active_board"] = 0
    gs["boards"][0][0] = 1
    gs["local_results"][2] = 1
    before = deepcopy(gs)
    assert u.apply_move(gs, board, cell, "black")
    assert gs == before


def test_sync_previews_match_actual_move_including_self_closure_and_macro_win(u):
    gs = u.create_game()
    gs["boards"][2][0:2] = [1, 1]
    gs["local_results"][0:2] = [1, 1]
    rng = random.Random(11)
    for _ in range(20):
        before = deepcopy(gs)
        view = u.game_to_sync(gs, 17)
        assert len(view["legal"]) == len(u.legal_moves(gs))
        for preview in view["legal"]:
            played = deepcopy(gs)
            assert u.apply_move(played, preview["board"], preview["cell"], gs["next"]) is None
            assert preview["next_board"] == played["active_board"]
            assert preview["winner"] == played["winner"]
        assert gs == before
        if not view["legal"]:
            break
        move = rng.choice(u.legal_moves(gs))
        assert u.apply_move(gs, *move, gs["next"]) is None


@pytest.mark.parametrize("serialized_keys", [False, True])
def test_sync_and_history_are_detached_public_copies(u, serialized_keys):
    gs = u.create_game()
    gs["player_colors"] = {(str(17) if serialized_keys else 17): "black"}
    gs["internal_secret"] = {"do_not_send": True}
    u.apply_move(gs, 4, 2, "black")
    before = deepcopy(gs)
    sync = u.game_to_sync(gs, 17)
    history = u.snapshot(gs)
    assert sync["your_color"] == "black"
    assert u.game_to_sync(gs, 99)["your_color"] == ""
    assert history["game"] == "uttt"
    assert history["last_move"] == {"board": 4, "cell": 2, "color": "black"}
    for payload in (sync, history):
        assert "internal_secret" not in payload
        payload["boards"][4][2] = 2
        payload["local_results"][4] = 2
        payload["moves"][0]["cell"] = 8
        payload["last_move"]["cell"] = 8
    assert gs == before
