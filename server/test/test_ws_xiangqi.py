"""中国象棋 WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房/加入、开局下发、合法走子广播、非法走子报错、将死广播、
认输、断线重连 sync、结束后续开、人机应招、战绩快照。

坐标约定：**客户端发 x=列 / y=行**，引擎内部是 (行, 列)——`_handle_xiangqi_move`
负责转置。测试里两个体系都会出现，写的时候盯紧一点。

Run: pytest test/test_ws_xiangqi.py -v
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.game.xiangqi import board_from_rows, RED, BLACK


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


def _create_room(client, token, game="xiangqi"):
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


def _setup_playing_game(client):
    """建房 → 加入 → 双 WS → ready → start。返回 (ws1, ws2, code, t1, t2, g1, g2)。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"xq_h{ts}a")
    t2, _ = _register(client, f"xq_h{ts}b")
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


def _close(*wss):
    for ws in wss:
        ws.__exit__(None, None, None)


# ── 房间与开局 ────────────────────────────────────────

def test_xiangqi_create_room(client):
    """象棋房间 2 人、等待态。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"xq_cr{ts}")
    room = _create_room(client, t1)
    assert room["game"] == "xiangqi"
    assert room["max_players"] == 2
    assert room["status"] == "waiting"


def test_xiangqi_game_start(client):
    """开局：10×9 棋盘、房主执红先行、合法着法 44 条。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        assert g1["game"]["your_color"] == RED
        assert g2["game"]["your_color"] == BLACK
        assert g1["game"]["next"] == RED
        assert g1["game"]["rows"] == 10 and g1["game"]["cols"] == 9
        assert len(g1["game"]["board"]) == 10
        assert len(g1["game"]["board"][0]) == 9
        assert g1["game"]["board"][9] == list("RNBAKABNR")
        assert len(g1["game"]["legal"]) == 44
        # 逐人下发：黑方也拿到同一盘面，但 your_color 不同
        assert g2["game"]["board"][0] == list("rnbakabnr")
    finally:
        _close(ws1, ws2)


def test_xiangqi_legal_move_broadcast(client):
    """红方走子：广播 from/to/next，并带上新的合法着法列表。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # 炮八平五之类的开局：引擎 (7,1) → (4,1)，客户端 x=列/y=行
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 1, "from_y": 7, "to_x": 1, "to_y": 4,
        })
        m1 = _recv_type(ws1, "move")
        m2 = _recv_type(ws2, "move")
        assert m1["from"] == [7, 1] and m1["to"] == [4, 1]
        assert m1["piece"] == "C"
        assert m1["next"] == BLACK
        assert m2["from"] == [7, 1]
        assert m1["check"] is False
        # 下发的 legal 必须与房间盘面自洽（客户端全靠它做高亮，不自己实现规则）。
        # move 消息不带 board（客户端按 from/to 自行落子），所以拿房间里的权威盘面比。
        from app.game.xiangqi import all_legal_moves
        board = rm.get_room(code)["game_state"]["board"]
        assert sorted(m1["legal"]) == sorted(
            [fr, fc, tr, tc]
            for (fr, fc), (tr, tc) in all_legal_moves(board, BLACK)
        )
    finally:
        _close(ws1, ws2)


def test_xiangqi_illegal_move_errors(client):
    """非法走子：不是自己的子、不合规则、抢回合，都要回 error。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # 黑方抢回合
        ws2.send_json({
            "type": "xiangqi_move",
            "from_x": 1, "from_y": 2, "to_x": 1, "to_y": 5,
        })
        assert _recv_type(ws2, "error")["message"] == "还没轮到你走"
        # 红方动黑子
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 1, "from_y": 2, "to_x": 1, "to_y": 5,
        })
        assert _recv_type(ws1, "error")["message"] == "还没轮到你走"
        # 红方乱走（车横穿自己的马）
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 0, "from_y": 9, "to_x": 1, "to_y": 9,
        })
        assert _recv_type(ws1, "error")["message"] == "这步棋不合规则"
        # 缺字段：静默丢弃，不回 error、也不改状态
        ws1.send_json({"type": "xiangqi_move", "from_x": 1})
        assert rm.get_room(code)["game_state"]["next"] == RED
    finally:
        _close(ws1, ws2)


# ── 终局 ──────────────────────────────────────────────

