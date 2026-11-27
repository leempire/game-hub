import asyncio
from collections import defaultdict
from contextlib import ExitStack
from uuid import uuid4

import pytest

from app import database, persistence, room_manager as rm, room_timing
from app import ws as ws_module
from app.game import concentration


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "concentration.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(persistence, "_enabled", False)
    persistence.init()
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_dc_timeout_callback", None)
    ws_module._register_dc_timeout_callback()
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(room_timing, "_tasks", {})
    monkeypatch.setattr(room_timing, "_anchors", {})
    yield ws_client

    async def clear_rooms():
        for code in list(rm._rooms):
            rm._destroy_room(code)

    ws_client.portal.call(clear_rooms)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _recv(ws, kind, predicate=lambda msg: True):
    for _ in range(60):
        msg = ws.receive_json()
        if msg["type"] == kind and predicate(msg):
            return msg
    raise AssertionError(f"Missing {kind}")


def _lobby(client, stack, count=2, cards=24):
    players = {}
    for _ in range(count):
        response = client.post("/api/auth/register", json={
            "username": "pair_" + uuid4().hex[:10], "password": "test123456",
        })
        assert response.status_code == 200
        data = response.json()
        players[data["user"]["id"]] = data["token"]
    host = next(iter(players))
    response = client.post("/api/rooms", json={"game": "concentration"}, headers=_auth(players[host]))
    assert response.status_code == 200, response.text
    room = response.json()
    assert room["max_players"] == 4
    code = room["code"]
    setting = next(s for s in room["settings"] if s["key"] == "concentration_cards")
    assert setting["value"] == 24
    assert [c["value"] for c in setting["choices"]] == [16, 24]
    for uid, token in players.items():
        if uid != host:
            assert client.post(f"/api/rooms/{code}/join", json={}, headers=_auth(token)).status_code == 200
    sockets = {
        uid: stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={token}"))
        for uid, token in players.items()
    }
    for ws in sockets.values():
        assert _recv(ws, "sync")["game"] is None
    if cards != 24:
        sockets[host].send_json({"type": "room_setting", "key": "concentration_cards", "value": cards})
        for ws in sockets.values():
            _recv(ws, "lobby", lambda msg: any(
                setting["key"] == "concentration_cards" and setting["value"] == cards
                for setting in msg["room"]["settings"]))
    for uid, ws in sockets.items():
        if uid != host:
            ws.send_json({"type": "ready"})
    if count > 1:
        _recv(sockets[host], "lobby", lambda msg: all(
            p["is_host"] or p["ready"] for p in msg["room"]["players"]))
    return code, players, sockets


def _start(sockets):
    next(iter(sockets.values())).send_json({"type": "start"})
    starts = [_recv(ws, "game_start")["game"] for ws in sockets.values()]
    assert all(view == starts[0] for view in starts)
    return starts[0]


def _flip(ws, view, index):
    ws.send_json({"type": "concentration_flip", "match_id": view["match_id"],
                  "revision": view["revision"], "index": index})


def _state(ws, phase, minimum_revision):
    return _recv(ws, "concentration_state", lambda msg: (
        msg["game"]["phase"] == phase and msg["game"]["revision"] >= minimum_revision))["game"]


def test_start_requires_at_least_two_people_and_disallows_bots(client):
    with ExitStack() as stack:
        code, _, sockets = _lobby(client, stack, count=1)
        host = next(iter(sockets.values()))
        host.send_json({"type": "add_bot"})
        assert "人机" in _recv(host, "error")["message"]
        host.send_json({"type": "start"})
        assert "2–4" in _recv(host, "error")["message"]
        assert rm.get_room(code)["status"] == "waiting"


