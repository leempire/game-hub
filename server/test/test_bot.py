"""Bot system integration tests using FastAPI TestClient.

Tests: add_bot permissions, minesweeper rejection, lobby-only constraint,
bot auto-ready, host starts gomoku with bot and bot responds, remove_bot,
bot game record persistence.

Run: pytest test/test_bot.py -v
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.database import get_conn


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    yield


@pytest.fixture
def client(ws_client):
    # 共用事件循环的 TestClient（原因见 conftest.py 的 ws_client）
    return ws_client


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="gomoku"):
    r = client.post(
        "/api/rooms",
        json={"game": game},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(
        f"/api/rooms/{code}/join",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=20):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def test_add_bot_requires_host(client):
    """Only host can add bot."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_h{ts}a")
    t2, uid2 = _register(client, f"bt_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1, \
         client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
        # ws1 gets: sync, lobby (from ws2 connect)
        _recv_type(ws1, "sync")
        # ws2 gets: sync, lobby (from ws2 connect broadcast to ws1, but ws2 also gets lobby from own connect? no)
        _recv_type(ws2, "sync")
        # Drain lobby broadcasts
        # ws1 may have received a lobby from ws2's connection
        msg = ws1.receive_json()
        if msg["type"] == "lobby":
            pass  # consumed
        # Non-host tries to add bot
        ws2.send_json({"type": "add_bot"})
        err = _recv_type(ws2, "error")
        assert "房主" in err["message"] or "只有" in err["message"]


def test_add_bot_minesweeper_rejected(client):
    """Minesweeper rooms should reject bot."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_m{ts}")
    r = client.post(
        "/api/rooms",
        json={"game": "minesweeper"},
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r.status_code == 200
    code = r.json()["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        err = _recv_type(ws1, "error")
        assert "不支持" in err["message"]


def test_add_bot_full_room_rejected(client):
    """Full room should reject bot."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_f{ts}a")
    t2, uid2 = _register(client, f"bt_f{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    # Room is full (2/2), only ws1 connects
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        # Room already full (2/2 for gomoku)
        ws1.send_json({"type": "add_bot"})
        err = _recv_type(ws1, "error")
        assert "已满" in err["message"]


def test_add_bot_success(client):
    """Host adds bot successfully in waiting room."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_s{ts}")
    room = _create_room(client, t1)
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        lobby = _recv_type(ws1, "lobby")
        players = lobby["room"]["players"]
        assert len(players) == 2
        bot = [p for p in players if p.get("is_bot")]
        assert len(bot) == 1
        assert bot[0]["nickname"] == "人机·棋手"
        assert bot[0]["ready"] is True


def test_remove_bot(client):
    """Host can remove bot from waiting room."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_r{ts}")
    room = _create_room(client, t1)
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "remove_bot"})
        lobby = _recv_type(ws1, "lobby")
        players = lobby["room"]["players"]
        assert len(players) == 1
        bot = [p for p in players if p.get("is_bot")]
        assert len(bot) == 0


