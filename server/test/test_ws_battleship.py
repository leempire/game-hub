"""Battleship WebSocket integration tests using FastAPI TestClient.

Tests: create room, join, start, placement, battle, fire, surrender,
game over, database persistence.

Run: pytest test/test_ws_battleship.py -v
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.database import get_conn


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    yield


@pytest.fixture
def client(ws_client):
    # 共用事件循环的 TestClient（原因见 conftest.py 的 ws_client）
    return ws_client


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="battleship"):
    r = client.post(
        "/api/rooms",
        json={"game": game},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(
        f"/api/rooms/{code}/join",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=20):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _default_placement():
    """Return a valid placement for testing."""
    return [
        {"id": 1, "x": 0, "y": 0, "horizontal": True},
        {"id": 2, "x": 0, "y": 1, "horizontal": True},
        {"id": 3, "x": 0, "y": 2, "horizontal": True},
        {"id": 4, "x": 0, "y": 3, "horizontal": True},
        {"id": 5, "x": 0, "y": 4, "horizontal": True},
    ]


def _place_both(ws1, ws2, ships=None, weapons=None):
    """Both players place (same layout), drain until the battle phase starts."""
    ships = ships or _default_placement()
    ws1.send_json({"type": "placement", "ships": ships, "weapons": weapons})
    _recv_type(ws1, "placement_ready")
    _recv_type(ws2, "placement_ready")
    ws2.send_json({"type": "placement", "ships": ships, "weapons": weapons})
    for ws in (ws1, ws2):
        for _ in range(15):
            try:
                m = ws.receive_json()
                if m["type"] == "game_start" and m["game"]["phase"] == "battle":
                    break
            except Exception:
                break


def _fire(ws_shooter, ws_other, x, y):
    """Fire from ws_shooter; returns (shooter's shot msg, other's shot msg)."""
    ws_shooter.send_json({"type": "fire", "x": x, "y": y})
    return _recv_type(ws_shooter, "shot"), _recv_type(ws_other, "shot")


WEAPONS = {"radar": 5, "salvo": 4, "strike": 3}


def _setup_battleship(client):
    """Create room, join, connect WS, ready, start. Returns (ws1, ws2, code, t1, t2)."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bs_h{ts}a")
    t2, uid2 = _register(client, f"bs_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws2 = client.websocket_connect(f"/ws/room/{code}?token={t2}")
    ws1.__enter__()
    ws2.__enter__()
    _recv_type(ws1, "sync")
    _recv_type(ws2, "sync")
    ws2.send_json({"type": "ready"})
    _recv_type(ws1, "lobby")
    _recv_type(ws2, "lobby")
    ws1.send_json({"type": "start"})
    g1 = _recv_type(ws1, "game_start")
    g2 = _recv_type(ws2, "game_start")
    return ws1, ws2, code, t1, t2, g1, g2


def test_battleship_create_room(client):
    """Create a battleship room and verify max_players=2."""
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"bs_c{ts}")
    room = _create_room(client, t1)
    assert room["game"] == "battleship"
    assert room["max_players"] == 2
    assert room["status"] == "waiting"


