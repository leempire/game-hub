import random
from collections import defaultdict

import pytest

from app.game import concentration


@pytest.mark.parametrize("card_count", [16, 24])
def test_deal_uses_exactly_two_different_suits_per_rank(card_count):
    state = concentration.create_game([11, 22, 33, 44], card_count, "round", random.Random(17))
    cards_by_rank = defaultdict(list)
    for rank, suit in state["deck"]:
        cards_by_rank[rank].append(suit)
    assert len(state["deck"]) == card_count
    assert len(cards_by_rank) == card_count // 2
    assert set(cards_by_rank) <= set("A 2 3 4 5 6 7 8 9 10 J Q K".split())
    for suits in cards_by_rank.values():
        assert len(suits) == len(set(suits)) == 2
        assert set(suits) <= {"S", "H", "C", "D"}
    assert state["turn_id"] in [11, 22, 33, 44]


def test_forfeit_while_revealing_skips_seat_and_cannot_win_with_more_pairs():
    state = concentration.create_game([11, 22, 33], 16, "round", random.Random(17))
    actor = state["turn_id"]
    other_seats = [11, 22, 33]
    next_uid = other_seats[(other_seats.index(actor) + 1) % 3]
    first = 0
    second = next(i for i, card in enumerate(state["deck"]) if i and card[0] == state["deck"][first][0])
    concentration.flip(state, actor, first)
    concentration.flip(state, actor, second)
    assert concentration.forfeit(state, actor, "leave")
    assert concentration.view(state)["phase"] == "revealing"
    concentration.finish_reveal(state)
    assert concentration.view(state)["turn_id"] == next_uid
    assert concentration.view(state)["slots"][first] == {"state": "collected"}
    assert concentration.forfeit(state, next_uid, "surrender")
    end = concentration.view(state)
    scores = {score["id"]: score for score in end["scores"]}
    winner = next(uid for uid in other_seats if uid not in (actor, next_uid))
    assert scores[winner] == {"id": winner, "pairs": 0, "rank": 1}
    assert scores[actor] == {"id": actor, "pairs": 1, "rank": 2, "forfeited": True}
    assert scores[next_uid] == {"id": next_uid, "pairs": 0, "rank": 2, "forfeited": True}
