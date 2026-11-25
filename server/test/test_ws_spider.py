"""蜘蛛纸牌独立牌桌、断线续玩与个人战绩的真实 WS 回归。"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app import database, persistence, room_manager as rm, security, ws as ws_module
from app.game import spider


@pytest.fixture
def spider_client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "spider.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "spider-protocol-test-secret-for-isolated-db")
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    original = spider.create_game
    monkeypatch.setattr(spider, "create_game", lambda **kwargs: original(5, **kwargs))
    persistence.init()
    return ws_client


def register(client, name):
    response = client.post("/api/auth/register", json={"username": name, "password": "test123456"})
    assert response.status_code == 200
    data = response.json()
    return data["token"], {"Authorization": f"Bearer {data['token']}"}, data["user"]["id"]


def recv(ws, kind, matches=lambda _: True):
    for _ in range(20):
        message = ws.receive_json()
        if message["type"] == kind and matches(message):
            return message
    raise AssertionError(f"Missing message: {kind}")


def pair(client, stack):
    host = register(client, "spider_host")
    partner = register(client, "spider_partner")
    room = client.post("/api/rooms", json={"game": "spider"}, headers=host[1]).json()
    code = room["code"]
    assert client.post(f"/api/rooms/{code}/join", json={}, headers=partner[1]).status_code == 200
    sockets = [stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={p[0]}"))
               for p in (host, partner)]
    for ws in sockets:
        assert recv(ws, "sync")["game"] is None
    return room, host, partner, sockets


def start_pair(sockets):
    sockets[1].send_json({"type": "ready"})
    for ws in sockets:
        recv(ws, "lobby", lambda msg: msg["room"]["players"][1]["ready"])
    sockets[0].send_json({"type": "start"})
    views = [recv(ws, "game_start")["game"] for ws in sockets]
    assert views[0] == views[1]
    return views[0]


def pending_messages(ws):
    ws.send_json({"type": "ping"})
    messages = []
    for _ in range(20):
        message = ws.receive_json()
        if message["type"] == "pong":
            return messages
        messages.append(message)
    raise AssertionError("Missing pong")


def test_independent_actions_privacy_and_resume(spider_client):
    client = spider_client
    with ExitStack() as stack:
        room, host, partner, sockets = pair(client, stack)
        code = room["code"]
        outsider = register(client, "spider_outside")
        assert room["max_players"] == 4
        assert client.get("/api/rooms?game=spider", headers=outsider[1]).json()["items"]
        assert client.post(f"/api/rooms/{code}/leave", headers=outsider[1]).status_code == 403
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(f"/ws/room/{code}?token={outsider[0]}"):
                pass
        assert rejected.value.code == 4003
        sockets[0].send_json({"type": "start"})
        recv(sockets[0], "error")
        view = start_pair(sockets)
        assert not any(key in view for key in ("stock", "history", "face_up"))
        move = view["legal_moves"][0]
        sockets[0].send_json({"type": "spider_action", "action": "move", "revision": 0, **move})
        moved = recv(sockets[0], "spider_state")["game"]
        assert moved["revision"] == 1 and moved["columns"] != view["columns"]
        assert not any("game" in msg for msg in pending_messages(sockets[1]))
        assert len(view["columns"]) == 10 and view["total_runs"] == 8
        assert sum(v is None for c in view["columns"] for v in c) == 44

        sockets[1].send_json({"type": "spider_action", "action": "deal", "revision": 0})
        dealt = recv(sockets[1], "spider_state")["game"]
        assert dealt["revision"] == 1 and dealt["stock_count"] == 40
        assert [col[:-1] for col in dealt["columns"]] == view["columns"]
        assert not any("game" in msg for msg in pending_messages(sockets[0]))

        sockets[0].send_json({"type": "spider_action", "action": "deal", "revision": 0})
        assert "更新" in recv(sockets[0], "error")["message"]
        assert recv(sockets[0], "spider_state")["game"] == moved
        sockets[0].send_json({"type": "spider_action", "action": "undo", "revision": 1})
        restored = recv(sockets[0], "spider_state")["game"]
        assert restored["revision"] == 2 and restored["columns"] == view["columns"]
        with client.websocket_connect(f"/ws/room/{code}?token={partner[0]}") as reconnected:
            assert recv(reconnected, "sync")["game"] == dealt
        assert client.get(f"/api/rooms/{code}", headers=partner[1]).json()["in_game"]


def test_personal_win_history_and_lobby_restart(spider_client):
    client = spider_client
    with ExitStack() as stack:
        room, host, partner, sockets = pair(client, stack)
        code = room["code"]
        initial = start_pair(sockets)
        board = rm.get_room(code)["game_state"]["boards"][host[2]]
        board.update(columns=[list(range(13, 1, -1)), [1]] + [[] for _ in range(8)],
                     face_up=[0] * 10, stock=[], completed=7)
        sockets[0].send_json({"type": "spider_action", "action": "move", "revision": 0,
                              "source": 1, "index": 0, "target": 0})
        won = recv(sockets[0], "spider_state")["game"]
        assert won["completed"] == 8 and won["finished"]
        messages = pending_messages(sockets[1])
        assert not any("game" in msg or msg["type"] == "game_over" for msg in messages)
        progress = [msg["room"]["players"][0].get("spider_progress") for msg in messages if msg["type"] == "lobby"]
        assert {"completed": 8, "total_runs": 8, "reason": "completed"} in progress
        assert database.get_user_history(host[2])["items"] == []
        with client.websocket_connect(f"/ws/room/{code}?token={host[0]}") as reconnected:
            sync = recv(reconnected, "sync")
            assert not sync["in_game"] and sync["game"] == won

        sockets[1].send_json({"type": "spider_action", "action": "deal", "revision": 0})
        deal = recv(sockets[1], "spider_state")["game"]
        assert deal["stock_count"] == 40
        sockets[1].send_json({"type": "spider_action", "action": "end", "revision": 1})
        ended = recv(sockets[1], "spider_state")["game"]
        assert ended["finished"] and ended["reason"] == "abandoned"
        assert recv(sockets[1], "game_over")["reason"] == "all_finished"
        for player, own_view in ((host, won), (partner, ended)):
            records = client.get("/api/history/mine", headers=player[1]).json()["items"]
            assert len(records) == 1
            record = records[0]
            assert record["snapshot"]["columns"] == own_view["columns"]
            assert "boards" not in record["snapshot"]
            assert [p["result"] for p in record["results"]] == ["win", "end"]
            share = client.post(f"/api/history/{record['id']}/share", headers=player[1]).json()
            public = client.get(f"/api/history/shared/{share['token']}").json()
            assert public["snapshot"]["columns"] == own_view["columns"]
            assert "boards" not in public["snapshot"]

        with client.websocket_connect(f"/ws/room/{code}?token={host[0]}") as reconnected:
            assert recv(reconnected, "sync")["game"] is None
            sockets[0] = reconnected
            sockets[0].send_json({"type": "start"})
            recv(sockets[0], "error")
            next_view = start_pair(sockets)
            assert next_view["revision"] == 3 and next_view["columns"] == initial["columns"]
            sockets[1].send_json({"type": "spider_action", "action": "end", "revision": 2})
            assert "更新" in recv(sockets[1], "error")["message"]
            assert recv(sockets[1], "spider_state")["game"] == next_view


def test_leaving_host_transfers_room_and_partner_continues(spider_client):
    with ExitStack() as stack:
        room, host, partner, sockets = pair(spider_client, stack)
        view = start_pair(sockets)
        assert spider_client.post(f"/api/rooms/{room['code']}/leave", headers=host[1]).status_code == 200
        lobby = recv(sockets[1], "lobby", lambda msg: msg["room"]["host_id"] == partner[2])["room"]
        assert lobby["players"][0]["spider_progress"]["reason"] == "abandoned"
        sockets[1].send_json({"type": "spider_action", "action": "deal", "revision": view["revision"]})
        assert recv(sockets[1], "spider_state")["game"]["stock_count"] == 40
        assert not spider_client.get(f"/api/rooms/{room['code']}", headers=host[1]).json()["in_game"]
        with spider_client.websocket_connect(f"/ws/room/{room['code']}?token={host[0]}") as lobby_ws:
            assert not recv(lobby_ws, "sync")["in_game"]
            sockets[1].send_json({"type": "leave"})
            assert recv(lobby_ws, "game_over")["reason"] == "all_finished"
            assert recv(lobby_ws, "lobby")["room"]["status"] == "waiting"


def test_slow_spider_lobby_cannot_overwrite_round_end(spider_client):
    host = register(spider_client, "spider_slow_host")
    peer = register(spider_client, "spider_slow_peer")
    room = spider_client.post("/api/rooms", json={"game": "spider"}, headers=host[1]).json()
    code = room["code"]
    spider_client.post(f"/api/rooms/{code}/join", json={}, headers=peer[1]).raise_for_status()
    current = rm.get_room(code)
    current["players"][1]["ready"] = True
    assert rm.start_game(code, host[2]) is not None

    async def check_broadcasts():
        blocked, unblock = asyncio.Event(), asyncio.Event()
        received = []

        async def slow_send(message):
            blocked.set()
            await unblock.wait()

        async def send(message):
            received.append(message["room"]["status"])

        connections = {host[2]: SimpleNamespace(send_json=slow_send),
                       peer[2]: SimpleNamespace(send_json=send)}
        ws_module._connections[code] = connections
        previous_lobby = {"type": "lobby", "room": rm.room_to_dict(current)}
        delayed = asyncio.create_task(ws_module._broadcast(code, previous_lobby))
        try:
            await asyncio.wait_for(blocked.wait(), timeout=2)
            connections.pop(host[2])  # 旧连接仍在发送，终局走其余连接
            for account in (host, peer):
                result = rm.spider_action(code, account[2], {"action": "end", "revision": 0})
                assert result["game"]["finished"]
            await ws_module._broadcast(code, {"type": "lobby", "room": rm.room_to_dict(current)})
        finally:
            unblock.set()
            await delayed
        assert received and set(received) == {"waiting"}

    spider_client.portal.call(check_broadcasts)


@pytest.mark.parametrize("count", [1, 2])
def test_inactive_spider_survives_disconnect_and_resumes(spider_client, count):
    """思考或切后台可以续玩，无人在线的旧房仍按公共 TTL 回收。"""
    import time

    players = [register(spider_client, f"spider_idle_{i}") for i in range(count)]
    room = spider_client.post("/api/rooms", json={"game": "spider"}, headers=players[0][1]).json()
    code = room["code"]
    for player in players[1:]:
        spider_client.post(f"/api/rooms/{code}/join", json={}, headers=player[1])
    with ExitStack() as stack:
        sockets = [stack.enter_context(spider_client.websocket_connect(f"/ws/room/{code}?token={p[0]}"))
                   for p in players]
        for ws in sockets:
            recv(ws, "sync")
        for ws in sockets[1:]:
            ws.send_json({"type": "ready"})
        sockets[0].send_json({"type": "start"})
        for ws in sockets:
            recv(ws, "game_start")
        sockets[0].send_json({"type": "spider_action", "action": "deal", "revision": 0})
        dealt = recv(sockets[0], "spider_state")["game"]
        saved = deepcopy(rm.get_room(code)["game_state"])
        rm.get_room(code)["last_activity"] = time.time() - rm.ROOM_IDLE_TTL - 10
        assert code not in rm.sweep_idle_rooms()

    current = rm.get_room(code)
    for player in players:
        # 模拟旧计时任务到期，也不能把暂时离线的成员记为退出。
        rm._on_dc_timeout(code, player[2])
    assert current["status"] == "playing"
    assert current["game_state"] == saved
    assert current["empty_since"] is not None
    assert not any((code, player[2]) in rm._dc_tasks for player in players)
    assert all(p["reconnect_left"] is None for p in rm.room_to_dict(current)["players"])
    with spider_client.websocket_connect(f"/ws/room/{code}?token={players[0][0]}") as ws:
        assert recv(ws, "sync")["game"] == dealt
        assert current["empty_since"] is None
        ws.send_json({"type": "spider_action", "action": "undo", "revision": dealt["revision"]})
        assert recv(ws, "spider_state")["game"]["move_count"] == 0
    current["empty_since"] = time.time() - rm.ROOM_IDLE_TTL - 10
    assert code in rm.sweep_idle_rooms()


def test_host_ends_round_and_everyone_gets_final_state(spider_client):
    client = spider_client
    with ExitStack() as stack:
        room, host, partner, sockets = pair(client, stack)
        code = room["code"]
        start_pair(sockets)
        sockets[0].send_json({"type": "spider_action", "action": "end_round", "revision": 0})
        states = [recv(ws, "spider_state")["game"] for ws in sockets]
        assert all(state["finished"] and state["reason"] == "abandoned" for state in states)
        for ws in sockets:
            assert recv(ws, "game_over")["reason"] == "all_finished"
        assert rm.get_room(code)["status"] == "waiting"
        for player in (host, partner):
            records = client.get("/api/history/mine", headers=player[1]).json()["items"]
            assert len(records) == 1
            assert [entry["result"] for entry in records[0]["results"]] == ["end", "end"]
            share = client.post(f"/api/history/{records[0]['id']}/share", headers=player[1]).json()
            public = client.get(f"/api/history/shared/{share['token']}").json()
            assert public["snapshot"]["finished"]
            assert "boards" not in public["snapshot"]


def test_non_host_cannot_end_round_from_ws(spider_client):
    client = spider_client
    with ExitStack() as stack:
        room, _host, _partner, sockets = pair(client, stack)
        start_pair(sockets)
        sockets[1].send_json({"type": "spider_action", "action": "end_round", "revision": 0})
        assert "房主" in recv(sockets[1], "error")["message"]
        recv(sockets[1], "spider_state")
        assert rm.get_room(room["code"])["status"] == "playing"


@pytest.mark.parametrize("count", [1, 3, 4])
def test_start_uses_standard_board_for_every_member(spider_client, count):
    with ExitStack() as stack:
        players = [register(spider_client, f"spider_scale_{i}") for i in range(count)]
        room = spider_client.post("/api/rooms", json={"game": "spider"}, headers=players[0][1]).json()
        code = room["code"]
        for player in players[1:]:
            assert spider_client.post(f"/api/rooms/{code}/join", json={}, headers=player[1]).status_code == 200
        sockets = [stack.enter_context(spider_client.websocket_connect(f"/ws/room/{code}?token={p[0]}")) for p in players]
        for ws in sockets:
            recv(ws, "sync")
        for ws in sockets[1:]:
            ws.send_json({"type": "ready"})
        if count > 1:
            recv(sockets[0], "lobby", lambda msg: all(p["ready"] for p in msg["room"]["players"][1:]))
        sockets[0].send_json({"type": "start"})
        views = [recv(ws, "game_start")["game"] for ws in sockets]
        assert all(view == views[0] for view in views)
        assert views[0]["total_runs"] == 8 and len(views[0]["columns"]) == 10
        if count == 1:
            assert spider_client.post(f"/api/rooms/{code}/leave", headers=players[0][1]).status_code == 200
            saved = rm.get_room(code)
            assert saved and saved["status"] == "waiting" and len(saved["players"]) == 1
