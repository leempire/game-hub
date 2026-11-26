from random import Random

from app.game import guandan, guandan_ai


def card(card_id, rank, suit="C"):
    return {"id": card_id, "rank": rank, "suit": suit}


def position(hand, *, remaining=(None, None, None, None)):
    return {"players": [1, 2, 3, 4], "current_id": 2, "phase": "playing", "level": "2",
            "hand": hand, "last_play": None, "remaining": list(remaining), "return_choices": [],
            "finish_order": []}


def test_normal_leads_a_combination_while_easy_leads_a_low_single():
    view = position([card(1, "3"), card(2, "3", "S"), card(3, "7")])
    assert guandan_ai.decide(view, 2, "easy") == {"action": "play", "cards": [1]}
    assert guandan_ai.decide(view, 2, "normal") == {"action": "play", "cards": [1, 2]}


def test_bot_yields_to_partner_but_closes_its_hand_if_possible():
    view = position([card(1, "3"), card(2, "5")])
    view["last_play"] = {"uid": 4, "kind": "single", "rank": "4", "cards": [card(8, "4")], "wild": []}
    assert guandan_ai.decide(view, 2, "normal") == {"action": "pass"}
    view["hand"] = [card(2, "5")]
    assert guandan_ai.decide(view, 2, "hard") == {"action": "play", "cards": [2]}


def test_hard_feeds_a_partner_with_one_card_using_public_remaining_count():
    view = position([card(1, "3"), card(2, "6"), card(3, "6", "S")], remaining=(None, 3, None, 1))
    assert guandan_ai.decide(view, 2, "normal") == {"action": "play", "cards": [2, 3]}
    assert guandan_ai.decide(view, 2, "hard") == {"action": "play", "cards": [1]}


def test_return_is_chosen_only_from_own_legal_cards():
    view = position([card(1, "3"), card(2, "4"), card(3, "4", "H"), card(4, "5")])
    view.update(phase="returning", current_id=None, return_choices=[2, 3, 4])
    assert guandan_ai.decide(view, 2, "easy") == {"action": "return", "card": 2}
    assert guandan_ai.decide(view, 2, "hard") == {"action": "return", "card": 4}


def test_all_difficulties_complete_deals_using_only_player_views():
    seeds = range(3)
    for seed in seeds:
        random = Random(seed)
        game = guandan.create_game([1, 2, 3, 4], str(seed), random)
        for _ in range(2000):
            phase = game["phase"]
            if phase == "finished":
                assert game["reason"] == "clear_ace"
                break
            if phase == "between":
                guandan.apply(game, 1, "next", {"rng": random})
            elif phase == "returning":
                for player in game["players"]:
                    view = guandan.view_for(game, player)
                    if view["return_choices"]:
                        action = guandan_ai.decide(view, player, "hard")
                        guandan.apply(game, player, action["action"], action)
            else:
                player = game["current_id"]
                view = guandan.view_for(game, player)
                difficulty = ("easy", "normal", "hard")[(seed + player) % 3]
                action = guandan_ai.decide(view, player, difficulty)
                guandan.apply(game, player, action["action"], action)
        else:
            raise AssertionError(f"Match did not finish for seed {seed}")
