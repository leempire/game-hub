from random import Random

import pytest

from app import persistence, room_settings
from app.game import doudizhu as engine


SEATS = [17, 29, 41]


def _game(rules=None):
    return engine.create_game(SEATS, rules, "fixed-match", Random(0))


def _playing(rules=None):
    gs = _game(rules)
    landlord = engine.view_for(gs, SEATS[0])["current_id"]
    engine.bid(gs, landlord, 3)
    return gs, landlord


def _all_pass(gs):
    for _ in SEATS:
        current = engine.view_for(gs, SEATS[0])["current_id"]
        engine.bid(gs, current, 0)


def _cards(ranks):
    cards = []
    counts = {}
    for rank in ranks.split():
        if rank in ("SJ", "BJ"):
            cards.append(rank)
        else:
            cards.append(rank + "CDHS"[counts.get(rank, 0)])
            counts[rank] = counts.get(rank, 0) + 1
    return cards


def test_room_settings_follow_complete_preset_configuration():
    room = {"game": "doudizhu"}

    def settings():
        return {item["key"]: item for item in room_settings.items_for("doudizhu", room)}

    def set_value(key, value):
        spec = room_settings.spec_for("doudizhu", key)
        room_settings.apply(room, spec, room_settings.coerce(spec, value))

    assert set(settings()) == {"preset", "all_pass", "airplane_pair_as_singles", "four_pair_as_singles"}
    assert [key for key, rule in settings().items() if rule.get("in_summary")] == ["preset"]
    assert settings()["preset"]["value_label"] == "经典"
    set_value("preset", "strict")
    assert settings()["preset"]["value_label"] == "严格带牌"
    assert settings()["airplane_pair_as_singles"]["value"] is False
    assert settings()["four_pair_as_singles"]["value"] is False
    assert settings()["all_pass"]["value"] == "redeal"
    set_value("all_pass", "first_bidder")
    assert settings()["preset"]["value_label"] == "自定义"
    set_value("all_pass", "redeal")
    assert settings()["preset"]["value"] == "strict"
    set_value("airplane_pair_as_singles", True)
    set_value("four_pair_as_singles", True)
    assert settings()["preset"]["value"] == "classic"
    with pytest.raises(ValueError):
        set_value("preset", "custom")
    with pytest.raises(ValueError):
        set_value("four_pair_as_singles", 1)


def test_deal_privacy_and_bidding_order():
    gs = _game()
    views = [engine.view_for(gs, uid) for uid in SEATS]
    all_dealt = {card for view in views for card in view["hand"]}
    assert len(all_dealt) == 51
    assert all(view["remaining"] == [17, 17, 17] and view["bottom"] == [] for view in views)
    assert engine.view_for(gs, 99)["hand"] == []
    assert views[1]["current_id"] == 29
    assert views[1]["available_bids"] == [0, 1, 2, 3]
    assert views[0]["available_bids"] == []
    engine.bid(gs, 29, 2)
    assert engine.view_for(gs, 41)["available_bids"] == [0, 3]
    assert engine.view_for(gs, 41)["actions"] == [None, {"kind": "bid", "bid": 2}, None]
    before = persistence.dumps_state(gs)
    for uid, invalid in [(29, 3), (41, 2), (41, True), (41, 1.0), (41, 4)]:
        with pytest.raises(ValueError):
            engine.bid(gs, uid, invalid)
        assert persistence.dumps_state(gs) == before
    engine.bid(gs, 41, 0)
    engine.bid(gs, 17, 0)
    view = engine.view_for(gs, 29)
    assert view["phase"] == "playing"
    assert view["landlord_id"] == view["current_id"] == 29
    assert view["revision"] == 3
    assert view["bid"] == 2
    assert view["remaining"] == [17, 20, 17]
    assert len(view["bottom"]) == 3
    assert set(view["bottom"]).isdisjoint(all_dealt)
    assert set(view["bottom"]) < set(view["hand"])
    assert engine.view_for(gs, 41)["bottom"] == view["bottom"]


def test_view_requires_an_integer_seat_id_for_private_hand():
    gs = engine.create_game([0, 1, 2], None, "fixed-match", Random(0))
    for not_seated in (True, 1.0, "1", None):
        view = engine.view_for(gs, not_seated)
        assert view["hand"] == []
        assert view["available_bids"] == []
    assert len(engine.view_for(gs, 1)["hand"]) == 17


@pytest.mark.parametrize(("seats", "rules"), [
    ([17, 29, 29], None), ([17, True, 29], None), (None, None),
    (SEATS, True), (SEATS, {"all_pass": "unknown"}),
    (SEATS, {"airplane_pair_as_singles": 1}),
])
def test_create_rejects_invalid_seats_and_rules(seats, rules):
    rng = Random(0)
    before = rng.getstate()
    with pytest.raises(ValueError):
        engine.create_game(seats, rules, "fixed-match", rng)
    assert rng.getstate() == before


