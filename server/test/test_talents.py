"""局外天赋树：加点 / 重置 / 属性换算 / 结算发点幂等。"""

import json

import pytest

from app import database, talents
from app.game import abyss


@pytest.fixture(autouse=True)
def _setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "talents.db"))
    database.init_db(database._db_path)
    yield


@pytest.fixture
def client(ws_client):
    return ws_client


def _register(client, name):
    response = client.post("/api/auth/register", json={
        "username": name, "password": "dive1234"})
    assert response.status_code == 200
    body = response.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['token']}"}


def _grant(user_id: int, points: int):
    conn = database.get_conn()
    try:
        conn.execute(
            """INSERT INTO abyss_talents (user_id, points, earned, levels, updated_at)
               VALUES (?, ?, ?, '{}', '2026-11-25')
               ON CONFLICT(user_id) DO UPDATE
               SET points = excluded.points, earned = excluded.earned""",
            (user_id, points, points),
        )
        conn.commit()
    finally:
        conn.close()


def _room(uid_a: int, uid_b: int | None = None, **overrides) -> dict:
    players = [{"id": uid_a, "username": f"diver{uid_a}"}]
    if uid_b is not None:
        players.append({"id": uid_b, "username": f"diver{uid_b}"})
    gs = abyss.create(players, 7)
    gs.update(result="won", boss_dead=True, count=len(players))
    for player in gs["players"].values():
        player.update(kills=500, damage_dealt=1000, hp=50, rescues=2)
    room = {
        "code": "TALENT", "game": "abyss", "game_started_at": "2026-11-25T00:00:00+00:00",
        "players": [{"id": p["id"], "username": p["username"], "nickname": None}
                    for p in players],
        "game_state": gs,
    }
    room.update(overrides)
    return room


def test_profile_requires_login_and_starts_empty(client):
    assert client.get("/api/talents").status_code == 401
    _, headers = _register(client, "talent_empty")
    body = client.get("/api/talents", headers=headers).json()
    assert (body["points"], body["earned"], body["spent"], body["levels"]) == (0, 0, 0, {})
    assert [branch["key"] for branch in body["tree"]] == [
        "endurance", "firepower", "salvage"]
    assert all(node["rank"] == 0 and node["max"] > 0
               for branch in body["tree"] for node in branch["nodes"])
    assert body["rules"]["per_game_max"] == 4


def test_allocate_to_max_then_free_reset(client):
    uid, headers = _register(client, "talent_alloc")
    _grant(uid, 6)
    for rank in range(1, 6):
        body = client.post("/api/talents/allocate", json={"node": "vitality"},
                           headers=headers).json()
        assert body["levels"]["vitality"] == rank
        assert body["points"] == 6 - rank
    denied = client.post("/api/talents/allocate", json={"node": "vitality"},
                         headers=headers)
    assert denied.status_code == 400 and denied.json()["detail"] == "该天赋已满级"
    assert client.post("/api/talents/allocate", json={"node": "prefit"},
                       headers=headers).status_code == 400
    body = client.post("/api/talents/reset", headers=headers).json()
    assert (body["points"], body["earned"], body["spent"]) == (6, 6, 0)
    assert body["levels"] == {}


def test_allocate_rejects_insufficient_points_and_unknown_node(client):
    uid, headers = _register(client, "talent_poor")
    _grant(uid, 1)
    poor = client.post("/api/talents/allocate", json={"node": "prefit"}, headers=headers)
    assert poor.status_code == 400 and poor.json()["detail"] == "天赋点不足"
    missing = client.post("/api/talents/allocate", json={"node": "nope"}, headers=headers)
    assert missing.status_code == 400 and missing.json()["detail"] == "没有这个天赋"


