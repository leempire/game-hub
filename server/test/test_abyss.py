"""Simulation and real room/WebSocket regression for 深渊回声."""
import asyncio
import json
from contextlib import ExitStack
from math import pi
from unittest.mock import patch

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
            gs.update(elapsed=14399, boss=True, boss_dead=True, salvage=175)
            gs["players"][uid]["weapons"] = {"holy_wand": 1}
            for socket in (sockets[0], again):
                assert _recv(socket, "game_over")["game"]["result"] == "won"
                assert _recv(socket, "lobby", lambda m: m["room"]["status"] == "waiting")["room"]["round"] == 1
            history = database.get_user_history(uid)["items"]
            assert len(history) == 1 and history[0]["snapshot"]["result"] == "won"
            summary = history[0]["snapshot"]
            assert (summary["seconds"], summary["salvage"], summary["boss_dead"]) == (720, 175, True)
            assert summary["players"][0]["weapons"] == {"holy_wand": 1}
            assert "position" not in summary["players"][0]
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
    for _ in range(abyss.UPGRADE_CHOICE_TICKS):
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


@pytest.mark.parametrize("weapon", ["rivet", "holy_wand", "cross", "heaven_sword", "charge", "hellfire"])
@pytest.mark.parametrize("map_size", ["medium", "endless"])
def test_targeted_weapon_does_not_spend_its_cooldown_without_a_target(weapon, map_size):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42, map_size=map_size)
    diver = gs["players"][1]
    diver["weapons"] = {weapon: 1}
    diver["cycles"].clear()
    gs["spawn_budget"] = -100
    if map_size == "endless":
        abyss._spawn(gs, "drifter", diver)
        gs["enemies"][-1].update(x=diver["x"] - 4000, y=diver["y"], speed=0)
    abyss.advance(gs)
    assert not gs["shots"] and weapon not in diver["cycles"]

    abyss._spawn(gs, "drifter", diver)
    gs["enemies"][-1].update(x=diver["x"] + 150, y=diver["y"], speed=0)
    abyss.advance(gs)
    assert gs["shots"][0]["weapon"] == weapon
    assert gs["shots"][0]["vx"] > 0
    cooldown = data.weapon_stats(weapon, 1, diver["stats"])["cooldown"]
    assert diver["cycles"][weapon] == gs["elapsed"] + round(cooldown / abyss.STEP)


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
    assert abyss.threshold(gs) == 185
    gs["level"] = 17
    gs["xp"] = abyss.threshold(gs)
    abyss._offer(gs)
    assert "rivet" in p["choices"]
    assert any(choice in data.WEAPONS or choice in data.PASSIVES for choice in p["choices"])


@pytest.mark.parametrize(
    "duration, milestones",
    [(720, (185, 445)), (1200, (452, 1512)), (1800, (785, 2845))],
)
def test_milestone_costs_follow_room_duration(duration, milestones):
    gs = abyss.create([{"id": 1, "username": "test"}], 42, duration=duration)
    for level, expected in zip((19, 39), milestones):
        gs["level"] = level
        assert abyss.snapshot(gs)["next_xp"] == expected
    gs["level"] = 20
    assert abyss.threshold(gs) == 198


def test_milestone_combines_room_pace_with_team_scaling_and_keeps_growth_reward():
    players = [{"id": i, "username": str(i)} for i in range(4)]
    gs = abyss.create(players, 42, xp_pace=.8)
    gs["level"] = 19
    gs["xp"] = 350
    assert abyss.threshold(gs) == 348
    abyss._offer(gs)
    assert (gs["level"], gs["xp"], gs["growth_boost"]) == (20, 2, 2.0)
    assert abyss.snapshot(gs)["growth_boost"] == 2.0
    for player in gs["players"].values():
        assert player["choices"]
    gs["xp"] = abyss.threshold(gs)
    abyss._offer(gs)
    assert abyss.snapshot(gs)["growth_boost"] == 1.0


def test_milestone_boost_reaches_live_battle_snapshot(client):
    with ExitStack() as stack:
        code, _, sockets = _open_room(client, stack, count=1)
        gs = rm.get_room(code)["game_state"]
        gs["spawn_budget"] = -1000
        gs["enemies"].clear()
        gs["level"] = 19
        gs["xp"] = 184
        ws_client = sockets[0]
        before = _recv(ws_client, "abyss_state", lambda msg: msg["game"]["level"] == 19)["game"]
        assert (before["xp"], before["next_xp"], before["growth_boost"]) == (184, 185, 1)
        gs["xp"] = 185
        offered = _recv(ws_client, "abyss_state", lambda msg: msg["game"]["level"] == 20)["game"]
        assert (offered["next_xp"], offered["growth_boost"]) == (198, 2)
        assert offered["choice_seconds"] > 0



def test_legacy_match_keeps_original_milestone_after_restore():
    gs = abyss.create([{"id": 1, "username": "test"}], 42)
    del gs["milestone_scale"]
    gs["level"] = 19
    gs["xp"] = 600
    restored = persistence.loads_state(persistence.dumps_state(gs))
    assert (abyss.threshold(restored), restored["xp"]) == (785, 600)


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


def test_chest_opens_for_nearest_online_diver_in_range():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2, 3)], 42)
    first, offline, nearest = gs['players'].values()
    first.update(x=739, y=500)
    offline.update(x=700, y=500)
    nearest.update(x=758, y=500)
    abyss._spawn_chest(gs, 700, 500)

    abyss._collect(gs, [first, offline, nearest], online_ids={1, 3})
    assert len(gs['chests']) == 1 and not gs['chest_events']

    first['x'] = 736
    nearest['x'] = 713
    chest = gs['chests'][0]
    with patch.object(abyss, '_open_chest', wraps=abyss._open_chest) as open_chest:
        abyss._collect(gs, [first, offline, nearest], online_ids={1, 3})
    open_chest.assert_called_once_with(gs, chest, nearest)
    assert not gs['chests']
    assert {event['player'] for event in gs['chest_events']} == {1, 2, 3}