def test_battleship_game_start(client):
    """Start a battleship game and verify placement phase."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        assert g1["game"]["board_size"] == 10
        assert g1["game"]["phase"] == "placing"
        assert g1["game"]["your_color"] == "blue"
        assert g2["game"]["your_color"] == "red"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_battleship_placement_and_battle(client):
    """Both players place ships, battle starts, fire shots."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        ships = _default_placement()
        ws1.send_json({"type": "placement", "ships": ships})
        pr1 = _recv_type(ws1, "placement_ready")
        pr2 = _recv_type(ws2, "placement_ready")
        assert pr1["user_id"] is not None
        # ws1 may also get a lobby from ws2 connect — skip to placement_ready
        ws2.send_json({"type": "placement", "ships": ships})
        # Both should get placement_ready for ws2, then battle_start
        # Drain messages until we get game_start (battle_start)
        msgs1 = []
        msgs2 = []
        for _ in range(10):
            try:
                m1 = ws1.receive_json()
                msgs1.append(m1)
                if m1["type"] == "game_start":
                    break
            except Exception:
                break
        for _ in range(10):
            try:
                m2 = ws2.receive_json()
                msgs2.append(m2)
                if m2["type"] == "game_start":
                    break
            except Exception:
                break
        # Verify battle started for both
        bs1 = [m for m in msgs1 if m["type"] == "game_start"]
        bs2 = [m for m in msgs2 if m["type"] == "game_start"]
        assert bs1, "ws1 did not receive battle_start"
        assert bs2, "ws2 did not receive battle_start"
        assert bs1[0]["game"]["phase"] == "battle"
        assert bs2[0]["game"]["phase"] == "battle"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_battleship_fire_miss_and_hit(client):
    """Fire shots and verify miss/hit results."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        ships = _default_placement()
        # Both place
        ws1.send_json({"type": "placement", "ships": ships})
        _recv_type(ws1, "placement_ready")
        _recv_type(ws2, "placement_ready")
        ws2.send_json({"type": "placement", "ships": ships})
        # Drain until battle_start
        for ws in [ws1, ws2]:
            for _ in range(15):
                try:
                    m = ws.receive_json()
                    if m["type"] == "game_start" and m["game"]["phase"] == "battle":
                        break
                except Exception:
                    break
        # Blue (ws1) fires first at (5,5) — should be miss (ships at row 0-4)
        ws1.send_json({"type": "fire", "x": 5, "y": 5})
        s1 = _recv_type(ws1, "shot")
        s2 = _recv_type(ws2, "shot")
        assert s1["result"] == "miss"
        assert s1["x"] == 5 and s1["y"] == 5
        assert s1["shooter_color"] == "blue"
        assert s2["result"] == "miss"
        # Red (ws2) fires at (0,0) — should be hit (ship 1 starts at 0,0)
        ws2.send_json({"type": "fire", "x": 0, "y": 0})
        s1 = _recv_type(ws1, "shot")
        s2 = _recv_type(ws2, "shot")
        assert s2["result"] == "hit"
        assert s2["ship_id"] == 1
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_battleship_surrender(client):
    """Surrender should end the game."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        ships = _default_placement()
        ws1.send_json({"type": "placement", "ships": ships})
        _recv_type(ws1, "placement_ready")
        _recv_type(ws2, "placement_ready")
        ws2.send_json({"type": "placement", "ships": ships})
        for ws in [ws1, ws2]:
            for _ in range(15):
                try:
                    m = ws.receive_json()
                    if m["type"] == "game_start" and m["game"]["phase"] == "battle":
                        break
                except Exception:
                    break
        # Blue surrenders
        ws1.send_json({"type": "surrender"})
        go1 = _recv_type(ws1, "game_over")
        go2 = _recv_type(ws2, "game_over")
        assert go1["winner"] == "red"
        assert go1["reason"] == "surrender"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_battleship_game_record(client, tmp_path):
    """Verify game record is saved after battleship game ends."""
    import app.database as db_mod
    from app.database import init_db, save_game_record
    orig_db_path = db_mod._db_path
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    rm.set_game_end_callback(save_game_record)
    try:
        ts = int(time.time() * 1000) % 100000
        t1, uid1 = _register(client, f"bs_r{ts}a")
        t2, uid2 = _register(client, f"bs_r{ts}b")
        room = _create_room(client, t1)
        code = room["code"]
        _join_room(client, t2, code)
        with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1, \
             client.websocket_connect(f"/ws/room/{code}?token={t2}") as ws2:
            _recv_type(ws1, "sync")
            _recv_type(ws2, "sync")
            ws2.send_json({"type": "ready"})
            _recv_type(ws1, "lobby")
            _recv_type(ws2, "lobby")
            ws1.send_json({"type": "start"})
            g1 = _recv_type(ws1, "game_start")
            _recv_type(ws2, "game_start")
            ships = _default_placement()
            ws1.send_json({"type": "placement", "ships": ships})
            _recv_type(ws1, "placement_ready")
            _recv_type(ws2, "placement_ready")
            ws2.send_json({"type": "placement", "ships": ships})
            for ws in [ws1, ws2]:
                for _ in range(15):
                    try:
                        m = ws.receive_json()
                        if m["type"] == "game_start" and m["game"]["phase"] == "battle":
                            break
                    except Exception:
                        break
            ws1.send_json({"type": "surrender"})
            _recv_type(ws1, "game_over")
            _recv_type(ws2, "game_over")
        # Check database
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM game_records WHERE game = 'battleship' AND room_code = ?",
                (code,),
            ).fetchall()
            assert len(rows) >= 1, "No battleship game record found"
            import json
            rec = rows[0]
            results = json.loads(rec["results_json"])
            assert len(results) == 2
            result_map = {r["username"]: r["result"] for r in results}
            assert result_map[f"bs_r{ts}a"] == "lose"
            assert result_map[f"bs_r{ts}b"] == "win"
            snapshot = json.loads(rec["snapshot_json"])
            assert snapshot["game"] == "battleship"
            assert "placements" in snapshot
        finally:
            conn.close()
    finally:
        rm.set_game_end_callback(None)
        db_mod.init_db(orig_db_path)