def test_miss_expires_and_hides_both_cards_for_the_next_seat(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack, count=3, cards=16)
        start = _start(sockets)
        assert start["slots"] == [{"state": "hidden"}] * 16
        assert start["phase"] == "first" and start["remaining_pairs"] == 8
        actor = start["turn_id"]
        ws = sockets[actor]
        seats = list(players)
        other = seats[(seats.index(actor) + 1) % len(seats)]
        deck = rm.get_room(code)["game_state"]["deck"]
        different = next(i for i, card in enumerate(deck) if card[0] != deck[0][0])
        _flip(sockets[other], start, 0)
        _recv(sockets[other], "error")
        _flip(ws, start, 0)
        second = _state(ws, "second", start["revision"] + 1)
        assert second["slots"][0] == {"state": "revealed", "rank": deck[0][0], "suit": deck[0][1]}
        assert second["slots"][1:] == [{"state": "hidden"}] * 15
        _flip(ws, start, different)
        _recv(ws, "error")
        _flip(ws, second, True)
        _recv(ws, "error")
        _flip(ws, second, 0)
        _recv(ws, "error")
        _flip(ws, second, different)
        reveal = _state(sockets[other], "revealing", second["revision"] + 1)
        assert reveal["outcome"] == "miss"
        assert [i for i, slot in enumerate(reveal["slots"]) if slot["state"] == "revealed"] == [0, different]
        with client.websocket_connect(f"/ws/room/{code}?token={players[other]}") as reconnect:
            assert _recv(reconnect, "sync")["game"] == reveal
            after = _state(reconnect, "first", reveal["revision"] + 1)
            assert after["turn_id"] == other
            assert after["slots"] == start["slots"]
            assert after["outcome"] is None
            _flip(reconnect, reveal, 0)
            _recv(reconnect, "error")
            _flip(reconnect, after, 0)
            assert _state(reconnect, "second", after["revision"] + 1)["turn_id"] == other


def test_surrender_resolves_two_player_round_and_keeps_private_history(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        start = _start(sockets)
        actor = start["turn_id"]
        winner = next(uid for uid in players if uid != actor)
        ws = sockets[actor]
        _flip(ws, start, 0)
        second = _state(ws, "second", start["revision"] + 1)
        ws.send_json({"type": "surrender"})
        finished = _state(sockets[winner], "finished", second["revision"] + 1)
        assert finished["turn_id"] is None
        assert finished["slots"] == [{"state": "hidden"}] * 24
        assert {row["id"]: row["rank"] for row in finished["scores"]} == {actor: 2, winner: 1}
        assert next(row for row in finished["scores"] if row["id"] == actor)["forfeited"] is True
        assert _recv(sockets[winner], "lobby", lambda msg: msg["room"]["status"] == "waiting")
        with client.websocket_connect(f"/ws/room/{code}?token={players[winner]}") as reconnect:
            sync = _recv(reconnect, "sync")
            assert sync["game"] is None
            assert sync["concentration_result"] == finished
        response = client.get("/api/history/mine", headers=_auth(players[winner]))
        assert response.status_code == 200
        record = response.json()["items"][0]
        assert record["snapshot"] == {"game": "concentration", "scores": finished["scores"], "card_count": 24}
        assert {row["result"] for row in record["results"]} == {"win", "lose"}
        share = client.post(f"/api/history/{record['id']}/share", headers=_auth(players[winner]))
        assert share.status_code == 200
        public = client.get(f"/api/history/shared/{share.json()['token']}")
        assert public.json()["snapshot"] == record["snapshot"]


def test_matches_clear_fixed_slots_keep_the_turn_and_record_a_tie(client, monkeypatch):
    monkeypatch.setattr(concentration, "MATCH_REVEAL_MS", 30)
    monkeypatch.setattr(concentration, "MISS_REVEAL_MS", 50)
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack, cards=16)
        state = _start(sockets)
        start = state
        first_uid = state["turn_id"]
        other = next(uid for uid in players if uid != first_uid)
        pair_slots = defaultdict(list)
        for index, (rank, _) in enumerate(rm.get_room(code)["game_state"]["deck"]):
            pair_slots[rank].append(index)
        pairs = list(pair_slots.values())
        for attempt, pair in enumerate(pairs):
            if attempt == 4:
                ws = sockets[first_uid]
                _flip(ws, state, pairs[4][0])
                state = _state(ws, "second", state["revision"] + 1)
                _flip(ws, state, pairs[5][0])
                state = _state(ws, "first", state["revision"] + 2)
                assert state["turn_id"] == other
            actor = state["turn_id"]
            ws = sockets[actor]
            _flip(ws, state, pair[0])
            state = _state(ws, "second", state["revision"] + 1)
            _flip(ws, state, pair[1])
            reveal = _state(ws, "revealing", state["revision"] + 1)
            assert reveal["outcome"] == "match"
            state = _state(ws, "finished" if attempt == 7 else "first", reveal["revision"] + 1)
            assert state["slots"][pair[0]] == state["slots"][pair[1]] == {"state": "collected"}
            assert state["remaining_pairs"] == 7 - attempt
            assert state["turn_id"] == (None if attempt == 7 else actor)
        assert state["scores"] == [{"id": uid, "pairs": 4, "rank": 1} for uid in players]
        assert _recv(sockets[other], "game_over")["winner"] == "draw"
        record = client.get("/api/history/mine", headers=_auth(players[other])).json()["items"][0]
        assert {row["result"] for row in record["results"]} == {"draw"}
        guest = list(players)[1]
        sockets[guest].send_json({"type": "ready"})
        rematch = _start(sockets)
        assert rematch["match_id"] != start["match_id"]
        assert rematch["slots"] == start["slots"]
        assert [row["pairs"] for row in rematch["scores"]] == [0, 0]
        ws = sockets[rematch["turn_id"]]
        _flip(ws, start, 0)
        _recv(ws, "error")
        assert _state(ws, "first", rematch["revision"]) == rematch


