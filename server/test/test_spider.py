"""蜘蛛纸牌：确定性规则、撤销原子性、隐藏信息与个人存档生命周期。"""
from collections import Counter
from copy import deepcopy
import random

import pytest

from app.game import spider
from app import persistence, room_manager as rm
from app.database import _build_snapshot, _build_results_record


def position(columns, *, stock=None, face_up=None, completed=0):
    game = spider._shuffled_game(random.Random(0))
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


def replay_win(game, path):
    assert path is not None
    assert sum(move is None for move in path) == 5
    for move in path:
        assert spider.apply_action(game, "move" if move else "deal", **(move or {})) is None
    assert game["finished"] and game["completed"] == game.get("total_runs", 8)
    assert game["stock"] == [] and all(not c for c in game["columns"])


@pytest.mark.parametrize("seed", range(32))
def test_new_deals_have_a_replayable_winning_path(seed):
    game = spider.create_game(seed)
    before = deepcopy(game)
    path = spider._winning_path(game)
    assert game == before  # 验证过程不能提前走子、翻牌或污染撤销历史。
    assert game["history"] == [] and game["revision"] == game["move_count"] == 0
    replay_win(game, path)


def test_search_limit_and_rejected_deal_do_not_change_input():
    game = spider._shuffled_game(random.Random(0))
    before = deepcopy(game)
    assert spider._winning_path(game, node_limit=0) is None
    assert spider._winning_path(game) is None
    assert game == before
    replacement = spider.create_game(0)
    assert replacement != before
    replay_win(replacement, spider._winning_path(replacement))


@pytest.mark.parametrize("players,total_runs", [(1, 8), (2, 12), (3, 16), (4, 16)])
def test_bounded_generation_falls_back_to_varied_winnable_deals(monkeypatch, players, total_runs):
    base = spider._shuffled_game(random.Random(spider._FALLBACK_SEEDS[total_runs]), total_runs)
    solution = spider._winning_path(base)
    assert solution is not None
    checked = []
    monkeypatch.setattr(spider, "_winning_path", lambda game: checked.append(game))
    layouts = set()
    for seed in range(8):
        checked.clear()
        game = spider.create_game(seed, num_players=players)
        assert len(checked) == spider._DEAL_ATTEMPTS
        count, tall = total_runs * 5 // 4, total_runs // 2
        assert [len(c) for c in game["columns"]] == [6] * tall + [5] * (count - tall)
        order = [base["columns"].index(c) for c in game["columns"]]
        assert sorted(order) == list(range(count))
        assert game["stock"] == [base["stock"][row + i]
                                 for row in range(0, count * 5, count) for i in order]
        layouts.add(tuple(map(tuple, game["columns"])))
        remap = {old: new for new, old in enumerate(order)}
        path = [None if move is None else {"source": remap[move["source"]],
                "index": move["index"], "target": remap[move["target"]]} for move in solution]
        replay_win(game, path)
    assert len(layouts) == 8


@pytest.mark.parametrize("players,total_runs,columns", [(1, 8, 10), (2, 12, 15), (3, 16, 20), (4, 16, 20)])
def test_cooperative_deal_size_and_five_stock_rounds(players, total_runs, columns):
    game = spider.create_game(5, num_players=players)
    assert len(game["columns"]) == columns
    assert game["total_runs"] == total_runs
    assert len(game["stock"]) == columns * 5
    assert Counter(sum(game["columns"], []) + game["stock"]) == Counter({rank: total_runs for rank in range(1, 14)})
    before = deepcopy(game)
    assert spider.apply_action(game, "deal") is None
    assert len(game["stock"]) == columns * 4
    assert spider.apply_action(game, "undo") is None
    assert game["columns"] == before["columns"] and game["stock"] == before["stock"]
    view = spider.game_to_sync(game)
    assert view["total_runs"] == total_runs
    assert sum(rank is None for c in view["columns"] for rank in c) == columns * 22 // 5
    assert not any(key in view for key in ("stock", "history", "face_up"))


@pytest.mark.parametrize("players", [0, 5, True, "2", None])
def test_invalid_player_count_is_rejected(players):
    with pytest.raises(ValueError):
        spider.create_game(num_players=players)


def test_legacy_game_without_goal_still_wins_and_syncs():
    game = position([list(range(13, 1, -1)), [1]] + [[]] * 8, completed=7)
    game.pop("total_runs")
    assert spider.game_to_sync(game)["total_runs"] == 8
    assert spider.apply_action(game, "move", 1, 0, 0) is None
    assert game["finished"]


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


def test_normal_room_creation_membership_and_stale_revision(solo_room):
    room = solo_room
    assert room["max_players"] == 4 and room in rm.get_all_rooms()
    again = rm.create_room({"id": 41, "username": "solo", "nickname": None}, None, None, "spider")
    assert again is not room
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
    timers = []
    monkeypatch.setattr(rm, "_start_dc_timer", lambda *args: timers.append(args))
    rm.handle_player_disconnect(room["code"], 41, None)
    assert timers == [(room["code"], 41, None)]
    room["max_players"] = 1  # 老存档转为普通房间，不重发牌。
    encoded = persistence.dumps_state(room)
    rm._rooms.clear()
    monkeypatch.setattr(persistence, "load_all_rooms", lambda: [persistence.loads_state(encoded)])
    assert rm.restore_rooms() == 1
    restored = rm.get_room(room["code"])
    assert restored["game_state"] == room["game_state"]
    assert restored["max_players"] == 4
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
    assert rm.spider_action(room["code"], 41, {"action": "new", "revision": 1}).get("error")
    rm.start_game(room["code"], 41)
    assert room["status"] == "playing" and room["game_state"]["revision"] == 2
    rm.spider_action(room["code"], 41, {"action": "end", "revision": 2})
    assert len(ended) == 2
    assert _build_results_record(ended[-1])[0]["result"] == "end"


def test_cooperative_members_ready_move_leave_and_host_transfer(solo_room):
    room = rm.create_room({"id": 51, "username": "host", "nickname": None}, None, None, "spider")
    rm.join_room(room["code"], {"id": 52, "username": "partner", "nickname": None}, None)
    assert rm.start_game(room["code"], 52) is None
    assert rm.start_game(room["code"], 51) is None
    room["players"][1]["ready"] = True
    assert rm.start_game(room["code"], 51)["total_runs"] == 12
    revision = room["game_state"]["revision"]
    assert not rm.spider_action(room["code"], 52, {"action": "deal", "revision": revision}).get("error")
    before = deepcopy(room["game_state"])
    assert rm.spider_action(room["code"], 51, {"action": "deal", "revision": revision}).get("error")
    assert rm.spider_action(room["code"], 52, {"action": "end", "revision": revision + 1}).get("error")
    assert room["game_state"] == before
    assert rm.leave_room(room["code"], 51)[0] == "removed"
    assert room["host_id"] == 52 and room["status"] == "playing"
    assert not rm.user_in_game(room, 51) and rm.user_in_game(room, 52)
    assert room["game_state"]["columns"] == before["columns"]
    assert rm.spider_action(room["code"], 51, {"action": "undo", "revision": revision + 2}).get("error")
    assert rm.leave_room(room["code"], 52)[0] == "game_over"
    assert room["status"] == "waiting" and room["game_state"]["finished"]


def test_disconnect_timeout_and_normal_room_cleanup(solo_room):
    room = solo_room
    rm._on_dc_timeout(room["code"], 41)
    assert not rm.user_in_game(room, 41) and room["status"] == "waiting"
    room["empty_since"] = room["last_activity"] = 0
    assert room["code"] in rm.sweep_idle_rooms()
