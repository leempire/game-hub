"""Exercise Undercover's private views, phase timers, departures and records."""

import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app import database, persistence, room_manager as rm, room_timing
from app import ws as ws_module


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "undercover.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(persistence, "_enabled", True)
    persistence.init()
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_dc_timeout_callback", None)
    ws_module._register_dc_timeout_callback()
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(rm, "_dc_tasks", {})
    monkeypatch.setattr(room_timing, "_tasks", {})
    monkeypatch.setattr(room_timing, "_anchors", {})
    return ws_client


def _register(client):
    response = client.post("/api/auth/register", json={
        "username": "uw_" + uuid4().hex[:10], "password": "test123456",
    })
    assert response.status_code == 200
    data = response.json()
    return data["user"]["id"], data["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _recv(ws, kind, predicate=lambda message: True):
    for _ in range(60):
        message = ws.receive_json()
        if message["type"] == kind:
            payload = message["game"] if kind == "undercover_state" else message
            if predicate(payload):
                return payload
    raise AssertionError(f"Missing {kind}")


def _connect(client, code, token):
    return client.websocket_connect(f"/ws/room/{code}?token={token}")


def _lobby(client, stack, count=4):
    players = dict(_register(client) for _ in range(count))
    host, host_token = next(iter(players.items()))
    response = client.post("/api/rooms", json={"game": "undercover"}, headers=_auth(host_token))
    assert response.status_code == 200, response.text
    room = response.json()
    assert room["max_players"] == 8
    code = room["code"]
    for pid, token in players.items():
        if pid != host:
            assert client.post(f"/api/rooms/{code}/join", json={}, headers=_auth(token)).status_code == 200
    sockets = {uid: stack.enter_context(_connect(client, code, token)) for uid, token in players.items()}
    for uid, ws in sockets.items():
        assert _recv(ws, "sync")["game"] is None
        if uid != host:
            ws.send_json({"type": "ready"})
    _recv(sockets[host], "lobby", lambda message: all(
        p["is_host"] or p["ready"] for p in message["room"]["players"]))
    return code, players, sockets


def _start(sockets):
    next(iter(sockets.values())).send_json({"type": "start"})
    return {uid: _recv(ws, "game_start")["game"] for uid, ws in sockets.items()}


def _action(kind, state, **values):
    return {"type": f"undercover_{kind}", "match_id": state["match_id"],
            "phase_id": state["phase_id"], **values}


def _clue_round(sockets, state):
    for _ in range(len(state["alive"])):
        speaker = sockets[state["speaker_id"]]
        speaker.send_json(_action("clue", state, text="可以在生活中遇到"))
        prior = state["revision"]
        state = _recv(speaker, "undercover_state", lambda message: message["revision"] > prior)
    assert state["phase"] == "vote"
    return state


def test_minimum_and_bot_restrictions(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack, count=3)
        host = sockets[next(iter(players))]
        host.send_json({"type": "add_bot"})
        assert "人机" in _recv(host, "error")["message"]
        host.send_json({"type": "start"})
        assert "4" in _recv(host, "error")["message"]
        assert rm.get_room(code)["status"] == "waiting"


def test_private_start_reconnect_and_rejected_actions(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        starts = _start(sockets)
        words = {uid: state["your_word"] for uid, state in starts.items()}
        assert sorted(list(words.values()).count(word) for word in set(words.values())) == [1, 3]
        for uid, view in starts.items():
            assert view["game"] == "undercover"
            assert view["phase"] == "clue"
            assert view["can_clue"] == (uid == view["speaker_id"])
            assert {"roles", "word_pair", "votes"}.isdisjoint(view)

        outsider, token = _register(client)
        assert outsider not in players
        assert client.get(f"/api/rooms/{code}", headers=_auth(token)).json()["in_game"] is False
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=_auth(token)).status_code == 409
        with pytest.raises(WebSocketDisconnect) as error:
            with _connect(client, code, token):
                pytest.fail("Outsider opened a private room socket")
        assert error.value.code == 4003

        state = next(iter(starts.values()))
        speaker_id = state["speaker_id"]
        speaker = sockets[speaker_id]
        other_id = next(uid for uid in players if uid != speaker_id)
        other = sockets[other_id]
        other.send_json(_action("clue", state, text="越权描述"))
        _recv(other, "error")
        forged = _action("clue", state, text="旧局描述")
        forged["match_id"] = "another-match"
        speaker.send_json(forged)
        _recv(speaker, "error")
        assert _recv(speaker, "undercover_state")["revision"] == state["revision"]

        clue = _action("clue", state, text="生活中常见")
        speaker.send_json(clue)
        after = _recv(speaker, "undercover_state")
        assert after["speaker_id"] != speaker_id
        speaker.send_json(clue)
        duplicate = _recv(speaker, "undercover_state")
        assert duplicate["revision"] == after["revision"]
        assert len(duplicate["clues"]) == 1
        speaker.send_json({**clue, "text": "旧阶段的新线索"})
        _recv(speaker, "error")
        assert _recv(speaker, "undercover_state")["revision"] == after["revision"]

        with _connect(client, code, players[other_id]) as reconnect:
            sync = _recv(reconnect, "sync")
            assert sync["in_game"] is True
            assert sync["game"]["your_word"] == words[other_id]
            assert sync["game"]["revision"] == after["revision"]
            assert "word_pair" not in sync["game"]


def test_players_share_vote_phase_and_private_ballots(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        starts = _start(sockets)
        state = _clue_round(sockets, next(iter(starts.values())))
        first, second, *others = players
        third = next(uid for uid in others if sum(
            other["your_word"] == starts[uid]["your_word"] for other in starts.values()) == 3)
        fourth = next(uid for uid in others if uid != third)
        vote = _action("vote", state, target_id=third)
        sockets[first].send_json(vote)
        sockets[second].send_json(_action("vote", state, target_id=fourth))
        first_view = _recv(sockets[first], "undercover_state", lambda message: message["votes_cast"] == 2)
        third_view = _recv(sockets[third], "undercover_state", lambda message: message["votes_cast"] == 2)
        assert first_view["phase_id"] == state["phase_id"]
        assert first_view["your_vote"] == third
        assert third_view["your_vote"] is None and "votes" not in third_view
        sockets[first].send_json(vote)
        repeat = _recv(sockets[first], "undercover_state")
        assert repeat["revision"] == first_view["revision"]
        assert repeat["votes_cast"] == 2

        response = client.post(f"/api/rooms/{code}/leave", headers=_auth(players[third]))
        assert response.status_code == 200
        assert rm.get_room(code)["status"] == "playing"
        after = _recv(sockets[first], "undercover_state", lambda message: third not in message["alive"])
        assert after["phase_id"] == state["phase_id"] and after["votes_cast"] == 1
        assert after["your_vote"] is None
        sockets[first].send_json(_action("vote", after, target_id=fourth))
        revoted = _recv(sockets[first], "undercover_state", lambda message: message["votes_cast"] == 2)
        assert revoted["your_vote"] == fourth


def test_expired_action_advances_once_and_cannot_land_in_next_phase(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(room_timing, "_now", lambda: now[0])
    with ExitStack() as stack:
        _, _, sockets = _lobby(client, stack)
        state = next(iter(_start(sockets).values()))
        ws = sockets[state["speaker_id"]]
        now[0] += 45.5
        ws.send_json(_action("clue", state, text="这条线索迟到了"))
        after = _recv(ws, "undercover_state")
        assert after["phase_id"] != state["phase_id"]
        assert after["clues"] == [{"round": 1, "user_id": state["speaker_id"], "text": None}]
        assert after["remaining_ms"] == 45000
        ws.send_json(_action("clue", state, text="再次提交迟到线索"))
        _recv(ws, "error")
        assert _recv(ws, "undercover_state")["phase_id"] == after["phase_id"]


def test_deadline_advances_without_a_client_message(client):
    with ExitStack() as stack:
        code, _, sockets = _lobby(client, stack)
        state = next(iter(_start(sockets).values()))

        async def shorten_deadline():
            room = rm.get_room(code)
            room["game_state"]["remaining_ms"] = 10
            room_timing.reset_deadline(room)

        client.portal.call(shorten_deadline)
        after = _recv(sockets[state["speaker_id"]], "undercover_state")
        assert after["phase_id"] != state["phase_id"]
        assert after["clues"] == [{"round": 1, "user_id": state["speaker_id"], "text": None}]


def test_restart_keeps_remaining_time_until_live_reconnect(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(room_timing, "_now", lambda: now[0])
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        starts = _start(sockets)
        now[0] += 2.5

    async def restart_rooms():
        for task in rm._dc_tasks.values():
            task.cancel()
        rm._dc_tasks.clear()
        room_timing.forget(code)
        rm._rooms.clear()
        assert rm.restore_rooms() == 1
        task = asyncio.create_task(ws_module.reschedule_after_restore())
        await asyncio.sleep(0)
        task.cancel()

    client.portal.call(restart_rooms)
    assert code not in room_timing._tasks
    assert len(rm._dc_tasks) == 4
    speaker_id = next(iter(starts.values()))["speaker_id"]
    token = players[speaker_id]
    now[0] += 120
    response = client.post(f"/api/rooms/{code}/join", json={}, headers=_auth(token))
    assert response.status_code == 200
    assert all(not p["connected"] for p in response.json()["players"])
    assert code not in room_timing._tasks
    with _connect(client, code, token) as ws:
        view = _recv(ws, "sync")["game"]
        assert view["match_id"] == starts[speaker_id]["match_id"]
        assert view["your_word"] == starts[speaker_id]["your_word"]
        assert view["phase_id"] == starts[speaker_id]["phase_id"]
        assert view["remaining_ms"] == 42500
        assert view["paused"] is False
        assert code in room_timing._tasks


def test_superseded_socket_close_does_not_extend_reconnect_grace(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        _start(sockets)
        uid, token = next(iter(players.items()))
        observer = next(ws for player, ws in sockets.items() if player != uid)
        with _connect(client, code, token) as older:
            _recv(older, "sync")
            with _connect(client, code, token) as newer:
                _recv(newer, "sync")
            _recv(observer, "player_disconnected", lambda message: message["user_id"] == uid)
            deadline = rm._dc_tasks[(code, uid)]
        assert rm._dc_tasks[(code, uid)] is deadline


def test_all_disconnected_seats_expire_as_aborted(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        _start(sockets)

    for uid in players:
        client.portal.call(rm._on_dc_timeout, code, uid)
    assert rm.get_room(code) is None
    for token in players.values():
        record, = client.get("/api/history/mine", headers=_auth(token)).json()["items"]
        assert record["snapshot"]["winner"] == "aborted"
        assert set(record["snapshot"]["forfeited"]) == set(players)
        assert all(result["result"] == "lose" for result in record["results"])


def test_two_disconnect_expirations_broadcast_one_terminal_result(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        starts = _start(sockets)
        undercover = next(uid for uid, view in starts.items() if sum(
            other["your_word"] == view["your_word"] for other in starts.values()) == 1)
        civ, survivor, _ = (uid for uid in players if uid != undercover)
        for uid in (civ, undercover):
            sockets[uid].close()
            _recv(sockets[survivor], "player_disconnected", lambda message: message["user_id"] == uid)

        async def expire_together():
            rm._on_dc_timeout(code, civ)
            rm._on_dc_timeout(code, undercover)

        client.portal.call(expire_together)
        ws = sockets[survivor]
        end = _recv(ws, "game_over")
        assert set(end["forfeited"]) == {civ, undercover}
        ws.send_json({"type": "ping"})
        for _ in range(10):
            message = ws.receive_json()
            assert message["type"] != "game_over"
            if message["type"] == "pong":
                break
        else:
            pytest.fail("Expected pong behind terminal messages")
        record, = client.get("/api/history/mine", headers=_auth(players[civ])).json()["items"]
        assert len(record["players"]) == 4
        result_by_user = dict(zip((p["user_id"] for p in record["players"]),
                                  (outcome["result"] for outcome in record["results"])))
        assert result_by_user[civ] == "lose"
        assert result_by_user[survivor] == "win"


def test_participants_history_has_terminal_summary_and_public_share_omits_private_fields(client):
    with ExitStack() as stack:
        code, players, sockets = _lobby(client, stack)
        starts = _start(sockets)
        state = _clue_round(sockets, next(iter(starts.values())))
        undercover = next(uid for uid, view in starts.items() if sum(
            other["your_word"] == view["your_word"] for other in starts.values()) == 1)
        survivors = [uid for uid in players if uid != undercover]
        for uid in survivors:
            sockets[uid].send_json(_action("vote", state, target_id=undercover))
        last_vote = _action("vote", state, target_id=survivors[0])
        sockets[undercover].send_json(last_vote)
        for ws in sockets.values():
            end = _recv(ws, "game_over")
            assert end["match_id"] == state["match_id"] and end["winner"] == "civilian"
            assert len(end["summary"]["roles"]) == 4
            assert "clues" not in end["summary"] and "votes" not in end["summary"]
        sockets[undercover].send_json(last_vote)
        _recv(sockets[undercover], "error")
        assert _recv(sockets[undercover], "sync")["undercover_result"] == end

        room = rm.get_room(code)
        assert room["status"] == "waiting"
        assert "clues" not in room["game_state"] and "votes" not in room["game_state"]
        database.save_game_record(room)
        for token in players.values():
            data = client.get("/api/history/mine", headers=_auth(token)).json()
            assert data["total"] == 1
            record = data["items"][0]
            assert record["snapshot"] == end["summary"]
            assert len(record["players"]) == 4

        token = players[survivors[0]]
        shared = client.post(f"/api/history/{record['id']}/share", headers=_auth(token)).json()
        public = client.get(f"/api/history/shared/{shared['token']}").json()
        assert public["room_code"] == ""
        assert set(public["snapshot"]) == {"game", "round", "winner", "reason"}
        assert public["results"] == []

        host = next(iter(players))
        sockets[host] = stack.enter_context(_connect(client, code, players[host]))
        sync = _recv(sockets[host], "sync")
        assert sync["undercover_result"] == end and sync["game"] is None
        outsider, outsider_token = _register(client)
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=_auth(outsider_token)).status_code == 200
        with _connect(client, code, outsider_token) as ws:
            assert "undercover_result" not in _recv(ws, "sync")
            assert client.post(f"/api/rooms/{code}/leave", headers=_auth(outsider_token)).status_code == 200
        for uid, ws in sockets.items():
            if uid != host:
                ws.send_json({"type": "ready"})
        successor = _start(sockets)
        assert set(view["your_word"] for view in successor.values()) != set(end["summary"]["word_pair"].values())
        sockets[host].send_json(_action("vote", state, target_id=outsider))
        _recv(sockets[host], "error")
        fresh = _recv(sockets[host], "undercover_state")
        assert fresh["match_id"] == successor[host]["match_id"] != state["match_id"]
        assert fresh["clues"] == []
