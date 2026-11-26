"""Exercise room settings, private hands, reconnect and recorded results."""

from contextlib import ExitStack
from uuid import uuid4

import pytest

from app import database, persistence, room_manager as rm, ws as ws_module


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    path = str(tmp_path / "doudizhu.db")
    monkeypatch.setattr(database, "_db_path", path)
    database.init_db(path)
    monkeypatch.setattr(persistence, "_enabled", True)
    persistence.init()
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_dc_timeout_callback", None)
    ws_module._register_dc_timeout_callback()
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(ws_module, "_connections", {})
    return ws_client


def register(client):
    response = client.post("/api/auth/register", json={
        "username": "ddz_" + uuid4().hex[:10], "password": "test123456",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    return data["user"]["id"], data["token"]


def receive(socket, kind, predicate=lambda _: True):
    for _ in range(30):
        message = socket.receive_json()
        if message["type"] == kind and predicate(message):
            return message
    pytest.fail(f"Missing {kind}")


def move(socket, state, action, **payload):
    socket.send_json({"type": "doudizhu_action", "action": action,
                      "match_id": state["match_id"], "revision": state["revision"], **payload})


def make_lobby(client, stack):
    players = dict(register(client) for _ in range(3))
    host = next(iter(players))

    def headers(uid):
        return {"Authorization": "Bearer " + players[uid]}

    created = client.post("/api/rooms", json={"game": "doudizhu"}, headers=headers(host))
    assert created.status_code == 200, created.text
    room = created.json()
    assert room["max_players"] == 3
    code = room["code"]
    for uid in players:
        if uid != host:
            joined = client.post(f"/api/rooms/{code}/join", json={}, headers=headers(uid))
            assert joined.status_code == 200, joined.text
    sockets = {uid: stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={token}"))
               for uid, token in players.items()}
    for uid, socket in sockets.items():
        assert receive(socket, "sync")["game"] is None
        if uid != host:
            socket.send_json({"type": "ready"})
    receive(sockets[host], "lobby", lambda msg: all(
        p["is_host"] or p["ready"] for p in msg["room"]["players"]))
    return code, players, sockets


def test_settings_lock_and_private_state_survive_reconnect(client):
    with ExitStack() as stack:
        code, players, sockets = make_lobby(client, stack)
        host, guest, last = sockets
        sockets[guest].send_json({"type": "room_setting", "key": "preset", "value": "strict"})
        assert "房主" in receive(sockets[guest], "error")["message"]
        sockets[host].send_json({"type": "room_setting", "key": "preset", "value": "strict"})

        def strict(message):
            return any(item["key"] == "preset" and item["value"] == "strict"
                       for item in message["room"]["settings"])

        for socket in sockets.values():
            receive(socket, "lobby", strict)
        sockets[host].send_json({"type": "start"})
        starts = {uid: receive(socket, "game_start")["game"] for uid, socket in sockets.items()}
        assert len({card for start in starts.values() for card in start["hand"]}) == 51
        for state in starts.values():
            assert state["phase"] == "bidding"
            assert len(state["hand"]) == 17
            assert state["bottom"] == []
            assert "hands" not in state
            assert state["rules"]["airplane_pair_as_singles"] is False

        sockets[host].send_json({"type": "room_setting", "key": "preset", "value": "classic"})
        assert "已经开始" in receive(sockets[host], "error")["message"]
        landlord = starts[host]["current_id"]
        move(sockets[landlord], starts[landlord], "bid", bid=3)
        states = {uid: receive(socket, "doudizhu_state")["game"] for uid, socket in sockets.items()}
        assert len(states[landlord]["hand"]) == 20
        assert len(states[guest]["bottom"]) == 3
        assert all(set(state["bottom"]) == set(states[landlord]["bottom"]) for state in states.values())
        move(sockets[landlord], starts[landlord], "bid", bid=3)
        receive(sockets[landlord], "error")
        move(sockets[landlord], states[landlord], "hint")
        hint = receive(sockets[landlord], "doudizhu_hint")
        assert hint["cards"] and set(hint["cards"]) <= set(states[landlord]["hand"])
        sockets[last].send_json({"type": "ping"})
        assert sockets[last].receive_json()["type"] == "pong"
        card = states[landlord]["hand"][0]
        move(sockets[landlord], states[landlord], "play", cards=[card])
        after = receive(sockets[guest], "doudizhu_state")["game"]
        assert after["last_play"]["cards"] == [card]
        with client.websocket_connect(f"/ws/room/{code}?token={players[guest]}") as reconnected:
            assert receive(reconnected, "sync")["game"] == after


def test_departure_settles_once_and_records_original_rules(client):
    with ExitStack() as stack:
        code, players, sockets = make_lobby(client, stack)
        host = next(iter(sockets))
        sockets[host].send_json({"type": "start"})
        state = receive(sockets[host], "game_start")["game"]
        landlord = state["current_id"]
        move(sockets[landlord], state, "bid", bid=3)
        for socket in sockets.values():
            receive(socket, "doudizhu_state")
        sockets[landlord].send_json({"type": "surrender"})
        for socket in sockets.values():
            summary = receive(socket, "game_over")["summary"]
            assert summary["winner"] == "farmers" and summary["scores"] is None
            assert "hands" not in summary and "hand" not in summary
        sockets[landlord].send_json({"type": "surrender"})
        sockets[host].send_json({"type": "room_setting", "key": "preset", "value": "strict"})
        records = database.get_user_history(landlord)
        assert records["total"] == 1
        row = records["items"][0]
        assert row["room_code"] == code
        assert row["snapshot"]["rules"]["airplane_pair_as_singles"] is True
        assert [entry["result"] for entry in row["results"]].count("win") == 2


def test_normal_win_stores_scores_and_restores_result(client):
    with ExitStack() as stack:
        code, players, sockets = make_lobby(client, stack)
        host = next(iter(sockets))
        sockets[host].send_json({"type": "start"})
        state = receive(sockets[host], "game_start")["game"]
        landlord = state["current_id"]
        move(sockets[landlord], state, "bid", bid=3)
        states = {uid: receive(socket, "doudizhu_state")["game"] for uid, socket in sockets.items()}
        # A fixed last hand exercises the normal game-over transaction via the real WS.
        final_hand = [states[landlord]["hand"][0]]
        rm.get_room(code)["game_state"]["hands"][landlord] = final_hand
        move(sockets[landlord], states[landlord], "play", cards=final_hand)
        expected = {str(uid): 12 if uid == landlord else -6 for uid in players}
        for socket in sockets.values():
            summary = receive(socket, "game_over")["summary"]
            assert summary["scores"] == expected and summary["spring"] == "spring"
        with client.websocket_connect(f"/ws/room/{code}?token={players[host]}") as reconnect:
            sync = receive(reconnect, "sync")
            assert sync["game"] is None and sync["room"]["status"] == "waiting"
            assert sync["doudizhu_result"]["summary"]["scores"] == expected
        record = database.get_user_history(host)["items"][0]
        assert record["snapshot"]["scores"] == expected
        assert sorted(entry["score"] for entry in record["results"]) == [-6, -6, 12]


@pytest.mark.parametrize("after_bid", [False, True])
def test_disconnect_deadline_cancels_bidding_or_forfeits_play(client, after_bid):
    with ExitStack() as stack:
        code, _, sockets = make_lobby(client, stack)
        host = next(iter(sockets))
        sockets[host].send_json({"type": "start"})
        starts = {uid: receive(socket, "game_start")["game"] for uid, socket in sockets.items()}
        disconnected = starts[host]["current_id"]
        survivor = next(socket for uid, socket in sockets.items() if uid != disconnected)
        if after_bid:
            move(sockets[disconnected], starts[disconnected], "bid", bid=3)
            for socket in sockets.values():
                receive(socket, "doudizhu_state")
        sockets[disconnected].close()
        receive(survivor, "player_disconnected")
        client.portal.call(rm._on_dc_timeout, code, disconnected)
        summary = receive(survivor, "game_over")["summary"]
        assert summary["scores"] is None
        assert summary["winner"] == ("farmers" if after_bid else None)
        assert summary["reason"] == ("disconnect" if after_bid else "canceled")
        assert receive(survivor, "lobby")["room"]["status"] == "waiting"
        assert database.get_user_history(disconnected)["total"] == int(after_bid)


def test_single_player_can_add_two_bots_and_play_a_round(client, monkeypatch):
    monkeypatch.setattr(rm.random, "uniform", lambda low, high: 0.01)
    uid, token = register(client)
    room = client.post("/api/rooms", json={"game": "doudizhu"},
                       headers={"Authorization": "Bearer " + token}).json()
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={token}") as socket:
        receive(socket, "sync")
        for count, difficulty in enumerate(("easy", "hard"), 2):
            socket.send_json({"type": "add_bot", "difficulty": difficulty})
            message = socket.receive_json()
            assert message["type"] == "lobby", message
            seats = message["room"]["players"]
            assert len(seats) == count
            assert seats[-1]["is_bot"] and seats[-1]["ready"]
            assert seats[-1]["ai_difficulty"] == difficulty
        socket.send_json({"type": "start"})
        state = receive(socket, "game_start")["game"]
        if state["current_id"] != uid:
            state = receive(socket, "doudizhu_state", lambda m: m["game"]["current_id"] == uid)["game"]
        if state["phase"] == "bidding":
            move(socket, state, "bid", bid=3)
            state = receive(socket, "doudizhu_state")["game"]
        assert state["current_id"] == uid and state["phase"] == "playing"
        move(socket, state, "hint")
        cards = receive(socket, "doudizhu_hint")["cards"]
        move(socket, state, "play" if cards else "pass", **({"cards": cards} if cards else {}))
        new_state = receive(socket, "doudizhu_state", lambda m: m["game"]["revision"] > state["revision"] + 1)["game"]
        assert "hands" not in new_state
        socket.send_json({"type": "surrender"})
        receive(socket, "game_over")
    record = database.get_user_history(uid)["items"][0]
    assert [p["ai_difficulty"] for p in record["players"] if p["is_bot"]] == ["easy", "hard"]
