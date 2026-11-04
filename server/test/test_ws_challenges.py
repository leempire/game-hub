"""Challenge HTTP/WS entry, fixed seeds, first reveal and natural completion."""

from contextlib import ExitStack
from copy import deepcopy

import pytest

from app import database, daily_challenges, persistence, room_manager as rm, security, ws as ws_module
from app.game.game2048 import move_with_trace
from app.competition_rules import challenge_seed

TODAY = "2026-11-04"


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "daily.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "daily-test-secret-that-is-at-least-32-bytes")
    for name in ("_rooms", "_dc_tasks", "_bot_tasks"):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    monkeypatch.setattr(daily_challenges, "utc_today", lambda: TODAY)
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


def challenge(client, person, game):
    response = client.post(f"/api/challenges/{game}/start", headers=person[2])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["challenge"]["date"] == TODAY
    return result


def connect_start(client, stack, person, room):
    socket = stack.enter_context(client.websocket_connect(f"/ws/room/{room['code']}?token={person[1]}"))
    recv(socket, "sync")
    socket.send_json({"type": "room_setting", "key": "start_level", "value": 10})
    assert "固定" in recv(socket, "error")["message"]
    socket.send_json({"type": "start"})
    return socket, recv(socket, "game_start")["game"]


def test_endpoints_require_auth_and_reject_unknown_games(client):
    assert client.get("/api/challenges/today").status_code in (401, 403)
    assert client.get("/api/leaderboards/gomoku").status_code in (401, 403)
    person = user(client, "daily_auth")
    assert client.post("/api/challenges/uno/start", headers=person[2]).status_code == 400
    assert client.get("/api/leaderboards/uno?metric=time", headers=person[2]).status_code == 400
    assert client.get("/api/leaderboards/2048?limit=500", headers=person[2]).status_code == 422


def test_private_lobby_reuses_pending_room_and_ignores_client_date(client):
    host, other = user(client, "daily_host"), user(client, "daily_other")
    room = challenge(client, host, "2048")
    second = client.post("/api/challenges/2048/start", json={"date": "2000-01-01", "score": 99999},
                         headers=host[2]).json()
    assert second["code"] == room["code"]
    assert second["challenge"]["date"] == TODAY
    assert room["max_players"] == 1
    assert client.get("/api/rooms", headers=other[2]).json()["items"] == []
    assert client.post(f"/api/rooms/{room['code']}/join", json={}, headers=other[2]).status_code == 409
    status = client.get("/api/challenges/today", headers=host[2]).json()
    assert status["games"][0]["room_code"] == room["code"]
    assert not status["games"][0]["completed"]


@pytest.mark.parametrize("game", ["2048", "tetris", "minesweeper"])
def test_all_clients_receive_same_seed_and_challenge_metadata(client, game):
    people = [user(client, f"same_{game}_{i}") for i in range(2)]
    rooms = [challenge(client, person, game) for person in people]
    with ExitStack() as stack:
        sockets, states = zip(*(connect_start(client, stack, person, room)
                                for person, room in zip(people, rooms)))
        assert states[0]["challenge"] == states[1]["challenge"]
        if game == "2048":
            assert states[0]["board"] == states[1]["board"]
            assert states[0]["seed"] == states[1]["seed"]
        elif game == "tetris":
            assert states[0]["current"] == states[1]["current"]
            assert states[0]["next_pieces"] == states[1]["next_pieces"]
        else:
            assert states[0]["challenge"]["first_reveal"] == {"x": 4, "y": 4}
            for socket in sockets:
                socket.send_json({"type": "reveal", "x": 0, "y": 0})
                assert "中央" in recv(socket, "error")["message"]
                socket.send_json({"type": "flag", "x": 4, "y": 4})
                assert "中央" in recv(socket, "error")["message"]
                socket.send_json({"type": "reveal", "x": 4, "y": 4})
                assert not recv(socket, "revealed")["hit_mine"]
            boards = [rm.get_room(room["code"])["game_state"]["mine_positions"] for room in rooms]
            assert boards[0] == boards[1]


def test_2048_undo_is_rejected_and_surrender_does_not_complete(client):
    person = user(client, "daily_undo")
    room = challenge(client, person, "2048")
    with ExitStack() as stack:
        socket, state = connect_start(client, stack, person, room)
        direction = next(direction for direction in ("up", "down", "left", "right")
                         if move_with_trace(state["board"], direction)[2])
        socket.send_json({"type": "move2048", "direction": direction})
        recv(socket, "move2048")
        board = deepcopy(rm.get_room(room["code"])["game_state"]["boards"][person[0]])
        socket.send_json({"type": "undo2048"})
        assert "不支持撤销" in recv(socket, "error")["message"]
        assert rm.get_room(room["code"])["game_state"]["boards"][person[0]] == board
        socket.send_json({"type": "surrender"})
        recv(socket, "game_over")
    status = client.get("/api/challenges/today", headers=person[2]).json()
    assert not status["games"][0]["completed"]
    board = client.get(f"/api/leaderboards/2048?metric=score&challenge_date={TODAY}", headers=person[2]).json()
    assert board["items"] == []
    retry = challenge(client, person, "2048")
    assert retry["code"] != room["code"]