def test_room_settings_drive_duration_pace_and_map_size(client):
    account = _register(client)
    room = rm.create_room(account["user"], None, None, "abyss")
    room["abyss_duration"] = 1800
    room["abyss_xp_pace"] = "fast"
    room["abyss_map_size"] = "large"
    gs = rm._start_abyss(room)
    assert gs["duration"] == 1800
    assert gs["xp_pace"] == .8
    assert abyss.snapshot(gs)["bounds"] == [2160, 1500]
    assert room_settings.spec_for("abyss", "map_size") is not None
    assert room_settings.spec_for("abyss", "duration") is not None
    assert room_settings.spec_for("abyss", "xp_pace") is not None


@pytest.mark.parametrize(("size", "bounds"), [
    ("small", (1080, 750)), ("medium", (1440, 1000)),
    ("large", (2160, 1500)), ("endless", (None, None)),
])
def test_map_size_controls_spawns_movement_and_projectile_bounce(size, bounds):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42, map_size=size)
    diver = gs["players"][1]
    width, height = bounds
    assert abyss.snapshot(gs)["bounds"] == list(bounds)
    assert (diver["x"], diver["y"]) == ((width or 1440) / 2, (height or 1000) / 2)

    edge = (width or 1440) - abyss.WALL_INSET
    diver["x"] = edge - 1
    abyss.move(gs, 1, 1, 0)
    abyss.advance(gs)
    assert diver["x"] == (edge if width else edge - 1 + abyss.data.PLAYER_SPEED * abyss.STEP)

    stats = data.weapon_stats("rune", 1, diver["stats"])
    abyss._shot(gs, diver, "rune", stats, (width or 1440) - 15, 100, 800, 0,
                kind="rune", life=10, radius=12, bounce=True)
    abyss._step_shots(gs)
    shot = gs["shots"][-1]
    assert (shot["x"], shot["vx"]) == ((width - 12, -800) if width else (1465, 800))

    if width is None:
        diver.update(x=-20, y=-40)
        abyss.move(gs, 1, -1, 0)
        abyss.advance(gs)
        assert diver["x"] < -20 and diver["y"] == -40


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

@pytest.mark.parametrize(
    "fraction, roster",
    [
        (0, {"drifter", "needlefish"}),
        (1 / 6, {"drifter", "needlefish", "eel", "crab", "jelly"}),
        (5 / 12, {"drifter", "needlefish", "eel", "crab", "jelly", "angler"}),
        (2 / 3, {"drifter", "needlefish", "eel", "crab", "jelly", "angler"}),
    ],
)
def test_wave_roster_by_phase(fraction, roster):
    gs = abyss.create([{"id": 1, "username": "Diver"}], seed=42)
    gs["elapsed"] = round(gs["duration"] * fraction / abyss.STEP) - 1
    gs["spawned"].update(("elite0", "elite1", "boss"))
    gs["players"][1]["weapons"].clear()

    seen = set()
    for _ in range(100):
        gs["spawn_budget"] = 1
        abyss.advance(gs)
        seen.add(gs["enemies"].pop()["kind"])

    assert seen == roster


@pytest.mark.parametrize(
    "kind, hp, speed, radius, damage, xp",
    [
        ("needlefish", 8, 125, 8, 4, 1),
        ("jelly", 26, 58, 19, 8, 2),
        ("angler", 38, 87, 19, 10, 3),
    ],
)
def test_new_enemy_combat_and_rewards(kind, hp, speed, radius, damage, xp):
    gs = abyss.create([{"id": 1, "username": "Diver"}], seed=42)
    diver = gs["players"][1]
    abyss._spawn(gs, kind, diver)
    enemy = gs["enemies"][0]
    assert (enemy["hp"], enemy["speed"], enemy["radius"]) == (hp, speed, radius)
    assert abyss.snapshot(gs)["enemies"][0][1] == kind

    enemy.update(x=diver["x"], y=diver["y"])
    starting_hp = diver["hp"]
    abyss._contact_damage(gs, [diver])
    abyss._contact_damage(gs, [diver])
    assert diver["hp"] == starting_hp - damage

    assert abyss._damage(gs, diver, enemy, hp)
    assert gs["loot"][0]["xp"] == xp


def test_blast_hits_at_the_combined_radius_but_not_beyond_it():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    player = gs['players'][1]
    for x in (600, 602):
        abyss._spawn(gs, 'drifter', player)
        gs['enemies'][-1].update(x=x, y=500, hp=60)
    abyss._area_damage(gs, player, 550, 500, 39, 12, weapon='charge', knockback=2)
    edge, outside = gs['enemies']
    assert (edge['hp'], edge['x']) == (48, 612)
    assert (outside['hp'], outside['x']) == (60, 602)


def test_contact_damage_excludes_tangent_enemy_and_uses_first_overlap():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    diver.update(x=700, y=500)
    abyss._spawn(gs, 'angler', diver)
    tangent = gs['enemies'][0]
    tangent.update(x=diver['x'] + tangent['radius'] + 10, y=diver['y'])
    abyss._contact_damage(gs, [diver])
    assert diver['hp'] == 100

    abyss._spawn(gs, 'needlefish', diver)
    abyss._spawn(gs, 'jelly', diver)
    for enemy in gs['enemies'][1:]:
        enemy.update(x=diver['x'], y=diver['y'])
    abyss._contact_damage(gs, [diver])
    assert diver['hp'] == 100 - abyss.CONTACT_DAMAGE['needlefish']


