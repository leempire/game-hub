"""军棋 WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房（2 人）/ 选明棋暗棋、开局进布阵阶段、布阵交换与确认、开打、走子广播、
碰子战报按收件人裁剪、**暗棋下对手在 WS 层就收不到我的棋子身份**、认输、断线重连
（sync 仍停在布阵阶段）、结束后续开、人机应招、战绩快照。

坐标约定：**客户端发 x=列 / y=行**，引擎内部是 (行, 列)——`_handle_luzhanqi_move`
负责转置。

Run: pytest test/test_ws_luzhanqi.py -v
"""

import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.game import luzhanqi as L
from app import ws as ws_mod


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
    return f"lz_{name}_{_TS}"


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="luzhanqi"):
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=12):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setting(room, key):
    """从房间载荷的统一 `settings` 里取一项的值（v1.15 起设置只有这一份结构）。"""
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item["value"]
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


def _setup_setup_phase(client, mode="open", reveal=True):
    """建房 → 加入 → 双 WS → （可选）选玩法 → ready → start，停在**布阵阶段**。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"lz_h{ts}a")
    t2, _ = _register(client, f"lz_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws2 = client.websocket_connect(f"/ws/room/{code}?token={t2}")
    ws1.__enter__()
    ws2.__enter__()
    _recv_type(ws1, "sync")
    _recv_type(ws2, "sync")
    # 两项各自独立（v1.15 起一条 `room_setting` 只改一项），只发改动过的那些
    if mode != "open":
        ws1.send_json({"type": "room_setting", "key": "mode", "value": mode})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
    if not reveal:
        ws1.send_json({"type": "room_setting", "key": "reveal", "value": reveal})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
    ws2.send_json({"type": "ready"})
    _recv_type(ws1, "lobby")
    _recv_type(ws2, "lobby")
    ws1.send_json({"type": "start"})
    # 进对局屏那一次必须是 `game_start` —— 客户端靠它导航到对局页
    s1 = _recv_type(ws1, "game_start")
    _recv_type(ws1, "luzhanqi_ready")          # 开局时那份「双方都没确认」
    s2 = _recv_type(ws2, "game_start")
    _recv_type(ws2, "luzhanqi_ready")
    return ws1, ws2, code, t1, t2, s1, s2


def _confirm_both(ws1, ws2):
    ws1.send_json({"type": "luzhanqi_setup", "action": "confirm"})
    _recv_type(ws1, "luzhanqi_ready")
    _recv_type(ws2, "luzhanqi_ready")
    ws2.send_json({"type": "luzhanqi_setup", "action": "confirm"})
    b1 = _recv_type(ws1, "luzhanqi_begin")
    b2 = _recv_type(ws2, "luzhanqi_begin")
    return b1, b2


def _play(client, mode="open", reveal=True):
    ws1, ws2, code, t1, t2, _s1, _s2 = _setup_setup_phase(client, mode, reveal)
    b1, b2 = _confirm_both(ws1, ws2)
    return ws1, ws2, code, t1, t2, b1, b2


def _close(*wss):
    for ws in wss:
        ws.__exit__(None, None, None)


def _move_msg(frm, to):
    """(行,列) → 客户端坐标（x=列, y=行）。"""
    return {"type": "luzhanqi_move",
            "from_x": frm[1], "from_y": frm[0], "to_x": to[1], "to_y": to[0]}


def _blank_board(gs):
    for r in range(L.ROWS):
        for c in range(L.COLS):
            gs["board"][r][c] = None


class TestRoomAndSetup:
    def test_create_room_is_two_players_with_open_mode(self, client):
        t1, _ = _register(client, _u("create"))
        room = _create_room(client, t1)
        assert room["max_players"] == 2
        assert _setting(room, "mode") == "open"
        assert _setting(room, "reveal") is True

    def test_host_can_pick_hidden_mode(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, _u("pick"))
        room = _create_room(client, t1)
        ws = client.websocket_connect(f"/ws/room/{room['code']}?token={t1}")
        ws.__enter__()
        _recv_type(ws, "sync")
        ws.send_json({"type": "room_setting", "key": "mode", "value": "hidden"})
        assert _setting(_recv_type(ws, "lobby")["room"], "mode") == "hidden"
        ws.send_json({"type": "room_setting", "key": "reveal", "value": False})
        assert _setting(_recv_type(ws, "lobby")["room"], "reveal") is False
        _close(ws)

    def test_start_enters_setup_phase_with_a_full_layout_each(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        gs = rm.get_room(code)["game_state"]
        assert gs["phase"] == "setup"
        assert gs["ready"] == {L.RED: False, L.BLACK: False}
        assert L.validate_layout(L.RED, L.layout_of(gs, L.RED)) is None
        assert L.validate_layout(L.BLACK, L.layout_of(gs, L.BLACK)) is None
        _close(ws1, ws2)

    def test_setup_view_hides_the_opponent_layout(self, client):
        """明棋也是「各自摆好、摆完同时亮出」—— 布阵阶段看不到对手摆了啥。"""
        ws1, ws2, code, t1, t2, s1, s2 = _setup_setup_phase(client)
        for view in (s1["game"], s2["game"]):
            mine = view["your_color"]
            other = L.BLACK if mine == L.RED else L.RED
            for r in range(L.ROWS):
                for c in range(L.COLS):
                    cell = view["cells"][r][c]
                    if cell and cell["c"] == other:
                        assert cell["p"] is None
        _close(ws1, ws2)

    def test_swap_is_validated_on_the_server(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        gs = rm.get_room(code)["game_state"]
        red_cells = [c for c in L.own_cells(L.RED) if gs["board"][c[0]][c[1]]]
        flag = next(c for c in red_cells if gs["board"][c[0]][c[1]]["p"] == L.JUNQI)
        target = next(c for c in red_cells if c not in L.HQ_CELLS)
        ws1.send_json({"type": "luzhanqi_setup", "action": "swap",
                       "a": list(flag), "b": list(target)})
        err = _recv_type(ws1, "error")
        assert err["message"] == "军旗必须放在大本营"
        shi = [c for c in red_cells if gs["board"][c[0]][c[1]]["p"] == L.SHIZHANG]
        ws1.send_json({"type": "luzhanqi_setup", "action": "swap",
                       "a": list(shi[0]), "b": list(shi[1])})
        fresh = _recv_type(ws1, "luzhanqi_setup")
        assert fresh["game"]["cells"][shi[0][0]][shi[0][1]]["p"] == L.SHIZHANG
        _close(ws1, ws2)

    def test_random_rerolls_into_a_valid_layout(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        ws1.send_json({"type": "luzhanqi_setup", "action": "random"})
        fresh = _recv_type(ws1, "luzhanqi_setup")
        assert fresh["game"]["phase"] == "setup"
        assert L.validate_layout(L.RED, L.layout_of(rm.get_room(code)["game_state"], L.RED)) is None
        _close(ws1, ws2)

    def test_preset_puts_the_published_formation_on_the_board(self, client):
        """点一下经典阵型 → 自己半场整套换掉，而且**只换自己的**（对手那半边不动）。"""
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        gs = rm.get_room(code)["game_state"]
        before_black = L.layout_of(gs, L.BLACK)
        ws1.send_json({"type": "luzhanqi_setup", "action": "preset", "name": "shuangxi"})
        fresh = _recv_type(ws1, "luzhanqi_setup")
        assert fresh["game"]["phase"] == "setup"
        after = L.layout_of(gs, L.RED)
        assert after == L.preset_layout(L.RED, "shuangxi")
        assert L.layout_of(gs, L.BLACK) == before_black
        # 阵型名单随视图下发（客户端不抄一份）
        assert [p["key"] for p in fresh["game"]["presets"]] == [k for k, _ in L.PRESETS]
        _close(ws1, ws2)

    def test_unknown_preset_reports_a_readable_error(self, client):
        """不认识的名字不能静默 —— 本项目铁律：点了必须有回应。"""
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        ws1.send_json({"type": "luzhanqi_setup", "action": "preset", "name": "没这套"})
        err = _recv_type(ws1, "error")
        assert err["message"] == "没有这套阵型"
        _close(ws1, ws2)

    def test_preset_is_rejected_after_confirming(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        ws1.send_json({"type": "luzhanqi_setup", "action": "confirm"})
        _recv_type(ws1, "luzhanqi_ready")
        ws1.send_json({"type": "luzhanqi_setup", "action": "preset", "name": "wuye"})
        err = _recv_type(ws1, "error")
        assert err["message"] == "已经确认布阵，不能再改"
        _close(ws1, ws2)

    def test_play_starts_only_after_both_confirm(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        ws1.send_json({"type": "luzhanqi_setup", "action": "confirm"})
        ready = _recv_type(ws1, "luzhanqi_ready")
        assert ready["ready"][L.RED] is True and ready["ready"][L.BLACK] is False
        assert ready["phase"] == "setup"
        ws2.send_json({"type": "luzhanqi_setup", "action": "confirm"})
        begin = _recv_type(ws1, "luzhanqi_begin")
        assert begin["game"]["phase"] == "playing"
        assert begin["game"]["legal"], "开局该有合法着法"
        _close(ws1, ws2)

    def test_cannot_change_after_confirming(self, client):
        ws1, ws2, code, *_ = _setup_setup_phase(client)
        ws1.send_json({"type": "luzhanqi_setup", "action": "confirm"})
        _recv_type(ws1, "luzhanqi_ready")
        ws1.send_json({"type": "luzhanqi_setup", "action": "random"})
        err = _recv_type(ws1, "error")
        assert err["message"] == "已经确认布阵，不能再改"
        _close(ws1, ws2)


class TestPlay:
    def test_first_move_is_broadcast_to_both(self, client):
        ws1, ws2, code, t1, t2, b1, b2 = _play(client)
        flat = b1["game"]["legal"][0]
        ws1.send_json(_move_msg((flat[0], flat[1]), (flat[2], flat[3])))
        m1 = _recv_type(ws1, "move")
        m2 = _recv_type(ws2, "move")
        assert m1["from"] == [flat[0], flat[1]] and m1["to"] == [flat[2], flat[3]]
        assert m1["next"] == L.BLACK and m2["next"] == L.BLACK
        assert m2["legal"] and m1["legal"] == []      # 合法着法只算收件人自己的
        _close(ws1, ws2)

    def test_illegal_move_reports_an_error(self, client):
        ws1, ws2, code, *_ = _play(client)
        ws1.send_json({"type": "luzhanqi_move", "from_x": 0, "from_y": 0,
                       "to_x": 0, "to_y": 0})
        err = _recv_type(ws1, "error")
        assert err["message"]
        _close(ws1, ws2)

    def test_missing_fields_are_silently_dropped(self, client):
        ws1, ws2, code, t1, t2, b1, b2 = _play(client)
        flat = b1["game"]["legal"][0]
        ws1.send_json({"type": "luzhanqi_move", "from_x": 0})            # 缺字段
        ws1.send_json(_move_msg((flat[0], flat[1]), (flat[2], flat[3])))  # 正常一步
        nxt = ws1.receive_json()
        assert nxt["type"] == "move", f"缺字段的那条应当被静默丢弃，实际收到 {nxt}"
        _close(ws1, ws2)

    def test_battle_is_masked_per_recipient(self, client):
        """暗棋·不公开身份：双方都只看到「谁出局」，看不到对手那枚是什么。"""
        ws1, ws2, code, *_ = _play(client, mode="hidden", reveal=False)
        gs = rm.get_room(code)["game_state"]
        _blank_board(gs)
        gs["board"][6][0] = {"c": L.RED, "p": L.SHIZHANG}
        gs["board"][6][1] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["board"][11][1] = {"c": L.RED, "p": L.JUNQI}
        gs["board"][0][1] = {"c": L.BLACK, "p": L.JUNQI}
        gs["next"] = L.RED
        ws1.send_json(_move_msg((6, 0), (6, 1)))
        m1 = _recv_type(ws1, "move")
        m2 = _recv_type(ws2, "move")
        assert m1["battle"]["outcome"] == "defender_dies"
        assert m1["battle"]["attacker_piece"] == L.SHIZHANG   # 自己的子自己知道
        assert "defender_piece" not in m1["battle"]
        assert m2["battle"]["defender_piece"] == L.PAIZHANG
        assert "attacker_piece" not in m2["battle"]
        _close(ws1, ws2)

    def test_open_mode_reveals_both_sides_of_a_battle(self, client):
        ws1, ws2, code, *_ = _play(client, mode="open")
        gs = rm.get_room(code)["game_state"]
        _blank_board(gs)
        gs["board"][6][0] = {"c": L.RED, "p": L.SHIZHANG}
        gs["board"][6][1] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["board"][11][1] = {"c": L.RED, "p": L.JUNQI}
        gs["board"][0][1] = {"c": L.BLACK, "p": L.JUNQI}
        gs["next"] = L.RED
        ws1.send_json(_move_msg((6, 0), (6, 1)))
        m2 = _recv_type(ws2, "move")
        assert m2["battle"]["attacker_piece"] == L.SHIZHANG
        assert m2["battle"]["defender_piece"] == L.PAIZHANG
        _close(ws1, ws2)

    def test_opponent_never_sees_my_pieces_over_the_wire(self, client):
        """暗棋的核心不变量：**线上根本没有对手棋子的身份**（不是渲染时藏）。"""
        ws1, ws2, code, t1, t2, b1, b2 = _play(client, mode="hidden")
        for view in (b1["game"], b2["game"]):
            mine = view["your_color"]
            for r in range(L.ROWS):
                for c in range(L.COLS):
                    cell = view["cells"][r][c]
                    if cell and cell["c"] != mine:
                        assert cell["p"] is None, "对手的棋子身份泄露到线上了"
        flat = b1["game"]["legal"][0]
        ws1.send_json(_move_msg((flat[0], flat[1]), (flat[2], flat[3])))
        m2 = _recv_type(ws2, "move")
        assert m2["battle"] is None
        _close(ws1, ws2)

    def test_flag_capture_broadcasts_game_over(self, client):
        ws1, ws2, code, *_ = _play(client)
        gs = rm.get_room(code)["game_state"]
        _blank_board(gs)
        gs["board"][11][1] = {"c": L.BLACK, "p": L.JUNQI}
        gs["board"][10][1] = {"c": L.RED, "p": L.PAIZHANG}
        gs["board"][11][3] = {"c": L.RED, "p": L.JUNQI}
        gs["board"][6][0] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["next"] = L.RED
        ws1.send_json(_move_msg((10, 1), (11, 1)))
        _recv_type(ws1, "move")
        over = _recv_type(ws1, "game_over")
        assert over["winner"] == L.RED and over["reason"] == "flag"
        # 终局必须把房间放回 waiting（否则点「返回房间」会被弹回对局页）
        assert rm.get_room(code)["status"] == "waiting"
        _close(ws1, ws2)

    def test_surrender_flips_the_winner_correctly(self, client):
        ws1, ws2, code, *_ = _play(client)
        ws1.send_json({"type": "surrender"})
        over = _recv_type(ws2, "game_over")
        assert over["winner"] == L.BLACK and over["reason"] == "surrender"
        assert rm.get_room(code)["status"] == "waiting"
        _close(ws1, ws2)

    def test_start_new_round_from_lobby(self, client):
        ws1, ws2, code, t1, t2, *_ = _play(client)
        ws1.send_json({"type": "surrender"})
        _recv_type(ws2, "game_over")
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        s1 = _recv_type(ws1, "game_start")
        assert s1["game"]["phase"] == "setup"
        _close(ws1, ws2)


class TestReconnect:
    def test_sync_during_setup_stays_in_setup(self, client):
        """布阵阶段掉线重连 → sync 必须还带 `phase == "setup"`，
        否则客户端会直接跳到对局界面（而对面还没确认）。"""
        ws1, ws2, code, t1, t2, _s1, _s2 = _setup_setup_phase(client)
        _close(ws1)
        ws1b = client.websocket_connect(f"/ws/room/{code}?token={t1}")
        ws1b.__enter__()
        sync = _recv_type(ws1b, "sync")
        assert sync["game"]["phase"] == "setup"
        assert sync["in_game"] is True
        _close(ws1b, ws2)

    def test_sync_midgame_carries_the_masked_view(self, client):
        ws1, ws2, code, t1, t2, _b1, _b2 = _play(client, mode="hidden")
        _close(ws1)
        ws1b = client.websocket_connect(f"/ws/room/{code}?token={t1}")
        ws1b.__enter__()
        view = _recv_type(ws1b, "sync")["game"]
        assert view["phase"] == "playing" and view["mode"] == "hidden"
        for r in range(L.ROWS):
            for c in range(L.COLS):
                cell = view["cells"][r][c]
                if cell and cell["c"] != view["your_color"]:
                    assert cell["p"] is None
        _close(ws1b, ws2)


class TestBot:
    def test_bot_confirms_setup_and_takes_a_move(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, _u("bot"))
        room = _create_room(client, t1)
        code = room["code"]
        ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
        ws1.__enter__()
        _recv_type(ws1, "sync")
        ws1.send_json({"type": "add_bot"})
        _recv_type(ws1, "lobby")
        ws1.send_json({"type": "start"})
        _recv_type(ws1, "game_start")
        gs = rm.get_room(code)["game_state"]
        assert gs["ready"][L.BLACK] is True, "人机应当自动确认布阵"
        assert gs["ready"][L.RED] is False
        ws1.send_json({"type": "luzhanqi_setup", "action": "confirm"})
        begin = _recv_type(ws1, "luzhanqi_begin")
        assert begin["game"]["phase"] == "playing"
        # 真人先走一步（红先），回合才轮到人机
        flat = begin["game"]["legal"][0]
        ws1.send_json(_move_msg((flat[0], flat[1]), (flat[2], flat[3])))
        assert _recv_type(ws1, "move")["by"] == L.RED
        # TestClient 不跑后台任务 → 手动触发人机回合
        bot_id = next(p["id"] for p in rm.get_room(code)["players"] if p.get("is_bot"))
        asyncio.get_event_loop().run_until_complete(ws_mod._bot_do_turn(code, bot_id))
        msg = _recv_type(ws1, "move", max_skip=20)
        assert msg["by"] == L.BLACK
        _close(ws1)

    def test_bot_rejected_for_other_games_unchanged(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, _u("nb"))
        room = _create_room(client, t1, game="2048")
        with pytest.raises(ValueError):
            rm.add_bot(room["code"], 1)


class TestSnapshot:
    def test_snapshot_is_full_truth_and_json_friendly(self, client):
        ws1, ws2, code, *_ = _play(client, mode="hidden", reveal=False)
        ws1.send_json({"type": "surrender"})
        _recv_type(ws2, "game_over")
        from app.database import _build_snapshot
        snap = _build_snapshot(rm.get_room(code))
        assert snap["game"] == "luzhanqi"
        assert snap["mode"] == "hidden"
        assert sum(1 for row in snap["board"] for cell in row if cell) == 50
        json.dumps(snap)
        _close(ws1, ws2)
