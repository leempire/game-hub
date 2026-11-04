"""Military setup deadlines, terminal truth and token-bound hidden beliefs."""

from copy import deepcopy
import random

import pytest

from app.game import luzhanqi as lz, luzhanqi_ai as ai


def setup(seconds=0, mode="hidden", reveal=False):
    gs = lz.create_game(mode, reveal, setup_timeout_seconds=seconds)
    gs["player_colors"] = {1: "red", 2: "black"}
    rng = random.Random(8)
    for color in ("red", "black"):
        lz.fill_layout(gs, color, lz.random_layout(color, rng))
    gs["ready"] = {"red": False, "black": False}
    return gs


def sparse_battle():
    gs = lz.create_game("hidden", False)
    gs.update(phase="playing", player_colors={1: "red", 2: "black"})
    for r, c, color, piece in [
        (1, 0, "black", lz.SILING), (2, 0, "red", lz.SHIZHANG),
        (5, 0, "black", lz.PAIZHANG), (6, 4, "red", lz.PAIZHANG),
        (0, 1, "black", lz.JUNQI), (11, 1, "red", lz.JUNQI),
    ]:
        gs["board"][r][c] = {"c": color, "p": piece}
    return gs


def test_default_setup_remains_unlimited():
    gs = setup()
    before = deepcopy(gs)
    assert not lz.advance_setup(gs, 10000000)
    assert gs == before
    assert lz.view_for(gs, "red")["setup_remaining_ms"] is None


@pytest.mark.parametrize("seconds", [30, 60, 120])
def test_deadline_auto_confirms_legal_current_layout_and_keeps_identity_private(seconds):
    gs = setup(seconds)
    before = deepcopy(gs["board"])
    assert not lz.advance_setup(gs, seconds * 1000 - 1)
    view = lz.view_for(gs, "red")
    assert view["setup_remaining_ms"] == 1
    assert all(cell["p"] is None for row in view["cells"] for cell in row
               if cell and cell["c"] == "black")
    assert lz.advance_setup(gs, 1)
    assert gs["phase"] == "playing"
    assert gs["ready"] == {"red": True, "black": True}
    assert gs["board"] == before
    assert not lz.advance_setup(gs, 10000)
    view = lz.view_for(gs, "red")
    assert all(cell["p"] is None for row in view["cells"] for cell in row
               if cell and cell["c"] == "black")


def test_invalid_setup_is_not_silently_confirmed_at_deadline():
    gs = setup(30)
    gs["board"][0][1] = None
    with pytest.raises(ValueError):
        lz.advance_setup(gs, 30000)
    assert gs["phase"] == "setup"
    with pytest.raises(ValueError):
        lz.advance_setup(gs, -1)


def test_finished_hidden_game_exposes_truth_but_active_game_does_not():
    gs = setup()
    for side in ("red", "black"):
        lz.confirm_setup(gs, side)
    assert not lz.game_to_sync(gs, 1)["truth_available"]
    assert any(cell and cell["p"] is None for row in lz.game_to_sync(gs, 1)["cells"] for cell in row)
    lz.surrender(gs, "red")
    view = lz.game_to_sync(gs, 1)
    assert view["truth_available"]
    assert all(cell["p"] is not None for row in view["cells"] for cell in row if cell)
    assert view["legal"] == []


def test_hidden_tracking_tokens_do_not_encode_real_piece_ranks():
    gs = setup()
    original = lz.view_for(gs, "red")
    other = deepcopy(gs)
    for row in other["board"]:
        for cell in row:
            if cell and cell["c"] == "black":
                cell["p"] = lz.PAIZHANG  # Deliberately change all actual identities.
    changed = lz.view_for(other, "red")
    assert original["cells"] == changed["cells"]
    for row in original["cells"]:
        for cell in row:
            if cell:
                assert cell["token"].split(":")[0] in ("red", "black")
                assert not any(rank in cell["token"] for rank in lz.PIECE_COUNTS)


def test_combat_constraint_follows_survivor_to_new_square_and_not_replacement():
    gs = sparse_battle()
    assert lz.make_move(gs, (2, 0), (1, 0))["ok"]   # Our division dies to their commander.
    assert lz.make_move(gs, (1, 0), (0, 0))["ok"]   # Commander moves away, into a back rank.
    assert lz.make_move(gs, (6, 4), (7, 4))["ok"]
    assert lz.make_move(gs, (5, 0), (1, 0))["ok"]   # Different enemy occupies the old battle square.
    view = lz.view_for(gs, "red")
    beliefs = ai._beliefs(view["cells"], "red", view)
    assert lz.SHIZHANG not in beliefs[(0, 0)]
    assert lz.PAIZHANG not in beliefs[(0, 0)]
    assert lz.SILING in beliefs[(0, 0)]
    assert lz.DILEI not in beliefs[(0, 0)]  # Moved before, though not the last-moving piece.
    assert lz.JUNQI not in beliefs[(0, 0)]
    assert lz.PAIZHANG in beliefs[(1, 0)]  # Never inherit the previous occupant's strength.
    battle = view["battle_history"][0]
    assert "defender_piece" not in battle
    assert battle["attacker_piece"] == lz.SHIZHANG


def test_enemy_attacker_surviving_combat_is_stronger_not_weaker():
    gs = sparse_battle()
    gs["next"] = "black"
    assert lz.make_move(gs, (1, 0), (2, 0))["ok"]
    view = lz.view_for(gs, "red")
    beliefs = ai._beliefs(view["cells"], "red", view)
    assert lz.SILING in beliefs[(2, 0)]
    assert lz.PAIZHANG not in beliefs[(2, 0)]
    assert lz.SHIZHANG not in beliefs[(2, 0)]


def test_death_counts_do_not_count_a_survivor_as_dead():
    view = {"battles": [
        {"attacker": "red", "attacker_piece": lz.PAIZHANG,
         "defender": "black", "defender_piece": lz.SILING, "outcome": "attacker_dies"},
        {"attacker": "black", "attacker_piece": lz.GONGBING,
         "defender": "red", "defender_piece": lz.DILEI, "outcome": "defender_dies"},
        {"attacker": "red", "attacker_piece": lz.ZHADAN,
         "defender": "black", "defender_piece": lz.JUNZHANG, "outcome": "both_die"},
    ]}
    assert ai._dead_enemy_ranks(view, "red") == {lz.JUNZHANG: 1}