def _warden_trial():
    gs = abyss.create([{"id": 1, "username": "a"}, {"id": 2, "username": "b"}], 3)
    p, q = gs["players"].values()
    p.update(x=600, y=450, weapons={})
    q.update(x=900, y=450, weapons={})
    gs["spawn_budget"] = -1000
    abyss._spawn(gs, "warden", p)
    warden = gs["enemies"][0]
    warden.update(x=720, y=750, speed=0, next_attack=1)
    return gs, p, q, warden


def test_warden_warning_freezes_during_choice_and_checks_positions_on_impact():
    gs, p, q, _ = _warden_trial()
    abyss.advance(gs)
    warning = abyss.snapshot(gs)["warden_attack"]
    assert warning["kind"] == "strike" and not warning["impact"]
    assert warning["duration_ms"] == 1100
    assert warning["zones"] == [[600, 450, 90], [900, 450, 90]]
    abyss._offer(gs)
    for _ in range(5):
        abyss.advance(gs)
    assert abyss.snapshot(gs)["warden_attack"] == warning
    for diver in (p, q):
        abyss.pick(gs, diver["id"], gs["level"], diver["choices"][0])
        diver["weapons"].clear()
    q["y"] += 91
    original_hp = p["hp"]
    q_hp = q["hp"]
    for _ in range(21):
        abyss.advance(gs)
    assert p["hp"] == original_hp
    abyss.advance(gs)
    assert abyss.snapshot(gs)["warden_attack"]["impact"]
    assert abyss.snapshot(gs)["warden_attack"]["duration_ms"] == 400
    assert p["hp"] == original_hp - 16
    assert q["hp"] == q_hp
    for _ in range(8):
        abyss.advance(gs)
    assert abyss.snapshot(gs)["warden_attack"] is None
    assert p["hp"] == original_hp - 16


@pytest.mark.parametrize("kind, enraged, radius, damage", [
    ("strike", False, 90, 16), ("strike", True, 105, 20),
    ("pulse", False, 160, 20), ("pulse", True, 180, 24),
])
@pytest.mark.parametrize("inside", [False, True])
def test_warden_snapshot_matches_fractional_hit_boundary(kind, enraged, radius, damage, inside):
    gs, p, q, warden = _warden_trial()
    x, y = 720.375, 500.625
    warden.update(next_attack_kind=kind)
    if enraged:
        warden["hp"] = warden["max_hp"] * .45
    if kind == "pulse":
        warden.update(x=x, y=y)
    p.update(x=x, y=y)
    q.update(x=x + 20, y=y)  # Both divers start in the same strike zone.
    gs["elapsed"] = 1
    abyss._step_warden(gs, warden, [p, q])

    warning = abyss.snapshot(gs)["warden_attack"]
    assert warning["zones"] == [[x, y, radius]]
    assert not warning["impact"]
    q["x"] = x + radius - .125 if inside else x - radius - .125
    original_hp = q["hp"]
    gs["elapsed"] = warden["attack"]["resolve_at"]
    abyss._step_warden(gs, warden, [p, q])

    impact = abyss.snapshot(gs)["warden_attack"]
    assert impact["impact"] and impact["zones"] == warning["zones"]
    assert q["hp"] == original_hp - (damage if inside else 0)


def test_warden_enrage_shortens_next_recovery_without_modifying_telegraphed_attack():
    gs, lead, _, warden = _warden_trial()
    abyss.advance(gs)
    original_attack = dict(warden["attack"])
    normal_recovery = warden["next_attack"]
    abyss._damage(gs, lead, warden, warden["hp"] * .6)
    assert warden["attack"] == original_attack
    assert warden["next_attack"] == normal_recovery - 20
    abyss._damage(gs, lead, warden, 1)
    assert warden["next_attack"] == normal_recovery - 20


def test_enraged_warden_pulse_survives_restore_and_cancels_on_death():
    gs, p, _, warden = _warden_trial()
    warden.update(hp=500, speed=80, next_attack_kind="pulse")
    abyss.advance(gs)
    attack = abyss.snapshot(gs)["warden_attack"]
    assert attack["kind"] == "pulse" and attack["zones"][0][2] == 180
    assert warden["next_attack"] - warden["attack"]["resolve_at"] == 54
    anchor = warden["x"], warden["y"]
    restored = persistence.loads_state(persistence.dumps_state(gs))
    assert abyss.snapshot(restored)["warden_attack"] == attack
    abyss.advance(gs)
    assert (warden["x"], warden["y"]) == anchor
    abyss._damage(gs, p, warden, 500)
    abyss.advance(gs)
    assert gs["boss_dead"] and abyss.snapshot(gs)["warden_attack"] is None


def test_warden_pulse_respects_armor_invulnerability_and_range():
    gs, p, q, warden = _warden_trial()
    warden.update(x=720, y=450, next_attack_kind="pulse")
    p["stats"]["armor"] = 3
    q["x"] = warden["x"] + 161
    abyss.advance(gs)
    assert abyss.snapshot(gs)["warden_attack"]["zones"] == [[720, 450, 160]]
    p_hp, q_hp = p["hp"], q["hp"]
    for _ in range(27):
        abyss.advance(gs)
    assert p["hp"] == p_hp
    abyss.advance(gs)
    assert p["hp"] == p_hp - 17
    assert q["hp"] == q_hp
    abyss._hurt_diver(gs, p, 50)
    assert p["hp"] == p_hp - 17


