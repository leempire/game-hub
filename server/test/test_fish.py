from contextlib import ExitStack

from app import database, room_manager as rm
from app.game import fish
from test_ws_uttt import client, recv, user  # shared isolated HTTP and WebSocket fixtures


def test_growth_three_lives_and_non_contact():
    gs = fish.create([{"id": 1, "username": "A"}, {"id": 2, "username": "B"}],
                     42, "small", False, False)
    a, b = gs["players"].values()
    a.update(x=300, y=300, immune=0)
    b.update(x=300, y=300, score=100)
    gs["fish"] = [{"id": 99, "x": 300, "y": 300, "r": 8, "vx": 0, "vy": 0}]
    fish.advance(gs, {1})
    assert a["lives"] == 3 and a["score"] == 1
    assert b["lives"] == 3 and b["score"] == 100

    for lives in (2, 1, 0):
        a["immune"] = 0
        gs["fish"] = [{"id": 99, "x": a["x"], "y": a["y"], "r": 69, "vx": 0, "vy": 0}]
        fish.advance(gs, {1})
        assert a["lives"] == lives and a["score"] == 1
        if lives:
            assert a["immune"] - gs["frame"] == 90
            gs["fish"][0].update(x=a["x"], y=a["y"])
            fish.advance(gs, {1})
            assert a["lives"] == lives
    assert not gs["finished"]
    fish.depart(gs, 2)
    assert gs["finished"]
    assert [(r["id"], r["rank"]) for r in fish.ranking(gs)] == [(1, 1), (2, None)]


def test_house_rules_and_input_lease():
    players = [{"id": 1, "username": "A"}]
    for enabled in (False, True):
        gs = fish.create(players, 21, "large", enabled, enabled)
        p = gs["players"][1]
        p.update(x=300, y=300, immune=9999)
        fish.move(gs, 1, 1, 1)
        fish.move(gs, 1, True, None)
        assert 0 < p["dx"] < 1 and p["dx"] == p["dy"]
        for frame in range(55 * fish.HZ):
            fish.advance(gs, {1})
            if frame == 30:
                assert p["dx"] == p["dy"] == 0
        assert bool(gs["items"]) == enabled
        assert gs["event"] == ("school" if enabled else None)
        assert gs["width"] == 1600


def test_two_socket_round_movement_reconnect_and_history(client):
    with ExitStack() as stack:
        a, b = user(client, "fish_host"), user(client, "fish_guest")
        room = client.post("/api/rooms", json={"game": "fish"}, headers=a[2]).json()
        code = room["code"]
        assert room["max_players"] == 4
        assert {s["key"] for s in room["settings"]} == {"map_size", "items", "events"}
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=b[2]).status_code == 200
        sockets = [stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={p[1]}")) for p in (a, b)]
        for ws in sockets:
            recv(ws, "sync")
        sockets[1].send_json({"type": "ready"})
        sockets[0].send_json({"type": "start"})
        starts = [recv(ws, "game_start")["game"] for ws in sockets]
        assert starts[0] == starts[1] and all(p["lives"] == 3 for p in starts[0]["players"])
        first = starts[0]["players"][0]
        sockets[0].send_json({"type": "fish_move", "dx": 1, "dy": 0})
        for ws in sockets:
            moved = recv(ws, "fish_state")["game"]
            assert moved["players"][0]["x"] > first["x"]
        replacement = stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={a[1]}"))
        assert recv(replacement, "sync")["game"]["frame"] >= moved["frame"]

        async def finish_next_tick():
            gs = rm.get_room(code)["game_state"]
            gs["players"][a[0]]["score"] = 12
            gs["frame"] = fish.DURATION * 30 - 1

        client.portal.call(finish_next_tick)
        for ws in (replacement, sockets[1]):
            rows = recv(ws, "game_over")["game"]["ranking"]
            assert [(row["id"], row["rank"]) for row in rows] == [(a[0], 1), (b[0], 2)]
        assert rm.get_room(code)["status"] == "waiting"
        assert database.get_user_history(a[0])["items"][0]["snapshot"]["ranking"] == rows


def test_ten_minute_growth_event_and_population_schedule():
    gs = fish.create([{"id": 1, "username": "Diver"}], 81, "medium", True, True)
    assert fish.snapshot(gs)["remaining"] == 600
    assert fish.snapshot(gs)["players"][0]["growth_to"] == 7
    gs["players"][1].update(immune=600 * fish.HZ + 1, score=7)
    taken = fish.snapshot(gs)["players"][0]
    assert (taken["tier"], taken["growth_from"], taken["growth_to"]) == (2, 7, 28)
    events = set()
    apex = set()
    for _ in range(600 * fish.HZ):
        fish.advance(gs, {1})
        assert len(gs["fish"]) <= gs["population"] + max(8, gs["population"] // 4)
        if gs["event"]:
            events.add(gs["event"])
        if gs["frame"] % (30 * fish.HZ) == 0:
            apex.add(max(f["r"] for f in gs["fish"]))
        if gs["frame"] == 50 * fish.HZ:
            assert fish.snapshot(gs)["upcoming"] == ("school", 5 * fish.HZ)
        if gs["frame"] == 77 * fish.HZ:
            assert gs["event"] is None
            assert not any(f.get("expires") for f in gs["fish"])
    assert events == {"school", "current"}
    assert 105 in apex
    assert gs["finished"] and fish.snapshot(gs)["remaining"] == 0


def test_legacy_saved_round_retains_its_deadline():
    gs = fish.create([{"id": 1, "username": "Diver"}], 11, "small", False, False)
    del gs["duration"]
    gs["frame"] = 120 * fish.HZ - 1
    fish.advance(gs, {1})
    assert gs["finished"] and fish.snapshot(gs)["duration"] == 120
