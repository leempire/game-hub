"""蜘蛛纸牌：确定性规则、撤销原子性、隐藏信息与个人存档生命周期。"""
from collections import Counter
from copy import deepcopy
import random

import pytest

from app.game import spider
from app import persistence, room_manager as rm
from app.database import _build_snapshot, _build_results_record


def position(columns, *, stock=None, face_up=None, completed=0):
    game = spider.create_game(0)
    game.update(columns=[list(c) for c in columns], face_up=face_up or [0] * 10,
                stock=stock or [], completed=completed)
    return game


def test_initial_deal_and_hidden_information():
    game = spider.create_game(12)
    assert [len(c) for c in game["columns"]] == [6] * 4 + [5] * 6
    cards = sum(game["columns"], []) + game["stock"]
    assert Counter(cards) == Counter({rank: 8 for rank in range(1, 14)})
    view = spider.game_to_sync(game)
    assert sum(rank is None for c in view["columns"] for rank in c) == 44
    assert view["stock_count"] == 50
    assert "stock" not in view and "history" not in view and "face_up" not in view
    assert game == spider.create_game(12)


def test_move_sequence_reveal_and_undo():
    game = position([[11, 8, 7], [9]] + [[13]] * 8,
                    face_up=[1] + [0] * 9)
    before = deepcopy(game)
    assert spider.apply_action(game, "move", 0, 1, 1) is None
    assert game["columns"][0] == [11]
    assert game["face_up"][0] == 0
    assert game["columns"][1] == [9, 8, 7]
    assert spider.apply_action(game, "undo") is None
    assert game["columns"] == before["columns"]
    assert game["face_up"] == before["face_up"]
    assert game["move_count"] == 0 and game["revision"] == 2


@pytest.mark.parametrize("source,index,target", [(-1, 0, 1), (0, -1, 1), (0, 100, 1),
    (0, 1, 10), (0, 1, 0), (True, 0, 1), (0, "1", 1), (0, 0, 1), (0, 1, 2)])
def test_invalid_move_is_atomic(source, index, target):
    game = position([[11, 8, 7], [9], [5]] + [[13]] * 7,
                    face_up=[1] + [0] * 9)
    before = deepcopy(game)
    assert spider.apply_action(game, "move", source, index, target)
    assert game == before


def test_broken_run_cannot_move_and_empty_column_accepts_any_rank():
    game = position([[9, 7, 6], []] + [[13]] * 8)
    assert spider.apply_action(game, "move", 0, 0, 1)
    assert spider.apply_action(game, "move", 0, 1, 1) is None
    assert game["columns"][1] == [7, 6]


def test_deal_and_undo_preserve_order_and_reject_empty_column():
    game = position([[13]] * 10, stock=list(range(1, 11)))
    before = deepcopy(game)
    assert spider.apply_action(game, "deal") is None
    assert [c[-1] for c in game["columns"]] == list(range(1, 11))
    assert game["stock"] == []
    assert spider.apply_action(game, "undo") is None
    assert game["columns"] == before["columns"] and game["stock"] == before["stock"]
    game["columns"][0] = []
    before = deepcopy(game)
    assert spider.apply_action(game, "deal")
    assert game == before


def test_collect_run_reveals_hidden_card_and_undo_restores_everything():
    game = position([[5] + list(range(13, 1, -1)), [1]] + [[]] * 8,
                    face_up=[1] + [0] * 9)
    before = deepcopy(game)
    assert spider.apply_action(game, "move", 1, 0, 0) is None
    assert game["completed"] == 1 and game["columns"][0] == [5]
    assert game["face_up"][0] == 0
    assert spider.apply_action(game, "undo") is None
    assert game["completed"] == 0
    assert game["columns"] == before["columns"] and game["face_up"] == before["face_up"]


def test_eighth_sequence_wins_and_rejects_further_moves():
    game = position([list(range(13, 1, -1)), [1]] + [[]] * 8, completed=7)
    assert spider.apply_action(game, "move", 1, 0, 0) is None
    assert game["completed"] == 8 and game["finished"]
    assert game["reason"] == "completed"
    assert spider.game_to_sync(game)["legal_moves"] == []
    assert spider.apply_action(game, "undo")