def test_upgrade_preview_uses_effective_stats_and_lists_evolution_requirements():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    diver["weapons"] = {"rivet": 2, "ring": 4}
    diver["passives"] = {"empty_tome": 1, "attractorb": 1}
    abyss._refresh_stats(diver)
    diver["choices"] = ["rivet", "attractorb", "spellbinder", "repair", "harpoon", "pentagram"]
    player = abyss.snapshot(gs)["players"][0]

    assert player["choice_changes"] == {
        "rivet": [["cooldown", 1.104, .92]],
        "attractorb": [["magnet", 1.5, 1.995]],
        "spellbinder": [["duration_scale", 1.0, 1.1]],
    }
    assert player["repair_health"] == {"repair": [100, 100]}
    assert player["new_weapon_stats"] == {
        "harpoon": [["damage", 6.5], ["cooldown", 0.92]],
        "pentagram": [["cooldown", 82.8]],
    }
    assert player["evolution_requirements"]["rivet"] == {
        "target": "holy_wand", "passive": "empty_tome", "rank": 8,
    }
    assert diver["weapons"] == {"rivet": 2, "ring": 4}
    assert diver["passives"] == {"empty_tome": 1, "attractorb": 1}


@pytest.mark.parametrize(("health", "team_size"), [(100, 1), (92, 1), (100, 2)])
def test_maxed_loadout_automatically_collects_only_salvage(health, team_size):
    gs = abyss.create([{"id": uid, "username": str(uid)} for uid in range(1, team_size + 1)], 42)
    diver = gs["players"][1]
    diver.update(
        hp=health,
        weapons={weapon: data.WEAPONS[weapon]["max_level"] for weapon in data.BASE_WEAPON_ORDER[:data.MAX_WEAPONS]},
        passives={passive: data.PASSIVES[passive]["max_level"] for passive in data.PASSIVE_ORDER[:data.MAX_PASSIVES]},
    )
    gs["xp"] = abyss.threshold(gs)
    abyss.advance(gs)
    if health == 100:
        assert diver["choices"] == []
        assert bool(abyss.snapshot(gs)["choice_seconds"]) == (team_size > 1)
        assert gs["salvage"] == 100
        elapsed = gs["elapsed"]
        abyss.advance(gs)
        assert gs["elapsed"] == elapsed + int(team_size == 1) and gs["salvage"] == 100
    else:
        assert set(diver["choices"]) == {"repair", "salvage"}
        assert abyss.snapshot(gs)["choice_seconds"] > 0
        assert gs["salvage"] == 0
        assert abyss.pick(gs, 1, gs["level"], "salvage")
        assert gs["salvage"] == 100


def test_upgrade_pause_discards_held_and_late_movement_until_battle_resumes():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    abyss.move(gs, 1, 1, 0)
    gs["xp"] = abyss.threshold(gs)
    abyss.advance(gs)
    assert gs["pick_until"] and diver["input_until"] == 0

    stopped_at = diver["x"]
    abyss.move(gs, 1, -1, 0)
    abyss.move(gs, 1, 0, 0, preserve_tap=True)
    assert diver["input_until"] == 0
    assert abyss.pick(gs, 1, gs["level"], diver["choices"][0])
    abyss.advance(gs)
    assert gs["pick_until"] == 0 and diver["x"] == stopped_at

    abyss.move(gs, 1, -1, 0)
    abyss.advance(gs)
    assert diver["x"] < stopped_at


def test_upgrade_auto_picks_first_choice_after_ten_seconds(monkeypatch):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    monkeypatch.setattr(abyss, "_choices_for", lambda *_: ["rivet", "repair"])
    gs["xp"] = abyss.threshold(gs)
    abyss.advance(gs)
    elapsed = gs["elapsed"]
    assert abyss.snapshot(gs)["choice_seconds"] == 10

    for _ in range(199):
        abyss.advance(gs)
    assert gs["elapsed"] == elapsed
    assert abyss.snapshot(gs)["choice_seconds"] == 1
    assert diver["weapons"]["rivet"] == 1

    abyss.advance(gs)
    assert abyss.snapshot(gs)["choice_seconds"] == 0
    assert gs["elapsed"] == elapsed + 1
    assert diver["weapons"]["rivet"] == 2


def test_rescue_progress_tracks_the_nearest_online_teammate_without_reset():
    gs = abyss.create([{"id": uid, "username": str(uid)} for uid in (1, 2, 3)], 42)
    first, second, target = gs["players"].values()
    target.update(x=700, y=500, hp=0, down=300)
    first.update(x=710, y=500)
    second.update(x=730, y=500)
    abyss.advance(gs, online_ids={1, 2, 3})
    downed = abyss.snapshot(gs)["players"][2]
    assert (downed["rescue"], downed["rescuer"]) == (1, 1)

    abyss.advance(gs, online_ids={2, 3})
    downed = abyss.snapshot(gs)["players"][2]
    assert (downed["rescue"], downed["rescuer"]) == (2, 2)

    second["x"] = 760
    abyss.advance(gs, online_ids={2, 3})
    downed = abyss.snapshot(gs)["players"][2]
    assert downed["rescue"] == 0 and "rescuer" not in downed


