"""扫雷难度的**房间级设置**集成测试。

难度以前是「创建房间时选、进房后改不了」，与军棋的玩法、深空休眠的地图不一致。
现在统一成「房主在等待大厅里挑」，本文件锁住这条契约：

- 建房只给缺省档（中级），创建接口**不再收** `difficulty`；
- 只有房主能改、只有等待中能改、非法档被拒；
- 改完广播一条 `lobby` 给全房（难度是房间级设置，别人也得看到）；
- 选中的档**真的驱动开局**（盘面尺寸 / 雷数 / 命数）。

v1.15 起设置走**统一入口** `room_setting`（`{key, value}`），下发的也不再是
`room.difficulty` 而是统一的 `room.settings` —— 跨游戏的那部分契约在
`test_room_settings.py`，这里只管扫雷这一档。

Run: pytest test/test_ws_mine_pick.py -v
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
    return f"mp_{name}_{_TS}"


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token):
    r = client.post("/api/rooms", json={"game": "minesweeper"},
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
    """从房间载荷的统一 `settings` 里取一项（找不到就是断言失败，不是返回 None）。"""
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


def _recv_lobby_with(ws, difficulty, limit=20):
    """读到一条 `difficulty` 已经生效的 `lobby`。

    **不能只读第一条 `lobby`**：别的连接握手时服务端会推一条（玩家名单/在线态变了），
    那条还带着旧难度 —— 读到它断言就假红。这里跳过旧的、只认生效的那条。
    """
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == "lobby" and _setting(msg["room"], "difficulty")["value"] == difficulty:
            return msg
    raise AssertionError(f"没有收到难度为 {difficulty} 的 lobby")


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


def _pick(ws, difficulty):
    ws.send_json({"type": "room_setting", "key": "difficulty", "value": difficulty})
    return _recv_lobby_with(ws, difficulty)


class TestMineDifficulty:
    def test_create_room_defaults_to_intermediate(self, client):
        """建房只给缺省档；创建接口不再收 `difficulty`。"""
        t1, _ = _register(client, _u("def"))
        room = _create_room(client, t1)
        assert _setting(room, "difficulty")["value"] == "intermediate"
        assert _setting(room, "difficulty")["value_label"] == "中级"
        assert room["max_players"] == 4

    def test_host_can_pick_and_everyone_sees_it(self, client, ws_open):
        t1, _ = _register(client, _u("host"))
        t2, _ = _register(client, _u("guest"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws1 = ws_open(code, t1)
        ws2 = ws_open(code, t2)

        ws1.send_json({"type": "room_setting", "key": "difficulty", "value": "beginner"})
        # **两条连接都要收到** —— 只回给房主的话，别人看到的还是旧档
        assert _setting(_recv_lobby_with(ws1, "beginner")["room"], "difficulty")["value"] \
            == "beginner"
        assert _setting(_recv_lobby_with(ws2, "beginner")["room"], "difficulty")["value"] \
            == "beginner"

    def test_non_host_cannot_pick(self, client, ws_open):
        t1, _ = _register(client, _u("h2"))
        t2, _ = _register(client, _u("g2"))
        code = _create_room(client, t1)["code"]
        _join_room(client, t2, code)
        ws_open(code, t1)
        ws2 = ws_open(code, t2)

        ws2.send_json({"type": "room_setting", "key": "difficulty", "value": "advanced"})
        assert "房主" in _recv_type(ws2, "error")["message"]
        assert rm.get_room(code)["difficulty"] == "intermediate"

    def test_invalid_difficulty_is_rejected(self, client, ws_open):
        t1, _ = _register(client, _u("bad"))
        code = _create_room(client, t1)["code"]
        ws = ws_open(code, t1)

        ws.send_json({"type": "room_setting", "key": "difficulty", "value": "nightmare"})
        assert "难度" in _recv_type(ws, "error")["message"]
        # 漏传 key 也走同一条拒绝路径（`value` 缺失由 coerce 兜住，见下一条）
        ws.send_json({"type": "room_setting", "value": "beginner"})
        assert "设置项" in _recv_type(ws, "error")["message"]
        ws.send_json({"type": "room_setting", "key": "difficulty"})
        assert "难度" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["difficulty"] == "intermediate"

    def test_cannot_pick_after_start(self, client, ws_open):
        """开局后改不了：难度决定盘面尺寸与命数，换掉等于把整局拆了。"""
        t1, _ = _register(client, _u("late"))
        code = _create_room(client, t1)["code"]
        ws = ws_open(code, t1)
        _pick(ws, "beginner")

        ws.send_json({"type": "start"})          # 扫雷 1 人即可开
        _recv_type(ws, "game_start")

        ws.send_json({"type": "room_setting", "key": "difficulty", "value": "advanced"})
        assert "开始" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["difficulty"] == "beginner"

    def test_picked_difficulty_drives_the_started_game(self, client, ws_open):
        """选中的档必须真的落到 `create_game` 上（尺寸/雷数/命数三项一起看）。"""
        t1, _ = _register(client, _u("drive"))
        code = _create_room(client, t1)["code"]
        ws = ws_open(code, t1)
        _pick(ws, "advanced")

        ws.send_json({"type": "start"})
        g = _recv_type(ws, "game_start")["game"]
        assert (g["width"], g["height"], g["mines"], g["lives"]) == (30, 16, 99, 3)

    def test_old_pick_message_tells_the_client_to_update(self, client, ws_open):
        """v1.15 之前的 `mine_pick` 留了一块「墓碑」：**回一句人话，不静默丢弃**。

        老 APK 不会被自动拦下（应用内更新是用户手动点的），静默丢弃在界面上就是
        「点了没反应」—— 本项目最忌讳的失败模式。
        """
        t1, _ = _register(client, _u("old"))
        code = _create_room(client, t1)["code"]
        ws = ws_open(code, t1)

        ws.send_json({"type": "mine_pick", "difficulty": "beginner"})
        assert "更新" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["difficulty"] == "intermediate"
