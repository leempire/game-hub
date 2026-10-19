"""热重启落盘（persistence）单测：编解码往返 + 存读删 + room_manager 恢复集成。"""

import random

import pytest

from app import database, persistence
from app import room_manager as rm


@pytest.fixture()
def tmp_db(tmp_path):
    """启用落盘到临时库，测试后完整还原全局状态（不污染其他用例）。"""
    old_path = database._db_path
    old_rooms = dict(rm._rooms)
    old_counter = rm._bot_id_counter
    database.init_db(str(tmp_path / "test.db"))
    persistence.init()
    rm._rooms.clear()
    yield
    persistence.disable()
    database._db_path = old_path
    rm._rooms.clear()
    rm._rooms.update(old_rooms)
    rm._bot_id_counter = old_counter


U1 = {"id": 1, "username": "u1", "nickname": "U1"}
U2 = {"id": 2, "username": "u2", "nickname": "U2"}


def test_roundtrip_int_keys():
    """int 键（玩家 id/座位号）必须原样恢复，不能被 JSON 转成 str。"""
    data = {"boards": {5: [[0, 2], [4, 0]], -3: [[0]]}, "scores": {5: 100, -3: 7}}
    out = persistence.loads_state(persistence.dumps_state(data))
    assert out == data
    assert all(isinstance(k, int) for k in out["boards"])


def test_roundtrip_set_and_rng():
    """set 与 random.Random 状态往返；恢复的 rng 必须延续原序列（2048 公平性）。"""
    rng = random.Random(42)
    data = {"votes": {1, 2, 3}, "rng": rng}
    out = persistence.loads_state(persistence.dumps_state(data))
    assert out["votes"] == {1, 2, 3}
    assert isinstance(out["rng"], random.Random)
    assert [out["rng"].random() for _ in range(5)] == [rng.random() for _ in range(5)]


def test_roundtrip_battleship_weapons_state():
    """海战棋 v1.10 的武器状态要能原样往返（走的是通用编码器，无需新增分支）。

    weapons / weapons_used / radar_results 都是 int 键 dict（玩家 uid）
    → 走 `__kv__`；first_shot_done 是普通 bool。热重启后若这里丢了，
    续射补偿与武器挂靠会静默失效。
    """
    data = {
        "weapons": {1: {"radar": 5, "salvo": 4, "strike": 3}, 2: {}},
        "weapons_used": {1: ["radar"], 2: []},
        "radar_results": {1: [{"x": 1, "y": 1, "has_ship": True}], 2: []},
        "first_shot_done": True,
    }
    out = persistence.loads_state(persistence.dumps_state(data))
    assert out == data
    assert all(isinstance(k, int) for k in out["weapons"])
    assert out["first_shot_done"] is True


def test_noop_when_disabled():
    """未 init 时全部操作 no-op（普通单测 import room_manager 不触库）。"""
    old = persistence._enabled
    persistence.disable()
    try:
        persistence.save_room({"code": "XXXXXX"})
        assert persistence.load_all_rooms() == []
    finally:
        persistence._enabled = old


def test_save_load_delete(tmp_db):
    room = {
        "code": "ABC123", "name": "r", "game": "gomoku", "host_id": 1,
        "status": "waiting", "password": None, "max_players": 2,
        "players": [{"id": 1, "username": "u1"}],
        "game_state": None, "rematch_votes": {1},
        "created_at": "x", "last_activity": 0.0, "empty_since": None,
    }
    persistence.save_room(room)
    rooms = persistence.load_all_rooms()
    assert len(rooms) == 1
    assert rooms[0]["code"] == "ABC123"
    assert rooms[0]["rematch_votes"] == {1}
    persistence.delete_room("ABC123")
    assert persistence.load_all_rooms() == []


def test_destroy_removes_row(tmp_db):
    """房间销毁（等待房无人）时磁盘行必须同步删除。"""
    room = rm.create_room(U1, None, None, "gomoku")
    code = room["code"]
    assert len(persistence.load_all_rooms()) == 1
    action, _ = rm.leave_room(code, 1)
    assert action == "closed"
    assert persistence.load_all_rooms() == []


def test_restore_rooms_playing(tmp_db):
    """五子棋对局中重启：恢复后可直接续下，int 键/离线标记/空房计时正确。"""
    room = rm.create_room(U1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, U2, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, 1) is not None
    assert rm.make_move(code, 1, 7, 7)["ok"]
    # 模拟进程重启
    rm._rooms.clear()
    assert rm.restore_rooms() == 1
    r2 = rm.get_room(code)
    assert r2["status"] == "playing"
    assert r2["game_state"]["board"][7][7] != 0
    assert all(isinstance(k, int) for k in r2["game_state"]["player_colors"])
    assert all(not p["connected"] for p in r2["players"])
    assert r2["empty_since"] is not None  # 重启后无人在线 → 空房计时兜底
    # 续下：轮到白（u2）
    assert rm.make_move(code, 2, 8, 8)["ok"]