@pytest.mark.parametrize("disconnect_at_offer", [False, True])
def test_offline_diver_gets_default_upgrade_without_holding_the_team(disconnect_at_offer):
    gs = abyss.create([{"id": 1, "username": "Lead"}, {"id": 2, "username": "Mate"}], 42)
    lead, mate = gs["players"].values()
    gs["xp"] = abyss.threshold(gs)
    abyss.advance(gs, online_ids={1} if disconnect_at_offer else {1, 2})
    assert lead["choices"]
    assert bool(mate["choices"]) is not disconnect_at_offer
    assert abyss.pick(gs, 1, gs["level"], lead["choices"][0])

    elapsed = gs["elapsed"]
    abyss.advance(gs, online_ids={1})
    assert gs["elapsed"] == elapsed + 1
    assert gs["pick_until"] == 0 and not mate["choices"]
    assert len(mate["weapons"]) + len(mate["passives"]) > 1 or mate["weapons"]["rivet"] > 1


@pytest.mark.parametrize(("health", "expected", "salvage"), [
    (64, "修复 30 点耐久", 0),
    (94, "修复 6 点耐久", 0),
    (100, "获得 25 打捞分", 25),
])
def test_maxed_chest_rewards_heal_actual_damage_or_grant_salvage(health, expected, salvage):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    diver.update(hp=health, weapons={"holy_wand": 1})
    assert abyss._chest_reward(gs, diver) == expected
    assert diver["hp"] == min(health + 30, 100)
    assert gs["salvage"] == salvage


@pytest.mark.parametrize(("choice", "health", "expected"), [
    ("repair", 64, 94),
    ("repair", 94, 100),
    ("rest", 64, 79),
])
def test_repair_preview_matches_health_after_selection(choice, health, expected):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    diver.update(hp=health, choices=[choice])
    assert abyss.snapshot(gs)["players"][0]["repair_health"][choice] == [health, expected]
    assert abyss.pick(gs, 1, gs["level"], choice)
    assert diver["hp"] == expected


def test_tick_encodes_once_and_skips_backpressured_connections(ws_client, monkeypatch):
    class Connection:
        def __init__(self):
            self.messages = []

        async def send_text(self, text):
            self.messages.append(text)

    gs = abyss.create([{"id": 1, "username": "潜水员"}], 42)
    room = {"code": "ABYSS1", "game_state": gs}
    fast = [Connection(), Connection(), Connection()]
    slow = Connection()
    monkeypatch.setattr(ws, "_connections", {room["code"]: dict(enumerate([*fast, slow]))})
    monkeypatch.setattr(ws, "_send_tails", {})

    async def exercise():
        blocked = asyncio.Event()
        wait_for_slow = asyncio.create_task(blocked.wait())
        ws._send_tails[id(slow)] = wait_for_slow
        try:
            with patch.object(abyss, "snapshot", wraps=abyss.snapshot) as snapshot:
                with patch.object(ws.json, "dumps", wraps=json.dumps) as encode:
                    ws.send_abyss_tick(room)
                    ws.send_abyss_tick(room)  # Every connection now has a pending send.
                    assert (snapshot.call_count, encode.call_count) == (1, 1)
                    await asyncio.gather(*(ws._send_tails[id(conn)] for conn in fast))
            message = fast[0].messages[0]
            assert all(conn.messages == [message] for conn in fast)
            assert "潜水员" in message
            assert json.loads(message)["game"] == abyss.snapshot(gs)
            assert not slow.messages
        finally:
            blocked.set()
            await wait_for_slow
        gs["frame"] = 8
        ws.send_abyss_tick(room)
        await asyncio.gather(*ws._send_tails.values())
        assert [json.loads(text)["game"]["frame"] for text in slow.messages] == [8]

    ws_client.portal.call(exercise)


def test_projectile_radius_rejection_preserves_tangent_hits_and_pierce_order():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    points = [(624, 500), (622, 522), (623, 500), (600, 523), (600, 500)]
    for x, y in points:
        abyss._spawn(gs, "drifter", diver)
        gs["enemies"][-1].update(x=x, y=y, hp=30)
    stats = data.weapon_stats("rivet", 1, diver["stats"])
    abyss._shot(gs, diver, "rivet", stats, 600, 500, 0, 0,
                kind="rivet", life=10, pierce=2, radius=12)
    abyss._step_shots(gs)
    assert [enemy["hp"] for enemy in gs["enemies"]] == [30, 30, 20, 20, 30]
    assert not gs["shots"]


def test_crowded_projectiles_keep_tie_order_after_knockback_crosses_a_band():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42, map_size="endless")
    diver = gs["players"][1]
    positions = [(100, 21), (94, 0), (84, 0), *((x, 400) for x in range(-1700, 1700, 30))]
    for x, y in positions:
        abyss._spawn(gs, "drifter", diver)
        gs["enemies"][-1].update(x=x, y=y, hp=100)
    stats = data.weapon_stats("rivet", 1, diver["stats"])
    # The first shot pushes the target from the preceding band into the second shot's reach.
    abyss._shot(gs, diver, "rivet", {**stats, "knockback": 2}, 90, 0, 0, 0,
                kind="rivet", life=10, pierce=1, radius=5)
    abyss._shot(gs, diver, "rivet", stats, 126, 0, 0, 0,
                kind="rivet", life=10, pierce=1, radius=12)
    # A stationary shot overlapping two bands must still hit the first roster entry.
    abyss._shot(gs, diver, "rivet", stats, 96, 9, 0, 0,
                kind="rivet", life=10, pierce=1, radius=5)
    for _ in range(24):
        abyss._shot(gs, diver, "rivet", stats, 0, 100, 0, 0, kind="rivet", life=10, delay=2)
    abyss._step_shots(gs)
    assert [enemy["hp"] for enemy in gs["enemies"][:3]] == [90, 80, 100]