def test_bot_auto_ready_gomoku(client):
    """Bot is auto-ready, host can start gomoku with bot."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_g{ts}")
    room = _create_room(client, t1)
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        lobby = _recv_type(ws1, "lobby")
        # Bot should be ready
        bot = [p for p in lobby["room"]["players"] if p.get("is_bot")][0]
        assert bot["ready"] is True
        # Host starts game
        ws1.send_json({"type": "start"})
        g1 = _recv_type(ws1, "game_start")
        assert g1["game"]["board_size"] == 15
        assert g1["game"]["your_color"] == "black"


def test_bot_responds_to_gomoku_move(client):
    """Host plays gomoku with bot, bot responds with AI move.

    In TestClient mode, asyncio tasks don't run automatically,
    so we directly invoke the bot callback to simulate the bot turn.
    """
    import asyncio
    from app.ws import _bot_do_turn

    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_mv{ts}")
    room = _create_room(client, t1)
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        g1 = _recv_type(ws1, "game_start")
        assert g1["game"]["next"] == "black"
        # Find bot id
        room_data = rm.get_room(code)
        bot_player = [p for p in room_data["players"] if p.get("is_bot")][0]
        bot_id = bot_player["id"]
        # Host (black) plays center
        ws1.send_json({"type": "move", "x": 7, "y": 7})
        m1 = _recv_type(ws1, "move")
        assert m1["x"] == 7 and m1["y"] == 7
        assert m1["color"] == "black"
        # Manually invoke bot turn (TestClient doesn't run background tasks)
        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot_id))
        # Drain pending messages to find bot's move
        m2 = _recv_type(ws1, "move", max_skip=5)
        assert m2["color"] == "white"
        assert 0 <= m2["x"] < 15
        assert 0 <= m2["y"] < 15


def test_bot_game_record(client, tmp_path):
    """Bot game record should save without crashing."""
    import app.database as db_mod
    from app.database import init_db, save_game_record
    orig_db_path = db_mod._db_path
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    rm.set_game_end_callback(save_game_record)
    try:
        ts = int(time.time() * 1000) % 100000
        t1, uid1 = _register(client, f"bt_rc{ts}")
        room = _create_room(client, t1)
        code = room["code"]
        with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
            _recv_type(ws1, "sync")
            ws1.send_json({"type": "add_bot"})
            _recv_type(ws1, "lobby")
            ws1.send_json({"type": "start"})
            g1 = _recv_type(ws1, "game_start")
            # Surrender to end the game quickly
            ws1.send_json({"type": "surrender"})
            go = _recv_type(ws1, "game_over")
            assert go["winner"] == "white"
        # Check database
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM game_records WHERE game = 'gomoku' AND room_code = ?",
                (code,),
            ).fetchall()
            assert len(rows) >= 1, "No game record for bot game"
            import json
            rec = rows[0]
            players = json.loads(rec["players_json"])
            # Should have both human and bot
            usernames = [p["username"] for p in players]
            assert f"bt_rc{ts}" in usernames
            assert any("bot_" in u for u in usernames)
            results = json.loads(rec["results_json"])
            result_map = {r["username"]: r["result"] for r in results}
            assert result_map[f"bt_rc{ts}"] == "lose"
        finally:
            conn.close()
    finally:
        rm.set_game_end_callback(None)
        db_mod.init_db(orig_db_path)


def test_add_bot_in_playing_room_rejected(client):
    """Cannot add bot during playing phase."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bt_p{ts}a")
    t2, uid2 = _register(client, f"bt_p{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1, \
         client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
        _recv_type(ws1, "sync")
        _recv_type(ws2, "sync")
        # ws1 may have lobby from ws2 connect
        msg = ws1.receive_json()
        # ws2 ready
        ws2.send_json({"type": "ready"})
        # ws1 gets lobby from ws2 ready, ws2 gets lobby from own ready
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")
        _recv_type(ws2, "game_start")
        # Try to add bot during game
        ws1.send_json({"type": "add_bot"})
        err = _recv_type(ws1, "error")
        assert "等待" in err["message"]


# ── 飞行棋人机 ───────────────────────────────────────────────


def test_add_bot_ludo_allows_multiple(client):
    """飞行棋 4 人房可以连加 3 个人机，加满后再加报错。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"ludo_mb{ts}")
    room = _create_room(client, t1, game="ludo")
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        for i in range(3):
            ws1.send_json({"type": "add_bot"})
            lobby = _recv_type(ws1, "lobby")
            assert len(lobby["room"]["players"]) == i + 2
        # 4/4 已满
        ws1.send_json({"type": "add_bot"})
        err = _recv_type(ws1, "error")
        assert "满" in err["message"]


def test_ludo_bot_takes_off_on_six(client, monkeypatch):
    """人机掷到 6：自动起飞并广播 piece_moved。"""
    import asyncio
    from app.ws import _bot_do_turn
    import app.ludo as ludo_mod

    monkeypatch.setattr(ludo_mod.random, "randint", lambda a, b: 6)

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"ludo_b6{ts}")
    room = _create_room(client, t1, game="ludo")
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")
        bot = [p for p in rm.get_room(code)["players"] if p.get("is_bot")][0]
        # 把回合交给人机（座位 1）
        gs = rm.get_room(code)["game_state"]
        gs["current_seat"] = 1
        gs["phase"] = "roll"
        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))
        rolled = _recv_type(ws1, "dice_rolled")
        assert rolled["seat"] == 1 and rolled["dice"] == 6
        moved = _recv_type(ws1, "piece_moved", max_skip=5)
        assert moved["seat"] == 1
        assert moved["old_progress"] == -1 and moved["new_progress"] == 0


def test_ludo_bot_passes_turn_when_stuck(client, monkeypatch):
    """人机掷到非 6 且飞机全在基地：无可动子，回合交回真人。"""
    import asyncio
    from app.ws import _bot_do_turn
    import app.ludo as ludo_mod

    monkeypatch.setattr(ludo_mod.random, "randint", lambda a, b: 3)

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"ludo_b3{ts}")
    room = _create_room(client, t1, game="ludo")
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")
        bot = [p for p in rm.get_room(code)["players"] if p.get("is_bot")][0]
        gs = rm.get_room(code)["game_state"]
        gs["current_seat"] = 1
        gs["phase"] = "roll"
        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))
        rolled = _recv_type(ws1, "dice_rolled")
        assert rolled["dice"] == 3 and rolled["movable"] == []
        assert rm.get_room(code)["game_state"]["current_seat"] == 0


def test_ludo_piece_moved_carries_jump_and_fly_path(client):
    """走子广播必须带 path/segments：跳格与虚线飞行的形状只有服务端知道。

    客户端按 old+dice 自己推路径，加了跳/飞之后会画错（飞机停在半路、
    提示文案也对不上），所以这段载荷是前后端契约的一部分。
    """
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"ludo_path{ts}")
    room = _create_room(client, t1, game="ludo")
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")

        gs = rm.get_room(code)["game_state"]
        # 座位 0 的一架飞机摆在相对进度 9，掷 3 → 落到 12（同色格）
        # → 跳 16（虚线格）→ 飞 28。走子、跳格、虚线三段都在这一条 path 里。
        gs["players"][0]["pieces"][0] = 9
        gs["current_seat"] = 0
        gs["phase"] = "move"
        gs["dice_value"] = 3
        gs["movable_pieces"] = [0]
        ws1.send_json({"type": "move_piece", "piece_id": 0})
        moved = _recv_type(ws1, "piece_moved")
        assert moved["old_progress"] == 9
        assert moved["new_progress"] == 28  # 12 → 跳 16 → 飞 28
        assert moved["path"] == [10, 11, 12, 16, 28]
        assert moved["segments"] == [
            {"kind": "jump", "from": 12, "to": 16},
            {"kind": "fly", "from": 16, "to": 28},
        ]


def test_ludo_surrender_hands_over_turn():
    """轮到出手的玩家认输后，回合必须移交（否则整桌卡死）。

    需要 3 人局：2 人局里任一方弃权即 last_remaining 终局，走不到移交分支。
    """
    u1 = {"id": 9001, "username": "ls_h", "nickname": "LS_H"}
    u2 = {"id": 9002, "username": "ls_p", "nickname": "LS_P"}
    u3 = {"id": 9003, "username": "ls_q", "nickname": "LS_Q"}
    room = rm.create_room(u1, None, None, "ludo")
    code = room["code"]
    rm.join_room(code, u2, None)
    rm.join_room(code, u3, None)
    for p in room["players"]:
        p["ready"] = True
    rm.start_game(code, u1["id"])
    gs = room["game_state"]
    assert gs["current_seat"] == 0
    assert rm.surrender(code, u1["id"]) is None  # 局继续
    assert gs["players"][0]["finished"] is True
    assert gs["current_seat"] == 1  # 回合交给还在局里的 u2
    assert rm.ludo_roll_action(code, u2["id"])["ok"]


# ── UNO ──────────────────────────────────────────────────────


def test_add_bot_uno_allows_multiple(client):
    """UNO 是 4 人房，可以连加 3 个人机凑满。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"uno_mb{ts}")
    code = _create_room(client, t1, game="uno")["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        for i in range(3):
            ws1.send_json({"type": "add_bot"})
            lobby = _recv_type(ws1, "lobby")
            assert len(lobby["room"]["players"]) == i + 2
        ws1.send_json({"type": "add_bot"})
        err = _recv_type(ws1, "error")
        assert "满" in err["message"]


def test_uno_bot_takes_its_turn(client):
    """轮到人机：自动出牌（数字牌优先）并广播状态，剩一张时自动喊 UNO。"""
    import asyncio
    from app.ws import _bot_do_turn

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"uno_bt{ts}")
    code = _create_room(client, t1, game="uno")["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")

        r = rm.get_room(code)
        gs = r["game_state"]
        bot = [p for p in r["players"] if p.get("is_bot")][0]
        # 造确定局面：轮到人机，手里一张数字牌 + 一张功能牌
        gs["hands"][1] = ["R3", "RS"]
        gs["discard_pile"] = ["R5"]
        gs["current_color"] = "R"
        gs["turn"] = 1
        gs["drawn_this_turn"] = None

        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))

        ev = _recv_type(ws1, "uno_event")
        assert ev["kind"] == "play"
        assert ev["seat"] == 1
        assert ev["card"] == "R3", "数字牌优先，功能牌留着"
        # 走完只剩一张 → 自动喊 UNO
        shout = _recv_type(ws1, "uno_event")
        assert shout["kind"] == "uno" and shout["seat"] == 1
        st = _recv_type(ws1, "uno_state")
        assert st["turn"] == 0, "人机走完回合交回真人"
        assert st["uno_called"] == [1]
        assert len(gs["hands"][1]) == 1


