"""**房间级设置**的跨游戏契约（v1.15）。

在这之前，五处设置只是「约定俗成地同构」：五个命名不一致的 setter、五条 ws 消息、
五段几乎一样的 handler，而且**可选项列表的形状有四种**，扫雷与军棋的档位表干脆
不下发（客户端各抄一份，军棋直接写死在 JSX 里）。

现在只有 `room_settings.py` 一张表 + 一条 `room_setting` 消息。本文件锁住的是
**五个游戏共用的那部分契约**，各游戏自己的细节仍在各自的文件里
（`test_ws_mine_pick.py` / `test_ws_battleship_size.py` / `test_ws_luzhanqi.py` /
`test_ws_uno.py` 的 `TestRulesPick`）。

Run: pytest test/test_room_settings.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app import room_settings as rs

#: 有房间级设置的五个游戏（其余游戏 `settings` 应当压根不下发）。
GAMES = ("minesweeper", "luzhanqi", "stasis", "battleship", "uno")


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
    """用户名要唯一且**不超过 20 字符**（注册接口的校验是 3-20 位）。"""
    return f"rs_{name[:10]}_{_TS}"


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game):
    r = client.post("/api/rooms", json={"game": game},
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
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


def _another_value(spec, current):
    """这一项的一个**不同于当前**的合法取值（每一项都要能真的改一下）。"""
    if spec.kind == rs.KIND_TOGGLE:
        return not current
    for choice in spec.choices:
        if choice["value"] != current:
            return choice["value"]
    raise AssertionError(f"{spec.key} 只有一个取值，没法验证写回")


@pytest.fixture
def ws_open(client):
    """开一条 WS 并读到 `sync`；用例结束**无论成败**都关掉。

    漏关会让 `ws_client` 的 portal 在退出时挂住（后面的用例根本跑不到），
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


class TestPayloadShape:
    """`room.settings` 的形状 —— 客户端按 `kind` 通用渲染，所以形状必须处处一致。"""

    @pytest.mark.parametrize("game", GAMES)
    def test_every_item_has_the_same_shape(self, client, game):
        t1, _ = _register(client, _u(f"shape_{game}"))
        room = _create_room(client, t1, game)
        items = room.get("settings")
        assert items, f"{game} 没有下发 settings"
        for item in items:
            for field in ("key", "label", "kind", "value", "value_label", "blurb"):
                assert field in item, f"{game} 的 {item.get('key')} 缺字段 {field}"
            assert item["kind"] in (rs.KIND_CHOICE, rs.KIND_TOGGLE)
            assert item["label"] and item["value_label"], f"{game}/{item['key']} 没写中文名"
            if item["kind"] == rs.KIND_CHOICE:
                assert item["choices"], f"{game}/{item['key']} 是 choice 却没有 choices"
                for choice in item["choices"]:
                    assert set(choice) == {"value", "label", "blurb"}
                    assert choice["label"] and choice["blurb"]
            else:
                # 开关按「一排芯片」渲染（两个状态各一枚），两个中文名都要给
                assert len(item["toggle_labels"]) == 2
                assert all(item["toggle_labels"])

    @pytest.mark.parametrize("game", GAMES)
    def test_current_value_is_among_the_choices(self, client, game):
        """当前值必须能在 choices 里找到 —— 否则界面会「一个芯片都不高亮」。

        唯一例外是 UNO 的 `preset`：`custom`（逐条改过房规）**刻意不在** choices 里，
        它不是一个可点的档位，只是「当前这套不完全是任何一个预设」这个状态。
        """
        t1, _ = _register(client, _u(f"cur_{game}"))
        room = _create_room(client, t1, game)
        for item in room["settings"]:
            if item["kind"] != rs.KIND_CHOICE or item["key"] == "preset":
                continue
            values = [c["value"] for c in item["choices"]]
            assert item["value"] in values, f"{game}/{item['key']} 的当前值不在 choices 里"

    def test_a_game_without_settings_ships_nothing(self, client):
        """没有房间级设置的游戏**压根不下发 `settings`**（客户端据此决定显不显示入口）。"""
        t1, _ = _register(client, _u("none"))
        assert "settings" not in _create_room(client, t1, "gomoku")

    def test_luzhanqi_reveal_is_gated_on_hidden_mode(self, client):
        """「阵亡公开身份」只在暗棋下有意义 —— 由 `visible_when` 交给客户端过滤。

        常显方案被否掉：明棋下点它界面上什么都不会变（服务端只在 `hidden` 下读它），
        正是本项目反复栽的「点了没反应」。
        """
        t1, _ = _register(client, _u("vis"))
        room = _create_room(client, t1, "luzhanqi")
        assert _setting(room, "reveal")["visible_when"] == {"key": "mode", "value": "hidden"}
        # `mode` 自己没有条件（它永远显示）
        assert "visible_when" not in _setting(room, "mode")

    @pytest.mark.parametrize("game", GAMES)
    def test_every_store_key_is_writable_and_readable(self, client, game):
        """注册表声明的 `store` 必须真的落在房间字典里 —— 防「注册表与引擎各写一个键」。

        做法是**每一项都改一遍再读回来**。UNO 的档位与 7 条房规是派生值
        （`store=None`，真正写的是 `uno_rules`），它们同样要能写能读。
        """
        token, uid = _register(client, _u(f"store_{game}"))
        code = _create_room(client, token, game)["code"]
        room = rm.get_room(code)
        for spec in rs.specs_for(game):
            if spec.store:
                assert spec.store in room, \
                    f"{game} 的注册表声明了 {spec.store}，房间字典里却没有"
            wanted = _another_value(spec, rs.value_of(spec, room))
            rm.set_room_setting(code, uid, spec.key, wanted)
            assert rs.value_of(spec, room) == wanted, f"{game}/{spec.key} 写了没生效"