@pytest.mark.parametrize(("crit", "radius"), [(0, 16), (1, 20)])
def test_piercing_hit_emits_directional_impact_before_projectile_is_consumed(crit, radius):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    abyss._spawn(gs, "drifter", diver)
    gs["enemies"][-1].update(x=600, y=524, hp=100)
    stats = {**data.weapon_stats("axe", 1, diver["stats"]), "crit": crit}
    abyss._shot(gs, diver, "axe", stats, 600, 500, 0, 600,
                kind="axe", life=10, pierce=3, radius=16)
    abyss._step_shots(gs)
    assert gs["shots"][0]["pierce"] == 2
    [impact] = gs["effects"]
    assert (impact["kind"], impact["x"], impact["y"], impact["r"]) == ("impact", 600, 500, radius)
    assert impact["angle"] == pytest.approx(pi / 2, abs=.001)
    assert abyss.snapshot(gs)["players"][0]["damage_dealt"] > 0


def test_defeat_bursts_cap_normal_kills_without_suppressing_boss():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    diver = gs["players"][1]
    for kind in ["drifter"] * 10 + ["warden"]:
        abyss._spawn(gs, kind, diver)
        enemy = gs["enemies"][-1]
        assert abyss._damage(gs, diver, enemy, enemy["hp"])
    view = abyss.snapshot(gs)
    bursts = [effect for effect in view["effects"] if effect["kind"] == "defeat"]
    assert view["kills"] == 11 and len(bursts) == 9
    assert bursts[-1]["r"] == abyss.ENEMIES["warden"][2] + 4
    assert bursts[-1]["until"] == 12 and gs["boss_dead"]


def test_moving_water_settles_at_owner_and_resumes_following():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    abyss._fire(gs, diver, 'la_borra', 1)
    field = gs['fields'][0]
    field.update(x=diver['x'] - .5, y=diver['y'])
    initial_radius = field['r']
    for _ in range(3):
        abyss._step_fields(gs)
        assert (field['x'], field['y']) == (diver['x'], diver['y'])
    assert field['r'] > initial_radius

    diver['x'] += 10
    abyss._step_fields(gs)
    assert (field['x'], field['y']) == (diver['x'] - 8, diver['y'])


def test_orbit_cooldown_is_shared_and_expired_entries_are_pruned():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    abyss._spawn(gs, 'drifter', diver)
    enemy = gs['enemies'][0]
    enemy.update(x=diver['x'], y=diver['y'], hp=1000)
    abyss._fire(gs, diver, 'bible', 1)
    orbit = diver['orbits']['bible']
    orbit.update(count=2, radius=0, speed=0, knockback=0, crit=0)
    hits = diver['orbit_hits']['bible'] = {-1: 2, -2: abyss.ORBIT_HIT_TICKS + 1}

    abyss._step_orbits(gs)
    assert enemy['hp'] == 1000 - orbit['damage']
    gs['elapsed'] = abyss.ORBIT_HIT_TICKS - 1
    abyss._step_orbits(gs)
    assert enemy['hp'] == 1000 - orbit['damage']

    gs['elapsed'] = abyss.ORBIT_HIT_TICKS
    abyss._step_orbits(gs)
    assert enemy['hp'] == 1000 - 2 * orbit['damage']
    assert -1 not in hits and -2 in hits
    assert hits[enemy['id']] == 2 * abyss.ORBIT_HIT_TICKS


def test_enemy_moves_toward_nearest_diver_and_stops_at_the_target():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2)], 42)
    first, nearest = gs['players'].values()
    first.update(x=400, y=500, weapons={})
    nearest.update(x=700, y=505, weapons={})
    gs['spawn_budget'] = -1000
    abyss._spawn(gs, 'needlefish', first)
    enemy = gs['enemies'][0]
    enemy.update(x=700, y=500)

    abyss.advance(gs)
    assert (enemy['x'], enemy['y']) == (700, 505)
    assert enemy['angle'] == pytest.approx(pi / 2)
    abyss.advance(gs)
    assert (enemy['x'], enemy['y']) == (700, 505)


def test_full_beacon_pool_keeps_distant_drop_local_and_conserves_experience():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2)], 42, map_size='endless')
    first, second = gs['players'].values()
    first.update(x=0, y=0)
    second.update(x=3000, y=0)
    for i in range(abyss.MAX_LOOT):
        abyss._drop_loot(gs, {'x': i, 'y': 0}, 1)

    oldest, neighbor = gs['loot'][:2]
    abyss._drop_loot(gs, first, 2)
    assert gs['loot'][0] is oldest and oldest['xp'] == 3
    abyss._drop_loot(gs, second, 5)
    assert len(gs['loot']) == abyss.MAX_LOOT
    assert gs['loot'][0] is neighbor and neighbor['xp'] == 4
    assert sum(item['xp'] for item in gs['loot']) == abyss.MAX_LOOT + 7

    abyss._collect(gs, [second])
    assert second['xp_collected'] == 5
    assert first['xp_collected'] == 0
    assert gs['xp'] == 5
    assert sum(item['xp'] for item in gs['loot']) == abyss.MAX_LOOT + 2


def test_beacon_targets_nearest_diver_within_their_magnet_range():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2)], 42)
    near, far = gs['players'].values()
    near.update(x=450, y=500)
    far.update(x=570, y=500)
    near['stats']['magnet'] = .75
    far['stats']['magnet'] = 2
    abyss._drop_loot(gs, {'x': 500, 'y': 500}, 1)

    abyss._collect(gs, [near, far])
    assert gs['loot'][0]['x'] == pytest.approx(511)

    near['x'], far['x'] = 498, 524  # Equal pickup distance retains seat order.
    abyss._collect(gs, [near, far])
    assert not gs['loot']
    assert (near['xp_collected'], far['xp_collected']) == (1, 0)


