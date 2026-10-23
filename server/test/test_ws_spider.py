"""蜘蛛纸牌普通房间、共享牌桌、冲突、续玩与全队战绩的真实 WS 回归。"""
from contextlib import ExitStack
from copy import deepcopy

import pytest
from starlette.websockets import WebSocketDisconnect

from app import database, persistence, room_manager as rm, security, ws as ws_module
from app.game import spider


@pytest.fixture
def coop_client(ws_client, monkeypatch, tmp_path):
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


def recv(ws, kind):
    for _ in range(20):
        message = ws.receive_json()
        if message["type"] == kind:
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
        recv(ws, "lobby")
    sockets[0].send_json({"type": "start"})
    views = [recv(ws, "game_start")["game"] for ws in sockets]
    assert views[0] == views[1]
    assert len(views[0]["columns"]) == 15 and views[0]["total_runs"] == 12
    return views[0]


def test_shared_board_conflicts_privacy_and_resume(coop_client):
    client = coop_client
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
        assert sum(v is None for c in view["columns"] for v in c) == 66
        assert not any(key in view for key in ("stock", "history", "face_up"))
        move = view["legal_moves"][0]
        for ws in sockets:
            ws.send_json({"type": "spider_action", "action": "move", "revision": 0, **move})
        first = recv(sockets[0], "spider_state")["game"]
        second = recv(sockets[1], "spider_state")["game"]
        assert first == second and first["revision"] == 1 and first["move_count"] == 1
        assert "更新" in recv(sockets[1], "error")["message"]
        assert recv(sockets[1], "spider_state")["game"] == first
        sockets[1].send_json({"type": "spider_action", "action": "undo", "revision": 1})
        restored = [recv(ws, "spider_state")["game"] for ws in sockets]
        assert restored[0] == restored[1] and restored[0]["columns"] == view["columns"]
        sockets[1].send_json({"type": "spider_action", "action": "deal", "revision": 2})
        dealt = [recv(ws, "spider_state")["game"] for ws in sockets]
        assert dealt[0] == dealt[1] and dealt[0]["stock_count"] == 60
        saved = deepcopy(rm.get_room(code)["game_state"])
        with client.websocket_connect(f"/ws/room/{code}?token={host[0]}") as reconnected:
            assert recv(reconnected, "sync")["game"] == dealt[0]
        assert rm.get_room(code)["game_state"] == saved
        assert client.get(f"/api/rooms/{code}", headers=partner[1]).json()["in_game"]


def test_cooperative_win_history_and_lobby_restart(coop_client):
    client = coop_client
    with ExitStack() as stack:
        room, host, partner, sockets = pair(client, stack)
        code = room["code"]
        start_pair(sockets)
        gs = rm.get_room(code)["game_state"]
        gs.update(columns=[list(range(13, 1, -1)), [1]] + [[] for _ in range(13)],
                  face_up=[0] * 15, stock=[], completed=11)
        sockets[1].send_json({"type": "spider_action", "action": "move", "revision": 0,
                              "source": 1, "index": 0, "target": 0})
        views = [recv(ws, "spider_state")["game"] for ws in sockets]
        assert views[0] == views[1] and views[0]["completed"] == 12 and views[0]["finished"]
        for ws in sockets:
            assert recv(ws, "game_over")["reason"] == "completed"
        for player in (host, partner):
            records = database.get_user_history(player[2])["items"]
            assert len(records) == 1 and records[0]["snapshot"]["total_runs"] == 12
            assert [p["result"] for p in records[0]["results"]] == ["win", "win"]
        sockets[0].send_json({"type": "start"})
        recv(sockets[0], "error")
        next_view = start_pair(sockets)
        assert next_view["revision"] == 2
        before = deepcopy(rm.get_room(code)["game_state"])
        sockets[1].send_json({"type": "spider_action", "action": "end", "revision": 2})
        assert "房主" in recv(sockets[1], "error")["message"]
        recv(sockets[1], "spider_state")
        assert rm.get_room(code)["game_state"] == before
        sockets[0].send_json({"type": "spider_action", "action": "end", "revision": 2})
        for ws in sockets:
            assert recv(ws, "spider_state")["game"]["finished"]
            assert recv(ws, "game_over")["reason"] == "abandoned"
        assert rm.get_room(code)["status"] == "waiting"


def test_leaving_host_transfers_room_and_partner_continues(coop_client):
    with ExitStack() as stack:
        room, host, partner, sockets = pair(coop_client, stack)
        start_pair(sockets)
        assert coop_client.post(f"/api/rooms/{room['code']}/leave", headers=host[1]).status_code == 200
        view = recv(sockets[1], "spider_state")["game"]
        assert host[2] in view["departed"] and not view["finished"]
        assert recv(sockets[1], "lobby")["room"]["host_id"] == partner[2]
        sockets[1].send_json({"type": "spider_action", "action": "deal", "revision": view["revision"]})
        assert recv(sockets[1], "spider_state")["game"]["stock_count"] == 60
        assert not coop_client.get(f"/api/rooms/{room['code']}", headers=host[1]).json()["in_game"]


@pytest.mark.parametrize("count,total,columns", [(1, 8, 10), (3, 16, 20), (4, 16, 20)])
def test_start_broadcast_scales_with_actual_members(coop_client, count, total, columns):
    with ExitStack() as stack:
        players = [register(coop_client, f"spider_scale_{i}") for i in range(count)]
        room = coop_client.post("/api/rooms", json={"game": "spider"}, headers=players[0][1]).json()
        code = room["code"]
        for player in players[1:]:
            assert coop_client.post(f"/api/rooms/{code}/join", json={}, headers=player[1]).status_code == 200
        sockets = [stack.enter_context(coop_client.websocket_connect(f"/ws/room/{code}?token={p[0]}")) for p in players]
        for ws in sockets:
            recv(ws, "sync")
        for ws in sockets[1:]:
            ws.send_json({"type": "ready"})
        sockets[0].send_json({"type": "start"})
        views = [recv(ws, "game_start")["game"] for ws in sockets]
        assert all(view == views[0] for view in views)
        assert views[0]["total_runs"] == total and len(views[0]["columns"]) == columns
        if count == 1:
            assert coop_client.post(f"/api/rooms/{code}/leave", headers=players[0][1]).status_code == 200
            saved = rm.get_room(code)
            assert saved and saved["status"] == "waiting" and len(saved["players"]) == 1
