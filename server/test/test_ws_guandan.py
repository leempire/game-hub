from asyncio import Event
from contextlib import ExitStack
from random import Random
from uuid import uuid4

import pytest

from app import database, persistence, room_manager as rm, sharing, ws
from app.game import guandan


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    path = str(tmp_path / "guandan.db")
    monkeypatch.setattr(database, "_db_path", path)
    database.init_db(path)
    monkeypatch.setattr(persistence, "_enabled", True)
    persistence.init()
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_dc_timeout_callback", None)
    ws._register_dc_timeout_callback()
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(rm, "_bot_tasks", {})
    monkeypatch.setattr(ws, "_connections", {})
    return ws_client


def receive(socket, kind, predicate=lambda _: True):
    for _ in range(30):
        message = socket.receive_json()
        if message["type"] == kind and predicate(message):
            return message
    pytest.fail(f"Missing {kind}")


def move(socket, state, action, **args):
    socket.send_json({"type": "guandan_action", "action": action, "match_id": state["match_id"],
                      "revision": state["revision"], "deal": state["deal"], **args})


def make_room(client, stack):
    tokens = {}
    for _ in range(4):
        response = client.post("/api/auth/register", json={
            "username": "gd_" + uuid4().hex[:10], "password": "test123456",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        tokens[data["user"]["id"]] = data["token"]
    host = next(iter(tokens))

    def headers(uid):
        return {"Authorization": "Bearer " + tokens[uid]}

    response = client.post("/api/rooms", json={"game": "guandan"}, headers=headers(host))
    assert response.status_code == 200, response.text
    assert response.json()["max_players"] == 4
    code = response.json()["code"]
    for uid in tokens:
        if uid != host:
            assert client.post(f"/api/rooms/{code}/join", json={}, headers=headers(uid)).status_code == 200
    sockets = {uid: stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={token}"))
               for uid, token in tokens.items()}
    for uid, socket in sockets.items():
        assert receive(socket, "sync")["game"] is None
        if uid != host:
            socket.send_json({"type": "ready"})
    receive(sockets[host], "lobby", lambda message: all(
        p["connected"] and (p["is_host"] or p["ready"]) for p in message["room"]["players"]))
    sockets[host].send_json({"type": "start"})
    states = {uid: receive(socket, "game_start")["game"] for uid, socket in sockets.items()}
    return code, tokens, sockets, states


def test_guandan_private_hand_preview_reconnect_and_team_zero_result(client):
    with ExitStack() as stack:
        code, tokens, sockets, states = make_room(client, stack)
        seats = list(sockets)
        assert all(len(state["hand"]) == 27 for state in states.values())
        assert len({card["id"] for state in states.values() for card in state["hand"]}) == 108
        assert states[seats[0]]["remaining"] == [27, None, None, None]
        actor = states[seats[0]]["current_id"]
        state = states[actor]
        card = state["hand"][0]
        move(sockets[actor], state, "preview", cards=[card["id"]], request_id=42)
        preview = receive(sockets[actor], "guandan_preview")
        assert preview["request_id"] == 42 and preview["revision"] == state["revision"]
        assert preview["combo"]["cards"] == [card]
        move(sockets[actor], state, "play", cards=[card["id"]])
        states = {uid: receive(socket, "guandan_state")["game"] for uid, socket in sockets.items()}
        assert all(state["last_play"]["cards"] == [card] for state in states.values())
        assert len(states[actor]["hand"]) == 26
        assert all(card not in view["hand"] for view in states.values())
        move(sockets[actor], state, "play", cards=[card["id"]])
        assert receive(sockets[actor], "error")["game"] == "guandan"
        with client.websocket_connect(f"/ws/room/{code}?token={tokens[actor]}") as rejoined:
            assert receive(rejoined, "sync")["game"] == states[actor]
            forfeiter = seats[1]
            current_sockets = {**sockets, actor: rejoined}
            current_sockets[forfeiter].send_json({"type": "surrender"})
            for socket in current_sockets.values():
                result = receive(socket, "game_over")["summary"]
                assert result["winner"] == 0 and result["reason"] == "surrender"
                assert "hands" not in result and "hand" not in result
                assert receive(socket, "lobby", lambda m: m["room"]["status"] == "waiting")
            record = database.get_user_history(seats[0])["items"][0]
            assert record["snapshot"]["game"] == "guandan"
            assert [result["result"] for result in record["results"]] == ["win", "lose", "win", "lose"]
            shared = sharing.public_share(sharing.create_share(record["id"], seats[0])["token"])
            assert shared["snapshot"]["deal"] == 1 and "match_id" not in shared["snapshot"]
            with client.websocket_connect(f"/ws/room/{code}?token={tokens[seats[2]]}") as after:
                sync = receive(after, "sync")
                assert sync["game"] is None and sync["guandan_result"]["summary"]["winner"] == 0


def test_guandan_continues_in_room_and_accepts_two_returns_from_same_version(client, monkeypatch):
    original = guandan._deal_hands

    def second_deal(players, level, rng):
        if level == "5":
            # This deck puts one big joker on each team at the second deal.
            return original(players, level, Random(0))
        return original(players, level, rng)

    monkeypatch.setattr(guandan, "_deal_hands", second_deal)
    with ExitStack() as stack:
        code, tokens, sockets, views = make_room(client, stack)
        host = next(iter(sockets))
        state = views[host]
        seats = state["players"]
        head = state["current_id"]
        partner = seats[(seats.index(head) + 2) % 4]
        gs = rm.get_room(code)["game_state"]
        for uid in (head, partner):
            gs["hands"][uid] = gs["hands"][uid][:1]

        def updated():
            return {uid: receive(socket, "guandan_state")["game"] for uid, socket in sockets.items()}

        move(sockets[head], state, "play", cards=[gs["hands"][head][0]["id"]])
        views = updated()
        for _ in range(3):
            actor = views[host]["current_id"]
            move(sockets[actor], views[actor], "pass")
            views = updated()
        assert views[partner]["current_id"] == partner and views[partner]["lead_reason"] == "partner"
        move(sockets[partner], views[partner], "play", cards=[gs["hands"][partner][0]["id"]])
        views = updated()
        before = views[host]
        assert before["phase"] == "between" and before["deal_result"]["advance"] == 3
        assert not database.get_user_history(host)["items"]
        assert rm.get_room(code)["status"] == "playing"
        guest = seats[1]
        move(sockets[guest], before, "next")
        assert receive(sockets[guest], "error")["game"] == "guandan"
        move(sockets[host], before, "next")
        views = updated()
        state = views[host]
        assert state["phase"] == "returning" and state["deal"] == 2 and state["level"] == "5"
        recipients = [pair["to_id"] for pair in state["exchange"]["pairs"]]
        first, second = recipients
        first_choice = views[first]["return_choices"][0]
        second_choice = views[second]["return_choices"][0]
        move(sockets[first], views[first], "return", card=first_choice)
        pending = updated()
        for view in pending.values():
            assert all(pair["returned"] is None for pair in view["exchange"]["pairs"])
        assert pending[first]["return_choices"] == []
        with client.websocket_connect(f"/ws/room/{code}?token={tokens[host]}") as rejoined:
            assert receive(rejoined, "sync")["game"] == pending[host]
            sockets[host] = rejoined
            move(sockets[second], views[second], "return", card=second_choice)
            final = updated()
            for view in final.values():
                assert view["phase"] == "playing" and len(view["hand"]) == 27
                assert view["current_id"] == view["exchange"]["lead_id"]
                assert [pair["returned"]["id"] for pair in view["exchange"]["pairs"]] == [first_choice, second_choice]


def test_guandan_fills_three_seats_and_bots_play_and_return_tribute(client, monkeypatch):
    create_game = guandan.create_game
    deal_hands = guandan._deal_hands
    ready = Event()

    def bot_leads(players, match_id, rng):
        state = create_game(players, match_id, rng)
        state["current_id"] = players[-1]
        return state

    def split_big_jokers(players, level, rng):
        return deal_hands(players, level, Random(0))

    async def wait_then_play(code, uid):
        await ready.wait()
        await ws._bot_do_turn(code, uid)

    monkeypatch.setattr(guandan, "create_game", bot_leads)
    monkeypatch.setattr(guandan, "_deal_hands", split_big_jokers)
    monkeypatch.setattr(rm.random, "uniform", lambda *_: 0.01)
    monkeypatch.setattr(rm, "_bot_turn_callback", wait_then_play)
    response = client.post("/api/auth/register", json={
        "username": "gd_bot_" + uuid4().hex[:9], "password": "test123456",
    })
    host = response.json()["user"]["id"]
    token = response.json()["token"]
    headers = {"Authorization": "Bearer " + token}
    code = client.post("/api/rooms", json={"game": "guandan"}, headers=headers).json()["code"]
    with ExitStack() as stack:
        def connect():
            return stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={token}"))

        socket = connect()
        receive(socket, "sync")
        for count, difficulty in enumerate(("easy", "normal", "hard"), 1):
            socket.send_json({"type": "add_bot", "difficulty": difficulty})
            socket.send_json({"type": "ping"})
            messages = []
            while (message := socket.receive_json())["type"] != "pong":
                messages.append(message)
            assert [m["type"] for m in messages] == ["lobby"]
            bots = [p for p in messages[0]["room"]["players"] if p["is_bot"]]
            assert len(bots) == count
            assert bots[-1]["ai_difficulty"] == difficulty and bots[-1]["ready"]
        socket.send_json({"type": "remove_bot", "bot_id": bots[1]["id"]})
        receive(socket, "lobby")
        socket.send_json({"type": "add_bot", "difficulty": "normal"})
        lobby = receive(socket, "lobby")["room"]
        bots = [p for p in lobby["players"] if p["is_bot"]]
        assert len({p["nickname"] for p in bots}) == 3
        socket.send_json({"type": "start"})
        start = receive(socket, "game_start")["game"]
        assert start["current_id"] == bots[-1]["id"]
        task = rm._bot_tasks[(code, bots[-1]["id"])]
        stack.close()

        async def release_turn():
            ready.set()
            await task

        client.portal.call(release_turn)
        socket = connect()
        assert receive(socket, "sync")["game"] == start
        after = receive(socket, "guandan_state")["game"]
        assert after["current_id"] == host and after["last_play"]["uid"] == bots[-1]["id"]
        assert after["hand"] == start["hand"] and "hands" not in after

        gs = rm.get_room(code)["game_state"]
        winners = [bots[0]["id"], bots[-1]["id"]]
        recap = {"deal": 1, "winner": 1, "finish_order": winners, "levels": ["2", "5"], "advance": 3, "level": "2"}
        gs.update(phase="between", levels=["2", "5"], deal_result=recap)
        move(socket, after, "next")
        returning = receive(socket, "guandan_state")["game"]
        assert returning["phase"] == "returning" and returning["deal"] == 2
        assert {p["to_id"] for p in returning["exchange"]["pairs"]} == set(winners)
        partial = receive(socket, "guandan_state")["game"]
        assert sum(p["submitted"] for p in partial["exchange"]["pairs"]) == 1
        assert all(p["returned"] is None for p in partial["exchange"]["pairs"])
        resumed = receive(socket, "guandan_state")["game"]
        assert resumed["phase"] == "playing" and len(resumed["hand"]) == 27
        assert all(p["returned"] is not None for p in resumed["exchange"]["pairs"])
        socket.send_json({"type": "surrender"})
        assert receive(socket, "game_over")["summary"]["winner"] == 1
        record = database.get_user_history(host)["items"][0]
        assert [p["ai_difficulty"] for p in record["players"] if p["is_bot"]] == ["easy", "hard", "normal"]