def test_undo_window_and_noop_errors():
    game = position([[1], []] + [[13]] * 8)
    assert spider.apply_action(game, "undo")
    assert spider.apply_action(game, "deal")
    assert spider.apply_action(game, "unknown")
    for i in range(spider.UNDO_LIMIT + 5):
        source = i % 2
        assert spider.apply_action(game, "move", source, 0, 1 - source) is None
    assert len(game["history"]) == spider.UNDO_LIMIT


def test_random_actions_conserve_cards_and_roundtrip():
    rng = random.Random(42)
    for seed in range(8):
        game = spider.create_game(seed)
        for _ in range(80):
            moves = spider.legal_moves(game)
            if game["finished"]:
                break
            if game["history"] and rng.random() < .15:
                spider.apply_action(game, "undo")
            elif game["stock"] and all(game["columns"]) and rng.random() < .1:
                spider.apply_action(game, "deal")
            elif moves:
                spider.apply_action(game, "move", **rng.choice(moves))
            else:
                break
            cards = sum(game["columns"], []) + game["stock"] + list(range(1, 14)) * game["completed"]
            assert Counter(cards) == Counter({rank: 8 for rank in range(1, 14)})
            assert all(0 <= first <= max(0, len(c) - 1) for first, c in zip(game["face_up"], game["columns"]))
        assert persistence.loads_state(persistence.dumps_state(game)) == game


@pytest.fixture
def solo_room(monkeypatch):
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    monkeypatch.setattr(rm, "_game_end_callback", None)
    room = rm.create_room({"id": 41, "username": "solo", "nickname": None}, None, None, "spider")
    rm.start_game(room["code"], 41)
    return room


def test_personal_room_resume_privacy_and_stale_revision(solo_room):
    room = solo_room
    assert room["max_players"] == 1 and room not in rm.get_all_rooms()
    again = rm.create_room({"id": 41, "username": "solo"}, None, None, "spider")
    assert again is room
    with pytest.raises(ValueError):
        rm.join_room(room["code"], {"id": 42}, None)
    before = deepcopy(room["game_state"])
    assert rm.spider_action(room["code"], 42, {"action": "deal", "revision": 0}).get("error")
    assert rm.spider_action(room["code"], 41, {"action": "deal", "revision": -1}).get("error")
    assert room["game_state"] == before
    assert not rm.spider_action(room["code"], 41, {"action": "deal", "revision": 0}).get("error")
    assert rm.spider_action(room["code"], 41, {"action": "deal", "revision": 0}).get("error")


def test_save_survives_disconnect_sweep_and_restart(solo_room, monkeypatch):
    room = solo_room
    rm.handle_player_disconnect(room["code"], 41, None)
    assert (room["code"], 41) not in rm._dc_tasks
    room["empty_since"] = room["last_activity"] = 0
    assert room["code"] not in rm.sweep_idle_rooms()
    encoded = persistence.dumps_state(room)
    rm._rooms.clear()
    monkeypatch.setattr(persistence, "load_all_rooms", lambda: [persistence.loads_state(encoded)])
    assert rm.restore_rooms() == 1
    restored = rm.get_room(room["code"])
    assert restored["game_state"] == room["game_state"]
    assert restored["players"][0]["connected"] is False


def test_natural_end_and_new_game_record_results(solo_room, monkeypatch):
    room = solo_room
    ended = []
    monkeypatch.setattr(rm, "_game_end_callback", lambda r: ended.append(deepcopy(r)))
    room["game_state"] = position([list(range(13, 1, -1)), [1]] + [[]] * 8, completed=7)
    result = rm.spider_action(room["code"], 41, {"action": "move", "source": 1, "index": 0, "target": 0, "revision": 0})
    assert result["game"]["finished"] and room["status"] == "waiting"
    assert len(ended) == 1
    assert _build_results_record(room)[0]["result"] == "win"
    assert _build_snapshot(room)["completed"] == 8
    assert not rm.spider_action(room["code"], 41, {"action": "new", "revision": 1}).get("error")
    assert room["status"] == "playing" and room["game_state"]["revision"] == 2
    rm.spider_action(room["code"], 41, {"action": "new", "revision": 2})
    assert len(ended) == 2
    assert _build_results_record(ended[-1])[0]["result"] == "end"