def test_uno_bot_stacks_penalty(client):
    """被 +2 罚到时，人机手里有 +2 就转嫁给下家（叠加房规）。"""
    import asyncio
    from app.ws import _bot_do_turn

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"uno_bs{ts}")
    code = _create_room(client, t1, game="uno")["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")

        r = rm.get_room(code)
        gs = r["game_state"]
        bot = [p for p in r["players"] if p.get("is_bot")][0]
        gs["hands"][1] = ["G+2", "B1"]
        gs["discard_pile"] = ["Y5"]
        gs["current_color"] = "Y"
        gs["turn"] = 1
        gs["drawn_this_turn"] = None
        gs["pending_draw"] = 2
        gs["pending_kind"] = "+2"

        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))

        ev = _recv_type(ws1, "uno_event")
        assert ev["kind"] == "play" and ev["card"] == "G+2"
        assert ev["effect"] == "draw2"
        st = _recv_type(ws1, "uno_state")
        assert st["pending_draw"] == 4, "累计罚牌传给真人"
        assert st["turn"] == 0


def test_uno_bot_does_not_act_out_of_turn():
    """不是人机的回合就不该动（回调可能被误触发）。"""
    import asyncio
    from app.ws import _bot_do_turn

    u1 = {"id": 9101, "username": "uno_oh", "nickname": "H"}
    room = rm.create_room(u1, None, None, "uno")
    code = room["code"]
    bot = rm._make_bot_player(code, 1)
    room["players"].append(bot)
    for p in room["players"]:
        p["ready"] = True
    rm.start_game(code, u1["id"])
    gs = room["game_state"]
    gs["turn"] = 0                       # 轮到真人
    before = list(gs["discard_pile"])
    asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))
    assert gs["discard_pile"] == before
    assert gs["turn"] == 0


