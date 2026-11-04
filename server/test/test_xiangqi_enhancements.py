"""Chinese notation, single-side perpetual checking and paired takebacks."""

from copy import deepcopy

from app.game import xiangqi as xq
from app.game import xiangqi_history
from app.game.xiangqi_notation import notation


def test_notation_for_opening_red_black_and_sideways_moves():
    board = xq.create_board()
    assert notation(board, (7, 7), (7, 4)) == "炮二平五"
    assert notation(board, (9, 7), (7, 6)) == "马二进三"
    assert notation(board, (0, 7), (2, 6)) == "马8进7"
    assert notation(board, (2, 7), (5, 7)) == "炮8进3"
    assert notation(board, (9, 4), (8, 4)) == "帅五进一"


def test_notation_disambiguates_front_back_and_multiple_pawns():
    board = [[None] * 9 for _ in range(10)]
    board[3][4] = board[7][4] = "R"
    assert notation(board, (3, 4), (3, 3)) == "前车平六"
    assert notation(board, (7, 4), (6, 4)) == "后车进一"
    board = [[None] * 9 for _ in range(10)]
    for row in (2, 3, 4, 5):
        board[row][4] = "P"
    assert notation(board, (2, 4), (2, 3)) == "前兵平六"
    assert notation(board, (3, 4), (3, 3)) == "二兵平六"
    assert notation(board, (5, 4), (5, 3)) == "后兵平六"
    board[4][0] = board[5][0] = "P"
    assert notation(board, (4, 0), (4, 1)) == "前九平八"


def test_single_side_check_on_third_identical_position_loses():
    gs = xq.create_game()
    gs["board"] = [[None] * 9 for _ in range(10)]
    gs["board"][0][4] = "k"
    gs["board"][9][3] = "K"
    gs["board"][1][5] = "R"
    cycle = [((1, 5), (1, 4)), ((0, 4), (0, 5)),
             ((1, 4), (1, 5)), ((0, 5), (0, 4))]
    for _ in range(2):
        for frm, to in cycle:
            result = xq.make_move(gs, frm, to)
            assert result["ok"], result
    assert gs["winner"] == "black"
    assert gs["reason"] == "perpetual_check"
    assert gs["no_capture"] == 8


def test_ordinary_repeated_positions_do_not_become_perpetual_check():
    gs = xq.create_game()
    cycle = [((9, 0), (8, 0)), ((0, 0), (1, 0)),
             ((8, 0), (9, 0)), ((1, 0), (0, 0))]
    for _ in range(2):
        for frm, to in cycle:
            assert xq.make_move(gs, frm, to)["ok"]
    assert gs["winner"] is None
    assert gs["reason"] is None


def test_mutual_checks_are_not_awarded_as_one_sided_win():
    gs = {"_repetition_positions": ["A", "B", "A", "B", "A"],
          "_repetition_checks": [{"by": side, "check": True}
                                  for side in ("red", "black", "red", "black")]}
    assert xiangqi_history.adjudicate(gs) == ("draw", "mutual_perpetual_check")
    gs["_repetition_checks"][0]["check"] = False
    assert xiangqi_history.adjudicate(gs) == ("red", "perpetual_check")
    gs["_repetition_checks"][1]["check"] = False
    assert xiangqi_history.adjudicate(gs) is None


def test_paired_takeback_restores_board_captures_counters_and_notation():
    gs = xq.create_game()
    original = deepcopy(gs["board"])
    # The red cannon can legally capture the black horse over one cannon.
    gs["no_capture"] = 50
    assert xq.make_move(gs, (7, 1), (0, 1))["ok"]
    black_move = xq.all_legal_moves(gs["board"], "black")[0]
    assert xq.make_move(gs, *black_move)["ok"]
    revision = gs["revision"]
    result = xq.undo_last_pair(gs, "red")
    assert result["ok"] and result["removed"] == 2
    assert gs["board"] == original
    assert gs["moves"] == [] and gs["last_move"] is None
    assert gs["no_capture"] == 50
    assert gs["revision"] > revision
    assert gs["next"] == "red"
    assert len(gs["_repetition_positions"]) == 1


def test_takeback_rejects_pending_bot_or_finished_game_without_mutation():
    gs = xq.create_game()
    before = deepcopy(gs)
    assert not xq.undo_last_pair(gs, "red")["ok"]
    assert gs == before
    xq.make_move(gs, (6, 0), (5, 0))
    before = deepcopy(gs)
    assert "等人机" in xq.undo_last_pair(gs, "red")["error"]
    assert gs == before
    gs["winner"] = "black"
    assert not xq.undo_last_pair(gs, "black")["ok"]


def test_move_and_sync_supply_notation_and_monotonic_revision():
    gs = xq.create_game()
    result = xq.make_move(gs, (7, 7), (7, 4))
    assert result["move"]["notation"] == "炮二平五"
    assert result["move"]["revision"] == 1
    assert "_no_capture_before" not in result["move"]
    view = xq.game_to_sync(gs, 1)
    assert view["moves"][0]["notation"] == "炮二平五"
    assert "_no_capture_before" not in view["last_move"]
    assert "_no_capture_before" not in xq.snapshot(gs)["moves"][0]