def test_http_leave_transfers_host_and_keeps_three_player_round_playing(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack, count=3)
        state = _start(sockets)
        host, new_host, remaining = players
        response = client.post(f"/api/rooms/{code}/leave", headers=_auth(players[host]))
        assert response.status_code == 200
        after = _state(sockets[remaining], "first", state["revision"] + 1)
        assert after["turn_id"] in (new_host, remaining)
        assert next(row for row in after["scores"] if row["id"] == host)["forfeited"] is True
        lobby = _recv(sockets[remaining], "lobby", lambda msg: msg["room"]["host_id"] == new_host)
        assert lobby["room"]["status"] == "playing"
        assert [p["id"] for p in lobby["room"]["players"]] == [new_host, remaining]


def test_disconnect_timeout_removes_the_seat_and_finishes(client, monkeypatch):
    monkeypatch.setattr(rm, "_disconnect_timeout", 0.05)
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        start = _start(sockets)
        actor = start["turn_id"]
        other = next(uid for uid in players if uid != actor)
        _flip(sockets[actor], start, 0)
        second = _state(sockets[other], "second", start["revision"] + 1)
        sockets[actor].close()
        finished = _state(sockets[other], "finished", second["revision"] + 1)
        assert finished["reason"] == "disconnect"
        assert finished["slots"][0] == {"state": "hidden"}
        lobby = _recv(sockets[other], "lobby", lambda msg: msg["room"]["status"] == "waiting")
        assert [p["id"] for p in lobby["room"]["players"]] == [other]
        assert rm.get_room(code)["host_id"] == other


def test_disconnect_timeout_updates_membership_after_a_newer_flip(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack, count=3)
        start = _start(sockets)
        actor = start["turn_id"]
        offline = next(uid for uid in players if uid != actor)
        sockets[offline].close()
        ws = sockets[actor]
        _recv(ws, "player_disconnected")

        async def expire_and_flip():
            rm._dc_tasks[(code, offline)].cancel()
            rm._on_dc_timeout(code, offline)
            game = rm.get_room(code)["game_state"]
            await ws_module._handle_message(code, actor, {
                "type": "concentration_flip", "match_id": game["match_id"],
                "revision": game["revision"], "index": 0,
            })

        client.portal.call(expire_and_flip)
        _state(ws, "second", start["revision"] + 2)
        ws.send_json({"type": "ping"})
        messages = []
        while (message := ws.receive_json())["type"] != "pong":
            messages.append(message)
        assert any(
            m["type"] == "lobby" and offline not in [p["id"] for p in m["room"]["players"]]
            for m in messages
        )


def test_restore_reschedules_a_pending_reveal(client, monkeypatch):
    monkeypatch.setattr(concentration, "MISS_REVEAL_MS", 250)
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        start = _start(sockets)
        actor = start["turn_id"]
        other = next(uid for uid in players if uid != actor)
        deck = rm.get_room(code)["game_state"]["deck"]
        different = next(i for i, card in enumerate(deck) if card[0] != deck[0][0])
        ws = sockets[actor]
        _flip(ws, start, 0)
        second = _state(ws, "second", start["revision"] + 1)
        _flip(ws, second, different)
        reveal = _state(ws, "revealing", second["revision"] + 1)

    async def restore():
        room = rm.get_room(code)
        await room_timing.shutdown([room])
        rm._cancel_all_dc_tasks(code)
        rm._rooms.clear()
        assert rm.restore_rooms() == 1
        assert rm.get_room(code)["game_state"]["phase"] == "revealing"
        restored = asyncio.create_task(ws_module.reschedule_after_restore())
        await asyncio.sleep(0)
        restored.cancel()
        assert code in room_timing._tasks
        await asyncio.sleep(0.30)

    client.portal.call(restore)
    with client.websocket_connect(f"/ws/room/{code}?token={players[other]}") as reconnect:
        after = _recv(reconnect, "sync")["game"]
        assert after["match_id"] == reveal["match_id"]
        assert after["revision"] == reveal["revision"] + 1
        assert after["phase"] == "first" and after["turn_id"] == other
        assert after["slots"] == start["slots"]