def test_natural_mine_clear_records_completion_and_time_board(client):
    person = user(client, "daily_clear")
    room = challenge(client, person, "minesweeper")
    with ExitStack() as stack:
        socket, state = connect_start(client, stack, person, room)
        socket.send_json({"type": "reveal", "x": 4, "y": 4})
        recv(socket, "revealed")
        gs = rm.get_room(room["code"])["game_state"]
        for y in range(gs["height"]):
            for x in range(gs["width"]):
                if gs["finished"]:
                    break
                if (x, y) in gs["mine_positions"] or (x, y) in gs["revealed"]:
                    continue
                socket.send_json({"type": "reveal", "x": x, "y": y})
                recv(socket, "revealed")
        assert recv(socket, "game_over")["reason"] == "cleared"
    status = client.get("/api/challenges/today", headers=person[2]).json()
    mine = next(item for item in status["games"] if item["game"] == "minesweeper")
    assert mine["completed"] and mine["streak"] == 1
    board = client.get(f"/api/leaderboards/minesweeper?metric=time&challenge_date={TODAY}", headers=person[2]).json()
    assert board["difficulty"] == "beginner"
    assert board["own"]["rank"] == 1
    assert board["own"]["value"] == mine["best_value"]


def test_cross_midnight_room_keeps_original_seed_and_completion_date(client, monkeypatch):
    person = user(client, "daily_midnight")
    room = challenge(client, person, "2048")
    with ExitStack() as stack:
        socket, state = connect_start(client, stack, person, room)
        seed = state["seed"]
        monkeypatch.setattr(daily_challenges, "utc_today", lambda: "2026-11-05")
        assert rm.get_room(room["code"])["challenge_date"] == TODAY
        assert rm.get_room(room["code"])["game_state"]["seed"] == seed
        next_room = client.post("/api/challenges/2048/start", headers=person[2]).json()
        assert next_room["code"] != room["code"]
        assert next_room["challenge"]["date"] == "2026-11-05"
        socket.send_json({"type": "surrender"})
        recv(socket, "game_over")
    conn = database.get_conn()
    record = conn.execute("SELECT challenge_date FROM game_records").fetchone()
    conn.close()
    assert record["challenge_date"] == TODAY


@pytest.mark.parametrize("game", ["2048", "tetris"])
def test_natural_score_game_finish_is_server_recorded_and_ranked(client, game):
    person = user(client, f"natural_{game}")
    room = challenge(client, person, game)
    with ExitStack() as stack:
        socket, _ = connect_start(client, stack, person, room)
        gs = rm.get_room(room["code"])["game_state"]
        if game == "2048":
            # Set up a legal final merge; only the WS move determines completion.
            gs["boards"][person[0]] = [[1024, 1024, 0, 0], [0]*4, [0]*4, [0]*4]
            socket.send_json({"type": "move2048", "direction": "left", "score": 99999999})
            recv(socket, "move2048")
        else:
            # A valid hold that cannot spawn the next piece causes natural top-out.
            gs["scores"][person[0]] = 240
            gs["boards"][person[0]] = [["T"] * 10 for _ in range(20)]
            socket.send_json({"type": "tetris_actions", "actions": ["hold"], "score": 99999999})
            recv(socket, "tetris_state")
        recv(socket, "game_over")
    status = client.get("/api/challenges/today", headers=person[2]).json()
    item = next(item for item in status["games"] if item["game"] == game)
    assert item["completed"] and item["streak"] == 1
    assert item["best_value"] == (2048 if game == "2048" else 240)
    board = client.get(f"/api/leaderboards/{game}?metric=score&challenge_date={TODAY}", headers=person[2]).json()
    assert board["own"]["value"] == item["best_value"]


def test_challenge_reconnect_preserves_metadata_seed_and_progress(client):
    person = user(client, "daily_reconnect")
    room = challenge(client, person, "2048")
    with ExitStack() as stack:
        socket, original = connect_start(client, stack, person, room)
        direction = next(direction for direction in ("up", "down", "left", "right")
                         if move_with_trace(original["board"], direction)[2])
        socket.send_json({"type": "move2048", "direction": direction})
        updated = recv(socket, "move2048")
    with client.websocket_connect(f"/ws/room/{room['code']}?token={person[1]}") as socket:
        sync = recv(socket, "sync")
        assert sync["game"]["challenge"] == {"date": TODAY}
        assert sync["room"]["challenge"] == {"date": TODAY}
        assert sync["game"]["seed"] == original["seed"]
        assert sync["game"]["board"] == updated["board"]
        assert sync["game"]["move_count"] == 1
        assert not sync["game"]["undo_available"]


def test_mine_challenge_uses_existing_daily_seed(client):
    assert challenge_seed("minesweeper", TODAY) == 20261104
