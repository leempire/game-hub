import random

import pytest


def new_game(count=4):
    from app.game import undercover

    return undercover.create_game(list(range(1, count + 1)), ("雨伞", "雨衣"), "match", random.Random(2))


def end_clues(game):
    from app.game import undercover

    while game["phase"] == "clue":
        undercover.expire(game)


@pytest.mark.parametrize(("count", "faction_sizes"), [(4, [1, 3]), (6, [1, 5]), (7, [2, 5]), (8, [2, 6])])
def test_opening_view_projects_only_your_word(count, faction_sizes):
    from app.game import undercover

    game = new_game(count)
    views = [undercover.view_for(game, uid) for uid in range(1, count + 1)]
    words = [view["your_word"] for view in views]
    assert sorted(words.count(word) for word in {"雨衣", "雨伞"}) == faction_sizes
    assert sum(view["can_clue"] for view in views) == 1
    assert views[0]["remaining_ms"] == 45000
    assert all("roles" not in view and "word_pair" not in view for view in views)
    assert undercover.view_for(game, 99)["your_word"] is None
    assert undercover.snapshot(game) is None


@pytest.mark.parametrize("count", [3, 9])
def test_rejects_room_sizes_outside_game_limits(count):
    with pytest.raises(ValueError):
        new_game(count)


def test_own_word_and_stale_actions_are_rejected_and_skip_is_public():
    from app.game import undercover

    game = new_game()
    first = undercover.view_for(game, 1)
    speaker = first["speaker_id"]
    phase_id = first["phase_id"]
    actual = undercover.view_for(game, speaker)["your_word"]
    other = "雨衣" if actual == "雨伞" else "雨伞"
    assert undercover.apply_clue(game, speaker, actual + "很实用", phase_id)
    assert undercover.apply_clue(game, speaker, "字" * 61, phase_id)
    assert undercover.apply_clue(game, speaker, "能挡雨", phase_id - 1)
    assert undercover.apply_clue(game, speaker, "两行\n线索", phase_id)
    assert undercover.apply_clue(game, speaker, "非法代理码\ud800", phase_id)
    assert undercover.apply_clue(game, speaker, f"不像{other}那样", phase_id) is None
    after = undercover.view_for(game, speaker)
    assert after["clues"] == [{"round": 1, "user_id": speaker, "text": f"不像{other}那样"}]
    assert after["speaker_id"] != speaker and not after["can_clue"]
    assert undercover.apply_clue(game, speaker, "又一句", phase_id)
    undercover.expire(game)
    assert undercover.view_for(game, speaker)["clues"][-1] == {
        "round": 1, "user_id": after["speaker_id"], "text": None,
    }


def test_lock_vote_until_result_then_runoff_once():
    from app.game import undercover

    game = new_game()
    end_clues(game)
    stage = game["phase_id"]
    assert game["remaining_ms"] == 30000
    assert undercover.apply_vote(game, 1, True, stage)
    assert undercover.apply_vote(game, 1, 1, stage)
    assert undercover.apply_vote(game, 1, 2, stage) is None
    revision = undercover.view_for(game, 1)["revision"]
    assert undercover.apply_vote(game, 1, 2, stage) is None
    assert undercover.view_for(game, 1)["revision"] == revision
    assert undercover.apply_vote(game, 1, 3, stage)
    peer = undercover.view_for(game, 2)
    assert peer["votes_cast"] == 1 and peer["your_vote"] is None and peer["tallies"] == []
    for voter, target in ((2, 1), (3, 1), (4, 2)):
        assert undercover.apply_vote(game, voter, target, stage) is None
    assert game["phase"] == "runoff" and game["phase_id"] != stage
    assert undercover.view_for(game, 1)["candidate_ids"] == [2]
    assert undercover.view_for(game, 3)["candidate_ids"] == [1, 2]
    assert undercover.apply_vote(game, 1, 2, stage)
    assert undercover.apply_vote(game, 1, 3, game["phase_id"])
    for voter, target in ((1, 2), (2, 1), (3, 1), (4, 2)):
        assert undercover.apply_vote(game, voter, target, game["phase_id"]) is None
    view = undercover.view_for(game, 1)
    assert view["round"] == 2 and view["phase"] == "clue" and view["eliminations"] == []
    assert view["speaker_id"] != view["clues"][0]["user_id"]
    assert {vote["user_id"]: vote["count"] for vote in view["tallies"][1]["votes"]} == {1: 2, 2: 2}


