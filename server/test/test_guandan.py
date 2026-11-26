from copy import deepcopy
from random import Random

import pytest

from app.game import guandan as engine


SEATS = [17, 29, 41, 53]


def _game(*hands, level="2", level_team=None):
    gs = engine.create_game(SEATS, "fixed-match", Random(0))
    gs.update(level=level, level_team=level_team, current_id=SEATS[0])
    if level_team is not None:
        gs["levels"][level_team] = level
    next_id = 0
    for uid, faces in zip(SEATS, hands):
        cards = []
        for face in faces.split():
            rank, suit = (face, "") if face in ("SJ", "BJ") else (face[:-1], face[-1])
            cards.append({"id": next_id, "rank": rank, "suit": suit})
            next_id += 1
        gs["hands"][uid] = cards
    return gs


def _ids(gs, uid):
    return [card["id"] for card in engine.view_for(gs, uid)["hand"]]


def _play(gs, uid, ids=None):
    engine.apply(gs, uid, "play", {"cards": _ids(gs, uid) if ids is None else ids})


def _pass(gs, *uids):
    for uid in uids:
        engine.apply(gs, uid, "pass", {})


def test_deal_has_distinct_entities_and_private_seat_views():
    gs = engine.create_game(SEATS, "fixed-match", Random(44))
    views = [engine.view_for(gs, uid) for uid in SEATS]
    cards = [card for view in views for card in view["hand"]]
    assert len({card["id"] for card in cards}) == 108
    assert len({(card["rank"], card["suit"]) for card in cards}) == 54
    for seat, view in enumerate(views):
        assert view["players"] == SEATS
        assert view["levels"] == ["2", "2"]
        assert (view["deal"], view["revision"], view["phase"]) == (1, 0, "playing")
        assert view["current_id"] in SEATS
        assert view["remaining"] == [27 if i == seat else None for i in range(4)]
        assert view["lead_reason"] == "first"
    views[0]["hand"][0]["rank"] = "changed"
    assert engine.view_for(gs, SEATS[0])["hand"][0]["rank"] != "changed"


@pytest.mark.parametrize(("faces", "kind", "rank"), [
    ("AS 2D 3C 4H 5S", "straight", "5"),
    ("10S JD QC KH AS", "straight", "A"),
    ("AS AH 2C 2S 3C 3D", "tube", "3"),
    ("QS QH KC KS AC AD", "tube", "A"),
    ("AS AH AC 2S 2C 2D", "plate", "2"),
    ("KS KH KC AS AC AD", "plate", "A"),
    ("SJ SJ", "pair", "SJ"),
    ("9C 9H 9S BJ BJ", "full_house", "9"),
])
def test_preview_understands_sequence_ends_and_valid_joker_pairs(faces, kind, rank):
    gs = _game(faces, level="10")
    combo = engine.preview(gs, SEATS[0], _ids(gs, SEATS[0]))
    assert (combo["kind"], combo["rank"], combo["wild"]) == (kind, rank, [])


@pytest.mark.parametrize("faces", ["QC KD AS 2S 3C", "5H SJ", "SJ BJ", "2S 3S 4S 5S 6S 7S"])
def test_invalid_combinations_do_not_mutate_state(faces):
    gs = _game(faces, level="5")
    old = deepcopy(gs)
    with pytest.raises(ValueError, match="牌型"):
        _play(gs, SEATS[0])
    assert gs == old


def test_wilds_prefer_plate_on_lead_and_tube_against_tube():
    first, second, *_ = SEATS
    ambiguous = "7S 7D 8C 8S 5H 5H"
    gs = _game(ambiguous, level="5")
    lead = engine.preview(gs, first, _ids(gs, first))
    assert (lead["kind"], lead["rank"]) == ("plate", "8")
    assert sorted(card["rank"] for card in lead["wild"]) == ["7", "8"]

    gs = _game("5S 5C 6S 6D 7C 7H 2S", ambiguous, level="5")
    _play(gs, first, _ids(gs, first)[:-1])
    selected = _ids(gs, second)
    response = engine.preview(gs, second, selected)
    assert (response["kind"], response["rank"]) == ("tube", "9")
    assert sorted(card["rank"] for card in response["wild"]) == ["9", "9"]
    assert engine.hint(gs, second)["kind"] == "tube"
    _play(gs, second, selected)
    assert engine.view_for(gs, first)["last_play"] == {"uid": second, **response}


