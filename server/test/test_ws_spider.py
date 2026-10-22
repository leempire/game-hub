"""单人牌局的真实 WS 协议、账号隔离、重开战绩与断线续玩。"""
from copy import deepcopy

import pytest
from starlette.websockets import WebSocketDisconnect

from app import database, persistence, room_manager as rm, security, ws as ws_module
from app.game import spider


@pytest.fixture
def solo_client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "spider.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "spider-protocol-test-secret-for-isolated-db")
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    persistence.init()
    return ws_client


def register(client, name):
    response = client.post("/api/auth/register", json={"username": name, "password": "test123456"})
    assert response.status_code == 200
    data = response.json()
    return data["token"], {"Authorization": f"Bearer {data['token']}"}, data["user"]["id"]


def recv(ws, kind):
    for _ in range(12):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"Missing message: {kind}")


def test_private_game_protocol_and_resume(solo_client):
    client = solo_client
    token, headers, uid = register(client, "spider_owner")
    other_token, other_headers, _ = register(client, "spider_other")
    room = client.post("/api/rooms", json={"game": "spider"}, headers=headers).json()
    code = room["code"]
    assert room["max_players"] == 1
    assert client.get(f"/api/rooms/{code}", headers=other_headers).status_code == 403
    assert client.post(f"/api/rooms/{code}/leave", headers=other_headers).status_code == 403
    assert client.post(f"/api/rooms/{code}/join", json={}, headers=other_headers).status_code == 409
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(f"/ws/room/{code}?token={other_token}"):
            pass
    assert rejected.value.code == 4003
    with client.websocket_connect(f"/ws/room/{code}?token={token}") as ws:
        assert recv(ws, "sync")["game"] is None
        ws.send_json({"type": "start"})
        view = recv(ws, "game_start")["game"]
        assert view["stock_count"] == 50 and "stock" not in view
        ws.send_json({"type": "spider_action", "action": "deal", "revision": view["revision"]})
        after = recv(ws, "spider_state")["game"]
        assert after["stock_count"] == 40 and after["move_count"] == 1
        ws.send_json({"type": "spider_action", "action": "undo", "revision": view["revision"]})
        assert "更新" in recv(ws, "error")["message"]
        assert recv(ws, "spider_state")["game"] == after
        ws.send_json({"type": "spider_action", "action": "undo", "revision": after["revision"]})
        saved = recv(ws, "spider_state")["game"]
        assert saved["columns"] == view["columns"] and saved["stock_count"] == 50
    assert (code, uid) not in rm._dc_tasks
    rm.get_room(code)["empty_since"] = 0
    assert code not in rm.sweep_idle_rooms()
    assert client.get("/api/rooms", headers=other_headers).json()["items"] == []
    assert client.post("/api/rooms", json={"game": "spider"}, headers=headers).json()["code"] == code
    saved_before_leave = deepcopy(rm.get_room(code)["game_state"])
    assert client.post(f"/api/rooms/{code}/leave", headers=headers).status_code == 200
    assert rm.get_room(code)["game_state"] == saved_before_leave
    before = deepcopy(rm.get_room(code)["game_state"])
    rm._rooms.clear()
    assert rm.restore_rooms() == 1
    assert rm.get_room(code)["game_state"] == before
    with client.websocket_connect(f"/ws/room/{code}?token={token}") as ws:
        assert recv(ws, "sync")["game"] == saved


def test_win_history_and_restart(solo_client):
    client = solo_client
    token, headers, _ = register(client, "spider_winner")
    room = client.post("/api/rooms", json={"game": "spider"}, headers=headers).json()
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={token}") as ws:
        recv(ws, "sync")
        ws.send_json({"type": "start"})
        recv(ws, "game_start")
        game = spider.create_game(1)
        game.update(columns=[list(range(13, 1, -1)), [1]] + [[] for _ in range(8)],
                    face_up=[0] * 10, stock=[], completed=7)
        rm.get_room(code)["game_state"] = game
        ws.send_json({"type": "spider_action", "action": "move", "source": 1, "index": 0,
                      "target": 0, "revision": 0})
        view = recv(ws, "spider_state")["game"]
        assert view["completed"] == 8 and view["finished"]
        records = database.get_user_history(room["host_id"])["items"]
        assert len(records) == 1
        assert records[0]["snapshot"]["completed"] == 8
        assert records[0]["results"][0]["result"] == "win"
        ws.send_json({"type": "spider_action", "action": "new", "revision": 1})
        fresh = recv(ws, "spider_state")["game"]
        assert fresh["stock_count"] == 50 and fresh["completed"] == 0
        assert len(database.get_user_history(room["host_id"])["items"]) == 1
        ws.send_json({"type": "spider_action", "action": "new", "revision": fresh["revision"]})
        recv(ws, "spider_state")
        records = database.get_user_history(room["host_id"])["items"]
        assert len(records) == 2 and records[0]["results"][0]["result"] == "end"
