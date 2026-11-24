"""Simulation and real room/WebSocket regression for 深渊回声."""
from contextlib import ExitStack

import pytest

from app import database, persistence, room_manager as rm, room_settings, security, ws
from app.game import abyss, abyss_data as data


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "abyss.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "abyss-test-secret-at-least-32-characters")
    for name in ("_rooms", "_dc_tasks", "_abyss_tasks"):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(ws, "_connections", {})
    monkeypatch.setattr(ws, "_send_tails", {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [ws._broadcast_lobby_after_game_end])
    monkeypatch.setattr(persistence, "_enabled", False)
    return ws_client


def _recv(socket, kind, match=lambda _: True):
    for _ in range(48):
        message = socket.receive_json()
        if message["type"] == kind and match(message):
            return message
    raise AssertionError(f"missing {kind}")


def _register(client, index=0):
    response = client.post("/api/auth/register", json={
        "username": f"abyss_diver_{index}", "password": "dive1234"})
    assert response.status_code == 200
    return response.json()


def _open_room(client, stack, count=2):
    users = []
    sockets = []
    for index in range(count):
        data = _register(client, index)
        users.append((data["user"]["id"], data["token"],
                      {"Authorization": f"Bearer {data['token']}"}))
    response = client.post("/api/rooms", json={"game": "abyss"}, headers=users[0][2])
    assert response.status_code == 200 and response.json()["max_players"] == 4
    code = response.json()["code"]
    for _, _, headers in users[1:]:
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=headers).status_code == 200
    for _, token, _ in users:
        socket = stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={token}"))
        assert _recv(socket, "sync")["room"]["game"] == "abyss"
        sockets.append(socket)
    for socket in sockets[1:]:
        socket.send_json({"type": "ready"})
    if count > 1:
        _recv(sockets[0], "lobby", lambda m: all(p["ready"] for p in m["room"]["players"][1:]))
    sockets[0].send_json({"type": "start"})
    for socket in sockets:
        assert len(_recv(socket, "game_start")["game"]["players"]) == count
    return code, users, sockets


def test_room_combat_selection_reconnect_finish_and_restart(client):
    with ExitStack() as stack:
        code, users, sockets = _open_room(client, stack)
        uid = users[0][0]
        room = rm.get_room(code)
        gs = room["game_state"]
        start_x = gs["players"][uid]["x"]
        sockets[0].send_json({"type": "abyss_move", "dx": 3, "dy": 0})
        assert _recv(sockets[0], "error")["message"] == "移动方向无效"
        sockets[0].send_json({"type": "abyss_move", "dx": 1, "dy": 0})
        _recv(sockets[1], "abyss_state", lambda m: m["game"]["players"][0]["position"][0] > start_x)
        gs["xp"] = abyss.threshold(gs)
        view = _recv(sockets[0], "abyss_state", lambda m: bool(m["game"]["choice_seconds"]))["game"]
        for i, socket in enumerate(sockets):
            player = view["players"][i]
            assert len(player["choices"]) == 3
            socket.send_json({"type": "abyss_pick", "level": view["level"], "choice": player["choices"][0]})
        _recv(sockets[0], "abyss_state", lambda m: m["game"]["level"] == 2 and not m["game"]["choice_seconds"])
        with client.websocket_connect(f"/ws/room/{code}?token={users[1][1]}") as again:
            assert _recv(again, "sync")["game"]["level"] == 2
            gs.update(elapsed=14399, boss=True, boss_dead=True)
            for socket in (sockets[0], again):
                assert _recv(socket, "game_over")["game"]["result"] == "won"
                assert _recv(socket, "lobby", lambda m: m["room"]["status"] == "waiting")["room"]["round"] == 1
            history = database.get_user_history(uid)["items"]
            assert len(history) == 1 and history[0]["snapshot"]["result"] == "won"
            assert [r["result"] for r in history[0]["results"]] == ["win", "win"]
            again.send_json({"type": "ready"})
            _recv(sockets[0], "lobby", lambda m: m["room"]["players"][1]["ready"])
            sockets[0].send_json({"type": "start"})
            fresh = _recv(again, "game_start")["game"]
            assert fresh["frame"] == 0 and fresh["level"] == 1


def test_host_leaves_and_other_diver_continues(client):
    with ExitStack() as stack:
        code, users, sockets = _open_room(client, stack)
        assert client.post(f"/api/rooms/{code}/leave", headers=users[0][2]).status_code == 200
        room = _recv(sockets[1], "lobby", lambda m: m["room"]["host_id"] == users[1][0])["room"]
        assert room["status"] == "playing"
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=users[0][2]).status_code == 409
        state = _recv(sockets[1], "abyss_state", lambda m: m["game"]["players"][0]["out"])["game"]
        assert state["players"][1]["hp"] > 0


