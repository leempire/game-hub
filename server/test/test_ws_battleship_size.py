"""海战棋**棋盘尺寸 / 舰队配置**的房间级设置集成测试。

尺寸是 v13 新加的（调研「第三梯队」第 5 条），走的是与扫雷难度 / 军棋玩法 /
深空休眠地图**完全同构**的那一套，本文件锁住这条契约：

- 建房给缺省档（标准 10×10），创建接口不收尺寸；
- 只有房主能改、只有等待中能改、非法尺寸被拒；
- 改完广播一条 `lobby` 给全房；
- **选中的尺寸真的驱动开局**（`gs["size"]` 与两边的空盘边长）。

v1.15 起设置走**统一入口** `room_setting`（`{key, value}`），下发的也不再是
`room.bs_size` / `room.bs_sizes` 而是统一的 `room.settings` —— 跨游戏的那部分
契约在 `test_room_settings.py`，这里只管海战棋这一档。

Run: pytest test/test_ws_battleship_size.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm


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


_TS = int(time.time() * 1000) % 100000


def _u(name: str) -> str:
    """用户名要唯一：库是持久化的，写死名字第二次跑就 409 了。"""
    return f"bs_{name}_{_TS}"


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token):
    r = client.post("/api/rooms", json={"game": "battleship"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"未收到 {msg_type}")


def _setting(room, key):
    """从房间载荷的统一 `settings` 里取一项（v1.15 起设置只有这一份结构）。"""
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


def _recv_lobby_with(ws, size, limit=20):
    """读到一条 `board_size` 已经生效的 `lobby`。

    **不能只读第一条 `lobby`**：别的连接握手时服务端会推一条（玩家名单/在线态变了），
    那条还带着旧尺寸 —— 读到它断言就假红。这里跳过旧的、只认生效的那条。
    """
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == "lobby" and _setting(msg["room"], "board_size")["value"] == size:
            return msg
    raise AssertionError(f"没有收到尺寸为 {size} 的 lobby")


@pytest.fixture
def ws_open(client):
    """开一条 WS 并读到 `sync`；用例结束**无论成败**都关掉。

    漏关会让 `ws_client` 的 portal 在退出时挂住（下一个用例根本跑不到），
    所以清理不能只写在用例末尾 —— 断言一失败就没人关它了。
    """
    opened = []

    def _open(code, token):
        ws = client.websocket_connect(f"/ws/room/{code}?token={token}")
        ws.__enter__()
        _recv_type(ws, "sync")
        opened.append(ws)
        return ws

    yield _open
    for ws in opened:
        try:
            ws.__exit__(None, None, None)
        except Exception:
            pass


def _pick(ws, size):
    ws.send_json({"type": "room_setting", "key": "board_size", "value": size})
    return _recv_lobby_with(ws, size)


class TestBattleshipSize:
    def test_create_room_defaults_to_standard(self, client):
        """建房给缺省档（标准 10×10）；创建接口不收尺寸。"""
        t1, _ = _register(client, _u("def"))
        room = _create_room(client, t1)
        item = _setting(room, "board_size")
        assert item["value"] == 10
        assert item["value_label"] == "标准 · 10×10"
        # **档位表由服务端下发**（客户端不自己抄一份尺寸表）
        assert [d["value"] for d in item["choices"]] == [6, 10, 12]
        assert all(d["label"] and d["blurb"] for d in item["choices"])

    def test_host_can_pick_and_everyone_sees_it(self, client, ws_open):
        t1, _ = _register(client, _u("host"))
        t2, _ = _register(client, _u("guest"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws1 = ws_open(code, t1)
        ws2 = ws_open(code, t2)

        ws1.send_json({"type": "room_setting", "key": "board_size", "value": 6})
        # **两条连接都要收到** —— 只回给房主的话，别人看到的还是旧尺寸
        assert _setting(_recv_lobby_with(ws1, 6)["room"], "board_size")["value"] == 6
        assert _setting(_recv_lobby_with(ws2, 6)["room"], "board_size")["value"] == 6

    def test_non_host_cannot_pick(self, client, ws_open):
        t1, _ = _register(client, _u("h2"))
        t2, _ = _register(client, _u("g2"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws_open(code, t1)
        ws2 = ws_open(code, t2)

        ws2.send_json({"type": "room_setting", "key": "board_size", "value": 12})
        assert "房主" in _recv_type(ws2, "error")["message"]
        assert rm.get_room(code)["bs_size"] == 10

    def test_invalid_size_is_rejected(self, client, ws_open):
        t1, _ = _register(client, _u("bad"))
        code = _create_room(client, t1)["code"]
        ws = ws_open(code, t1)

        # `True` 也在这条路上：Python 里 `True == 1`，不先挡掉就能冒充数字档位
        for bad in (99, "6", None, True):
            ws.send_json({"type": "room_setting", "key": "board_size", "value": bad})
            assert "棋盘" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["bs_size"] == 10

    def test_cannot_pick_after_start(self, client, ws_open):
        """开局后改不了：尺寸决定棋盘与舰队，换掉等于把整局拆了。"""
        t1, _ = _register(client, _u("late"))
        t2, _ = _register(client, _u("late2"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws = ws_open(code, t1)
        ws2 = ws_open(code, t2)
        _pick(ws, 6)

        # 海战棋要 2 人**都准备**才开得起来（少一个 `ready` 就只会回一条 error，
        # 而 `receive_json()` 没有超时 —— 漏了这一步整个用例会永久挂住）
        ws2.send_json({"type": "ready"})
        _recv_type(ws, "lobby")
        ws.send_json({"type": "start"})
        _recv_type(ws, "game_start")

        ws.send_json({"type": "room_setting", "key": "board_size", "value": 12})
        assert "开始" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["bs_size"] == 6

    def test_picked_size_drives_the_started_game(self, client, ws_open):
        """选中的尺寸必须真的落到开局上：`gs["size"]`、两张盘边长、以及**舰队**。"""
        t1, _ = _register(client, _u("drive"))
        t2, _ = _register(client, _u("drive2"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws = ws_open(code, t1)
        ws2 = ws_open(code, t2)
        _pick(ws, 12)

        ws2.send_json({"type": "ready"})
        _recv_type(ws, "lobby")
        ws.send_json({"type": "start"})
        g = _recv_type(ws, "game_start")["game"]
        assert g["board_size"] == 12
        assert len(g["own_board"]) == 12 and len(g["own_board"][0]) == 12
        assert [d["size"] for d in g["fleet"]] == [5, 4, 3, 3, 2, 2]
        assert rm.get_room(code)["game_state"]["size"] == 12