def test_all_pass_redeals_in_same_match_after_restoring_rng():
    gs = _game()
    before = engine.view_for(gs, 29)["hand"]
    restored = persistence.loads_state(persistence.dumps_state(gs))
    _all_pass(gs)
    _all_pass(restored)
    view = engine.view_for(gs, 29)
    assert view == engine.view_for(restored, 29)
    assert view["revision"] == 3
    assert view["match_id"] == "fixed-match"
    assert view["current_id"] == 41
    assert view["phase"] == "bidding"
    assert view["hand"] != before
    assert view["actions"] == [None, None, None]
    assert view["bottom"] == []


def test_all_pass_can_assign_first_bidder_at_one_point():
    rules = {"all_pass": "first_bidder"}
    gs = _game(rules)
    rules["all_pass"] = "redeal"
    _all_pass(gs)
    view = engine.view_for(gs, 29)
    assert view["phase"] == "playing"
    assert view["bid"] == 1
    assert view["landlord_id"] == view["current_id"] == 29
    assert view["rules"]["all_pass"] == "first_bidder"


@pytest.mark.parametrize(("ranks", "kind"), [
    ("2", "single"), ("7 7", "pair"), ("8 8 8", "triple"),
    ("8 8 8 BJ", "triple_single"), ("8 8 8 4 4", "triple_pair"),
    ("10 J Q K A", "straight"), ("Q Q K K A A", "pair_straight"),
    ("K K K A A A", "airplane"), ("3 3 3 4 4 4 7 7", "airplane_single"),
    ("3 3 3 4 4 4 7 7 2 2", "airplane_pair"),
    ("6 6 6 6 7 7", "four_two_singles"), ("6 6 6 6 3 3 2 2", "four_two_pairs"),
    ("Q Q Q Q", "bomb"), ("SJ BJ", "rocket"),
    ("3 3 3 4 4 4 5 5 5 6 6 6", "airplane"),
])
def test_accepts_legal_combinations(ranks, kind):
    gs, landlord = _playing()
    cards = _cards(ranks)
    gs["hands"][landlord] = cards + ["2S"]
    engine.play(gs, landlord, list(reversed(cards)))
    view = engine.view_for(gs, landlord)
    assert view["last_play"]["kind"] == kind
    assert view["last_play"]["label"]
    assert view["hand"] == ["2S"]
    assert view["multiplier"] == (2 if kind in ("bomb", "rocket") else 1)


@pytest.mark.parametrize("ranks", [
    "J Q K A 2", "K K A A 2 2", "A A A 2 2 2",
    "3 3 3 4 4 4 3 8", "3 3 3 4 4 4 SJ BJ",
    "3 3 3 4 4 4 5 5 5 7 7 7",
    "3 3 3 4 4 4 7 7 7 7", "6 6 6 6 7 7 7 7", "6 6 6 6 SJ BJ",
])
def test_rejects_illegal_sequences_and_wings(ranks):
    gs, landlord = _playing()
    cards = _cards(ranks)
    gs["hands"][landlord] = cards
    before = persistence.dumps_state(gs)
    with pytest.raises(ValueError):
        engine.play(gs, landlord, cards)
    assert persistence.dumps_state(gs) == before


@pytest.mark.parametrize(("key", "lead", "reply"), [
    ("airplane_pair_as_singles", "3 3 3 4 4 4 8 9", "5 5 5 6 6 6 7 7"),
    ("four_pair_as_singles", "3 3 3 3 8 9", "6 6 6 6 7 7"),
])
def test_strict_settings_filter_same_rank_wings_in_play_and_hint(key, lead, reply):
    first, second = _cards(lead), _cards(reply)
    for enabled in (True, False):
        gs, landlord = _playing({key: enabled})
        farmer = SEATS[(SEATS.index(landlord) + 1) % 3]
        gs["hands"][landlord] = first + ["2S"]
        gs["hands"][farmer] = second.copy()
        if key == "four_pair_as_singles":
            gs["hands"][farmer].append("10S")
        engine.play(gs, landlord, first)
        before = persistence.dumps_state(gs)
        suggestion = engine.hint(gs, farmer)
        assert persistence.dumps_state(gs) == before
        if enabled:
            assert set(suggestion) == set(second)
            engine.play(gs, farmer, second)
        else:
            with pytest.raises(ValueError):
                engine.play(gs, farmer, _cards(reply))
            assert persistence.dumps_state(gs) == before
            if key == "four_pair_as_singles":
                assert "10S" in suggestion
                engine.play(gs, farmer, suggestion)
            else:
                assert suggestion == []