def test_xiangqi_checkmate_broadcasts_game_over(client):
    """将死：广播 game_over，房间回到等待态，战绩快照带棋盘。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # 直接摆一个「一车沉底成杀」的残局（红方 R(2,0) 走 (0,0)）
        board = board_from_rows([
            "....k....",
            ".........",
            "R........",
            ".........",
            ".........",
            ".........",
            ".........",
            ".........",
            ".........",
            "...K.....",
        ])
        board[1][1] = "R"
        rm.get_room(code)["game_state"]["board"] = board
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 0, "from_y": 2, "to_x": 0, "to_y": 0,
        })
        m = _recv_type(ws1, "move")
        assert m["check"] is True
        go = _recv_type(ws1, "game_over")
        assert go["winner"] == RED and go["reason"] == "checkmate"
        assert _recv_type(ws2, "game_over")["winner"] == RED
        # 房间回到 waiting（对局结束 = 等待中，可以续开）
        assert rm.get_room(code)["status"] == "waiting"
        # 战绩快照走的是 database 的 xiangqi 分支
        from app.database import _build_snapshot
        snap = _build_snapshot(rm.get_room(code))
        assert snap["game"] == "xiangqi"
        assert snap["board"][0][0] == "R"
        assert snap["winner"] == RED and snap["reason"] == "checkmate"
        assert snap["rows"] == 10 and snap["cols"] == 9
    finally:
        _close(ws1, ws2)


def test_xiangqi_surrender(client):
    """红方认输 → 黑方胜（红黑互翻，不能走成「白方胜」）。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        ws1.send_json({"type": "surrender"})
        go1 = _recv_type(ws1, "game_over")
        go2 = _recv_type(ws2, "game_over")
        assert go1["winner"] == BLACK and go1["reason"] == "surrender"
        assert go2["winner"] == BLACK
    finally:
        _close(ws1, ws2)


def test_xiangqi_leave_midgame_is_surrender(client):
    """对局中离开 = 认输，赢家是对方（同样要红黑互翻）。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        ws2.send_json({"type": "leave"})
        go = _recv_type(ws1, "game_over")
        assert go["winner"] == RED
    finally:
        _close(ws1, ws2)


def test_xiangqi_start_new_round_from_lobby(client):
    """结束后房间回到等待态，房主直接 start 续开，棋盘复位。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 1, "from_y": 7, "to_x": 1, "to_y": 4,
        })
        _recv_type(ws1, "move")
        _recv_type(ws2, "move")
        ws1.send_json({"type": "surrender"})
        _recv_type(ws1, "game_over")
        _recv_type(ws2, "game_over")
        # 终局把真人非房主复位为未准备，续开前黑方要重新准备
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        rs1 = _recv_type(ws1, "game_start")
        assert rs1["game"]["board"][9] == list("RNBAKABNR")
        assert rs1["game"]["next"] == RED
        assert len(rs1["game"]["legal"]) == 44
    finally:
        _close(ws1, ws2)


# ── 重连 ──────────────────────────────────────────────

def test_xiangqi_disconnect_reconnect_sync(client):
    """断线重连：sync 里必须带对局数据，且不是五子棋那套（board_size 15）。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"xq_dc{ts}a")
    t2, _ = _register(client, f"xq_dc{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    rm.set_disconnect_timeout(5)
    try:
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
            ws1.send_json({
                "type": "xiangqi_move",
                "from_x": 1, "from_y": 7, "to_x": 1, "to_y": 4,
            })
            _recv_type(ws1, "move")
            _recv_type(ws2, "move")
        # 红方重连
        _join_room(client, t1, code)
        with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1_new:
            sync = _recv_type(ws1_new, "sync")
            assert sync["game"] is not None
            assert sync["game"]["your_color"] == RED
            assert sync["game"]["rows"] == 10
            assert "board_size" not in sync["game"]
            assert sync["game"]["board"][4][1] == "C"     # 炮已走到 (4,1)
            assert sync["game"]["board"][7][1] is None
            assert sync["game"]["next"] == BLACK
    finally:
        rm.set_disconnect_timeout(120)


# ── 人机 ──────────────────────────────────────────────

def test_xiangqi_bot_takes_a_move(client):
    """人机局：真人走完后，人机应招（回合交回真人）。"""
    import asyncio
    from app.ws import _bot_do_turn

    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"xq_bt{ts}")
    room = _create_room(client, t1)
    code = room["code"]
    with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1:
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        g1 = _recv_type(ws1, "game_start")
        assert g1["game"]["your_color"] == RED        # 真人执红
        bot_id = next(p["id"] for p in rm.get_room(code)["players"] if p.get("is_bot"))
        # 真人走一步
        ws1.send_json({
            "type": "xiangqi_move",
            "from_x": 1, "from_y": 7, "to_x": 1, "to_y": 4,
        })
        m1 = _recv_type(ws1, "move")
        assert m1["next"] == BLACK
        # TestClient 不跑后台任务，手动触发人机回合
        asyncio.get_event_loop().run_until_complete(_bot_do_turn(code, bot_id))
        m2 = _recv_type(ws1, "move", max_skip=5)
        assert m2["piece"] in "rnbakcp"                # 走的是黑子
        assert m2["next"] == RED                       # 回合交回真人
        assert len(m2["from"]) == 2 and len(m2["to"]) == 2


def test_xiangqi_bot_rejected_for_other_games_unchanged(client):
    """加人机白名单加了 xiangqi，但不能顺手把扫雷/2048 放进来。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"xq_bw{ts}")
    for game in ("minesweeper", "2048"):
        room = _create_room(client, t1, game=game)
        with client.websocket_connect(f"/ws/room/{room['code']}?token={t1}") as ws:
            _recv_type(ws, "sync")
            ws.send_json({"type": "add_bot"})
            err = _recv_type(ws, "error")
            assert err["message"] == "该游戏不支持人机"