def test_restore_2048_rng(tmp_db):
    """2048 重启恢复：rng 对象复活，恢复后仍可继续走子且出块序列延续。"""
    room = rm.create_room(U1, None, None, "2048")
    code = room["code"]
    rm.join_room(code, U2, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, 1) is not None
    expect = room["game_state"]["rngs"][1].random()  # 记录下一个随机数
    rm._rooms.clear()
    rm.restore_rooms()
    r2 = rm.get_room(code)
    rng = r2["game_state"]["rngs"][1]
    assert isinstance(rng, random.Random)
    assert rng.random() == expect
    moved = any(
        rm.game2048_make_move(code, 1, d).get("ok")
        for d in ("up", "left", "right", "down")
    )
    assert moved


def test_restore_bot_counter(tmp_db):
    """恢复含 bot 的房间后，新 bot id 不与旧 bot 冲突。"""
    room = rm.create_room(U1, None, None, "gomoku")
    rm.add_bot(room["code"], 1)
    bot_id = next(p["id"] for p in room["players"] if p["is_bot"])
    rm._rooms.clear()
    rm.restore_rooms()
    room2 = rm.create_room(U2, None, None, "gomoku")
    rm.add_bot(room2["code"], 2)
    bot_id2 = next(p["id"] for p in room2["players"] if p["is_bot"])
    assert bot_id2 != bot_id
    assert bot_id2 < bot_id


def test_restore_drops_legacy_finished_rooms(tmp_db):
    """v1.5.1 之前落盘的 finished 房不该被恢复——sweeper 只认 waiting/playing，
    恢复进来就是永远回收不掉的僵尸房（占内存、还留在落盘表里）。"""
    persistence.save_room({
        "code": "OLDONE", "game": "gomoku", "status": "finished",
        "host_id": 1, "players": [{"id": 1, "username": "a", "is_bot": False}],
        "game_state": None, "created_at": "2024-01-01T00:00:00",
        "last_activity": 1.0, "empty_since": None,
    })
    assert rm.restore_rooms() == 0
    assert rm.get_room("OLDONE") is None
    assert persistence.load_all_rooms() == []      # 落盘行也一并清掉


def test_roundtrip_set_of_tuples(tmp_db):
    """集合里的 tuple 必须能原样转回来（扫雷的 flagged / mine_positions 就是
    set[tuple[int,int]]）。JSON 没有 tuple，编码后是数组，解码时直接 set() 会
    TypeError——而单房解码失败是静默跳过，整个房间就永远恢复不回来。"""
    state = {"flagged": {(0, 1), (2, 3)}, "mine_positions": {(4, 5)}}
    assert persistence.loads_state(persistence.dumps_state(state)) == state


def test_restore_room_with_flagged_mines(tmp_db):
    """端到端：带插旗的扫雷房必须能恢复（曾经整房静默消失）。"""
    from app import minesweeper as mine
    room = rm.create_room(U1, None, None, "minesweeper")
    code = room["code"]
    room["game_state"] = mine.create_game("beginner")
    room["game_state"]["flagged"] = {(0, 1), (2, 3)}
    rm._touch_activity(room)

    rm._rooms.clear()
    assert rm.restore_rooms() == 1
    back = rm.get_room(code)
    assert back is not None
    assert back["game_state"]["flagged"] == {(0, 1), (2, 3)}


def test_restore_backfills_new_uno_rules(tmp_db):
    """热重启恢复**老版本落盘的** UNO 对局：房规缺了新键要补齐，不能 KeyError。

    ⚠️ 每加一条房规，老房间的 `gs["rules"]` 就少一个键，而引擎是直接
    `game["rules"]["teams"]` 这样读的 —— 少一个键不是「退回默认值」，是 `KeyError`，
    而且炸在走子 / tick 路径上（不是恢复的时候）。v1.16 加 `teams` 时正好撞上：
    v1.14/v1.15 落盘的进行中对局只有 7 个键。
    """
    room = rm.create_room(U1, None, None, "uno")
    code = room["code"]
    rm.join_room(code, U2, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, 1) is not None
    gs = room["game_state"]
    # 造一份「上一版」的房规：把 v1.16 才有的键摘掉
    assert gs["rules"]["teams"] == "off"
    gs["rules"].pop("teams")
    rm._touch_activity(room)

    rm._rooms.clear()
    assert rm.restore_rooms() == 1
    r2 = rm.get_room(code)
    assert r2["game_state"]["rules"]["teams"] == "off", "缺的键没补回来"
    # 补回来之后引擎才读得动（`team_of` 一上来就读它）
    seat = r2["game_state"]["turn"]
    assert rm.uno_play_action(code, r2["game_state"]["seat_uids"][seat],
                              {"action": "draw"}) is not None