def test_departure_invalidates_votes_and_preserves_roster_and_private_state():
    from app.game import undercover

    game = new_game(8)
    end_clues(game)
    stage = game["phase_id"]
    assert undercover.apply_vote(game, 1, 2, stage) is None
    assert undercover.depart(game, 2, "disconnect")
    view = undercover.view_for(game, 1)
    assert view["phase_id"] == stage and view["your_vote"] is None and view["votes_cast"] == 0
    assert view["eliminations"][0]["reason"] == "disconnect"
    assert view["player_ids"] == list(range(1, 9)) and 2 not in view["alive"]
    assert undercover.apply_vote(game, 1, 3, stage) is None
    assert undercover.apply_vote(game, 2, 3, stage)


def test_departing_speakers_keep_their_published_clues_visible():
    from app.game import undercover

    game = new_game()
    speaker = undercover.view_for(game, 1)["speaker_id"]
    assert undercover.apply_clue(game, speaker, "常见于阴雨天", game["phase_id"]) is None
    assert undercover.depart(game, speaker, "leave", settle=False)
    view = undercover.view_for(game, 1)
    assert speaker not in view["alive"] and speaker in view["turn_order"]
    assert view["clues"][0]["text"] == "常见于阴雨天"


def test_eliminating_last_undercover_reveals_words_and_discards_live_text():
    from app.game import undercover

    game = new_game()
    end_clues(game)
    word_views = {uid: undercover.view_for(game, uid)["your_word"] for uid in range(1, 5)}
    target = next(uid for uid, word in word_views.items() if list(word_views.values()).count(word) == 1)
    others = [uid for uid in word_views if uid != target]
    stage = game["phase_id"]
    for voter, choice in [(target, others[0]), *((uid, target) for uid in others)]:
        assert undercover.apply_vote(game, voter, choice, stage) is None
    recap = undercover.snapshot(game)
    assert recap["winner"] == "civilian" and recap["forfeited"] == []
    assert recap["word_pair"]["undercover"] == word_views[target]
    assert recap["eliminations"] == [{"round": 1, "user_id": target, "role": "undercover", "reason": "vote"}]
    assert recap["tallies"] == [{"round": 1, "runoff": False, "votes": [
        {"user_id": uid, "count": 3 if uid == target else int(uid == others[0])}
        for uid in range(1, 5)
    ]}]
    assert "clues" not in recap and "votes" not in recap
    assert undercover.view_for(game, 1)["clues"] == []


def test_counts_remaining_players_for_undercover_win_and_all_abandonment():
    from app.game import undercover

    game = new_game()
    revealed = {uid: undercover.view_for(game, uid)["your_word"] for uid in range(1, 5)}
    civilians = [uid for uid in revealed if list(revealed.values()).count(revealed[uid]) == 3]
    for uid in civilians[:2]:
        assert undercover.depart(game, uid, "leave")
    assert undercover.snapshot(game)["winner"] == "undercover"
    assert undercover.snapshot(game)["forfeited"] == civilians[:2]

    game = new_game()
    for uid in range(1, 5):
        assert undercover.depart(game, uid, "disconnect", settle=False)
    assert undercover.resolve_departures(game)
    assert undercover.snapshot(game)["winner"] == "aborted"


def test_uncontested_timeout_caps_the_match():
    from app.game import undercover

    game = new_game()
    for round_number in range(1, 9):
        assert game["round"] == round_number
        end_clues(game)
        undercover.expire(game)
    assert undercover.snapshot(game)["winner"] == "draw"


def test_word_pair_is_not_repeated_with_reversed_roles():
    from app.game import undercover

    pair = undercover.choose_word_pair(random.Random(7))
    assert len(pair) == 2 and pair[0] != pair[1]
    assert set(undercover.choose_word_pair(random.Random(7), pair[::-1])) != set(pair)
