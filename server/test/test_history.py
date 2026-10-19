"""History feature tests: save/get records + REST API.

Run: cd server && python -m pytest test/test_history.py -v
"""

import sys
import os
import json
import tempfile
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.database import init_db, get_conn, save_game_record, get_user_history


@pytest.fixture(autouse=True)
def _setup_db(tmp_path):
    """Initialize a fresh SQLite DB in a temp dir for each test."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    yield
    # Reset callback so tests don't bleed
    rm.set_game_end_callback(None)


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
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
    return r.json()["token"], r.json()["user"]["id"]


def _create_room(client, token, game="gomoku"):
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


def _recv_type(ws, msg_type, max_skip=10):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def test_save_and_get_record():
    """Directly save a game record and read it back."""
    ts = int(time.time() * 1000) % 100000
    # Insert a user first
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, created_at) "
        "VALUES (?, 'h', 's', '2024-01-01T00:00:00')",
        (f"alice_{ts}",),
    )
    conn.commit()
    conn.close()
    room = {
        "code": "HIST01",
        "name": "test",
        "game": "gomoku",
        "host_id": 1,
        "status": "finished",
        "players": [
            {"id": 1, "username": f"alice_{ts}", "nickname": "A"},
            {"id": 2, "username": f"bob_{ts}", "nickname": "B"},
        ],
        "game_state": {
            "board": [[0] * 15 for _ in range(15)],
            "winner": "black",
            "reason": "five",
            "player_colors": {1: "black", 2: "white"},
        },
        "game_started_at": "2024-01-01T00:00:00+00:00",
    }
    save_game_record(room)
    conn = get_conn()
    user = conn.execute(
        "SELECT id FROM users WHERE username = ?", (f"alice_{ts}",)
    ).fetchone()
    conn.close()
    result = get_user_history(user["id"], 1, 10)
    assert result["total"] == 1
    assert result["items"][0]["game"] == "gomoku"
    assert result["items"][0]["players"][0]["username"] == f"alice_{ts}"
    assert result["items"][0]["results"][0]["result"] == "win"
    assert result["items"][0]["results"][1]["result"] == "lose"


def test_api_requires_auth(client):
    r = client.get("/api/history/mine")
    assert r.status_code == 401 or r.status_code == 403


def test_api_empty_history(client):
    import time as _t
    ts = int(_t.time() * 1000) % 100000
    token, _ = _register(client, f"empty_{ts}")
    r = client.get(
        "/api/history/mine",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_gomoku_surrender_creates_record(client):
    """A gomoku surrender should persist one record for both players."""
    # Register the callback so _set_game_over saves records
    rm.set_game_end_callback(save_game_record)
    import time as _t
    ts = int(_t.time() * 1000) % 100000
    t1, uid1 = _register(client, f"gh_{ts}a")
    t2, uid2 = _register(client, f"gh_{ts}b")
    room = _create_room(client, t1, "gomoku")
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
        _recv_type(ws1, "game_start")
        _recv_type(ws2, "game_start")
        # Host (black) surrenders
        ws1.send_json({"type": "surrender"})
        go = _recv_type(ws1, "game_over")
        assert go["winner"] == "white"
        assert go["reason"] == "surrender"
    # Query history for player 1
    r = client.get(
        "/api/history/mine",
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    rec = data["items"][0]
    assert rec["game"] == "gomoku"
    assert rec["room_code"] == code
    assert rec["snapshot"]["game"] == "gomoku"
    assert rec["snapshot"]["board_size"] == 15
    # Player 1 surrendered → lose
    me = next(r for r in rec["results"] if r["username"] == f"gh_{ts}a")
    assert me["result"] == "lose"
    # Player 2 won
    other = next(r for r in rec["results"] if r["username"] == f"gh_{ts}b")
    assert other["result"] == "win"
    # Player 2 can also see the record
    r2 = client.get(
        "/api/history/mine",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r2.json()["total"] == 1


def test_reversi_game_over_creates_record(client):
    rm.set_game_end_callback(save_game_record)
    import time as _t
    ts = int(_t.time() * 1000) % 100000
    t1, _ = _register(client, f"rv_{ts}a")
    t2, _ = _register(client, f"rv_{ts}b")
    room = _create_room(client, t1, "reversi")
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
        _recv_type(ws1, "game_start")
        _recv_type(ws2, "game_start")
        ws1.send_json({"type": "surrender"})
        _recv_type(ws1, "game_over")
        _recv_type(ws2, "game_over")
    r = client.get(
        "/api/history/mine",
        headers={"Authorization": f"Bearer {t1}"},
    )
    data = r.json()
    assert data["total"] == 1
    rec = data["items"][0]
    assert rec["game"] == "reversi"
    assert rec["snapshot"]["board_size"] == 8


def test_pagination(client):
    """Save 5 records, request page=1 size=2 and verify pagination."""
    rm.set_game_end_callback(save_game_record)
    import time as _t
    ts = int(_t.time() * 1000) % 100000
    t1, _ = _register(client, f"pg_{ts}a")
    t2, _ = _register(client, f"pg_{ts}b")
    for i in range(5):
        room = _create_room(client, t1, "gomoku")
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
            _recv_type(ws1, "game_start")
            _recv_type(ws2, "game_start")
            ws1.send_json({"type": "surrender"})
            _recv_type(ws1, "game_over")
            _recv_type(ws2, "game_over")
            # 对局结束房间回到 waiting，玩家仍留在房里；这里显式退出只是为了
            # 不把房间留在内存里影响后续用例（v1.7 起已允许同时待在多个房间）
            ws1.send_json({"type": "leave"})
            ws2.send_json({"type": "leave"})
    r = client.get(
        "/api/history/mine?page=1&size=2",
        headers={"Authorization": f"Bearer {t1}"},
    )
    data = r.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["size"] == 2
    # Page 3 size 2 = 1 item
    r3 = client.get(
        "/api/history/mine?page=3&size=2",
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert len(r3.json()["items"]) == 1


# ── 2048 战绩语义 ───────────────────────────────────────────────
# 2048 是「各玩各的」跑分竞速，不是胜负对抗：只有真的合出 2048 才算胜，
# 其余（分数最高但没合出来 / 卡死 / 认输）一律记「结束」。名次另有 ranking。

def _room_2048(ts, *, a_board, b_board, a_score, b_score, winner):
    """造一个已终局的 2048 房间（盘面直接用最终状态）。"""
    return {
        "code": "G2048A",
        "name": "test",
        "game": "2048",
        "host_id": 1,
        "status": "waiting",
        "players": [
            {"id": 1, "username": f"a2048_{ts}", "nickname": "甲"},
            {"id": 2, "username": f"b2048_{ts}", "nickname": "乙"},
        ],
        "game_state": {
            "boards": {1: a_board, 2: b_board},
            "scores": {1: a_score, 2: b_score},
            "player_colors": {1: "blue", 2: "red"},
            "winner": winner,
            "reason": "ranking",
            "finish_reasons": {1: "stuck", 2: "stuck"},
            "ranking": [
                {"seat": 0, "user_id": 1, "username": f"a2048_{ts}", "nickname": "甲",
                 "color": "blue", "score": a_score, "max_tile": max(max(r) for r in a_board),
                 "finish_reason": "stuck", "rank": 1},
                {"seat": 1, "user_id": 2, "username": f"b2048_{ts}", "nickname": "乙",
                 "color": "red", "score": b_score, "max_tile": max(max(r) for r in b_board),
                 "finish_reason": "stuck", "rank": 2},
            ],
        },
        "game_started_at": "2024-01-01T00:00:00+00:00",
    }


def _blank_2048_board(max_tile: int) -> list[list[int]]:
    board = [[0] * 4 for _ in range(4)]
    board[0][0] = max_tile
    return board


def _saved_record(room):
    save_game_record(room)
    conn = get_conn()
    row = conn.execute(
        "SELECT results_json, snapshot_json FROM game_records "
        "WHERE room_code = ? ORDER BY id DESC LIMIT 1",
        (room["code"],),
    ).fetchone()
    conn.close()
    assert row is not None, "记录没有落库"
    return json.loads(row["results_json"]), json.loads(row["snapshot_json"])


def test_2048_rank1_without_2048_is_not_a_win():
    """分数最高但没合出 2048 —— 排名第一≠胜利，必须记「结束」。

    回归点：早期结果沿用 winner==color 判断，而 2048 的 winner 记的是
    「排名第一的颜色」，会把分高者误标成胜方。
    """
    ts = int(time.time() * 1000) % 100000
    room = _room_2048(
        ts,
        a_board=_blank_2048_board(1024), b_board=_blank_2048_board(256),
        a_score=9000, b_score=1200, winner="blue",
    )
    results, _ = _saved_record(room)
    assert [r["result"] for r in results] == ["end", "end"]


def test_2048_only_reaching_2048_wins():
    """真的合出 2048 的那位记「胜」，其余仍是「结束」。"""
    ts = int(time.time() * 1000) % 100000
    room = _room_2048(
        ts,
        a_board=_blank_2048_board(2048), b_board=_blank_2048_board(512),
        a_score=20000, b_score=3000, winner="blue",
    )
    results, _ = _saved_record(room)
    assert [r["result"] for r in results] == ["win", "end"]


def test_2048_snapshot_carries_player_names():
    """快照要带 uid→身份 映射，战绩页才能显示用户名而不是「蓝方/红方」。"""
    ts = int(time.time() * 1000) % 100000
    room = _room_2048(
        ts,
        a_board=_blank_2048_board(64), b_board=_blank_2048_board(128),
        a_score=100, b_score=200, winner="red",
    )
    _, snap = _saved_record(room)
    assert snap["players"]["1"]["username"] == f"a2048_{ts}"
    assert snap["players"]["1"]["nickname"] == "甲"
    assert snap["players"]["2"]["color"] == "red"
    # 排名也要在快照里（落库发生在 _set_game_over 之内，排名必须先写回 gs）
    assert [e["rank"] for e in snap["ranking"]] == [1, 2]


def test_finalize_2048_writes_ranking_before_persisting():
    """走真实终局路径：快照里的 ranking 不能是空的。

    回归点：_maybe_finish_2048 曾经先调 _set_game_over（内部触发落库）、
    后写 gs["ranking"]，导致存下来的快照排名永远是 []。
    """
    ts = int(time.time() * 1000) % 100000
    room = _room_2048(
        ts,
        a_board=_blank_2048_board(2048), b_board=_blank_2048_board(8),
        a_score=5000, b_score=60, winner="blue",
    )
    room["game_state"]["game_over_flags"] = {1: True, 2: True}
    room["game_state"]["finish_order"] = [1, 2]
    # 关键：把 ranking 清空，逼 _maybe_finish_2048 自己去算并写回。
    # 若这里留着预置的 ranking，快照无论写回顺序先后都能读到它，测试就永远绿。
    room["game_state"]["ranking"] = []
    rm.set_game_end_callback(save_game_record)
    rm._rooms[room["code"]] = room
    try:
        out = rm._maybe_finish_2048(room)
    finally:
        rm.set_game_end_callback(None)
    assert out is not None
    conn = get_conn()
    row = conn.execute(
        "SELECT snapshot_json FROM game_records WHERE room_code = ? ORDER BY id DESC LIMIT 1",
        (room["code"],),
    ).fetchone()
    conn.close()
    snap = json.loads(row["snapshot_json"])
    assert len(snap["ranking"]) == 2, "快照里的排名为空——终局写回顺序又反了"


# ── 深空休眠 ──────────────────────────────────────────────
#
# 回归点：`_build_snapshot` 原来没有 stasis 分支，落到兜底的 `{"game": game}`，
# 于是**每一局深空休眠的战绩快照都只有一个 game 字段**——战绩页点开记录什么都
# 渲染不出来（`StasisSnapshot` 拿到空 players 直接 return null），而且不报错。
# 浏览器 E2E 断言「快照可渲染」时才暴露出来。

def _room_stasis(ts, *, reason="wiped", winner="entity"):
    """造一个已终局的深空休眠房间（用真引擎跑出真实对局态）。

    v2 没有「投票选舱室」了（跑动阶段是走过去占），所以这里直接派房。
    """
    from app import stasis
    from app import stasis_map as M

    DORM = M.get_map("dorm")

    gs = stasis.create_game([1, 2], seed=7)
    for uid, rid in ((1, "A1"), (2, "C1")):
        stasis.room_of(gs, rid)["owner"] = uid
        p = gs["players"][uid]
        p["room"] = rid
        bx, by = DORM.room_bed(rid)
        p["pos"] = [bx + 0.5, by + 0.5]
        p["dir"] = [0.0, 0.0]
        # v4：建造与升级都要求「已躺下」（`_require_lying`），所以夹具里要先躺。
        p["lying"] = True
        p["in_bed"] = True
    gs["phase"] = "playing"
    # 2 号造点建筑 + 升一级门，验证快照真的带上了布局与等级
    # （1 号要弃守，而弃守会清空他舱室里的建筑 —— 那是引擎的既定语义）
    stasis.build(gs, 2, "turret", stasis.buildable_cells(gs, 2)[0])
    stasis.upgrade(gs, 2, "door")
    # 走到终局：1 号弃守、2 号失压
    stasis.surrender(gs, 1)
    gs["players"][2]["alive"] = False
    gs["players"][2]["eliminated_at_ms"] = 61000
    gs["finished"] = True
    gs["phase"] = "over"
    gs["reason"] = reason
    gs["winner"] = winner

    return {
        "code": f"ST{ts % 100000:05d}",
        "name": "test",
        "game": "stasis",
        "host_id": 1,
        "status": "waiting",
        "players": [
            {"id": 1, "username": f"astasis_{ts}", "nickname": "甲"},
            {"id": 2, "username": f"bstasis_{ts}", "nickname": "乙"},
        ],
        "game_state": gs,
        "game_started_at": "2024-01-01T00:00:00+00:00",
    }


def test_stasis_snapshot_carries_layout_and_fates():
    """快照必须带舱室归属、等级、建筑与每人结局——否则战绩页点开是空白。"""
    ts = int(time.time() * 1000) % 100000
    room = _room_stasis(ts)
    results, snap = _saved_record(room)

    assert snap["game"] == "stasis"
    assert snap["reason"] == "wiped" and snap["winner"] == "entity"
    assert len(snap["seat_uids"]) == 2
    # v2 的房型是每局随机分配的，快照要带上（战绩页照着它画平面图）
    assert len(snap["map"]["shapes"]) == 12
    # **v9 起有多张地图**，所以 id 与名字也要进快照 —— 名字由服务端给，
    # 客户端没有那张表（否则战绩页只能显示「A1 舱」却不知道打的是哪张图）。
    assert snap["map"]["id"] == "dorm"
    assert snap["map"]["name"] == "标准货舱区"

    one = snap["players"]["1"]
    assert one["room"] == "A1", "快照丢了舱室归属"
    assert one["surrendered"] is True, "快照丢了弃守标记"
    assert one["alive"] is False
    # 弃守 = 舱室失压：建筑被清空（引擎语义），门等级留在快照里
    assert one["buildings"] == []

    two = snap["players"]["2"]
    assert two["room"] == "C1"
    assert two["door_level"] == 2, "快照丢了升级后的门等级"
    assert [b["kind"] for b in two["buildings"]] == ["turret"], "快照丢了建筑布局"
    # v2 的建筑键是 "x,y" 字符串
    assert isinstance(two["buildings"][0]["cell"], str)
    assert two["alive"] is False
    assert two["eliminated_at_ms"] == 61000, "快照丢了失压时刻"

    assert snap["entity"]["level"] >= 1
    assert len(snap["ranking"]) == 2, "快照缺排名"
    # 合作塔防共享同一个结局，名次记的是「谁撑得更久」：2 号撑到 61 秒才失压，
    # 1 号开局就弃守 —— 所以 2 号排第一，不是按座位号排。
    assert [r["username"] for r in snap["ranking"]] == [f"bstasis_{ts}", f"astasis_{ts}"]
    # 全员失压 = 败，两位都记负
    assert [r["result"] for r in results] == ["lose", "lose"]
