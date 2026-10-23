"""WS 层的连接隔离：一个人的连接出问题，不能把**别人**的连接一起带走。

`ws_room` 的接收循环把 `_handle_message` 包在 `try` 里，任何异常都会让循环退出、
走掉线流程。所以处理器里**给别人发消息**时抛出的异常（对手的连接刚断、但他那边的
接收循环还没来得及注销），会把**正在操作的这个人**踢下线——他自己的连接毫无问题。

Run: pytest test/test_ws_resilience.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app import ws as wsmod


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    yield


class _DeadSocket:
    """对端已经断开、但还挂在 `_connections` 里的连接：一发就抛。"""

    async def send_json(self, message):
        raise RuntimeError("Cannot call send once a close message has been sent.")

    async def close(self, *a, **kw):
        pass


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _recv_type(ws, msg_type, max_skip=15):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setup(client, game):
    ts = int(time.time() * 1000) % 100000
    tokens, uids = [], []
    for i in range(2):
        t, uid = _register(client, f"rs_{game[:3]}_{ts}_{i}")
        tokens.append(t)
        uids.append(uid)
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {tokens[0]}"})
    assert r.status_code == 200
    code = r.json()["code"]
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {tokens[1]}"})
    assert r.status_code == 200
    wss = [client.websocket_connect(f"/ws/room/{code}?token={t}") for t in tokens]
    for ws in wss:
        ws.__enter__()
    for ws in wss:
        _recv_type(ws, "sync")
    wss[1].send_json({"type": "ready"})
    for ws in wss:
        _recv_type(ws, "lobby")
    wss[0].send_json({"type": "start"})
    for ws in wss:
        _recv_type(ws, "game_start")
    return wss, code, uids


def _close(wss):
    for ws in wss:
        try:
            ws.__exit__(None, None, None)
        except Exception:
            pass


def test_dead_peer_socket_does_not_kick_the_actor(ws_client):
    """俄罗斯方块：给对手推排行榜时对手的连接抛了 —— 操作者本人必须照常在线。"""
    wss, code, uids = _setup(ws_client, "tetris")
    try:
        wsmod._connections[code][uids[1]] = _DeadSocket()
        wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
        assert _recv_type(wss[0], "tetris_state")["last_action"] == "lock"
        # 连接还活着：下一手照常回包（老代码这里收到的是断开）
        wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
        assert _recv_type(wss[0], "tetris_state")["last_action"] == "lock"
        assert wsmod._connections[code].get(uids[0]) is not None
    finally:
        _close(wss)


def test_handler_exception_keeps_connection(ws_client, monkeypatch):
    """处理器自己抛了异常：这一条消息作废，但连接不能断（否则玩家被判掉线）。"""
    wss, code, uids = _setup(ws_client, "tetris")
    try:
        real = rm.tetris_apply_actions
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyError("boom")
            return real(*a, **kw)

        monkeypatch.setattr(rm, "tetris_apply_actions", flaky)
        wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
        wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
        assert _recv_type(wss[0], "tetris_state")["last_action"] == "lock"
        room = rm.get_room(code)
        me = next(p for p in room["players"] if p["id"] == uids[0])
        assert me["connected"] is True
    finally:
        _close(wss)
