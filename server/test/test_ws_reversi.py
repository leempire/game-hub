"""Reversi WebSocket integration tests using FastAPI TestClient.

Tests: create room, join, start, legal move with flips, illegal move error,
pass detection, game over with score, disconnect/reconnect sync.

Run: pytest test/test_ws_reversi.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm


@pytest.fixture(autouse=True)
def _clean_rooms():
    """Clear all rooms before each test to avoid cross-test interference."""
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
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="reversi"):
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
    """Receive messages until we get one of the specified type."""
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setup_playing_game(client):
    """Helper: create room, join, connect WS, ready, start. Returns (ws1, ws2, code)."""
    import time
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"rv_h{ts}a")
    t2, uid2 = _register(client, f"rv_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws2 = client.websocket_connect(f"/ws/room/{code}?token={t2}")
    ws1.__enter__()
    ws2.__enter__()
    # Drain sync + lobby messages from connection
    _recv_type(ws1, "sync")
    _recv_type(ws2, "sync")
    # ws1 may have received lobby from ws2's connection — drain it
    # We do this by sending ready and then draining all lobby messages
    ws2.send_json({"type": "ready"})
    # Drain until we're past lobby messages
    # ws1 might have: lobby (from ws2 connect), lobby (from ws2 ready)
    # ws2 might have: lobby (from ws2 ready)
    _recv_type(ws1, "lobby")
    _recv_type(ws2, "lobby")
    # Start game
    ws1.send_json({"type": "start"})
    g1 = _recv_type(ws1, "game_start")
    g2 = _recv_type(ws2, "game_start")
    return ws1, ws2, code, t1, t2, g1, g2


def test_reversi_create_and_join(client):
    """Create reversi room and verify max_players=2."""
    import time
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"rv_cj{ts}")
    room = _create_room(client, t1)
    assert room["game"] == "reversi"
    assert room["max_players"] == 2
    assert room["status"] == "waiting"


def test_reversi_game_start(client):
    """Start a reversi game and verify initial board state."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        assert g1["game"]["board_size"] == 8
        assert g1["game"]["your_color"] == "black"
        assert g2["game"]["your_color"] == "white"
        assert g1["game"]["next"] == "black"
        board = g1["game"]["board"]
        assert board[3][3] == 2  # white
        assert board[4][4] == 2  # white
        assert board[3][4] == 1  # black
        assert board[4][3] == 1  # black
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_reversi_legal_move_with_flips(client):
    """Make a legal move and verify flips are broadcast."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # Black plays (2,3): flips (3,3) from W to B
        ws1.send_json({"type": "move", "x": 2, "y": 3})
        m1 = _recv_type(ws1, "move")
        m2 = _recv_type(ws2, "move")
        assert m1["x"] == 2 and m1["y"] == 3
        assert m1["color"] == "black"
        # Flipped positions should include (3,3)
        flipped = m1.get("flipped", [])
        assert [3, 3] in flipped
        assert m1["next"] == "white"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_reversi_illegal_move_error(client):
    """Illegal move should return an error message."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # Black tries to play at (0,0) — illegal (doesn't flip anything)
        ws1.send_json({"type": "move", "x": 0, "y": 0})
        err = _recv_type(ws1, "error")
        assert "message" in err
        # Black tries to play at occupied center (3,3) — illegal
        ws1.send_json({"type": "move", "x": 3, "y": 3})
        err2 = _recv_type(ws1, "error")
        assert err2["type"] == "error"
        # White tries to play out of turn
        ws2.send_json({"type": "move", "x": 5, "y": 4})
        err3 = _recv_type(ws2, "error")
        assert err3["type"] == "error"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_reversi_disconnect_reconnect_sync(client):
    """Disconnect and reconnect should restore full game state via sync."""
    import time
    ts = int(time.time() * 1000) % 100000
    t1, uid1 = _register(client, f"rv_dc{ts}a")
    t2, uid2 = _register(client, f"rv_dc{ts}b")
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
            # Black plays (2,3)
            ws1.send_json({"type": "move", "x": 2, "y": 3})
            _recv_type(ws1, "move")
            _recv_type(ws2, "move")
        # ws1 closed, ws2 still connected
        # Black reconnects: POST join + new WS
        _join_room(client, t1, code)
        with client.websocket_connect(f"/ws/room/{code}?token={t1}") as ws1_new:
            sync = _recv_type(ws1_new, "sync")
            assert sync["game"] is not None
            assert sync["game"]["your_color"] == "black"
            assert sync["game"]["board_size"] == 8
            board = sync["game"]["board"]
            assert board[3][2] == 1  # black placed at (2,3)
            assert board[3][3] == 1  # flipped from white to black
    finally:
        rm.set_disconnect_timeout(120)


def test_reversi_surrender(client):
    """Surrender should end the game with opponent winning."""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        ws1.send_json({"type": "surrender"})
        go1 = _recv_type(ws1, "game_over")
        go2 = _recv_type(ws2, "game_over")
        assert go1["winner"] == "white"
        assert go1["reason"] == "surrender"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_reversi_start_new_round_from_lobby(client):
    """对局结束后房间回到等待态，房主直接 start 即可续开（rematch 协议已删除）。"""
    ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
    try:
        # Black surrenders to end game
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
        # Board should be reset to initial state
        board = rs1["game"]["board"]
        assert board[3][3] == 2 and board[4][4] == 2
        assert board[3][4] == 1 and board[4][3] == 1
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)