class TestUniformEntry:
    """一条消息、一个入口、一套校验。"""

    def test_cross_game_key_is_refused(self, client, ws_open):
        """在 UNO 房里改扫雷难度要被拒 —— 老代码的 `set_battleship_size` 恰恰漏了这道校验。

        v1.15 之后它由 `spec_for(game, key)` **结构性保证**：查不到就是「没这项设置」。
        """
        t1, _ = _register(client, _u("cross"))
        code = _create_room(client, t1, "uno")["code"]
        ws = ws_open(code, t1)
        ws.send_json({"type": "room_setting", "key": "difficulty", "value": "beginner"})
        assert "没有" in _recv_type(ws, "error")["message"]
        ws.send_json({"type": "room_setting", "key": "board_size", "value": 6})
        assert "没有" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code).get("difficulty") is None
        assert "bs_size" not in rm.get_room(code)

    def test_unknown_key_is_refused(self, client, ws_open):
        t1, _ = _register(client, _u("unk"))
        code = _create_room(client, t1, "minesweeper")["code"]
        ws = ws_open(code, t1)
        ws.send_json({"type": "room_setting", "key": "nonsense", "value": 1})
        assert "设置" in _recv_type(ws, "error")["message"]
        ws.send_json({"type": "room_setting", "value": 1})
        assert "设置项" in _recv_type(ws, "error")["message"]

    def test_non_host_cannot_change_anything(self, client, ws_open):
        t1, _ = _register(client, _u("h"))
        t2, _ = _register(client, _u("g"))
        code = _create_room(client, t1, "stasis")["code"]
        _join_room(client, t2, code)
        ws_open(code, t1)
        ws2 = ws_open(code, t2)
        ws2.send_json({"type": "room_setting", "key": "map", "value": "suite"})
        assert "房主" in _recv_type(ws2, "error")["message"]
        assert rm.get_room(code)["stasis_map_id"] is None

    def test_pick_is_refused_while_playing(self, client, ws_open):
        t1, _ = _register(client, _u("play"))
        code = _create_room(client, t1, "minesweeper")["code"]
        ws = ws_open(code, t1)
        ws.send_json({"type": "start"})          # 扫雷 1 人即可开
        _recv_type(ws, "game_start")
        ws.send_json({"type": "room_setting", "key": "difficulty", "value": "advanced"})
        assert "开始" in _recv_type(ws, "error")["message"]
        assert rm.get_room(code)["difficulty"] == "intermediate"

    def test_change_is_broadcast_to_the_whole_room(self, client, ws_open):
        """只回给房主的话，别人看到的还是旧值 —— 房间级设置必须全房广播。"""
        t1, _ = _register(client, _u("b1"))
        t2, _ = _register(client, _u("b2"))
        code = _create_room(client, t1, "stasis")["code"]
        _join_room(client, t2, code)
        ws1 = ws_open(code, t1)
        ws2 = ws_open(code, t2)
        ws1.send_json({"type": "room_setting", "key": "map", "value": "suite"})
        for ws in (ws1, ws2):
            found = False
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "lobby" and \
                        _setting(msg["room"], "map")["value"] == "suite":
                    found = True
                    break
            assert found, "改完地图后没收到生效的 lobby"

    def test_retired_messages_answer_instead_of_silence(self, client, ws_open):
        """v1.15 之前那五条消息留了「墓碑」：**回一句人话，不静默丢弃**。

        老 APK 不会被自动拦下（应用内更新是用户手动点的），静默丢弃在界面上就是
        「点了没反应」—— 本项目最忌讳的失败模式。
        """
        t1, _ = _register(client, _u("old"))
        code = _create_room(client, t1, "uno")["code"]
        ws = ws_open(code, t1)
        for old in ("mine_pick", "stasis_pick_map", "battleship_pick_size",
                    "luzhanqi_pick", "uno_pick"):
            ws.send_json({"type": old})
            assert "更新" in _recv_type(ws, "error")["message"], old