def test_wild_can_change_suit_in_a_straight_flush():
    gs = _game("6S 7S 8S 9S 5H", level="5")
    combo = engine.preview(gs, SEATS[0], _ids(gs, SEATS[0]))
    assert (combo["kind"], combo["rank"], combo["wild"]) == (
        "straight_flush", "10", [{"id": 4, "rank": "10", "suit": "S"}],
    )


def test_wild_single_keeps_the_level_rank_and_a_sequence_keeps_natural_strength():
    first, second, *_ = SEATS
    gs = _game("5H", level="5")
    assert engine.preview(gs, first, _ids(gs, first)) == {
        "kind": "single", "rank": "5", "cards": [{"id": 0, "rank": "5", "suit": "H"}], "wild": [],
    }

    gs = _game("AS 2D 3C 4H 5D 8S", "2S 3D 4C 5S 6H", level="5")
    _play(gs, first, _ids(gs, first)[:-1])
    assert engine.preview(gs, second, _ids(gs, second))["rank"] == "6"


@pytest.mark.parametrize(("lead", "reply", "wins"), [
    ("9S 9C 9D 9H", "8S 8C 8D 8H 8S", True),
    ("9S 9C 9D 9H 9S", "2S 3S 4S 5S 6S", True),
    ("2S 3S 4S 5S 6S", "9S 9C 9D 9H 9S", False),
    ("10S JS QS KS AS", "3S 3C 3D 3H 3S 3C", True),
    ("9S 9C 9D 9H 9S 9C 9D 9H 2H 2H", "BJ SJ BJ SJ", True),
    ("BJ SJ BJ SJ", "9S 9C 9D 9H 9S 9C 9D 9H 2H 2H", False),
    ("2S 2D 2C 2S", "AS AH AC AD", False),
    ("AS AH AC AD", "2S 2D 2C 2S", True),
    ("8S 8C 8D 8H", "8S 8C 8D 8H", False),
])
def test_bombs_follow_size_flush_and_joker_strength(lead, reply, wins):
    first, second, *_ = SEATS
    gs = _game(f"{lead} 4C", reply)
    _play(gs, first, _ids(gs, first)[:-1])
    if wins:
        assert engine.hint(gs, second) == engine.preview(gs, second, _ids(gs, second))
        _play(gs, second)
    else:
        assert engine.hint(gs, second) is None
        with pytest.raises(ValueError, match="压过"):
            _play(gs, second)


def test_play_rejects_wrong_turn_foreign_and_duplicate_card_ids():
    first, second, *_ = SEATS
    gs = _game("3C 4D", "5S 6H")
    before = deepcopy(gs)
    card = _ids(gs, first)[0]
    foreign = _ids(gs, second)[0]
    for uid, cards in [(second, [foreign]), (first, [foreign]), (first, [card, card]),
                       (first, [True]), (first, [str(card)]), (first, None)]:
        with pytest.raises(ValueError):
            engine.apply(gs, uid, "play", {"cards": cards})
        assert gs == before
    with pytest.raises(ValueError):
        engine.apply(gs, first, "pass", {})
    assert gs == before


def test_partner_leads_when_every_active_opponent_passes_after_a_finish():
    first, second, partner, fourth = SEATS
    gs = _game("3C", "4D", "5H", "6S")
    _play(gs, first)
    _pass(gs, second, partner)
    assert engine.view_for(gs, fourth)["last_play"]["uid"] == first
    _pass(gs, fourth)
    view = engine.view_for(gs, fourth)
    assert view["current_id"] == partner
    assert view["lead_reason"] == "partner"
    assert view["passed"] == []
    assert view["last_play"] is None
    assert view["revision"] == 4


def test_three_passes_restore_the_last_players_lead_and_the_tenth_card_is_reported():
    first, second, partner, fourth = SEATS
    gs = _game("3C 4D 5H 6S 7C 8D 9H 10S JC QD KH", "4S", "5C", "6D")
    assert engine.view_for(gs, second)["remaining"][0] is None
    _play(gs, first, [_ids(gs, first)[0]])
    assert engine.view_for(gs, second)["remaining"][0] == 10
    _pass(gs, second, partner, fourth)
    view = engine.view_for(gs, second)
    assert (view["current_id"], view["lead_reason"], view["last_play"]) == (first, "pass", None)


def test_overtaking_the_last_hand_reopens_play_for_seats_that_passed():
    first, second, partner, fourth = SEATS
    gs = _game("3C", "8D", "5H 6C", "4S")
    _play(gs, first)
    _pass(gs, second)
    _play(gs, partner, [_ids(gs, partner)[0]])
    assert engine.view_for(gs, second)["passed"] == []
    _pass(gs, fourth)
    assert engine.view_for(gs, fourth)["current_id"] == second
    _play(gs, second)
    _pass(gs, partner, fourth)
    assert engine.view_for(gs, fourth)["lead_reason"] == "partner"
    assert engine.view_for(gs, fourth)["current_id"] == fourth


