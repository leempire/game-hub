"""等待大厅里「互换座位」的 WS 契约（v1.16）。

用户要这条的理由：「棋类第一个位置是先手、牌类决定出牌顺序」。座位就是
`room["players"]` 里的下标，各 `_start_*` 直接读它 —— 所以这条功能有两条
必须钉死的边界：

1. **只在大厅里能用**。开局之后座位已经发出去（`your_seat` / `your_color` /
   UNO 的座位下标），再换等于把整局打乱。
2. **真人之间必须双方同意**。人机没有「不同意」这个语义，直接算同意；
   真人则由服务端把请求转给对方，他点头才真的换。

Run: pytest test/test_room_swap.py -v
"""

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    yield
    rm._rooms.clear()


@pytest.fixture
def client(ws_client):
    return ws_client


def _name(prefix="sw") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 100000}_{uuid.uuid4().hex[:6]}"


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="gomoku"):
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=60):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"没收到 {msg_type}")


def _seats(room, *uids):
    return [rm._get_player_seat(room, u) for u in uids]


def _setup(client, players=2, game="gomoku"):
    """建房 → 加人 → 各连一条 WS（停在等待大厅）。

    五子棋只有 2 个座位，三人用例得换 UNO（4 座）。
    """
    tokens, uids = [], []
    for i in range(players):
        t, uid = _register(client, _name(f"sw{i}"))
        tokens.append(t)
        uids.append(uid)
    room = _create_room(client, tokens[0], game=game)
    code = room["code"]
    for t in tokens[1:]:
        _join_room(client, t, code)
    wss = [client.websocket_connect(f"/ws/room/{code}?token={t}") for t in tokens]
    for ws in wss:
        ws.__enter__()
    for ws in wss:
        _recv_type(ws, "sync")
    return wss, code, tokens, uids


def _close(wss):
    for ws in wss:
        try:
            ws.__exit__(None, None, None)
        except Exception:
            pass


class TestHumanSwap:
    def test_request_then_accept_swaps_seats(self, client):
        wss, code, _, uids = _setup(client)
        try:
            room = rm.get_room(code)
            assert _seats(room, *uids) == [0, 1]

            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            # 被请求方收到弹窗用的那条（带发起人名字，界面不用自己查表）
            req = _recv_type(wss[1], "swap_request")
            assert req["from_id"] == uids[0] and req["from_name"]
            # 发起方也要有回话 —— 不回就是「点了没反应」
            res = _recv_type(wss[0], "swap_result")
            assert res["accepted"] is None and res["auto"] is False
            assert "等对方同意" in res["message"]

            assert _seats(rm.get_room(code), *uids) == [0, 1], "还没同意就换了？"

            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            lobby = _recv_type(wss[0], "lobby")
            assert [p["id"] for p in lobby["room"]["players"]] == [uids[1], uids[0]]
            assert _seats(rm.get_room(code), *uids) == [1, 0]
            done = _recv_type(wss[0], "swap_result")
            assert done["accepted"] is True
            assert "已经调换" in done["message"]
        finally:
            _close(wss)

    def test_reject_keeps_seats(self, client):
        wss, code, _, uids = _setup(client)
        try:
            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            _recv_type(wss[1], "swap_request")
            _recv_type(wss[0], "swap_result")
            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": False})
            done = _recv_type(wss[0], "swap_result")
            assert done["accepted"] is False and "拒绝" in done["message"]
            assert _seats(rm.get_room(code), *uids) == [0, 1]
        finally:
            _close(wss)

    def test_duplicate_request_is_deduped(self, client):
        """连点两次只留一条待答请求 —— 否则对方会连弹两个框。"""
        wss, code, _, uids = _setup(client)
        try:
            for _ in range(2):
                wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
                _recv_type(wss[1], "swap_request")
                _recv_type(wss[0], "swap_result")
            assert len(rm.get_room(code)["swap_requests"]) == 1
            # 同意一次就干净了：第二条不存在的请求必须报「已失效」而不是静默
            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            _recv_type(wss[0], "lobby")
            _recv_type(wss[0], "swap_result")
            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            err = _recv_type(wss[1], "error")
            assert "失效" in err["message"]
        finally:
            _close(wss)

    def test_respond_without_request_is_rejected(self, client):
        """没人向你发过请求时不能凭空换座位（否则可以单方面把别人挪走）。"""
        wss, code, _, uids = _setup(client)
        try:
            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            err = _recv_type(wss[1], "error")
            assert "失效" in err["message"]
            assert _seats(rm.get_room(code), *uids) == [0, 1]
        finally:
            _close(wss)

    def test_third_party_cannot_answer(self, client):
        """请求是发给 B 的，C 替 B 点同意必须无效。"""
        wss, code, _, uids = _setup(client, players=3, game="uno")
        try:
            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            _recv_type(wss[1], "swap_request")
            _recv_type(wss[0], "swap_result")
            wss[2].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            err = _recv_type(wss[2], "error")
            assert "失效" in err["message"]
            assert _seats(rm.get_room(code), *uids) == [0, 1, 2]
        finally:
            _close(wss)

    def test_requester_leaving_invalidates_the_request(self, client):
        """发起人退房后，那条待答请求不能再生效（人已经不在了）。"""
        wss, code, _, uids = _setup(client, players=3, game="uno")
        try:
            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            _recv_type(wss[1], "swap_request")
            _recv_type(wss[0], "swap_result")
            rm.remove_player(code, uids[0])
            assert rm.get_room(code)["swap_requests"] == []
            wss[1].send_json({"type": "swap_respond", "from_id": uids[0], "accept": True})
            err = _recv_type(wss[1], "error")
            assert "失效" in err["message"]
        finally:
            _close(wss)