def test_bonus_numbers_stay_small_and_stack_with_passives():
    levels = {
        "vitality": 5, "pressure_shell": 1, "self_heal": 3, "emergency_float": 1,
        "overcharge": 5, "quick_reload": 4, "sonar": 3, "prefit": 1,
        "beacon": 3, "lucky_coin": 3, "salvage_license": 3, "reserve": 1,
    }
    bonus = talents.bonuses(levels)
    assert bonus["max_hp"] == pytest.approx(.20)
    assert bonus["might"] == pytest.approx(.15)
    assert bonus["cooldown"] == pytest.approx(-.08)
    assert bonus["area"] == pytest.approx(.12)
    assert bonus["armor"] == pytest.approx(.08)
    assert bonus["recovery"] == pytest.approx(.24)
    assert bonus["magnet"] == pytest.approx(.30)
    assert bonus["luck"] == pytest.approx(.12)
    assert bonus["growth"] == pytest.approx(.12)
    assert bonus["start_weapon_level"] == 1
    assert bonus["start_invincible"] == 6
    assert bonus["start_salvage"] == 150

    gs = abyss.create([{"id": 1, "username": "a", "talent_bonus": bonus}], 1)
    diver = gs["players"][1]
    assert diver["stats"]["might"] == pytest.approx(1.15)
    assert diver["stats"]["max_hp"] == pytest.approx(120)
    assert diver["hp"] == pytest.approx(120)
    assert diver["stats"]["cooldown"] == pytest.approx(.92)
    assert diver["stats"]["magnet"] == pytest.approx(1.3)
    assert diver["invincible"] == 6 * abyss.FRAMES_PER_SECOND
    assert diver["weapons"] == {"rivet": 2}
    assert gs["salvage"] == 150

    # 局外百分比只放大基础生命，局内「高压气瓶」仍是固定 +20/级。
    with_passive = abyss.create([{
        "id": 1, "username": "a", "talent_bonus": {"max_hp": .2},
    }], 1)
    diver = with_passive["players"][1]
    diver["passives"]["hollow_heart"] = 1
    abyss._refresh_stats(diver)
    assert diver["stats"]["max_hp"] == pytest.approx(140)


def test_award_is_capped_idempotent_and_surfaces_in_snapshot(client):
    uid_a, _ = _register(client, "talent_award_a")
    uid_b, _ = _register(client, "talent_award_b")
    room = _room(uid_a, uid_b)
    awards = talents.award_abyss(room)
    assert set(awards) == {uid_a, uid_b}
    for award in awards.values():
        assert award["points"] == 4
        assert [item["key"] for item in award["detail"]] == [
            "complete", "win", "boss", "survive"]
        assert award["balance"] == 4
    assert talents.profile(uid_a)["points"] == 4
    assert any(player.get("talent_award") for player in abyss.snapshot(room["game_state"])["players"])

    again = talents.award_abyss(room)
    assert again[uid_a]["points"] == 4
    assert talents.profile(uid_a)["points"] == 4
    assert talents.profile(uid_a)["earned"] == 4


def test_award_skips_practice_departed_bots_and_inactive_players(client):
    uid, _ = _register(client, "talent_skip")
    room = _room(uid, practice=True)
    assert talents.award_abyss(room) == {}
    assert talents.profile(uid)["points"] == 0

    room = _room(uid)
    room["game_state"]["players"][uid]["kills"] = 0
    room["game_state"]["players"][uid]["damage_dealt"] = 0
    awards = talents.award_abyss(room)
    assert awards[uid]["points"] == 1
    assert [item["key"] for item in awards[uid]["detail"]] == ["complete"]

    departed_room = _room(uid)
    departed_room["game_started_at"] = "2026-11-25T01:00:00+00:00"
    departed_room["game_state"]["players"][uid]["departed"] = True
    assert talents.award_abyss(departed_room) == {}

    bot_room = _room(uid)
    bot_room["game_started_at"] = "2026-11-25T02:00:00+00:00"
    bot_room["players"].append({"id": -1, "username": "bot", "is_bot": True})
    bot_room["game_state"]["players"][-1] = dict(bot_room["game_state"]["players"][uid])
    assert set(talents.award_abyss(bot_room)) == {uid}


def test_award_detail_is_persisted_as_json(client):
    uid, _ = _register(client, "talent_json")
    talents.award_abyss(_room(uid))
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT detail FROM abyss_talent_awards WHERE user_id = ?", (uid,)
        ).fetchone()
    finally:
        conn.close()
    assert [item["label"] for item in json.loads(row["detail"])][0] == "完成试潜"


def test_room_start_injects_talents_for_humans_only(monkeypatch):
    from app import room_manager as rm

    monkeypatch.setattr(talents, "bonus_for_user",
                        lambda uid: {"might": .15} if uid == 1 else {})
    human = {"id": 1, "username": "human"}
    entries = rm._abyss_players([
        human,
        {"id": -1, "username": "bot", "is_bot": True},
    ])
    assert entries[0]["talent_bonus"] == {"might": .15}
    assert entries[1]["talent_bonus"] == {}
    # 房间里的原始对象不被注入字段（它是落盘 / 广播共用的一份）
    assert "talent_bonus" not in human