@pytest.mark.parametrize(("level", "level_team", "order", "phase", "advance", "next_level"), [
    ("2", None, [0, 2], "between", 3, "5"),
    ("K", 0, [0, 1, 2], "between", 2, "A"),
    ("A", 0, [0, 1, 3], "between", 1, "A"),
    ("A", 0, [0, 1, 2], "finished", 2, "A"),
    ("A", 1, [0, 2], "between", 3, "5"),
])
def test_match_levels_finish_orders_and_clear_ace(level, level_team, order, phase, advance, next_level):
    faces = ["8S" if index not in order else f"{3 + order.index(index)}C" for index in range(4)]
    gs = _game(*faces, level=level, level_team=level_team)
    for finishing in order:
        while engine.view_for(gs, SEATS[0])["current_id"] != SEATS[finishing]:
            current = engine.view_for(gs, SEATS[0])["current_id"]
            _pass(gs, current)
        _play(gs, SEATS[finishing])
    summary = engine.snapshot(gs)
    result = summary["deal_result"]
    expected_order = [SEATS[index] for index in order]
    if len(order) == 3:
        expected_order.extend(uid for uid in SEATS if uid not in expected_order)
    assert engine.view_for(gs, SEATS[0])["phase"] == phase
    assert result["winner"] == 0
    assert result["finish_order"] == expected_order
    assert (result["advance"], result["level"], result["levels"][0]) == (advance, level, next_level)
    assert summary["winner"] == (0 if phase == "finished" else None)
    assert summary["reason"] == ("clear_ace" if phase == "finished" else None)


def _single_down():
    gs = _game("3C", "4D", "8H", "5S")
    _play(gs, SEATS[0])
    _play(gs, SEATS[1])
    _pass(gs, SEATS[2])
    _play(gs, SEATS[3])
    return gs


def _double_down():
    gs = _game("3C", "8D", "4H", "9S")
    _play(gs, SEATS[0])
    _pass(gs, SEATS[1])
    _play(gs, SEATS[2])
    return gs


class _NextDeal(Random):
    def __init__(self, requested):
        super().__init__(0)
        self.requested = requested

    def shuffle(self, deck):
        available = deck.copy()
        hands = [[] for _ in SEATS]
        for hand, faces in zip(hands, self.requested):
            for face in faces.split():
                card = next(card for card in available if card["rank"] + card["suit"] == face)
                available.remove(card)
                hand.append(card)
        for hand in hands:
            missing = 27 - len(hand)
            hand.extend(available[:missing])
            del available[:missing]
        deck[:] = [card for hand in hands for card in hand]


def test_single_tribute_includes_a_first_and_last_finisher_from_the_same_team():
    first, second, partner, fourth = SEATS
    gs = _single_down()
    engine.apply(gs, first, "next", {"rng": _NextDeal(["BJ", "BJ", "SJ 3H", ""])})
    view = engine.view_for(gs, first)
    assert (view["deal"], view["phase"], view["current_id"]) == (2, "returning", None)
    assert (view["level"], view["level_team"]) == ("3", 0)
    pair, = view["exchange"]["pairs"]
    assert (pair["from_id"], pair["to_id"]) == (partner, first)
    assert pair["tribute"]["rank"] == "SJ"
    assert engine.view_for(gs, second)["return_choices"] == []
    assert engine.view_for(gs, fourth)["return_choices"] == []
    card_id = view["return_choices"][0]
    assert not any(card["id"] == card_id for card in engine.view_for(gs, partner)["hand"])
    engine.apply(gs, first, "return", {"card": card_id})
    view = engine.view_for(gs, partner)
    assert (view["phase"], view["current_id"], view["lead_reason"]) == ("playing", partner, "tribute")
    assert view["exchange"]["pairs"][0]["returned"]["id"] == card_id
    assert card_id in _ids(gs, partner)