def test_xp_collection_credits_only_the_diver_who_picks_up_the_beacon():
    gs = abyss.create([{'id': 1, 'username': 'a'}, {'id': 2, 'username': 'b'}], 42)
    first, second = gs['players'].values()
    del first['xp_collected']  # Rooms restored from pre-counter saves.
    gs['growth_boost'] = 2
    abyss._drop_loot(gs, first, 3)
    abyss._collect(gs, [first, second])
    assert gs['xp'] == 6
    assert [p['xp_collected'] for p in abyss.snapshot(gs)['players']] == [6, 0]
    abyss._drop_loot(gs, second, 2)
    abyss._fire(gs, second, 'gorgeous_moon', 1)
    assert gs['xp'] == 10
    assert [p['xp_collected'] for p in abyss.snapshot(gs)['players']] == [6, 4]


@pytest.mark.parametrize("duration,expected", [(720, 3780), (1200, 5103), (1800, 7560)])
def test_warden_health_scales_with_longer_new_matches(duration, expected):
    players = [{'id': i, 'username': str(i)} for i in range(4)]
    gs = abyss.create(players, seed=42, duration=duration)
    restored = persistence.loads_state(persistence.dumps_state(gs))
    diver = restored['players'][0]
    abyss._spawn(restored, 'warden', diver)
    abyss._spawn(restored, 'elite', diver)
    warden, elite = restored['enemies']
    assert (warden['hp'], warden['max_hp']) == (expected, expected)
    assert elite['max_hp'] == 1260


def test_legacy_long_match_uses_original_warden_health():
    gs = abyss.create([{'id': 1, 'username': 'a'}], seed=42, duration=1800)
    del gs['warden_hp_scale']
    abyss._spawn(gs, 'warden', gs['players'][1])
    assert gs['enemies'][0]['max_hp'] == 1350


def test_rescue_credit_goes_to_nearest_diver_at_completion_and_enters_record():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in range(3)], 42)
    further, nearer, downed = gs['players'].values()
    further.update(x=680, y=500)
    nearer.update(x=700, y=500)
    downed.update(x=710, y=500, hp=0, down=300)
    gs['spawn_budget'] = -1000
    for _ in range(39):
        abyss.advance(gs)
    assert downed['hp'] == 0 and nearer['rescues'] == 0
    abyss.advance(gs)
    assert downed['hp'] == 35 and nearer['rescues'] == 1 and further['rescues'] == 0
    protected = abyss.snapshot(gs)['players'][2]
    assert protected['protected'] and protected['protection_ms'] == 1500
    for _ in range(30):
        abyss.advance(gs)
    assert 'protected' not in abyss.snapshot(gs)['players'][2]
    abyss._drop_loot(gs, nearer, 5)
    abyss._collect(gs, [further, nearer, downed])
    summary = abyss.record_summary(gs)
    assert [(p['rescues'], p['xp_collected']) for p in summary['players']] == [(0, 0), (1, 5), (0, 0)]


def test_rescue_excludes_offline_helpers_but_allows_rescuing_offline_teammates():
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2, 3)], 42)
    downed, offline, online = gs['players'].values()
    downed.update(x=700, y=500, hp=0, down=300, rescue=abyss.RESCUE_TICKS - 1)
    offline.update(x=710, y=500)
    online.update(x=760, y=500)
    gs['spawn_budget'] = -1000

    abyss.advance(gs, online_ids={online['id']})
    assert downed['rescue'] == 0 and downed['hp'] == 0

    online['x'] = 745
    for _ in range(abyss.RESCUE_TICKS):
        abyss.advance(gs, online_ids={online['id']})
    assert downed['hp'] == 35
    assert (offline['rescues'], online['rescues']) == (0, 1)


@pytest.mark.parametrize("downed_id", [1, 2])
@pytest.mark.parametrize("distance, direction, rescued", [(55, -1, True), (49, 1, False)])
def test_rescue_uses_position_after_movement(downed_id, distance, direction, rescued):
    gs = abyss.create([{'id': i, 'username': str(i)} for i in (1, 2)], 42)
    downed, rescuer = gs['players'][downed_id], gs['players'][3 - downed_id]
    downed.update(x=700, y=500, hp=0, down=300, rescue=abyss.RESCUE_TICKS - 1)
    rescuer.update(x=700 + distance, y=500)
    gs['spawn_budget'] = -1000
    abyss.move(gs, rescuer['id'], direction, 0)

    abyss.advance(gs)

    assert downed['hp'] == (35 if rescued else 0)
    assert downed['rescue'] == 0
    assert rescuer['rescues'] == int(rescued)


def test_fast_projectile_hits_crossed_enemy_before_later_one_in_roster():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    # The nearer enemy is crossed in one tick and does not overlap the end point.
    for x, y in [(648, 500), (605, 500), (606, 530)]:
        abyss._spawn(gs, 'drifter', diver)
        gs['enemies'][-1].update(x=x, y=y, hp=30)
    stats = data.weapon_stats('harpoon', 1, diver['stats'])
    abyss._shot(gs, diver, 'harpoon', stats, 580, 500, 1400, 0,
                kind='harpoon', life=10, pierce=1, radius=7)
    abyss._step_shots(gs)
    assert [enemy['hp'] for enemy in gs['enemies']] == [30, 23.5, 30]
    assert not gs['shots']
    impact, = abyss.snapshot(gs)['effects']
    assert (impact['kind'], impact['x'], impact['y']) == ('impact', 587, 500)


