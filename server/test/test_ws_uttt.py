"""终极井字棋真实房间/WS/战绩回归；每条用例独立数据库。"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import threading

import pytest

from app import database, persistence, room_manager as rm, security, ws as ws_module


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "uttt.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "isolated-uttt-test-secret-at-least-32-bytes")
    for name in ("_rooms", "_dc_tasks", "_bot_tasks"):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    return ws_client


def user(client, name):
    response = client.post("/api/auth/register", json={"username": name, "password": "test123456"})
    assert response.status_code == 200
    data = response.json()
    return data["user"]["id"], data["token"], {"Authorization": f"Bearer {data['token']}"}


def recv(ws, kind):
    for _ in range(30):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"missing {kind}")


def run_async(coroutine):
    # asyncio.run 会清空主线程默认循环，影响后续旧用例的 get_event_loop。
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def start_pair(client, stack):
    users = [user(client, "uttt_host"), user(client, "uttt_guest")]
    response = client.post("/api/rooms", json={"game": "uttt"}, headers=users[0][2])
    assert response.status_code == 200, response.text
    room = response.json()
    code = room["code"]
    assert client.post(f"/api/rooms/{code}/join", json={}, headers=users[1][2]).status_code == 200
    sockets = [stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={p[1]}")) for p in users]
    for ws in sockets:
        recv(ws, "sync")
    sockets[1].send_json({"type": "ready"})
    sockets[0].send_json({"type": "start"})
    games = [recv(ws, "game_start")["game"] for ws in sockets]
    assert [g["your_color"] for g in games] == ["black", "white"]
    assert all(len(g["legal"]) == 81 and g["active_board"] is None for g in games)
    return code, users, sockets


def test_move_rejection_revision_and_reconnect(client):
    with ExitStack() as stack:
        code, users, sockets = start_pair(client, stack)
        sockets[1].send_json({"type": "uttt_move", "board": 0, "cell": 0, "revision": 0})
        recv(sockets[1], "error")
        assert recv(sockets[1], "uttt_state")["game"]["revision"] == 0
        sockets[0].send_json({"type": "uttt_move", "board": 6, "cell": 2, "revision": 0})
        views = [recv(ws, "uttt_state")["game"] for ws in sockets]
        assert all(g["boards"][6][2] == 1 and g["active_board"] == 2 and g["revision"] == 1 for g in views)
        before = deepcopy(rm.get_room(code)["game_state"])
        for board, cell, revision in ((2, 0, 0), (True, 0, 1), (2, "0", 1), (9, 0, 1), (0, 0, 1)):
            sockets[1].send_json({"type": "uttt_move", "board": board, "cell": cell, "revision": revision})
            recv(sockets[1], "error")
            assert recv(sockets[1], "uttt_state")["game"]["revision"] == 1
            assert rm.get_room(code)["game_state"] == before
        with client.websocket_connect(f"/ws/room/{code}?token={users[1][1]}") as again:
            sync = recv(again, "sync")["game"]
            assert sync["boards"] == views[1]["boards"] and sync["revision"] == 1
            again.send_json({"type": "uttt_move", "board": 2, "cell": 4, "revision": 1})
            assert recv(again, "uttt_state")["game"]["active_board"] == 4


def test_natural_win_history_return_and_restart(client):
    with ExitStack() as stack:
        code, users, sockets = start_pair(client, stack)
        gs = rm.get_room(code)["game_state"]
        gs["boards"][0] = gs["boards"][1] = [1, 1, 1, 0, 0, 0, 0, 0, 0]
        gs["local_results"][:2] = [1, 1]
        gs["boards"][2] = [1, 1, 0, 2, 0, 0, 2, 0, 0]
        gs["active_board"] = 2
        sockets[0].send_json({"type": "uttt_move", "board": 2, "cell": 2, "revision": 0})
        for ws in sockets:
            assert recv(ws, "uttt_state")["game"]["winner"] == "black"
            assert recv(ws, "game_over")["winner"] == "black"
        room = rm.get_room(code)
        assert room["status"] == "waiting" and room["round"] == 1
        history = database.get_user_history(users[0][0])["items"]
        assert len(history) == 1 and history[0]["snapshot"]["game"] == "uttt"
        assert history[0]["snapshot"]["local_results"][:3] == [1, 1, 1]
        assert [entry["result"] for entry in history[0]["results"]] == ["win", "lose"]
        sockets[1].send_json({"type": "ready"})
        sockets[0].send_json({"type": "start"})
        fresh = recv(sockets[0], "game_start")["game"]
        assert fresh["revision"] > gs["revision"]
        sockets[0].send_json({"type": "uttt_move", "board": 2, "cell": 2, "revision": 0})
        recv(sockets[0], "error")
        assert recv(sockets[0], "uttt_state")["game"]["moves"] == []


@pytest.mark.parametrize("leaving_seat", [0, 1])
def test_http_leave_settles_both_players_and_notifies_opponent(client, leaving_seat):
    with ExitStack() as stack:
        code, users, sockets = start_pair(client, stack)
        leaving = users[leaving_seat]
        response = client.post(f"/api/rooms/{code}/leave", headers=leaving[2])
        assert response.status_code == 200
        for index, player in enumerate(users):
            history = database.get_user_history(player[0])["items"]
            assert len(history) == 1
            results = {p["username"]: p["result"] for p in history[0]["results"]}
            assert results["uttt_host" if index == 0 else "uttt_guest"] == ("lose" if index == leaving_seat else "win")
        remaining = sockets[1 - leaving_seat]
        result = recv(remaining, "game_over")
        assert result["winner"] == ("white" if leaving_seat == 0 else "black")
        assert result["reason"] == "surrender"
        lobby = recv(remaining, "lobby")["room"]
        assert lobby["status"] == "waiting" and len(lobby["players"]) == 1
        assert lobby["host_id"] == users[1 - leaving_seat][0]


def test_nonmember_http_leave_cannot_end_game(client):
    with ExitStack() as stack:
        code, _, _ = start_pair(client, stack)
        outsider = user(client, "uttt_outsider")
        response = client.post(f"/api/rooms/{code}/leave", headers=outsider[2])
        assert response.status_code == 403
        assert rm.get_room(code)["status"] == "playing"


def test_game_end_lobby_reads_members_after_leave(client, monkeypatch):
    with ExitStack() as stack:
        code, users, _ = start_pair(client, stack)
        messages = []

        async def capture_broadcast(room_code, message):
            messages.append(message)

        monkeypatch.setattr(ws_module, "_broadcast", capture_broadcast)
        monkeypatch.setattr(rm, "_game_end_listeners", [ws_module._broadcast_lobby_after_game_end])
        assert client.post(f"/api/rooms/{code}/leave", headers=users[0][2]).status_code == 200
        client.portal.call(asyncio.sleep, 0)
        lobbies = [msg["room"] for msg in messages if msg["type"] == "lobby"]
        assert lobbies
        assert all([p["id"] for p in lobby["players"]] == [users[1][0]] for lobby in lobbies)


def test_hot_restore_and_disconnect_timeout(client, monkeypatch):
    with ExitStack() as stack:
        code, users, sockets = start_pair(client, stack)
        sockets[0].send_json({"type": "uttt_move", "board": 0, "cell": 4, "revision": 0})
        for ws in sockets:
            recv(ws, "uttt_state")
        encoded = persistence.dumps_state(rm.get_room(code))
    rm._rooms.clear()
    monkeypatch.setattr(persistence, "load_all_rooms", lambda: [persistence.loads_state(encoded)])
    assert rm.restore_rooms() == 1
    restored = rm.get_room(code)
    assert restored["game_state"]["boards"][0][4] == 1 and restored["status"] == "playing"
    assert rm.handle_player_reconnect(code, users[0][0])
    assert restored["empty_since"] is None
    rm._on_dc_timeout(code, users[1][0])
    assert restored["status"] == "waiting"
    assert restored["game_state"]["winner"] == "black"
    assert restored["game_state"]["reason"] == "disconnect"


@pytest.mark.parametrize("difficulty", ["easy", "normal", "hard"])
def test_bot_difficulty_and_swapped_first_seat(client, difficulty):
    host = user(client, "uttt_bot_host")
    response = client.post("/api/rooms", json={"game": "uttt"}, headers=host[2])
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    rm.add_bot(code, host[0], difficulty)
    room = rm.get_room(code)
    bot = room["players"][1]
    assert bot["ai_difficulty"] == difficulty
    rm._swap_seats(room, host[0], bot["id"])
    gs = rm.start_game(code, host[0])
    assert gs["player_colors"][bot["id"]] == "black"
    run_async(ws_module._bot_uttt_move(code, bot["id"], room, gs))
    assert gs["revision"] == 1 and gs["next"] == "white"
    rm.surrender(code, host[0])
    record = database.get_user_history(host[0])["items"][0]
    saved_bot = next(p for p in record["players"] if p["username"] == bot["username"])
    assert saved_bot["ai_difficulty"] == difficulty


def test_stale_bot_search_cannot_move_in_next_game(client, monkeypatch):
    from app.game import uttt_ai
    host = user(client, "uttt_search_host")
    response = client.post("/api/rooms", json={"game": "uttt"}, headers=host[2])
    assert response.status_code == 200
    code = response.json()["code"]
    room = rm.add_bot(code, host[0], "hard")
    bot = room["players"][1]
    rm._swap_seats(room, host[0], bot["id"])
    gs = rm.start_game(code, host[0])
    started, release = threading.Event(), threading.Event()

    def searching(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return 4, 4

    monkeypatch.setattr(uttt_ai, "best_move", searching)

    async def exercise():
        task = asyncio.create_task(ws_module._bot_uttt_move(code, bot["id"], room, gs))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            rm.surrender(code, host[0])
            fresh = rm.start_game(code, host[0])
            revision = fresh["revision"]
        finally:
            release.set()
        await task
        assert fresh["revision"] == revision and not any(any(b) for b in fresh["boards"])

    run_async(exercise())