class TestBotSwap:
    def test_bot_agrees_immediately(self, client):
        # UNO 有 4 个座位，1 个真人 + 1 个人机，还能再换
        wss, code, _, uids = _setup(client, players=1, game="uno")
        try:
            rm.add_bot(code, uids[0])
            bot_id = next(p["id"] for p in rm.get_room(code)["players"] if p["is_bot"])
            assert rm._get_player_seat(rm.get_room(code), bot_id) == 1

            wss[0].send_json({"type": "swap_request", "target_id": bot_id})
            # 服务端先广播 `lobby`（座位顺序变了），再单独回一句 `swap_result`
            lobby = _recv_type(wss[0], "lobby")
            assert [p["id"] for p in lobby["room"]["players"]] == [bot_id, uids[0]]
            res = _recv_type(wss[0], "swap_result")
            assert res["auto"] is True and res["accepted"] is True
            assert "人机" in res["message"]
            assert rm._get_player_seat(rm.get_room(code), uids[0]) == 1
        finally:
            _close(wss)


class TestGuards:
    def test_swap_rejected_while_playing(self, client):
        """**开局后座位就锁死了** —— 各 `_start_*` 已经把座位号发出去了。"""
        wss, code, _, uids = _setup(client)
        try:
            rm._rooms[code]["status"] = "playing"
            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            err = _recv_type(wss[0], "error")
            assert "对局中" in err["message"]
            assert _seats(rm.get_room(code), *uids) == [0, 1]
        finally:
            _close(wss)

    def test_cannot_swap_with_self(self, client):
        wss, code, _, uids = _setup(client)
        try:
            wss[0].send_json({"type": "swap_request", "target_id": uids[0]})
            err = _recv_type(wss[0], "error")
            assert "自己" in err["message"]
        finally:
            _close(wss)

    def test_unknown_target_is_reported(self, client):
        wss, code, _, uids = _setup(client)
        try:
            wss[0].send_json({"type": "swap_request", "target_id": 99999999})
            err = _recv_type(wss[0], "error")
            assert "不在房间" in err["message"]
        finally:
            _close(wss)

    def test_missing_target_is_reported(self, client):
        """载荷不合法也要回一句人话，不能静默丢弃。"""
        wss, code, _, _ = _setup(client)
        try:
            wss[0].send_json({"type": "swap_request"})
            err = _recv_type(wss[0], "error")
            assert err["message"]
            wss[0].send_json({"type": "swap_respond", "accept": True})
            err = _recv_type(wss[0], "error")
            assert err["message"]
        finally:
            _close(wss)

    def test_pending_requests_are_cleared_on_start(self, client):
        """上一局之前挂着的请求不能在下一局回到大厅时突然生效。"""
        wss, code, _, uids = _setup(client)
        try:
            wss[0].send_json({"type": "swap_request", "target_id": uids[1]})
            _recv_type(wss[1], "swap_request")
            _recv_type(wss[0], "swap_result")
            assert rm.get_room(code)["swap_requests"]
            wss[1].send_json({"type": "ready"})
            _recv_type(wss[0], "lobby")
            wss[0].send_json({"type": "start"})
            assert rm.get_room(code)["swap_requests"] == []
        finally:
            _close(wss)