def test_delayed_projectile_faces_its_new_target_in_first_visible_snapshot():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    diver.update(x=600, y=500)
    abyss._spawn(gs, 'drifter', diver)
    gs['enemies'][0].update(x=600, y=650)
    stats = data.weapon_stats('cross', 1, diver['stats'])
    abyss._shot(gs, diver, 'cross', stats, 600, 500, 300, 0,
                kind='cross', life=20, delay=1, retarget=True)
    diver['x'] = 620
    gs['enemies'][0]['x'] = 620
    assert not abyss.snapshot(gs)['shots']
    abyss._step_shots(gs)
    shot, = abyss.snapshot(gs)['shots']
    assert shot[2:5] == [620, 500, round(pi / 2, 2)]
    abyss._step_shots(gs)
    shot, = abyss.snapshot(gs)['shots']
    assert shot[2:5] == [620, 515, round(pi / 2, 2)]


def test_reflection_allows_repeat_hit_on_the_new_leg_only():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    abyss._spawn(gs, 'drifter', diver)
    enemy = gs['enemies'][0]
    enemy.update(x=22, y=500, hp=30)
    stats = data.weapon_stats('rune', 1, diver['stats'])
    abyss._shot(gs, diver, 'rune', stats, 26, 500, -520, 0,
                kind='rune', life=10, radius=7, bounce=True)
    gs['shots'][0]['hit'].add(enemy['id'])
    abyss._step_shots(gs)
    assert enemy['hp'] == 30 and gs['shots'][0]['vx'] > 0
    assert abyss.snapshot(gs)['shots'][0][4] == 0
    abyss._step_shots(gs)
    assert enemy['hp'] == 30 - stats['damage']


@pytest.mark.parametrize("map_size", ["medium", "endless"])
def test_evolved_sonar_in_endless_detonates_only_on_first_contact(map_size):
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42, map_size=map_size)
    diver = gs["players"][1]
    for x in (630, 720):
        abyss._spawn(gs, "drifter", diver)
        gs["enemies"][-1].update(x=x, y=500, hp=200)
    stats = data.weapon_stats("no_future", 1, diver["stats"])
    abyss._shot(gs, diver, "no_future", stats, 600, 500, 1000, 0,
                kind="bounce", life=10, pierce=9999, radius=14, bounce=True, explode=30)
    for _ in range(3):
        abyss._step_shots(gs)
    bursts = [effect for effect in gs["effects"] if effect["kind"] == "burst"]
    assert len(bursts) == int(map_size == "endless")
    assert len(gs["shots"][0]["hit"]) == 2
    assert gs["enemies"][0]["hp"] == (140 if map_size == "endless" else 170)


def test_short_impact_remains_in_snapshots_for_the_delayed_render_timeline():
    gs = abyss.create([{"id": 1, "username": "Diver"}], 42)
    abyss._effect(gs, "harpoon", "impact", life=5, x=550, y=500, r=12)
    for _ in range(8):
        abyss.advance(gs)
    impact, = abyss.snapshot(gs)["effects"]
    assert (impact["start"], impact["until"]) == (0, 5)
    assert gs["frame"] == 8 and impact["until"] == 5
    abyss.advance(gs)
    assert not abyss.snapshot(gs)["effects"]


def test_snapshot_effect_shapes_use_whole_world_units():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    diver.update(x=717.25, y=503.6875)
    abyss._effect(gs, 'charge', 'burst', x=diver['x'], y=diver['y'], r=71.94)
    abyss._effect(gs, 'arc', points=[(717.25, 503.6875), (685.8, 550.12)])

    burst, arc = abyss.snapshot(gs)['effects']
    assert (burst['x'], burst['y'], burst['r']) == (717, 504, 72)
    assert arc['points'] == [[717, 504], [686, 550]]
    assert (diver['x'], diver['y']) == (717.25, 503.6875)


def test_projectile_snapshot_exposes_launch_only_in_first_four_movement_ticks():
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    diver = gs['players'][1]
    diver['weapons'].clear()
    gs['spawn_budget'] = -1000
    stats = data.weapon_stats('harpoon', 1, diver['stats'])
    abyss._shot(gs, diver, 'harpoon', stats, 500, 550, 1400, 0,
                kind='harpoon', life=12, delay=1, radius=7)
    assert not abyss.snapshot(gs)['shots']
    abyss.advance(gs)
    assert len(abyss.snapshot(gs)['shots'][0]) == 6

    for age in range(1, 5):
        abyss.advance(gs)
        shot = abyss.snapshot(gs)['shots'][0]
        assert shot[2:4] == [500 + age * 70, 550]
        assert shot[6] == [1, 500, 550]

    abyss.advance(gs)
    assert len(abyss.snapshot(gs)['shots'][0]) == 6


@pytest.mark.parametrize("preserve_tap, ticks_held, expected_ticks", [(True, 0, 1), (False, 0, 0), (True, 2, 2)])
def test_keyboard_tap_applies_at_most_one_unprocessed_step(preserve_tap, ticks_held, expected_ticks):
    gs = abyss.create([{'id': 1, 'username': 'Diver'}], 42)
    player = gs['players'][1]
    initial_x = player['x']
    abyss.move(gs, 1, 1, 0)
    for _ in range(ticks_held):
        abyss.advance(gs)
    abyss.move(gs, 1, 0, 0, preserve_tap=preserve_tap)
    for _ in range(3):
        abyss.advance(gs)
    assert player['x'] == pytest.approx(initial_x + expected_ticks * data.PLAYER_SPEED * abyss.STEP)
    assert (player['dx'], player['dy'], player['input_until']) == (0, 0, 0)