def test_rescue_choice_timeout_and_final_boss():
    gs = abyss.create([{"id": 1, "username": "a"}, {"id": 2, "username": "b"}], 3)
    p, q = gs["players"].values()
    q.update(x=p["x"], hp=0, down=300)
    gs["spawn_budget"] = -100
    for _ in range(40):
        abyss.advance(gs)
    assert q["hp"] == 35
    gs["xp"] = abyss.threshold(gs)
    abyss.advance(gs)
    assert gs["level"] == 2
    assert not abyss.pick(gs, 1, 1, p["choices"][0])
    p["weapons"] = {"rivet": 1}
    p["choices"] = ["rivet"]
    assert abyss.pick(gs, 1, 2, "rivet")
    assert p["weapons"]["rivet"] == 2
    for _ in range(120):
        abyss.advance(gs)
    assert not q["choices"]
    gs["elapsed"] = 13199
    abyss.advance(gs)
    assert any(enemy["kind"] == "warden" for enemy in gs["enemies"])
    gs["elapsed"] = 14399
    abyss.advance(gs)
    assert gs["result"] == "timeout"


@pytest.mark.parametrize("count,expected", [(1, 5), (4, 12)])
def test_supported_room_sizes(client, count, expected):
    with ExitStack() as stack:
        code, _, sockets = _open_room(client, stack, count)
        for socket in sockets:
            view = _recv(socket, "abyss_state")["game"]
            assert view["next_xp"] == expected
        assert rm.get_room(code)["status"] == "playing"


def _weapon_trial(weapon, evolved=False):
    gs = abyss.create([{"id": 1, "username": "test"}], 7)
    p = gs["players"][1]
    p["weapons"] = {weapon: 1 if evolved else data.WEAPONS[weapon]["max_level"]}
    p["cycles"] = {weapon: 0}
    p["invincible"] = 10 ** 9
    gs["spawn_budget"] = -10000
    gs["enemies"] = []
    abyss._spawn(gs, "warden", p)
    dummy = gs["enemies"][0]
    dummy.update(x=p["x"], y=p["y"], radius=300, speed=0, hp=1000, max_hp=1000)
    for _ in range(120):
        abyss.advance(gs)
        if not gs["enemies"]:
            break
    return 1000 - (gs["enemies"][0]["hp"] if gs["enemies"] else 0)


@pytest.mark.parametrize("weapon", (*data.BASE_WEAPON_ORDER, *data.EVOLVED_WEAPONS))
def test_all_weapons_deal_authoritative_damage(weapon):
    evolved = weapon in data.EVOLVED_WEAPONS
    assert _weapon_trial(weapon, evolved) > 0


def test_weapon_tables_match_vampire_survivors():
    assert data.WEAPONS["whip"]["levels"][-1]["damage"] == 40
    assert data.WEAPONS["whip"]["levels"][-1]["amount"] == 2
    assert data.WEAPONS["rivet"]["levels"][-1]["damage"] == 30
    assert data.WEAPONS["rivet"]["levels"][-1]["amount"] == 4
    assert data.WEAPONS["harpoon"]["levels"][-1]["damage"] == 16.5
    assert data.WEAPONS["harpoon"]["levels"][-1]["amount"] == 6
    assert data.WEAPONS["axe"]["levels"][-1]["damage"] == 80
    assert data.WEAPONS["axe"]["levels"][-1]["pierce"] == 7
    assert data.WEAPONS["bible"]["levels"][-1]["amount"] == 4
    assert data.WEAPONS["spore"]["levels"][-1]["amount"] == 4
    assert data.WEAPONS["arc"]["levels"][-1]["amount"] == 6
    assert data.WEAPONS["pentagram"]["levels"][-1]["cooldown"] == 60
    assert data.WEAPONS["pentagram"]["levels"][-1]["preserve"] == .65


def test_xp_curve_matches_vampire_survivors():
    assert data.xp_step(1) == 5
    assert data.xp_step(2) == 15
    assert data.xp_step(9) == 85
    assert data.xp_step(19) == 785
    assert data.xp_step(20) == 198
    assert data.xp_step(39) == 2845
    assert data.xp_step(40) == 461
    assert data.xp_total(2) == 5
    assert data.xp_total(10) == 405
    assert data.xp_total(20) == 2405
    assert data.xp_total(40) == 11235
    assert data.xp_total(41) == 11696