def test_following_checks_shape_and_rank_then_two_passes_restore_lead():
    gs, landlord = _playing()
    farmer = SEATS[(SEATS.index(landlord) + 1) % 3]
    other = next(uid for uid in SEATS if uid not in (landlord, farmer))
    gs["hands"][landlord] = _cards("3 4 5 6 7 8")
    gs["hands"][farmer] = _cards("4 5 6 7 8 9")
    with pytest.raises(ValueError):
        engine.pass_turn(gs, landlord)
    assert engine.view_for(gs, landlord)["revision"] == 1
    opening = gs["hands"][landlord][:5]
    engine.play(gs, landlord, opening)
    before = persistence.dumps_state(gs)
    for cards in (gs["hands"][farmer], ["9C"], ["9C", "9C"], ["BJ"], [{}], None, "9C"):
        with pytest.raises(ValueError):
            engine.play(gs, farmer, cards)
        assert persistence.dumps_state(gs) == before
    assert engine.view_for(gs, farmer)["can_pass"] is True
    assert set(engine.hint(gs, farmer)) == {"4C", "5C", "6C", "7C", "8C"}
    engine.pass_turn(gs, farmer)
    assert engine.view_for(gs, other)["last_play"]["player_id"] == landlord
    engine.pass_turn(gs, other)
    view = engine.view_for(gs, landlord)
    assert view["current_id"] == landlord
    assert view["last_play"] is None
    assert view["can_pass"] is False
    assert engine.hint(gs, landlord) == ["8C"]


def test_bomb_beats_normal_play_and_higher_bomb_then_rocket_win():
    gs, landlord = _playing()
    farmer = SEATS[(SEATS.index(landlord) + 1) % 3]
    other = next(uid for uid in SEATS if uid not in (landlord, farmer))
    gs["hands"][landlord] = _cards("8 8 4")
    gs["hands"][farmer] = _cards("6 6 6 6 SJ BJ")
    gs["hands"][other] = _cards("7 7 7 7 5")
    engine.play(gs, landlord, _cards("8 8"))
    assert set(engine.hint(gs, farmer)) == set(_cards("6 6 6 6"))
    engine.play(gs, farmer, _cards("6 6 6 6"))
    assert set(engine.hint(gs, other)) == set(_cards("7 7 7 7"))
    engine.play(gs, other, _cards("7 7 7 7"))
    engine.pass_turn(gs, landlord)
    assert set(engine.hint(gs, farmer)) == {"BJ", "SJ"}
    engine.play(gs, farmer, ["BJ", "SJ"])
    end = engine.snapshot(gs)
    assert (end["winner"], end["spring"], end["bombs"], end["rockets"]) == ("farmers", "reverse", 2, 1)
    assert end["multiplier"] == 16
    assert end["scores"] == {landlord: -96, farmer: 48, other: 48}


def test_landlord_spring_scores_two_farmers_and_terminal_projection_is_public():
    gs = _game()
    engine.bid(gs, 29, 2)
    engine.bid(gs, 41, 0)
    engine.bid(gs, 17, 0)
    gs["hands"][29] = _cards("3 3 3 3")
    engine.play(gs, 29, _cards("3 3 3 3"))
    end = engine.snapshot(gs)
    assert end == {
        "match_id": "fixed-match", "revision": 4, "seat_uids": SEATS,
        "landlord_id": 29, "winner": "landlord", "reason": "empty_hand",
        "bid": 2, "multiplier": 4, "bombs": 1, "rockets": 0, "spring": "spring",
        "scores": {17: -8, 29: 16, 41: -8},
        "last_play": {"player_id": 29, "cards": ["3S", "3H", "3D", "3C"], "kind": "bomb", "label": "炸弹"},
        "rules": {"all_pass": "redeal", "airplane_pair_as_singles": True, "four_pair_as_singles": True},
        "remaining": [17, 0, 17],
    }
    assert sum(end["scores"].values()) == 0
    end["rules"]["all_pass"] = "first_bidder"
    assert engine.snapshot(gs)["rules"]["all_pass"] == "redeal"


@pytest.mark.parametrize("reason", ["surrender", "leave", "disconnect"])
def test_departure_cancels_bidding_or_awards_opposing_team_without_scores(reason):
    gs = _game()
    engine.depart(gs, 17, reason)
    end = engine.snapshot(gs)
    assert (end["reason"], end["winner"], end["landlord_id"], end["scores"]) == ("canceled", None, None, None)
    assert gs["finished"] and gs["phase"] == "finished"
    assert end["revision"] == 1
    before = persistence.dumps_state(gs)
    with pytest.raises(ValueError):
        engine.depart(gs, 17, reason)
    assert persistence.dumps_state(gs) == before

    for leaver, winner in ((29, "farmers"), (17, "landlord")):
        gs, _ = _playing()
        engine.depart(gs, leaver, reason)
        end = engine.snapshot(gs)
        assert end["reason"] == reason
        assert end["winner"] == winner
        assert end["scores"] is end["spring"] is None
        assert end["revision"] == 2
