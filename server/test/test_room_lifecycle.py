"""Room lifecycle tests: host transfer, finished-room rejoin, idle sweeper.

Tests the new room lifecycle contract:
- Host leaving does NOT unconditionally delete the room
- Host is transferred to next human player
- Finished rooms are preserved and can be rejoined / restarted
- Idle rooms are cleaned up by the sweeper after ROOM_IDLE_TTL
- Disconnect in finished room preserves seat

Run: pytest test/test_room_lifecycle.py -v
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.ws import _build_sync


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


def _leave_room(client, token, code):
    r = client.post(
        f"/api/rooms/{code}/leave",
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


def _u(name):
    """注册用户名要带时间戳：用户表是持久化的，固定名字第二次跑就 409。"""
    return f"{name}_{int(time.time() * 1000) % 10 ** 9}"


_user_seq = [0]


def _make_user(username="testuser"):
    """Create a user dict directly for unit-level tests.

    id 用**进程内自增**，不要用 `abs(hash(username)) % 100000`：字符串 hash 每个
    进程随机（PYTHONHASHSEED），取模后不同用户名还可能撞成同一个 id——一撞
    多人局的 `gs["boards"][uid]` / `game_over_flags` 就少一项，2048 那几条用例
    偶发失败（全量跑几次挂一次，单独跑必过）。**测试不要依赖随机性**。
    """
    _user_seq[0] += 1
    return {
        "id": 100000 + _user_seq[0],
        "username": username,
        "nickname": username,
    }


# ── Host transfer tests ──────────────────────────────────────


def test_host_leaves_waiting_transfers_host():
    """Host leaving a waiting room transfers host to next human."""
    u1 = _make_user("host1")
    u2 = _make_user("player2")
    u3 = _make_user("player3")
    # Use minesweeper (max_players=4) so we can have 3 players
    room = rm.create_room(u1, None, None, "minesweeper")
    code = room["code"]
    rm.join_room(code, u2, None)
    rm.join_room(code, u3, None)
    assert room["host_id"] == u1["id"]
    # Host leaves
    action, updated = rm.leave_room(code, u1["id"])
    assert action == "removed"
    assert updated is not None
    assert updated["host_id"] == u2["id"]  # transferred to seat 1
    assert len(updated["players"]) == 2
    assert rm.get_room(code) is not None  # room still exists


def test_host_leaves_waiting_no_humans_destroys():
    """Host leaving waiting room with only bots → room destroyed."""
    u1 = _make_user("host_only")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.add_bot(code, u1["id"])
    assert len(room["players"]) == 2  # human + bot
    # Host leaves → no humans left → destroy
    action, updated = rm.leave_room(code, u1["id"])
    assert action == "closed"
    assert updated is None
    assert rm.get_room(code) is None


def test_host_leaves_playing_surrender():
    """Host leaving during playing = surrender (opponent wins)."""
    u1 = _make_user("h_playing")
    u2 = _make_user("p_playing")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    # Manually set ready and start
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    gs = rm.start_game(code, u1["id"])
    assert gs is not None
    assert room["status"] == "playing"
    # Host leaves → surrender
    action, updated = rm.leave_room(code, u1["id"])
    assert action == "game_over"
    assert updated is not None
    assert updated["status"] == "waiting"
    assert updated["game_state"]["winner"] == "white"
    assert updated["game_state"]["reason"] == "surrender"
    # Host should have transferred to u2
    assert updated["host_id"] == u2["id"]


# ── Finished room tests ──────────────────────────────────────


def test_played_room_preserved_after_game():
    """对局结束后房间保留（回到等待态，可续开）。"""
    u1 = _make_user("fin_h")
    u2 = _make_user("fin_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    # Surrender to end game
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    assert rm.get_room(code) is not None  # room preserved
    assert room["round"] == 1  # 已打完 1 局


def test_played_room_original_player_rejoin():
    """原玩家可凭房号回到刚打完的房间（不再被 find_user_waiting_room 挡住）。"""
    u1 = _make_user("rej_h")
    u2 = _make_user("rej_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    # 原玩家重进：即使他还在房间里，也必须放行而不是抛「你已在房间中」
    result = rm.join_room(code, u2, None)
    assert result is not None
    assert result["status"] == "waiting"


def test_finished_room_new_player_join():
    """New player can join a finished room if space + password correct."""
    u1 = _make_user("nj_h")
    u2 = _make_user("nj_p")
    u3 = _make_user("nj_new")
    room = rm.create_room(u1, None, "pass123", "gomoku")
    code = room["code"]
    rm.join_room(code, u2, "pass123")
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    # Room is full (2/2) — new player can't join
    with pytest.raises(ValueError, match="已满"):
        rm.join_room(code, u3, "pass123")


def test_played_room_start_new_game():
    """续开一局要重新表态：终局复位 ready 后，对方重新准备才能开下一局。"""
    u1 = _make_user("ng_h")
    u2 = _make_user("ng_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    # 终局已把 u2 复位为未准备 → 直接 start 开不起来
    assert rm.start_game(code, u1["id"]) is None
    assert room["status"] == "waiting"
    # 对方重新准备后房主即可续开
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    assert rm.start_game(code, u1["id"]) is not None
    assert room["status"] == "playing"


def test_get_all_rooms_played_waiting_visibility():
    """打完一局回到等待态的房间仍在列表；真人全部离开即销毁（人走即销毁）。"""
    u1 = _make_user("list_h")
    u2 = _make_user("list_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    # 列表只展示「有真人在线」的等待房 → 先把双方标记为在线
    for p in room["players"]:
        p["connected"] = True
    assert any(r["code"] == code for r in rm.get_all_rooms())
    # 玩家逐个离开：还剩一个真人时仍可见
    rm.leave_room(code, u2["id"])
    assert any(r["code"] == code for r in rm.get_all_rooms())
    # 真人全部离开 → 房间直接销毁（结束后等同普通等待房，人走即销毁）
    rm.leave_room(code, u1["id"])
    assert not any(r["code"] == code for r in rm.get_all_rooms())
    assert rm.get_room(code) is None


# ── Sweeper tests ────────────────────────────────────────────


def test_sweeper_deletes_idle_waiting():
    """Sweeper deletes waiting room idle > TTL."""
    u1 = _make_user("idle_w")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    # Fake last_activity in the past
    room["last_activity"] = time.time() - rm.ROOM_IDLE_TTL - 10
    deleted = rm.sweep_idle_rooms()
    assert code in deleted
    assert rm.get_room(code) is None


def test_sweeper_keeps_fresh_waiting():
    """Sweeper does NOT delete a recently active waiting room."""
    u1 = _make_user("fresh_w")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    # last_activity is now (just created)
    deleted = rm.sweep_idle_rooms()
    assert code not in deleted
    assert rm.get_room(code) is not None


def test_sweeper_deletes_idle_played_waiting():
    """打完一局的房间回到等待态，久无活动同样被 sweeper 清理。"""
    u1 = _make_user("sw_fin_h")
    u2 = _make_user("sw_fin_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    assert room["round"] == 1
    room["last_activity"] = time.time() - rm.ROOM_IDLE_TTL - 10
    deleted = rm.sweep_idle_rooms()
    assert code in deleted


def test_sweeper_keeps_fresh_played_waiting():
    """刚打完一局、还在活动的等待房不会被 sweeper 误删。"""
    u1 = _make_user("sw_on_h")
    u2 = _make_user("sw_on_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    deleted = rm.sweep_idle_rooms()
    assert code not in deleted
    assert rm.get_room(code) is not None


def test_sweeper_keeps_playing_with_empty_since_none():
    """Playing room with empty_since=None is not swept."""
    u1 = _make_user("sw_play_h")
    u2 = _make_user("sw_play_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    assert room["status"] == "playing"
    assert room["empty_since"] is None
    deleted = rm.sweep_idle_rooms()
    assert code not in deleted


def test_sweeper_monkeypatch_ttl():
    """TTL can be monkeypatched for fast testing."""
    u1 = _make_user("ttl_test")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    # Set last_activity 5 seconds ago
    room["last_activity"] = time.time() - 5
    # With default TTL (1800s), should NOT be deleted
    deleted = rm.sweep_idle_rooms()
    assert code not in deleted
    # Monkeypatch TTL to 2 seconds
    original_ttl = rm.ROOM_IDLE_TTL
    rm.ROOM_IDLE_TTL = 2.0
    try:
        deleted = rm.sweep_idle_rooms()
        assert code in deleted
    finally:
        rm.ROOM_IDLE_TTL = original_ttl


# ── Disconnect in finished room ──────────────────────────────


def test_played_waiting_disconnect_keeps_seat_with_grace():
    """对局结束回到等待态后掉线：保留座位 + 记空房计时，不立即移出。

    客户端从对局页返回房间是「先关对局页连接、再重连房间页」，立即移出会让
    房间瞬间没人而被销毁（返回房间 → 房间不存在）。宽限期内重连即取消。
    """
    u1 = _make_user("dc_fin_h")
    u2 = _make_user("dc_fin_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    for p in room["players"]:
        p["connected"] = True
    import asyncio
    loop = asyncio.new_event_loop()
    rm.handle_player_disconnect(code, u1["id"], loop)
    loop.close()
    # u1 仍在房里（只是离线）；u2 还在线 → 房间保留、无空房计时
    p1 = next(p for p in room["players"] if p["id"] == u1["id"])
    assert p1["connected"] is False
    assert room["empty_since"] is None
    # 重连后立刻恢复在线
    assert rm.handle_player_reconnect(code, u1["id"]) is True
    assert p1["connected"] is True


def test_played_waiting_all_disconnect_marks_empty():
    """对局结束后全部掉线 → 记空房计时，由 sweeper 在宽限期后回收。"""
    u1 = _make_user("dc_all_h")
    u2 = _make_user("dc_all_p")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    rm.start_game(code, u1["id"])
    rm.surrender(code, u1["id"])
    for p in room["players"]:
        p["connected"] = True
    import asyncio
    loop = asyncio.new_event_loop()
    rm.handle_player_disconnect(code, u1["id"], loop)
    rm.handle_player_disconnect(code, u2["id"], loop)
    loop.close()
    # 宽限期内房间还在（给「返回房间」的重连留窗口），只是记了空房计时
    assert rm.get_room(code) is not None
    assert room["empty_since"] is not None
    # 超时后 sweeper 回收
    room["empty_since"] = time.time() - rm.WAITING_EMPTY_TTL - 5
    assert code in rm.sweep_idle_rooms()
    assert rm.get_room(code) is None


def test_reconnect_clears_empty_since_in_playing_room():
    """游戏中重连会清掉空房计时（掉线重连只在游戏中存在）。"""
    room, code, u1, u2 = _start_two_player("gomoku", "recon")
    for p in room["players"]:
        p["connected"] = True
    room["empty_since"] = time.time() - 5
    assert room["status"] == "playing"
    rm.handle_player_reconnect(code, u1["id"])
    assert room["empty_since"] is None


# ── WebSocket integration: host leave doesn't destroy ────────


def test_ws_host_leave_room_preserved(client):
    """Via WS: host leaves waiting room, room preserved with new host."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"lc_h{ts}a")
    t2, uid2 = _register(client, f"lc_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1, \
         client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
        _recv_type(ws1, "sync")
        _recv_type(ws2, "sync")
        # Drain lobby from ws2 connect
        _recv_type(ws1, "lobby")
        # Host leaves via WS
        ws1.send_json({"type": "leave"})
        # ws2 should get lobby (room preserved, new host)
        lobby = _recv_type(ws2, "lobby", max_skip=5)
        assert lobby["room"]["host_id"] == uid2
        assert len(lobby["room"]["players"]) == 1
        # Room still exists via REST (ws2 still connected)
        r = client.get(
            f"/api/rooms/{code}",
            headers={"Authorization": f"Bearer {t2}"},
        )
        assert r.status_code == 200
        assert r.json()["host_id"] == uid2


def test_ws_game_end_then_start_new(client):
    """Via WS: 对局结束房间回到等待态，房主可直接续开新一局。"""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"ng{ts}a")
    t2, uid2 = _register(client, f"ng{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1, \
         client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
        _recv_type(ws1, "sync")
        _recv_type(ws2, "sync")
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        # Start game
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")
        _recv_type(ws2, "game_start")
        # Host surrenders
        ws1.send_json({"type": "surrender"})
        _recv_type(ws1, "game_over")
        _recv_type(ws2, "game_over")
        # 房间已回到等待态，双方仍在连接中（不需要 rematch）。终局会把真人非房主
        # 复位为未准备，所以续开前对方要重新准备——这正是前端的实际流程。
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        g1 = _recv_type(ws1, "game_start", max_skip=5)
        g2 = _recv_type(ws2, "game_start", max_skip=5)
        assert g1["game"]["board_size"] == 15
        assert g2["game"]["board_size"] == 15


# ── 自然终局必须落 finished（房间状态契约） ─────────────────────


def _start_two_player(game: str, tag: str):
    """建房 + 加入 + 准备 + 开局，返回 (room, code, u1, u2)。"""
    u1 = _make_user(f"{tag}_h")
    u2 = _make_user(f"{tag}_p")
    room = rm.create_room(u1, None, None, game)
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        if p["id"] == u2["id"]:
            p["ready"] = True
    assert rm.start_game(code, u1["id"]) is not None
    return room, code, u1, u2


def _play_dots_to_end(code: str, u1, u2):
    """轮流画完所有边（含封格加手），直到引擎判定终局。"""
    for _ in range(300):
        gs = rm.get_room(code)["game_state"]
        if gs["winner"]:
            return
        pid = u1["id"] if gs["next"] == 1 else u2["id"]
        played = False
        for y in range(gs["rows"] + 1):
            for x in range(gs["cols"]):
                if gs["h"][y][x] == 0:
                    assert rm.dots_make_move(code, pid, "h", y, x)["ok"]
                    played = True
                    break
            if played:
                break
        if played:
            continue
        for y in range(gs["rows"]):
            for x in range(gs["cols"] + 1):
                if gs["v"][y][x] == 0:
                    assert rm.dots_make_move(code, pid, "v", y, x)["ok"]
                    played = True
                    break
            if played:
                break
        assert played, "还有空边却找不到可下的边"
    raise AssertionError("点格棋未在有限步内终局")


def test_dots_natural_end_marks_waiting():
    """点格棋自然终局（画完所有边）必须把房间落回等待态。

    回归：dots_make_move 曾漏调 _set_game_over，房间停在 playing，
    玩家点「返回房间」后 RoomScreen 收到 sync（status=playing）立刻
    又被弹回对局页，表现为「返回房间无效」。
    """
    room, code, u1, u2 = _start_two_player("dots", "dotsend")
    _play_dots_to_end(code, u1, u2)
    assert room["status"] == "waiting"
    assert room["game_state"]["winner"] in ("black", "white", "draw")


def test_gomoku_natural_end_marks_waiting():
    """五子棋连成五子自然终局同样要落回等待态。"""
    room, code, u1, u2 = _start_two_player("gomoku", "gomokuend")
    # 黑：(0..4,0)，白：(0..3,1) 陪走，黑第五子成五
    for i in range(4):
        assert rm.make_move(code, u1["id"], i, 0)["ok"]
        assert rm.make_move(code, u2["id"], i, 1)["ok"]
    result = rm.make_move(code, u1["id"], 4, 0)
    assert result["ok"] and result["game_over"]["reason"] == "five"
    assert room["status"] == "waiting"


def test_reversi_natural_end_marks_waiting():
    """翻转棋双方均无子可下时自然终局要落回等待态。"""
    room, code, u1, u2 = _start_two_player("reversi", "revend")
    # 双方轮流走合法手，直到引擎自动 pass 判定终局
    for _ in range(200):
        # 终局判据看对局态（房间状态已回到 waiting，不能用来判断是否下完）
        if room["game_state"].get("winner"):
            break
        gs = room["game_state"]
        color = gs["next"]
        pid = u1["id"] if gs["player_colors"][u1["id"]] == color else u2["id"]
        stone = 1 if color == "black" else 2
        legal = rm.reversi_get_legal_moves(gs["board"], stone)
        assert legal, "轮到该方却无合法手，引擎应已判终局"
        x, y = sorted(legal)[0]
        assert rm.reversi_make_move(code, pid, x, y)["ok"]
    assert room["status"] == "waiting"


# ── 终局复位 ready：真人非房主要重新表态 ───────────────────────


def test_end_resets_ready_for_human_players():
    """对局结束回房间：真人非房主复位为未准备（续开一局要重新点准备）。

    v1.5.1 曾故意不复位（「刚一起打完的人显然还想继续」），但那让房主点
    「开始游戏」时把还没表态的人直接拖进下一局。
    """
    room, code, u1, u2 = _start_two_player("gomoku", "readyset")
    p2 = next(p for p in room["players"] if p["id"] == u2["id"])
    assert p2["ready"] is True
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    assert p2["ready"] is False
    # 房主本来就不需要准备：前端 allReady 里房主恒真、服务端开局校验也跳过房主
    p1 = next(p for p in room["players"] if p["id"] == u1["id"])
    assert p1["ready"] is False


def test_end_keeps_bots_ready():
    """人机自动准备不参与复位，否则续开一局会卡在「等待准备」。"""
    u1 = _make_user("readybot_h")
    room = rm.create_room(u1, None, None, "gomoku")
    code = room["code"]
    rm.add_bot(code, u1["id"])
    assert rm.start_game(code, u1["id"]) is not None
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    bot = next(p for p in room["players"] if p.get("is_bot"))
    assert bot["ready"] is True
    # 打完之后房主直接就能续开（bot 已准备，房主不用准备）
    assert rm.start_game(code, u1["id"]) is not None


# ── 出局后回房间页不能被弹回对局页 ─────────────────────────────
# 扫雷 / 飞行棋 / 2048 允许「本人已出局、其他人继续」，这类玩家离开对局页后
# 房间仍是 playing。若房间页只看「status=playing 且有 game」就自动跳对局页，
# 玩家一点「退出/返回房间」就被立刻弹回去——表现为点了没反应、页面闪一下还在原处。
# 服务端用 `user_in_game` 在 sync 里告知，房间页据此决定要不要跳。


def _assert_sync_says_out(room: dict, leaver_id: int, stayer_id: int):
    """离开者在 sync 里应拿到 in_game=False（且游戏数据仍在），留下者仍是 True。"""
    sync_out = _build_sync(room, leaver_id)
    assert sync_out["game"] is not None, "对局还在进行，sync 应带 game"
    assert sync_out["in_game"] is False, "离开者不该被判定为在局玩家"
    assert _build_sync(room, stayer_id)["in_game"] is True


def test_2048_leaver_not_pulled_back_to_game():
    """2048 中途退出：只结束本人，房间仍在进行——但退出者不能被弹回对局页。"""
    room, code, u1, u2 = _start_two_player("2048", "g2048out")
    action, _ = rm.leave_room(code, u1["id"])
    assert action == "removed"
    assert room["status"] == "playing"          # 另一位还在玩
    assert rm.user_in_game(room, u1["id"]) is False
    _assert_sync_says_out(room, u1["id"], u2["id"])


def test_minesweeper_leaver_not_pulled_back_to_game():
    """扫雷中途退出 = 淘汰出局，座位保留（算排名），但不该被弹回对局页。"""
    room, code, u1, u2 = _start_two_player("minesweeper", "mineout")
    action, _ = rm.leave_room(code, u1["id"])
    assert action == "removed"
    assert room["status"] == "playing"          # 另一位还活着
    assert rm.user_in_game(room, u1["id"]) is False
    _assert_sync_says_out(room, u1["id"], u2["id"])


def test_ludo_surrendered_not_pulled_back_to_game():
    """飞行棋弃权后（3 人局，其余继续）同样不能被弹回对局页。"""
    u1, u2, u3 = (_make_user("ludoout_a"), _make_user("ludoout_b"), _make_user("ludoout_c"))
    room = rm.create_room(u1, None, None, "ludo")
    code = room["code"]
    rm.join_room(code, u2, None)
    rm.join_room(code, u3, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, u1["id"]) is not None
    action, _ = rm.leave_room(code, u1["id"])
    assert action == "removed"
    assert room["status"] == "playing"          # 另外两人继续
    assert rm.user_in_game(room, u1["id"]) is False
    _assert_sync_says_out(room, u1["id"], u2["id"])


def test_uno_surrendered_not_pulled_back_to_game():
    """UNO 认输后（3 人局，其余继续）同样不能被弹回对局页。

    UNO 的认输是「本人出局、牌局继续」，玩家留在 players 名单里（要算排名、
    座位号是列表下标），房间仍是 playing —— 与扫雷/飞行棋/2048 同一类。
    """
    u1, u2, u3 = (_make_user("unoout_a"), _make_user("unoout_b"), _make_user("unoout_c"))
    room = rm.create_room(u1, None, None, "uno")
    code = room["code"]
    rm.join_room(code, u2, None)
    rm.join_room(code, u3, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, u1["id"]) is not None
    action, _ = rm.leave_room(code, u1["id"])
    assert action == "removed"
    assert room["status"] == "playing"          # 另外两人继续
    assert rm.user_in_game(room, u1["id"]) is False
    _assert_sync_says_out(room, u1["id"], u2["id"])


def test_uno_last_remaining_ends_the_game():
    """UNO 只剩一个人时终局（单局定胜负），房间回等待态。"""
    u1, u2 = _make_user("unoend_a"), _make_user("unoend_b")
    room = rm.create_room(u1, None, None, "uno")
    code = room["code"]
    rm.join_room(code, u2, None)
    for p in room["players"]:
        p["ready"] = True
    assert rm.start_game(code, u1["id"]) is not None
    go = rm.surrender(code, u1["id"])
    assert go is not None
    assert go["reason"] == "last_remaining"
    assert go["winner"] == 1
    assert room["status"] == "waiting"


def test_room_to_dict_reports_reconnect_left():
    """断线玩家在房间数据里带**重连剩余秒数**（客户端据此倒计时），在线玩家为 None。

    秒数由服务端算并下发，客户端不写死 120——测试会把超时调小，写死就对不上。
    """
    import asyncio
    room, code, u1, u2 = _start_two_player("gomoku", "dcleft")
    for p in room["players"]:
        p["connected"] = True        # 夹具默认是离线，先置成在线再模拟断线
    rm.set_disconnect_timeout(30)
    loop = asyncio.new_event_loop()
    try:
        rm.handle_player_disconnect(code, u2["id"], loop)
        by_id = {p["id"]: p for p in rm.room_to_dict(room)["players"]}
        assert by_id[u1["id"]]["reconnect_left"] is None        # 在线 → None
        left = by_id[u2["id"]]["reconnect_left"]
        assert isinstance(left, int) and 28 <= left <= 30, f"剩余秒数异常: {left}"
        # 重连后归 None（客户端据此收起倒计时）
        rm.handle_player_reconnect(code, u2["id"])
        by_id = {p["id"]: p for p in rm.room_to_dict(room)["players"]}
        assert by_id[u2["id"]]["reconnect_left"] is None
    finally:
        loop.close()
        rm.set_disconnect_timeout(120)


def test_reconnect_left_only_during_play():
    """等待房没有「掉线」概念，也就不该给倒计时（界面等待房不显示断线）。"""
    room, code, u1, u2 = _start_two_player("gomoku", "dcleft2")
    rm.surrender(code, u1["id"])
    assert room["status"] == "waiting"
    p2 = next(p for p in room["players"] if p["id"] == u2["id"])
    p2["connected"] = False
    assert rm.reconnect_seconds_left(room, p2) is None


def test_two_player_game_leaver_room_back_to_waiting():
    """2 人对战中途退出必然终局、房间回到等待态——此时 sync 根本不带 game，
    所以走不到 in_game 判断（两种机制合起来覆盖全部情况）。"""
    room, code, u1, u2 = _start_two_player("gomoku", "gomokuout")
    action, _ = rm.leave_room(code, u1["id"])
    assert action == "game_over"
    assert room["status"] == "waiting"
    sync = _build_sync(room, u2["id"])
    assert sync["game"] is None
    assert sync["in_game"] is False
    assert room["game_state"]["winner"] in ("black", "white", "draw")


# ── 2048 多人独立竞速（1-4 人） ─────────────────────────────


def _start_2048_room(n: int, tag: str):
    """建 2048 房并开局，返回 (room, code, [users])。"""
    users = [_make_user(f"{tag}_{i}") for i in range(n)]
    room = rm.create_room(users[0], None, None, "2048")
    code = room["code"]
    for u in users[1:]:
        rm.join_room(code, u, None)
    for p in room["players"]:
        if p["id"] != users[0]["id"]:
            p["ready"] = True
    assert rm.start_game(code, users[0]["id"]) is not None
    return room, code, users


def _pin_2048_board(gs: dict, pid: int, row0: list[int]):
    """把某人的 2048 盘面摆成确定局面。

    2048 开局盘是**随机出块**的，直接断言「某个方向一定能走」会 flaky——
    两张初始方块恰好都落在第 0 列时，「左移」就是空操作（ok=False）。
    **测试不要依赖随机性**。
    """
    gs["boards"][pid] = [list(row0), [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]


def test_2048_supports_one_to_four_players():
    """2048 房间上限 4 人，1 人也能开局（单人刷分）。"""
    solo, code, _ = _start_2048_room(1, "g2048solo")
    assert solo["status"] == "playing"
    gs = solo["game_state"]
    assert len(gs["boards"]) == 1
    # 单人卡死后直接结算
    pid = next(iter(gs["boards"]))
    gs["boards"][pid] = [[2, 4, 8, 16], [32, 64, 128, 256],
                         [512, 1024, 2, 4], [8, 16, 32, 64]]
    rm._finish_2048_player(gs, pid, "stuck")
    go = rm._maybe_finish_2048(solo)
    assert go and solo["status"] == "waiting"

    room4, code4, users4 = _start_2048_room(4, "g2048four")
    assert len(room4["game_state"]["boards"]) == 4
    assert room4["max_players"] == 4


def test_2048_reach_2048_does_not_end_others():
    """有人合成 2048 只结束他自己，其他人继续玩自己的盘。"""
    room, code, users = _start_2048_room(3, "g2048keep")
    gs = room["game_state"]
    # 把 0 号玩家盘面摆成「左移一步即合成 2048」
    gs["boards"][users[0]["id"]] = [
        [1024, 1024, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0],
    ]
    result = rm.game2048_make_move(code, users[0]["id"], "left")
    assert result["ok"] and result["player_finished"] == "reach_2048"
    assert result["game_over"] is None, "还有人在玩，不应结算"
    assert room["status"] == "playing"
    assert gs["game_over_flags"][users[0]["id"]] is True
    assert gs["game_over_flags"][users[1]["id"]] is False
    # 已结束的人不能再走
    assert rm.game2048_make_move(code, users[0]["id"], "left")["ok"] is False
    # 其他人照常走（先摆成确定盘面，别依赖随机出块）
    _pin_2048_board(gs, users[1]["id"], [0, 0, 2, 0])
    assert rm.game2048_make_move(code, users[1]["id"], "left")["ok"] is True


def test_2048_ranking_orders_by_reach_then_score():
    """排名：合成 2048 的排最前（按达成先后），其余按分数降序。"""
    room, code, users = _start_2048_room(3, "g2048rank")
    gs = room["game_state"]
    a, b, c = (u["id"] for u in users)
    gs["scores"] = {a: 500, b: 9000, c: 300}
    rm._finish_2048_player(gs, b, "reach_2048")   # 先达成
    rm._finish_2048_player(gs, a, "reach_2048")   # 后达成
    rm._finish_2048_player(gs, c, "stuck")
    go = rm._maybe_finish_2048(room)
    assert go is not None
    order = [r["user_id"] for r in go["ranking"]]
    assert order == [b, a, c], f"排名顺序错误: {order}"
    assert go["ranking"][0]["rank"] == 1


def test_2048_leave_only_finishes_leaver():
    """2048 中途退出只结束本人（垫底），其他人继续。"""
    room, code, users = _start_2048_room(2, "g2048leave")
    action, updated = rm.leave_room(code, users[0]["id"])
    assert action == "removed"
    assert room["status"] == "playing"
    assert room["game_state"]["finish_reasons"][users[0]["id"]] == "surrender"
    _pin_2048_board(room["game_state"], users[1]["id"], [0, 0, 2, 0])
    assert rm.game2048_make_move(code, users[1]["id"], "left")["ok"] is True
    # 剩下的玩家再退出 → 全员结束 → 结算
    action2, _ = rm.leave_room(code, users[1]["id"])
    assert action2 == "game_over"
    assert room["status"] == "waiting"


def test_can_be_in_multiple_rooms_at_once():
    """一个人可以同时待在多个房间（v1.7 起去掉了「只能在一个等待房」的限制）。

    旧限制只在 create_room / join_room 拦一道「你已在房间中」，却反复惹麻烦：
    等待房掉线有 60s 宽限期，人早退回大厅了、座位还挂着，这 60 秒里他建不了也
    进不了任何房（用户实测报过）。现在直接允许并存——房间按 (房间, 玩家) 维度
    管理连接与回收，多房并存不需要额外处理。
    """
    u1 = _make_user("multi_h")
    u2 = _make_user("multi_p")
    room_a = rm.create_room(u1, None, None, "gomoku")
    rm.join_room(room_a["code"], u2, None)
    for p in room_a["players"]:
        p["ready"] = True
        p["connected"] = True
    rm.start_game(room_a["code"], u1["id"])
    rm.surrender(room_a["code"], u1["id"])
    assert room_a["status"] == "waiting"
    # u1 还坐在打完的房间 A 里，照样能再建一个 B
    room_b = rm.create_room(u1, None, None, "reversi")
    assert room_b["code"] != room_a["code"]
    # 也能加入别人的房
    room_c = rm.create_room(u2, None, None, "reversi")
    assert rm.join_room(room_c["code"], u1, None) is not None
    # 三个房都在，互不影响
    assert rm.get_room(room_a["code"]) is not None
    assert rm.get_room(room_b["code"]) is not None
    assert rm.get_room(room_c["code"]) is not None


# ── 被新连接顶替的旧连接关闭 ≠ 掉线 ─────────────────────────────


def test_ws_superseded_socket_close_keeps_player_online(client):
    """旧连接（对局页）被新连接（房间页）顶替后关闭，不能把玩家标成掉线。

    症状：对局结束点「返回房间」时房间页 socket 先重连并置 connected=True，
    随后旧的对局页 socket 才关闭；旧连接的关闭事件若无条件走掉线流程，就会把
    刚置上的在线态覆盖成掉线——房间列表/房间里概率性显示「断线」。
    """
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"sup{ts}a")
    room = _create_room(client, t1)
    code = room["code"]

    ws_old = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws_old.__enter__()
    _recv_type(ws_old, "sync")

    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws_new:
        _recv_type(ws_new, "sync")
        # 旧连接在「新连接已注册」之后才关闭——这正是返回房间时的真实顺序
        ws_old.__exit__(None, None, None)

        r = client.get(f"/api/rooms/{code}", headers={"Authorization": f"Bearer {t1}"})
        assert r.status_code == 200
        assert r.json()["players"][0]["connected"] is True, \
            "旧连接关闭把新连接的在线态覆盖成了掉线"


def test_ws_superseded_socket_close_does_not_start_dc_timer(client):
    """游戏中同理：顶替后的旧连接关闭不应广播掉线、也不应启动 120s 判负计时。"""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"supd{ts}a")
    t2, uid2 = _register(client, f"supd{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)

    ws_old = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws_old.__enter__()
    _recv_type(ws_old, "sync")
    with client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
        _recv_type(ws2, "sync")
        ws_old.send_json({"type": "ready"})
        _recv_type(ws2, "lobby", max_skip=5)
        ws2.send_json({"type": "ready"})
        ws_old.send_json({"type": "start"})
        _recv_type(ws_old, "game_start", max_skip=5)

        with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws_new:
            _recv_type(ws_new, "sync")
            ws_old.__exit__(None, None, None)

            r = client.get(f"/api/rooms/{code}", headers={"Authorization": f"Bearer {t1}"})
            assert r.json()["players"][0]["connected"] is True
            assert rm.get_room(code)["status"] == "playing"
            # 没有为 uid1 挂上掉线计时任务
            assert (code, uid1) not in rm._dc_tasks


# ── 一个人可以同时在多个房间（v1.7 去掉了「只能在一个等待房」的限制） ─────────
# 旧限制只在 create_room / join_room 拦一道「你已在房间中」，却反复惹麻烦：
# 等待房掉线有 60s 宽限期，人早退回大厅了、座位还挂着，这 60 秒里他建不了也
# 进不了任何房（用户实测报过）。房间本身按 (房间, 玩家) 维度管理连接与回收，
# 多房并存不需要额外处理。


def test_create_second_room_while_in_waiting_room(client):
    """坐在一个等待房里也能再建一个——不再报「你已在房间中」。"""
    t1, uid1 = _register(client, _u("multi_a"))
    first = _create_room(client, t1, game="gomoku")
    second = _create_room(client, t1, game="reversi")
    assert second["code"] != first["code"]
    # 两个房都在，自己都还是成员
    assert rm.get_room(first["code"]) is not None
    assert rm.get_room(second["code"]) is not None
    assert any(p["id"] == uid1 for p in rm.get_room(first["code"])["players"])


def test_join_other_room_while_in_waiting_room(client):
    """也能去加入别人的房，原房不受影响。"""
    t1, uid1 = _register(client, _u("multi_b"))
    t2, _ = _register(client, _u("multi_c"))
    own = _create_room(client, t1, game="gomoku")
    target = _create_room(client, t2, game="ludo")
    joined = _join_room(client, t1, target["code"])
    assert any(p["id"] == uid1 for p in joined["players"])
    assert rm.get_room(own["code"]) is not None


def test_offline_seat_does_not_block_creating(client):
    """掉线后座位还挂着的旧房同样不拦——这正是旧限制最坑的场景。"""
    t1, uid1 = _register(client, _u("multi_d"))
    old = _create_room(client, t1, game="gomoku")
    for p in rm.get_room(old["code"])["players"]:
        if p["id"] == uid1:
            p["connected"] = False
    rm.get_room(old["code"])["empty_since"] = time.time()
    new_room = _create_room(client, t1, game="dots")
    assert new_room["code"] != old["code"]