def test_opening_health_and_upgrade_curve():
    gs = abyss.create([{"id": 1, "username": "test"}], 1)
    p = gs["players"][1]
    gs["elapsed"] = 30 * 20
    abyss._spawn(gs, "drifter", p)
    assert gs["enemies"][-1]["hp"] == 10
    gs["elapsed"] = 100 * 20
    abyss._spawn(gs, "drifter", p)
    assert gs["enemies"][-1]["hp"] == 14
    gs["level"] = 9
    assert abyss.threshold(gs) == 85
    gs["level"] = 19
    assert abyss.threshold(gs) == 785
    gs["level"] = 17
    gs["xp"] = abyss.threshold(gs)
    abyss._offer(gs)
    assert "rivet" in p["choices"]
    assert any(choice in data.WEAPONS or choice in data.PASSIVES for choice in p["choices"])


def test_six_weapon_and_six_passive_slots():
    gs = abyss.create([{"id": 1, "username": "test"}], 1)
    p = gs["players"][1]
    p["weapons"] = {weapon: 1 for weapon in data.BASE_WEAPON_ORDER[:6]}
    p["passives"] = {passive: 1 for passive in data.PASSIVE_ORDER[:6]}
    assert len(p["weapons"]) == data.MAX_WEAPONS
    assert len(p["passives"]) == data.MAX_PASSIVES
    assert not abyss._upgrade_weapon(p, data.BASE_WEAPON_ORDER[6])
    assert not abyss._upgrade_passive(p, data.PASSIVE_ORDER[6])
    upgrades, news = abyss._available_choices(p)
    assert not any(item in data.WEAPONS for item in news)
    assert not any(item in data.PASSIVES for item in news)


def test_thirteen_passives_and_bonuses():
    assert len(data.PASSIVES) == 13
    assert not {"tiragisu", "stone_mask", "skull_omaniac"} & set(data.PASSIVES)
    p = {"passives": {"spinach": 5, "empty_tome": 5, "hollow_heart": 5,
                      "duplicator": 2, "attractorb": 5, "wings": 5}}
    stats = data.player_stats(p["passives"])
    assert stats["might"] == pytest.approx(1.5)
    assert stats["cooldown"] == pytest.approx(.6)
    assert stats["max_hp"] == pytest.approx(200)
    assert stats["amount"] == 2
    assert stats["magnet"] == pytest.approx(3.980025)
    assert stats["move_speed"] == pytest.approx(1.5)


def test_chest_evolves_maxed_weapon_with_passive():
    gs = abyss.create([{"id": 1, "username": "test"}], 1)
    p = gs["players"][1]
    p["weapons"] = {"rivet": 8}
    p["passives"] = {"empty_tome": 1}
    gs["chests"] = [{"id": abyss._id(gs), "x": p["x"], "y": p["y"]}]
    abyss._collect(gs, [p])
    assert "rivet" not in p["weapons"]
    assert p["weapons"]["holy_wand"] == 1
    assert p["passives"]["empty_tome"] == 1
    assert gs["chest_events"] and "圣铆炮" in gs["chest_events"][-1]["text"]


def test_room_settings_drive_duration_and_pace(client):
    account = _register(client)
    room = rm.create_room(account["user"], None, None, "abyss")
    room["abyss_duration"] = 1800
    room["abyss_xp_pace"] = "fast"
    gs = rm._start_abyss(room)
    assert gs["duration"] == 1800
    assert gs["xp_pace"] == .8
    assert room_settings.spec_for("abyss", "duration") is not None
    assert room_settings.spec_for("abyss", "xp_pace") is not None


def test_hot_restart_round_trip_keeps_abyss_state():
    gs = abyss.create([{"id": 1, "username": "test"}], 11)
    p = gs["players"][1]
    p["weapons"] = {"rivet": 1, "bible": 1}
    p["cycles"] = {"rivet": 0, "bible": 0}
    p["invincible"] = 10 ** 9
    p["orbit_hits"]["bible"] = {123: 45}
    gs["spawned"].add("elite0")
    gs["spawn_budget"] = -100
    abyss._spawn(gs, "drifter", p)
    for _ in range(12):
        abyss.advance(gs)
    blob = persistence.dumps_state(gs)
    restored = persistence.loads_state(blob)
    assert restored["players"][1]["orbit_hits"]["bible"] == {123: 45}
    assert "elite0" in restored["spawned"]
    assert isinstance(restored["rng"], type(gs["rng"]))
    before = restored["frame"]
    abyss.advance(restored)
    assert restored["frame"] == before + 1


def test_edge_spawns_keep_clear_of_diver():
    gs = abyss.create([{"id": 1, "username": "test"}], 1)
    p = gs["players"][1]
    p.update(x=22, y=22)
    for kind in abyss.ENEMIES:
        for _ in range(10):
            abyss._spawn(gs, kind, p)
            assert abyss._gap(gs["enemies"][-1], p) >= 234