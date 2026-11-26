from random import Random

import pytest

from app.game import doudizhu


def choose(gs, difficulty="normal"):
    from app.game.doudizhu_ai import decide

    uid = gs["current_id"]
    return decide(doudizhu.view_for(gs, uid), uid, difficulty)


def lead_state():
    gs = doudizhu.create_game([1, 2, 3], None, "bot-test", Random(10))
    doudizhu.bid(gs, gs["current_id"], 3)
    return gs


def test_difficulty_changes_opening_choice_when_an_opponent_has_one_card():
    gs = lead_state()
    uid = gs["current_id"]
    gs["hands"][uid] = ["AS", "5H", "3S", "3H"]
    for opponent, card in zip((p for p in gs["seat_uids"] if p != uid), ("2S", "2H")):
        gs["hands"][opponent] = [card]
    assert choose(gs, "easy") == {"action": "play", "cards": ["3H"]}
    assert set(choose(gs, "hard")["cards"]) == {"3S", "3H"}


def test_farmer_leaves_teammate_in_control_unless_it_can_finish():
    gs = lead_state()
    landlord = gs["landlord_id"]
    gs["hands"][landlord] = ["2S", "9S", "7H"]
    doudizhu.play(gs, landlord, ["7H"])
    farmer = gs["current_id"]
    gs["hands"][farmer] = ["BJ", "3S", "5S"]
    doudizhu.play(gs, farmer, ["BJ"])
    doudizhu.pass_turn(gs, gs["current_id"])
    # Let a farmer lead, then put the other farmer in position to beat the lead.
    doudizhu.pass_turn(gs, gs["current_id"])
    doudizhu.play(gs, farmer, ["3S"])
    other_farmer = gs["current_id"]
    gs["hands"][other_farmer] = ["4C", "8S"]
    assert choose(gs) == {"action": "pass"}
    gs["hands"][other_farmer] = ["4C"]
    assert choose(gs) == {"action": "play", "cards": ["4C"]}


@pytest.mark.parametrize("difficulty", ["easy", "normal", "hard"])
@pytest.mark.parametrize("preset", ["classic", "strict"])
def test_seeded_bot_games_finish_using_only_legal_actions(difficulty, preset):
    for seed in range(8):
        rules = doudizhu.PRESETS[preset]["rules"]
        gs = doudizhu.create_game([1, -2, -3], rules, str(seed), Random(seed))
        for _ in range(240):
            uid = gs["current_id"]
            action = choose(gs, difficulty)
            if action["action"] == "bid":
                doudizhu.bid(gs, uid, action["bid"])
            elif action["action"] == "play":
                doudizhu.play(gs, uid, action["cards"])
            else:
                assert action["action"] == "pass"
                doudizhu.pass_turn(gs, uid)
            if gs["finished"]:
                break
        assert gs["finished"] and gs["reason"] == "empty_hand"