def test_uno_bot_jumps_in(client):
    """抢出房规下，**不轮到他**的人机也能抢一手（`_bot_uno_jump`）。

    这是人机唯一一条**非本回合动作**，走的是 `_bot_uno_turn` 里「不是他的回合 →
    转 `_bot_uno_jump`」那个分支。它坏掉不会有任何报错，只会静默地表现为
    「人机从来不用这条房规」—— 所以要单独锁一条。
    """
    import asyncio
    from app.ws import _bot_do_turn

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"uno_bj{ts}")
    code = _create_room(client, t1, game="uno")["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        # 房主在大厅开「抢出」（必须在 start 之前 —— 房规是 create_game 时定下的）
        ws1.send_json({"type": "room_setting", "key": "jump_in", "value": True})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        starts = _recv_type(ws1, "game_start")
        assert starts["game"]["rules"]["jump_in"] is True

        r = rm.get_room(code)
        gs = r["game_state"]
        bot = [p for p in r["players"] if p.get("is_bot")][0]
        # 造确定局面：**轮到真人**，人机手里有一张与牌堆顶完全相同的牌
        gs["hands"][1] = ["R5", "B3"]
        gs["discard_pile"] = ["R5"]
        gs["current_color"] = "R"
        gs["turn"] = 0
        gs["drawn_this_turn"] = None

        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot["id"]))

        ev = _recv_type(ws1, "uno_event")
        assert ev["kind"] == "play" and ev["seat"] == 1
        assert ev["jump"] is True, "人机这一手应当是『抢出』"
        assert ev["card"] == "R5"
        st = _recv_type(ws1, "uno_state")
        assert st["discard_top"] == "R5"
        assert st["hand_counts"]["1"] == 1
        assert st["turn"] == 0, "2 人局里从座位 1（抢出者）往下走就回到座位 0"