def test_battleship_start_new_round_from_lobby(client):
    """对局结束后房间回到等待态，房主直接 start 即可续开（rematch 协议已删除）。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        ships = _default_placement()
        ws1.send_json({"type": "placement", "ships": ships})
        _recv_type(ws1, "placement_ready")
        _recv_type(ws2, "placement_ready")
        ws2.send_json({"type": "placement", "ships": ships})
        for ws in [ws1, ws2]:
            for _ in range(15):
                try:
                    m = ws.receive_json()
                    if m["type"] == "game_start" and m["game"]["phase"] == "battle":
                        break
                except Exception:
                    break
        ws1.send_json({"type": "surrender"})
        _recv_type(ws1, "game_over")
        _recv_type(ws2, "game_over")
        # 房间回到 waiting；终局把真人非房主复位为未准备，续开前对方要重新准备
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        rs1 = _recv_type(ws1, "game_start")
        rs2 = _recv_type(ws2, "game_start")
        assert rs1["game"]["phase"] == "placing"
        assert rs2["game"]["phase"] == "placing"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


# ── v1.10 命中续射 ────────────────────────────────────────────


def test_bonus_shot_on_hit_except_opening(client):
    """命中续射：先手第一发不触发（补偿后手），之后命中/击沉都保留回合。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2)
        # 蓝方（先手）第一发就命中 —— 开局补偿，不续射
        s1, _ = _fire(ws1, ws2, 0, 0)
        assert s1["result"] == "hit"
        assert s1["keeps_turn"] is False
        assert s1["game"]["next"] == "red"
        # 红方（后手）命中 —— 续射，回合仍是自己的
        s2, _ = _fire(ws2, ws1, 0, 0)
        assert s2["result"] == "hit"
        assert s2["keeps_turn"] is True
        assert s2["game"]["next"] == "red"
        # 未命中则正常换手
        s3, _ = _fire(ws2, ws1, 9, 9)
        assert s3["result"] == "miss"
        assert s3["keeps_turn"] is False
        assert s3["game"]["next"] == "blue"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_bonus_shot_chains_while_hitting(client):
    """连续命中可以连打——击沉整条船不需要换手。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2)
        _fire(ws1, ws2, 9, 9)          # 蓝方先手放空，交出回合 + 解除开局补偿
        _fire(ws2, ws1, 9, 9)          # 红方也放空
        # 蓝方连打 destroyer（(0,4)、(1,4)）
        s1, _ = _fire(ws1, ws2, 0, 4)
        assert s1["result"] == "hit" and s1["keeps_turn"] is True
        s2, _ = _fire(ws1, ws2, 1, 4)
        assert s2["result"] == "sunk"
        assert s2["ship_id"] == 5
        assert s2["keeps_turn"] is True
        assert s2["game"]["next"] == "blue"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


# ── v1.10 特殊武器 ────────────────────────────────────────────


def test_weapons_assignment_broadcast_to_both(client):
    """武器挂靠对双方公开——「找出并击沉武器舰」是它的反制手段。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2, weapons=WEAPONS)
        ws1.send_json({"type": "fire", "x": 9, "y": 9})
        mine = _recv_type(ws1, "shot")
        assert mine["game"]["own_weapons"]["radar"]["ship_id"] == 5
        assert mine["game"]["own_weapons"]["radar"]["available"] is True
        # 对手的挂靠关系自己也能看到（只暴露舰名，不暴露位置）
        assert mine["game"]["opp_weapons"]["strike"]["ship_id"] == 3
        assert mine["game"]["weapon_defs"]["radar"]["name"] == "雷达"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_missing_weapons_fall_back_to_default(client):
    """旧客户端不发 weapons 字段也要能用默认分配开局（向后兼容）。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2)          # 不带 weapons
        ws1.send_json({"type": "fire", "x": 9, "y": 9})
        mine = _recv_type(ws1, "shot")
        assert mine["game"]["own_weapons"]["radar"]["ship_id"] == 5
        assert mine["game"]["own_weapons"]["strike"]["ship_id"] == 3
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_radar_scans_area_and_uses_turn(client):
    """雷达只报有无、结果只回使用者，且用掉本回合。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2, weapons=WEAPONS)
        ws1.send_json({"type": "use_weapon", "weapon": "radar", "x": 1, "y": 1})
        # weapon_used 先广播、radar_result 后发——顺序反了会把前者吃掉再永久阻塞
        mine = _recv_type(ws1, "weapon_used")
        other = _recv_type(ws2, "weapon_used")
        rr = _recv_type(ws1, "radar_result")
        assert rr["has_ship"] is True          # 3×3 覆盖 (0,0)-(2,2)，有舰
        assert mine["weapon"] == "radar"
        assert mine["cells"] == [] and mine["shots"] == []
        # 对手看得到「他放了一次雷达」，但拿不到探测结果
        assert other["weapon"] == "radar"
        assert "radar" not in other or "has_ship" not in other
        # 用武器 = 本回合全部行动
        assert mine["game"]["next"] == "red"
        assert mine["game"]["own_weapons"]["radar"]["used"] is True
        assert mine["game"]["own_weapons"]["radar"]["available"] is False
        # 空海域回报无舰
        ws2.send_json({"type": "use_weapon", "weapon": "radar", "x": 9, "y": 9})
        rr2 = _recv_type(ws2, "radar_result")
        assert rr2["has_ship"] is False
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_strike_hits_whole_area(client):
    """空袭按 3×3 逐格结算。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2, weapons=WEAPONS)
        ws1.send_json({"type": "use_weapon", "weapon": "strike", "x": 1, "y": 1})
        mine = _recv_type(ws1, "weapon_used")
        assert mine["weapon"] == "strike"
        assert len(mine["cells"]) == 9
        assert len(mine["shots"]) == 9
        # 测试布阵里 (0..2, 0..2) 全在舰上：9 格全中，其中巡洋舰(3 格)被一次打沉
        assert all(s["result"] in ("hit", "sunk") for s in mine["shots"])
        sunk = [s for s in mine["shots"] if s["result"] == "sunk"]
        assert len(sunk) == 1 and sunk[0]["ship_id"] == 3
        assert mine["game"]["next"] == "red"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_salvo_fires_three_cells(client):
    """齐射一次打 3 格，逐格结算。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        _place_both(ws1, ws2, weapons=WEAPONS)
        ws1.send_json({"type": "use_weapon", "weapon": "salvo", "cells": [[0, 0], [9, 9], [5, 5]]})
        mine = _recv_type(ws1, "weapon_used")
        assert mine["weapon"] == "salvo"
        assert len(mine["shots"]) == 3
        by_cell = {(s["x"], s["y"]): s["result"] for s in mine["shots"]}
        assert by_cell[(0, 0)] == "hit"      # 测试布阵里 (0,0) 是舰首
        assert by_cell[(9, 9)] == "miss"
        assert by_cell[(5, 5)] == "miss"
        assert mine["game"]["next"] == "red"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_weapon_rejects_when_off_turn_or_unavailable(client):
    """不在自己回合 / 用过 / 携带舰被击沉 —— 三种情况都要被拒。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_battleship(client)
    try:
        # strike 挂在 destroyer(id=5)，位于 (0,4)-(1,4)
        _place_both(ws1, ws2, weapons={"radar": 1, "salvo": 2, "strike": 5})
        # 红方（非当前回合）不能先用武器
        ws2.send_json({"type": "use_weapon", "weapon": "radar", "x": 1, "y": 1})
        err = _recv_type(ws2, "error")
        assert "没轮到你" in err["message"]
        _fire(ws1, ws2, 9, 9)          # 蓝方放空 → 轮到红方
        ws2.send_json({"type": "use_weapon", "weapon": "radar", "x": 1, "y": 1})
        _recv_type(ws2, "weapon_used")     # 先到
        _recv_type(ws1, "weapon_used")
        _recv_type(ws2, "radar_result")    # 后到
        _fire(ws1, ws2, 9, 8)          # 蓝方放空 → 轮到红方
        # 同一件武器不能再用
        ws2.send_json({"type": "use_weapon", "weapon": "radar", "x": 2, "y": 2})
        err = _recv_type(ws2, "error")
        assert "用过" in err["message"]
        # 蓝方沉掉红方的 destroyer → 红方的 strike 失效
        _fire(ws2, ws1, 9, 7)
        s1, _ = _fire(ws1, ws2, 0, 4)
        assert s1["result"] == "hit" and s1["keeps_turn"] is True
        s2, other = _fire(ws1, ws2, 1, 4)
        assert s2["result"] == "sunk" and s2["ship_id"] == 5
        assert other["game"]["own_weapons"]["strike"]["available"] is False
        # 红方此刻仍在自己的回合吗？沉了船但 keeps_turn，所以轮到蓝方——先让红方拿到回合
        ws1.send_json({"type": "fire", "x": 9, "y": 6})
        _recv_type(ws1, "shot")
        _recv_type(ws2, "shot")
        ws2.send_json({"type": "use_weapon", "weapon": "strike", "x": 1, "y": 1})
        err = _recv_type(ws2, "error")
        assert "失效" in err["message"]
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_bot_difficulty_stored_per_player(client):
    """加人机时可以指定难度，难度存在该 bot 自己身上（同一房可混搭）。"""
    ts = int(time.time() * 1000) % 100000
    t1, _uid1 = _register(client, f"bs_diff{ts}")
    code = _create_room(client, t1)["code"]
    ws = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws.__enter__()
    try:
        _recv_type(ws, "sync")
        ws.send_json({"type": "add_bot", "difficulty": "hard"})
        bots = [p for p in _recv_type(ws, "lobby")["room"]["players"]
                if p.get("is_bot")]
        assert bots and bots[0]["ai_difficulty"] == "hard"
        bot_id = bots[0]["id"]
        # 海战棋是 2 人房，host + 1 bot 即满员；移除后重加来验缺省与非法值回落
        ws.send_json({"type": "remove_bot", "bot_id": bot_id})
        _recv_type(ws, "lobby")
        ws.send_json({"type": "add_bot", "difficulty": "nightmare"})
        bots = [p for p in _recv_type(ws, "lobby")["room"]["players"]
                if p.get("is_bot")]
        assert bots[0]["ai_difficulty"] == "normal"   # 非法值回落缺省档
    finally:
        ws.__exit__(None, None, None)