class TestEachGameShipsItsOwnSettings:
    """五个游戏各自下发什么（含深空休眠的地图 —— 它在这之前没有任何服务端单测）。"""

    def test_minesweeper_ships_three_difficulties(self, client):
        t1, _ = _register(client, _u("mine"))
        room = _create_room(client, t1, "minesweeper")
        item = _setting(room, "difficulty")
        assert [c["value"] for c in item["choices"]] == \
            ["beginner", "intermediate", "advanced"]
        # 说明由几何推导，与引擎同一份数据
        assert item["choices"][1]["blurb"] == "16×16 / 40雷 · 每人 2 条命"

    def test_stasis_ships_the_maps_and_resolves_the_default(self, client):
        t1, _ = _register(client, _u("stasis"))
        room = _create_room(client, t1, "stasis")
        item = _setting(room, "map")
        assert [c["value"] for c in item["choices"]] == ["dorm", "suite", "atrium"]
        # `create_room` 写的是 `stasis_map_id = None`，**默认图由注册表兜底**
        # （旧版是让客户端读 `stasis_maps[0].id`，口径散在两处）
        assert item["value"] == "dorm"
        assert item["value_label"] == "标准货舱区"

    def test_battleship_value_label_carries_the_size(self, client):
        t1, _ = _register(client, _u("bs"))
        room = _create_room(client, t1, "battleship")
        assert _setting(room, "board_size")["value_label"] == "标准 · 10×10"

    def test_uno_ships_the_preset_plus_eight_rules(self, client):
        t1, _ = _register(client, _u("uno"))
        room = _create_room(client, t1, "uno")
        assert len(room["settings"]) == 9
        assert _setting(room, "preset")["value_label"] == "经典"
        assert _setting(room, "deck")["value"] == "classic"


class TestRegistryGuards:
    """纯函数层面的护栏（不起房间，直接问注册表）。"""

    def test_adding_a_wild_rule_needs_no_client_change(self):
        """UNO 的 7 条是**遍历 `RULE_META` 生成**的 —— 引擎加一条房规，客户端零改动。"""
        from app.game import uno_rules
        keys = [s.key for s in rs.specs_for("uno")]
        assert keys[1:] == [m["key"] for m in uno_rules.RULE_META]

    def test_unknown_game_has_no_specs(self):
        assert rs.specs_for("gomoku") == []
        assert rs.spec_for("gomoku", "anything") is None

    def test_boolean_cannot_impersonate_a_number(self):
        """`True == 1` 在 Python 里成立 —— 开关不能冒充数字档位。"""
        spec = rs.spec_for("battleship", "board_size")
        with pytest.raises(ValueError):
            rs.coerce(spec, True)

    def test_toggle_rejects_non_boolean(self):
        spec = rs.spec_for("luzhanqi", "reveal")
        assert rs.coerce(spec, True) is True
        with pytest.raises(ValueError):
            rs.coerce(spec, "yes")

    def test_reading_a_room_without_the_key_falls_back_to_the_default(self):
        """房间字典里没有这个键（或值是 `None`）时退回默认值，不能显示成空的。"""
        spec = rs.spec_for("stasis", "map")
        assert rs.value_of(spec, {}) == "dorm"
        assert rs.value_of(spec, {"stasis_map_id": None}) == "dorm"
