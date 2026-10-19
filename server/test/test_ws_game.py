"""
WebSocket integration test: full gomoku game + disconnect/reconnect.
Run: python test/test_ws_game.py
Requires: pip install websockets httpx
"""

import sys
import os
import asyncio
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import websockets

BASE = "http://localhost:50541"
WS_BASE = "ws://localhost:50541"


def register(username: str, password: str) -> dict:
    r = httpx.post(f"{BASE}/api/auth/register", json={"username": username, "password": password})
    if r.status_code == 409:
        r = httpx.post(f"{BASE}/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"register/login failed: {r.status_code} {r.text}"
    return r.json()


def create_room(token: str, name: str = None) -> dict:
    r = httpx.post(
        f"{BASE}/api/rooms",
        json={"game": "gomoku", "name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"create_room failed: {r.status_code} {r.text}"
    return r.json()


def join_room(token: str, code: str, password: str = None) -> dict:
    r = httpx.post(
        f"{BASE}/api/rooms/{code}/join",
        json={"password": password},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"join_room failed: {r.status_code} {r.text}"
    return r.json()


async def recv(ws, timeout=5) -> dict:
    """Receive a JSON message with timeout."""
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def recv_until(ws, msg_type: str, timeout=5) -> dict:
    """Receive messages until we get one of the specified type."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {msg_type}")
        msg = await recv(ws, timeout=remaining)
        if msg.get("type") == msg_type:
            return msg


async def test_full_game():
    """Test a complete gomoku game: create → join → ready → start → play → win."""
    print("\n=== Test: Full Game ===")
    ts = int(time.time())
    u1 = register(f"black_{ts}", "password123")
    u2 = register(f"white_{ts}", "password123")
    t1, t2 = u1["token"], u2["token"]
    uid1, uid2 = u1["user"]["id"], u2["user"]["id"]
    print(f"  Registered: black={uid1}, white={uid2}")

    room = create_room(t1, "test_game")
    code = room["code"]
    print(f"  Room created: {code}")
    assert room["status"] == "waiting"
    assert room["host_id"] == uid1
    assert len(room["players"]) == 1

    join_room(t2, code)
    print(f"  Player 2 joined")

    # Connect WebSockets
    async with websockets.connect(
        f"{WS_BASE}/ws/room/{code}?token={t1}"
    ) as ws1, websockets.connect(
        f"{WS_BASE}/ws/room/{code}?token={t2}"
    ) as ws2:
        # Both receive sync
        sync1 = await recv(ws1)
        sync2 = await recv(ws2)
        assert sync1["type"] == "sync", f"Expected sync, got {sync1['type']}"
        assert sync2["type"] == "sync"
        assert len(sync1["room"]["players"]) == 2
        print("  Both received sync")

        # ws2 may receive lobby for its own join
        # Ready up
        await ws1.send(json.dumps({"type": "ready"}))
        await ws2.send(json.dumps({"type": "ready"}))

        # Both should receive lobby updates
        lobby1 = await recv_until(ws1, "lobby")
        lobby2 = await recv_until(ws2, "lobby")
        assert lobby1["type"] == "lobby"
        # Drain any extra lobby messages
        await asyncio.sleep(0.2)

        # Host starts game
        await ws1.send(json.dumps({"type": "start"}))

        gs1 = await recv_until(ws1, "game_start")
        gs2 = await recv_until(ws2, "game_start")
        assert gs1["first"] == "black"
        assert gs1["game"]["your_color"] == "black"
        assert gs2["game"]["your_color"] == "white"
        assert gs1["game"]["board_size"] == 15
        print("  Game started. black=player1, white=player2")

        # Play: Black wins with horizontal 5 at y=0
        moves = [
            (0, 0, "black"), (0, 1, "white"),
            (1, 0, "black"), (1, 1, "white"),
            (2, 0, "black"), (2, 1, "white"),
            (3, 0, "black"), (3, 1, "white"),
            (4, 0, "black"),  # Black wins
        ]
        for i, (x, y, expected_color) in enumerate(moves):
            if expected_color == "black":
                await ws1.send(json.dumps({"type": "move", "x": x, "y": y}))
            else:
                await ws2.send(json.dumps({"type": "move", "x": x, "y": y}))
            # Both receive move broadcast
            m1 = await recv_until(ws1, "move")
            m2 = await recv_until(ws2, "move")
            assert m1["x"] == x and m1["y"] == y
            assert m1["color"] == expected_color
            if i < len(moves) - 1:
                assert m1["next"] == ("white" if expected_color == "black" else "black")

        # Last move: Black wins
        go1 = await recv_until(ws1, "game_over")
        go2 = await recv_until(ws2, "game_over")
        assert go1["winner"] == "black"
        assert go1["reason"] == "five"
        assert go2["winner"] == "black"
        print("  Black wins with five in a row! ✓")

        # 续开新局：房间已回到 waiting，房主直接 start（rematch 协议已删除）
        await ws1.send(json.dumps({"type": "start"}))
        rs1 = await recv_until(ws1, "game_start")
        rs2 = await recv_until(ws2, "game_start")
        assert rs1["game"]["your_color"] == "black"  # Host still black
        assert rs2["game"]["your_color"] == "white"
        print("  New round started from lobby successfully ✓")

    print("=== Full Game Test PASSED ===\n")


async def test_disconnect_reconnect():
    """Test disconnect during game and reconnect within 120s."""
    print("=== Test: Disconnect & Reconnect ===")
    ts = int(time.time())
    u1 = register(f"dc_b_{ts}", "password123")
    u2 = register(f"dc_w_{ts}", "password123")
    t1, t2 = u1["token"], u2["token"]
    uid1 = u1["user"]["id"]
    print(f"  Registered players: black={uid1}")

    room = create_room(t1, "dc_test")
    code = room["code"]
    join_room(t2, code)

    ws1 = await websockets.connect(f"{WS_BASE}/ws/room/{code}?token={t1}")
    ws2 = await websockets.connect(f"{WS_BASE}/ws/room/{code}?token={t2}")

    # Drain sync messages
    await recv(ws1)
    await recv(ws2)

    # Ready + start
    await ws1.send(json.dumps({"type": "ready"}))
    await ws2.send(json.dumps({"type": "ready"}))
    await asyncio.sleep(0.2)

    await ws1.send(json.dumps({"type": "start"}))
    await recv_until(ws1, "game_start")
    await recv_until(ws2, "game_start")
    print("  Game started")

    # Black makes 2 moves
    await ws1.send(json.dumps({"type": "move", "x": 0, "y": 0}))
    await recv_until(ws1, "move")
    await recv_until(ws2, "move")

    await ws2.send(json.dumps({"type": "move", "x": 0, "y": 1}))
    await recv_until(ws1, "move")
    await recv_until(ws2, "move")

    await ws1.send(json.dumps({"type": "move", "x": 1, "y": 0}))
    await recv_until(ws1, "move")
    await recv_until(ws2, "move")

    await ws2.send(json.dumps({"type": "move", "x": 1, "y": 1}))
    await recv_until(ws1, "move")
    await recv_until(ws2, "move")
    print("  4 moves played. Black disconnects...")

    # Black disconnects abruptly
    await ws1.close()
    await asyncio.sleep(0.5)

    # White should receive player_disconnected
    dc_msg = await recv_until(ws2, "player_disconnected", timeout=3)
    assert dc_msg["user_id"] == uid1
    print(f"  player_disconnected received for user {uid1} ✓")

    # Black reconnects: POST /join + WS connect
    join_result = join_room(t1, code)
    assert join_result["code"] == code
    print("  Black rejoined via POST /join ✓")

    ws1_new = await websockets.connect(f"{WS_BASE}/ws/room/{code}?token={t1}")
    sync = await recv(ws1_new, timeout=3)
    assert sync["type"] == "sync"
    assert sync["game"] is not None
    assert sync["game"]["your_color"] == "black"
    assert len(sync["game"]["moves"]) == 4
    print(f"  Reconnected! sync has {len(sync['game']['moves'])} moves ✓")

    # White should receive player_reconnected
    rc_msg = await recv_until(ws2, "player_reconnected", timeout=3)
    assert rc_msg["user_id"] == uid1
    print("  player_reconnected received ✓")

    # Continue game - Black wins
    remaining = [
        (2, 0, "black"), (2, 1, "white"),
        (3, 0, "black"), (3, 1, "white"),
        (4, 0, "black"),  # Black wins
    ]
    for x, y, color in remaining:
        if color == "black":
            await ws1_new.send(json.dumps({"type": "move", "x": x, "y": y}))
        else:
            await ws2.send(json.dumps({"type": "move", "x": x, "y": y}))
        await recv_until(ws1_new, "move")
        await recv_until(ws2, "move")

    go = await recv_until(ws1_new, "game_over")
    go2 = await recv_until(ws2, "game_over")
    assert go["winner"] == "black"
    assert go["reason"] == "five"
    print("  Black wins after reconnect! ✓")

    await ws1_new.close()
    await ws2.close()
    print("=== Disconnect & Reconnect Test PASSED ===\n")


async def test_surrender():
    """Test surrender during game."""
    print("=== Test: Surrender ===")
    ts = int(time.time())
    u1 = register(f"sur_b_{ts}", "password123")
    u2 = register(f"sur_w_{ts}", "password123")
    t1, t2 = u1["token"], u2["token"]

    room = create_room(t1, "surrender_test")
    code = room["code"]
    join_room(t2, code)

    async with websockets.connect(
        f"{WS_BASE}/ws/room/{code}?token={t1}"
    ) as ws1, websockets.connect(
        f"{WS_BASE}/ws/room/{code}?token={t2}"
    ) as ws2:
        await recv(ws1)  # sync
        await recv(ws2)  # sync
        await ws1.send(json.dumps({"type": "ready"}))
        await ws2.send(json.dumps({"type": "ready"}))
        await asyncio.sleep(0.2)
        await ws1.send(json.dumps({"type": "start"}))
        await recv_until(ws1, "game_start")
        await recv_until(ws2, "game_start")

        # White surrenders
        await ws2.send(json.dumps({"type": "surrender"}))
        go1 = await recv_until(ws1, "game_over")
        go2 = await recv_until(ws2, "game_over")
        assert go1["winner"] == "black"
        assert go1["reason"] == "surrender"
        print("  White surrendered, black wins ✓")

    print("=== Surrender Test PASSED ===\n")


async def main():
    print("=" * 50)
    print("GameHub WebSocket Integration Tests")
    print("=" * 50)
    await test_full_game()
    await test_disconnect_reconnect()
    await test_surrender()
    print("=" * 50)
    print("ALL TESTS PASSED ✓✓✓")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