def test_double_tribute_ties_send_to_upper_seat_and_returns_commit_together():
    first, second, partner, fourth = SEATS
    gs = _double_down()
    engine.apply(gs, first, "next", {"rng": _NextDeal(["BJ BJ", "SJ", "", "SJ"])})
    pairs = engine.view_for(gs, fourth)["exchange"]["pairs"]
    assert [(pair["from_id"], pair["to_id"]) for pair in pairs] == [(second, first), (fourth, partner)]
    assert [pair["tribute"]["rank"] for pair in pairs] == ["SJ", "SJ"]
    first_hand = _ids(gs, first)
    second_hand = _ids(gs, second)
    card = engine.view_for(gs, first)["return_choices"][0]
    engine.apply(gs, first, "return", {"card": card})

    assert _ids(gs, first) == first_hand
    assert _ids(gs, second) == second_hand
    for uid in SEATS:
        view = engine.view_for(gs, uid)
        assert view["phase"] == "returning"
        assert view["exchange"]["pairs"][0]["submitted"] is True
        assert view["exchange"]["pairs"][0]["returned"] is None
    assert engine.view_for(gs, first)["return_choices"] == []
    with pytest.raises(ValueError):
        engine.apply(gs, first, "return", {"card": card})

    partner_choice = engine.view_for(gs, partner)["return_choices"][0]
    engine.apply(gs, partner, "return", {"card": partner_choice})
    view = engine.view_for(gs, second)
    assert (view["phase"], view["current_id"], view["lead_reason"]) == ("playing", second, "tribute")
    assert [pair["returned"]["id"] for pair in view["exchange"]["pairs"]] == [card, partner_choice]
    assert card in _ids(gs, second) and partner_choice in _ids(gs, fourth)
    assert len({card_id for uid in SEATS for card_id in _ids(gs, uid)}) == 108


def test_double_tribute_is_resisted_when_the_two_givers_share_two_big_jokers():
    first, *_ = SEATS
    gs = _double_down()
    engine.apply(gs, first, "next", {"rng": _NextDeal(["", "BJ", "", "BJ"])})
    view = engine.view_for(gs, first)
    assert view["exchange"] == {"antitribute": True, "lead_id": first, "pairs": []}
    assert (view["phase"], view["current_id"], view["lead_reason"]) == ("playing", first, "antitribute")


def test_larger_double_tribute_goes_to_the_head_even_from_the_other_side():
    first, second, partner, fourth = SEATS
    gs = _double_down()
    engine.apply(gs, first, "next", {"rng": _NextDeal(["BJ", "SJ SJ", "", "BJ"])})
    exchange = engine.view_for(gs, first)["exchange"]
    assert exchange["lead_id"] == fourth
    assert [(p["from_id"], p["to_id"], p["tribute"]["rank"]) for p in exchange["pairs"]] == [
        (fourth, first, "BJ"), (second, partner, "SJ"),
    ]


def test_a_team_at_ace_must_win_a_deal_played_at_its_own_level():
    first, second, partner, _ = SEATS
    gs = _game("3C", "8D", "4H", "9S", level="8", level_team=1)
    gs["levels"][0] = "A"
    _play(gs, first)
    _pass(gs, second)
    _play(gs, partner)
    assert engine.view_for(gs, first)["phase"] == "between"
    engine.apply(gs, first, "next", {"rng": Random(28)})
    view = engine.view_for(gs, first)
    assert (view["deal"], view["level"], view["level_team"]) == (2, "A", 0)


def test_return_choices_fall_back_to_the_weakest_card_without_a_normal_option():
    first, *_ = SEATS
    gs = _single_down()
    high_cards = [f"{rank}{suit}" for rank in ("J", "Q", "K", "A", "3", "J", "Q") for suit in "SHDC"]
    engine.apply(gs, first, "next", {"rng": _NextDeal([" ".join(high_cards[:27]), "BJ BJ", "SJ SJ", ""])})
    view = engine.view_for(gs, first)
    assert {c["rank"] for c in view["hand"] if c["id"] in view["return_choices"]} == {"J"}
    before = deepcopy(gs)
    invalid = next(c["id"] for c in view["hand"] if c["rank"] == "3")
    with pytest.raises(ValueError):
        engine.apply(gs, first, "return", {"card": invalid})
    assert gs == before


def test_departure_finishes_the_match_for_the_other_team_even_between_deals():
    gs = _double_down()
    revision = engine.view_for(gs, SEATS[0])["revision"]
    engine.depart(gs, SEATS[1], "disconnect")
    result = engine.snapshot(gs)
    assert (result["winner"], result["reason"], result["deal"]) == (0, "disconnect", 1)
    assert result["levels"] == ["5", "2"]
    view = engine.view_for(gs, SEATS[0])
    assert (view["phase"], view["current_id"], view["revision"]) == ("finished", None, revision + 1)
    with pytest.raises(ValueError):
        engine.apply(gs, SEATS[0], "next", {})
