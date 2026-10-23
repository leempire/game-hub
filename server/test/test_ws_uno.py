"""UNO WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房/加入、逐人下发开局（**手牌私有**）、出牌/摸牌/过牌广播、
非本回合与非法动作被拒、认输只出局不终局、只剩一人时终局结算、
断线自动过牌与「全员断线停手」护栏、断线重连 sync、战绩快照。

**手牌私有是核心不变量**：`uno_state` 里 `hand` 只能是本人那副牌，别人只有张数。
所以每个可能出现手牌的消息都断言一次「别人的牌没有漏出来」。

场景构造直接改房间里的 `gs`（绕过洗牌）——与 test_ws_tetris.py 造「差一列就满」
同一个思路：靠随机发牌去凑「手里只剩一张能出的牌」是撞运气，会 flaky。

Run: pytest test/test_ws_uno.py -v
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.database as db_mod
from app import room_manager as rm
from app.game import uno
from app.database import get_conn, save_game_record


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


@pytest.fixture
def temp_db(tmp_path):
    """把全局 DB 指向临时库，跑完**恢复原路径**。

    `database.init_db()` 改的是模块级 `_db_path` 全局量。不恢复的话，后面所有
    用到 DB 的用例（比如 test_bot.py 的注册流程）会连到一个已被 tmp_path 清理掉的
    库上，报 `no such table: users`——**从仓库根跑全量时就是这样**：7 个毫不相干的
    用例莫名其妙全红，而单独跑它们又全绿（用例顺序不同，侥幸不触发）。
    """
    saved = db_mod._db_path
    db_mod.init_db(str(tmp_path / "test.db"))
    yield
    db_mod._db_path = saved


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="uno"):
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=25):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setting(room, key):
    """从房间载荷的统一 `settings` 里取一项（v1.15 起设置只有这一份结构）。"""
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


def _setting_value(room, key):
    return _setting(room, key)["value"]


def _lobby(client, players=2):
    """建房 → 加入 → 各 WS → ready。**不开局** —— 房规只能在 `waiting` 时改。"""
    ts = int(time.time() * 1000) % 100000
    tokens, uids = [], []
    for i in range(players):
        t, uid = _register(client, f"tu_{ts}_{i}")
        tokens.append(t)
        uids.append(uid)
    room = _create_room(client, tokens[0])
    code = room["code"]
    for t in tokens[1:]:
        _join_room(client, t, code)
    wss = [client.websocket_connect(f"/ws/room/{code}?token={t}") for t in tokens]
    for ws in wss:
        ws.__enter__()
    for ws in wss:
        _recv_type(ws, "sync")
    # 服务端在每个人 connect 时会 `exclude` 掉自己、给**已经在房里的人**广播一条
    # lobby（让所有人看到新的 connected 标记）。先连的人因此会多压几条 —— 这里按
    # 连接顺序精确清掉，否则后面每读一条 lobby 都会慢一拍（读到的还是上一条）。
    for i, ws in enumerate(wss):
        for _ in range(players - 1 - i):
            _recv_type(ws, "lobby")
    for ws in wss[1:]:
        ws.send_json({"type": "ready"})
    for ws in wss:
        _recv_type(ws, "lobby")
    return wss, code, tokens, uids


def _setup_game(client, players=2, settings=None):
    """建房 → 加入 → 各 WS → ready → （可选：房主选房规）→ start。

    返回 (wss, code, tokens, uids, starts)。`settings` 是 `{key: value}`，逐条发
    `room_setting`（如 `{"preset": "party"}` 或 `{"jump_in": True}`），**必须在
    start 之前发** —— 房规是 `create_game` 时定下并落进 `gs` 的。
    """
    wss, code, tokens, uids = _lobby(client, players)
    for key, value in (settings or {}).items():
        wss[0].send_json({"type": "room_setting", "key": key, "value": value})
        # **全房都要收到**：房规是房间级设置，只回给房主的话别人看到的还是旧档
        for ws in wss:
            _recv_type(ws, "lobby")
    wss[0].send_json({"type": "start"})
    starts = [_recv_type(ws, "game_start") for ws in wss]
    return wss, code, tokens, uids, starts


def _close(wss):
    for ws in wss:
        ws.__exit__(None, None, None)


def _force(room, turn=0, discard="R5", color=None):
    """把对局改成确定局面：指定回合与当前牌。"""
    gs = room["game_state"]
    gs["discard_pile"] = [discard]
    gs["current_color"] = color or discard[0]
    gs["turn"] = turn
    gs["drawn_this_turn"] = None
    gs["pending_draw"] = 0
    gs["pending_kind"] = None
    return gs


class TestStart:
    def test_room_creation(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"tu_r{ts}")
        room = _create_room(client, t1)
        assert room["game"] == "uno"
        assert room["max_players"] == 4, "UNO 支持 2-4 人"
        assert room["status"] == "waiting"

    def test_start_requires_two_players(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"tu_solo{ts}")
        code = _create_room(client, t1)["code"]
        ws = client.websocket_connect(f"/ws/room/{code}?token={t1}")
        ws.__enter__()
        try:
            _recv_type(ws, "sync")
            ws.send_json({"type": "start"})
            err = _recv_type(ws, "error")
            assert "开始" in err["message"]
            assert rm.get_room(code)["status"] == "waiting"
        finally:
            ws.__exit__(None, None, None)

    def test_game_start_is_per_player(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            g1, g2 = starts[0]["game"], starts[1]["game"]
            assert g1["game"] == "uno"
            # 每人开局 7 张，而且**各自拿到的是自己那副**
            assert len(g1["hand"]) == 7
            assert len(g2["hand"]) == 7
            assert g1["hand"] is not g2["hand"]
            # 关键：我收到的消息里不能出现对手的手牌
            assert "hands" not in g1
            assert g1["hand_counts"] == {"0": 7, "1": 7}
            assert g1["your_seat"] == 0 and g2["your_seat"] == 1
        finally:
            _close(wss)

    def test_only_first_player_can_act_at_start(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            assert starts[0]["game"]["turn"] == 0
            # 先手有可出牌或至少能摸牌；后手此刻什么都不能做
            assert starts[0]["game"]["can_draw"] is True
            assert starts[1]["game"]["can_draw"] is False
            assert starts[1]["game"]["playable"] == []
        finally:
            _close(wss)

    def test_sync_carries_uno_state(self, client):
        """断线重连：sync 要把本人的手牌与整桌状态带回来。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        _close(wss)
        ws = client.websocket_connect(f"/ws/room/{code}?token={tokens[0]}")
        ws.__enter__()
        try:
            sync = _recv_type(ws, "sync")
            assert sync["game"]["game"] == "uno"
            assert sync["in_game"] is True
            assert sync["game"]["your_seat"] == 0
            assert len(sync["game"]["hand"]) == 7
            assert "hands" not in sync["game"]
        finally:
            ws.__exit__(None, None, None)


class TestPlay:
    def test_play_broadcasts_event_and_state(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1", "G2"]
            gs["hands"][1] = ["B3", "Y4"]

            wss[0].send_json({"type": "uno_play", "card": "R1"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "play"
            assert ev["seat"] == 0 and ev["card"] == "R1"
            st = _recv_type(wss[0], "uno_state")
            assert st["hand"] == ["G2"]
            assert st["hand_counts"] == {"0": 1, "1": 2}
            assert st["discard_top"] == "R1"
            assert st["turn"] == 1
        finally:
            _close(wss)

    def test_opponent_never_sees_my_hand(self, client):
        """**核心不变量**：对手收到的每一条消息里都不能有我的手牌。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1", "G2"]
            gs["hands"][1] = ["B3", "Y4"]

            wss[0].send_json({"type": "uno_play", "card": "R1"})
            ev = _recv_type(wss[1], "uno_event")
            assert ev["kind"] == "play" and "hand" not in ev
            st = _recv_type(wss[1], "uno_state")
            assert st["hand"] == ["B3", "Y4"]
            assert set(st["hand"]) & {"R1", "G2"} == set()
            assert "hands" not in st
            assert st["playable"] == []          # 不是我的回合
        finally:
            _close(wss)

    def test_out_of_turn_action_is_rejected(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][1] = ["R1"]
            wss[1].send_json({"type": "uno_play", "card": "R1"})
            err = _recv_type(wss[1], "error")
            assert "轮到你" in err["message"]
            # 只有发起者收到 error，且盘面没动
            assert rm.get_room(code)["game_state"]["discard_pile"][-1] == "R5"
        finally:
            _close(wss)

    def test_unplayable_card_is_rejected(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["G3", "B4"]
            wss[0].send_json({"type": "uno_play", "card": "G3"})
            err = _recv_type(wss[0], "error")
            assert "不能出" in err["message"]
            assert rm.get_room(code)["game_state"]["hands"][0] == ["G3", "B4"]
        finally:
            _close(wss)

    def test_wild_needs_a_color(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["W", "G2"]
            wss[0].send_json({"type": "uno_play", "card": "W"})
            err = _recv_type(wss[0], "error")
            assert "颜色" in err["message"]
            wss[0].send_json({"type": "uno_play", "card": "W", "color": "G"})
            st = _recv_type(wss[0], "uno_state")
            assert st["current_color"] == "G"
        finally:
            _close(wss)

    def test_playing_last_card_ends_the_game(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1"]
            gs["hands"][1] = ["B3", "Y4"]
            wss[0].send_json({"type": "uno_play", "card": "R1"})
            go = _recv_type(wss[0], "game_over")
            assert go["game"] == "uno"
            assert go["reason"] == "emptied"
            assert go["winner"] == 0
            assert len(go["ranking"]) == 2
            assert go["ranking"][0]["seat"] == 0
            assert rm.get_room(code)["status"] == "waiting", "终局后房间回等待态"
        finally:
            _close(wss)


class TestDraw:
    def test_draw_passes_turn_when_unplayable(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["G1"]
            gs["draw_pile"] = ["B3", "G9"]      # pop 取顶 → 先摸到 G9
            wss[0].send_json({"type": "uno_draw"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "draw" and ev["drawn"] == 1
            st = _recv_type(wss[0], "uno_state")
            assert st["turn"] == 1
            assert st["hand"] == ["G1", "G9"]
            assert st["can_pass"] is False, "回合已经交出去，没有可放弃的出牌"
        finally:
            _close(wss)

    def test_draw_keeps_turn_when_playable(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["G1"]
            gs["draw_pile"] = ["B3", "R9"]      # R9 可出
            wss[0].send_json({"type": "uno_draw"})
            st = _recv_type(wss[0], "uno_state")
            assert st["turn"] == 0, "摸到能出的牌，回合还在我手里"
            assert st["can_pass"] is True
            assert st["playable"] == ["R9"], "摸过牌之后只能出刚摸的那张"
        finally:
            _close(wss)

    def test_pass_after_drawing(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["G1"]
            gs["draw_pile"] = ["B3", "R9"]
            wss[0].send_json({"type": "uno_draw"})
            _recv_type(wss[0], "uno_state")
            wss[0].send_json({"type": "uno_pass"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "pass"
            st = _recv_type(wss[0], "uno_state")
            assert st["turn"] == 1
        finally:
            _close(wss)

    def test_penalty_is_drawn_all_at_once(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="Y5")
            gs["hands"][0] = ["G1"]
            gs["draw_pile"] = ["G9", "B3", "Y4", "R8", "G7"]
            gs["pending_draw"] = 4
            gs["pending_kind"] = "+4"
            wss[0].send_json({"type": "uno_draw"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "draw" and ev["penalty"] == 4
            st = _recv_type(wss[0], "uno_state")
            assert len(st["hand"]) == 5
            assert st["pending_draw"] == 0
            assert st["turn"] == 1
        finally:
            _close(wss)


class TestStacking:
    def test_stacking_passes_penalty_on(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="Y5")
            gs["hands"][0] = ["R+2", "G1"]
            gs["pending_draw"] = 2
            gs["pending_kind"] = "+2"
            wss[0].send_json({"type": "uno_play", "card": "R+2"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["effect"] == "draw2"
            st = _recv_type(wss[1], "uno_state")
            assert st["pending_draw"] == 4, "被 +2 之后又有人出 +2 → 累计 4 张"
            assert st["turn"] == 1
        finally:
            _close(wss)

    def test_wrong_penalty_kind_is_rejected(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="Y5")
            gs["hands"][0] = ["W+4", "R+2"]
            gs["pending_draw"] = 2
            gs["pending_kind"] = "+2"
            wss[0].send_json({"type": "uno_play", "card": "W+4", "color": "R"})
            err = _recv_type(wss[0], "error")
            assert "不能出" in err["message"]
        finally:
            _close(wss)


class TestUnoCall:
    def test_call_broadcasts(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1"]
            wss[0].send_json({"type": "uno_call"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "uno" and ev["seat"] == 0
            st = _recv_type(wss[1], "uno_state")
            assert st["uno_called"] == [0], "喊 UNO 是公开信息"
        finally:
            _close(wss)

    def test_cannot_call_with_more_than_one_card(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1", "G2"]
            wss[0].send_json({"type": "uno_call"})
            err = _recv_type(wss[0], "error")
            assert "UNO" in err["message"]
        finally:
            _close(wss)


class TestSurrender:
    def test_surrender_keeps_game_running_with_three(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=3)
        try:
            room = rm.get_room(code)
            _force(room, turn=0, discard="Y5")
            wss[1].send_json({"type": "surrender"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "surrender" and ev["seat"] == 1
            st = _recv_type(wss[0], "uno_state")
            assert st["out"] == [1]
            assert st["finished"] is False
            assert rm.get_room(code)["status"] == "playing", "还有人没打完，局继续"
            # 认输者仍在房间名单里（要算排名、座位号是下标）
            assert len(rm.get_room(code)["players"]) == 3
        finally:
            _close(wss)

    def test_last_remaining_wins(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=3)
        try:
            room = rm.get_room(code)
            _force(room, turn=0, discard="Y5")
            wss[0].send_json({"type": "surrender"})
            wss[1].send_json({"type": "surrender"})
            go = _recv_type(wss[2], "game_over")
            assert go["reason"] == "last_remaining"
            assert go["winner"] == 2
            assert len(go["ranking"]) == 3
            assert rm.get_room(code)["status"] == "waiting"
        finally:
            _close(wss)

    def test_two_player_surrender_ends_game(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            _force(rm.get_room(code), turn=0, discard="Y5")
            wss[0].send_json({"type": "surrender"})
            go = _recv_type(wss[1], "game_over")
            assert go["winner"] == 1
            assert go["reason"] == "last_remaining"
        finally:
            _close(wss)

    def test_surrendered_player_is_not_in_game(self, client):
        """出局的人不该被房间页判定为「仍在局中」（否则返回房间会被弹回对局页）。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=3)
        try:
            _force(rm.get_room(code), turn=0, discard="Y5")
            wss[1].send_json({"type": "surrender"})
            _recv_type(wss[0], "uno_state")
            room = rm.get_room(code)
            assert rm.user_in_game(room, uids[1]) is False
            assert rm.user_in_game(room, uids[0]) is True
        finally:
            _close(wss)


class TestDisconnectAutopass:
    def test_turn_of_disconnected_player_is_autopassed(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            room["players"][1]["connected"] = False
            gs = _force(room, turn=0, discard="R5")
            gs["hands"][0] = ["R1", "G2"]
            gs["hands"][1] = ["B3", "Y4"]

            wss[0].send_json({"type": "uno_play", "card": "R1"})
            assert _recv_type(wss[0], "uno_event")["kind"] == "play"
            # 轮到断线的 seat1 → 服务端替他摸牌过牌，而不是卡到 120s
            auto = _recv_type(wss[0], "uno_event")
            assert auto["kind"] == "autopass"
            assert auto["seat"] == 1
            assert auto["drawn"] == 1
            st = _recv_type(wss[0], "uno_state")
            assert st["turn"] == 0, "过完牌回合转回在线的人"
        finally:
            _close(wss)

    def test_should_autopass_true_when_others_are_online(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            room["players"][0]["connected"] = False
            room["players"][1]["connected"] = True
            room["game_state"]["turn"] = 0
            assert rm.uno_should_autopass(room) is True
        finally:
            _close(wss)

    def test_should_not_autopass_when_everyone_is_offline(self, client):
        """**护栏**：全员断线时必须停手，否则回合会在断线座位间无限自转。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=3)
        try:
            room = rm.get_room(code)
            for p in room["players"]:
                p["connected"] = False
            room["game_state"]["turn"] = 0
            assert rm.uno_should_autopass(room) is False
        finally:
            _close(wss)

    def test_bot_counts_as_still_playing(self, client):
        """场上还有人机就该继续推进 —— 「全员断线」不该把人机也算进去。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            room["players"][0]["connected"] = False
            room["players"][1]["connected"] = False
            room["players"][1]["is_bot"] = True
            room["game_state"]["turn"] = 0
            assert rm.uno_should_autopass(room) is True
        finally:
            _close(wss)

    def test_connected_turn_player_is_not_autopassed(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            room["game_state"]["turn"] = 0
            assert rm.uno_should_autopass(room) is False
        finally:
            _close(wss)


class TestHistory:
    def test_record_and_snapshot(self, client, temp_db):
        rm.set_game_end_callback(save_game_record)
        try:
            wss, code, tokens, uids, starts = _setup_game(client, players=2)
            try:
                room = rm.get_room(code)
                gs = _force(room, turn=0, discard="R5")
                gs["hands"][0] = ["R1"]
                gs["hands"][1] = ["RS", "R9"]
                wss[0].send_json({"type": "uno_play", "card": "R1"})
                _recv_type(wss[0], "game_over")
            finally:
                _close(wss)
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT results_json, snapshot_json FROM game_records "
                    "WHERE game = 'uno' AND room_code = ?", (code,),
                ).fetchall()
                assert len(rows) == 1, "UNO 战绩没落库"
                results = {r["result"] for r in json.loads(rows[0]["results_json"])}
                assert results == {"win", "lose"}, results
                snap = json.loads(rows[0]["snapshot_json"])
            finally:
                conn.close()
            assert snap["game"] == "uno"
            assert snap["hands"]["1"] == ["RS", "R9"]
            assert snap["points"]["1"] == 29          # 20 + 9
            assert snap["ranking"][0]["seat"] == 0
            assert snap["ranking"][0]["rank"] == 1
            assert snap["ranking"][1]["points"] == 29
        finally:
            rm.set_game_end_callback(None)

    def test_player_colors_are_recorded(self, client, temp_db):
        """player_colors 同时被 ws 的通用循环当作 uid 集合用，必须非空。"""
        rm.set_game_end_callback(save_game_record)
        try:
            wss, code, tokens, uids, starts = _setup_game(client, players=2)
            try:
                room = rm.get_room(code)
                assert set(room["game_state"]["player_colors"]) == set(uids)
                _force(room, turn=0, discard="Y5")
                wss[0].send_json({"type": "surrender"})
                _recv_type(wss[1], "game_over")
            finally:
                _close(wss)
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT players_json FROM game_records WHERE game = 'uno'"
                ).fetchone()
            finally:
                conn.close()
            players = json.loads(row["players_json"])
            assert all(p.get("color") for p in players), players
        finally:
            rm.set_game_end_callback(None)

# ── 房规（v1.14）：大厅选档位 / 抢出 / 7 换手 ──────────────────


class TestRulesPick:
    """房主在大厅选玩法 —— 与扫雷难度 / 军棋玩法 / 地图 / 棋盘尺寸同一套。"""

    def test_lobby_exposes_presets_and_rule_meta(self, client):
        """档位名与每条房规的中文名都由**服务端**下发，客户端不抄一份。"""
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"tu_pk{ts}")
        room = _create_room(client, t1)
        preset = _setting(room, "preset")
        assert [p["value"] for p in preset["choices"]] == [
            "classic", "party", "team", "no_mercy"]
        assert preset["value"] == "classic"
        assert preset["value_label"] == "经典"
        # 8 条房规各是一个独立项，顺序就是 `RULE_META` 的顺序
        assert [s["key"] for s in room["settings"]] == [
            "preset", "stack_mode", "draw_until_play", "must_play",
            "jump_in", "seven_zero", "deck", "mercy", "teams"]
        assert _setting_value(room, "jump_in") is False

    def test_preset_pick_is_broadcast_to_everyone(self, client):
        wss, code, tokens, uids = _lobby(client, players=2)
        try:
            wss[0].send_json({"type": "room_setting", "key": "preset", "value": "party"})
            lobbies = [_recv_type(ws, "lobby") for ws in wss]
            for lob in lobbies:
                assert _setting(lob["room"], "preset")["value"] == "party"
                assert _setting_value(lob["room"], "jump_in") is True
                assert _setting_value(lob["room"], "seven_zero") == "both"
        finally:
            _close(wss)

    def test_single_rule_pick_merges_into_the_current_set(self, client):
        """逐条改**只合并给出的键** —— 界面每点一个开关只发一个键。"""
        wss, code, tokens, uids = _lobby(client, players=2)
        try:
            wss[0].send_json({"type": "room_setting", "key": "jump_in", "value": True})
            lob = _recv_type(wss[0], "lobby")["room"]
            assert _setting_value(lob, "jump_in") is True
            assert _setting_value(lob, "stack_mode") == "same"   # 没给的保持原样
            # 不匹配任何预设 → 档位落回 `custom`，但它**不是**一个可点的档位
            assert _setting(lob, "preset")["value"] == "custom"
            assert _setting(lob, "preset")["value_label"] == "自定义"
            assert "custom" not in [c["value"] for c in _setting(lob, "preset")["choices"]]
        finally:
            _close(wss)

    def test_restoring_the_defaults_highlights_classic_again(self, client):
        """档位是**反查**出来的，不是记下来的 —— 界面显示的就是真实生效的那份。"""
        wss, code, tokens, uids = _lobby(client, players=2)
        try:
            wss[0].send_json({"type": "room_setting", "key": "preset", "value": "party"})
            _recv_type(wss[0], "lobby")
            for key, value in (("jump_in", False), ("seven_zero", "off"),
                               ("draw_until_play", False), ("must_play", False),
                               ("stack_mode", "same")):
                wss[0].send_json({"type": "room_setting", "key": key, "value": value})
                lob = _recv_type(wss[0], "lobby")["room"]
            assert _setting(lob, "preset")["value"] == "classic"
        finally:
            _close(wss)

    def test_non_host_cannot_pick(self, client):
        wss, code, tokens, uids = _lobby(client, players=2)
        try:
            wss[1].send_json({"type": "room_setting", "key": "preset", "value": "party"})
            err = _recv_type(wss[1], "error")
            assert "房主" in err["message"]
            assert rm.get_room(code).get("uno_preset", "classic") == "classic"
        finally:
            _close(wss)

    def test_pick_is_refused_while_playing(self, client):
        """房规是 `create_game` 时定下并落进 `gs` 的，打到一半改等于把整局拆了。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            wss[0].send_json({"type": "room_setting", "key": "preset", "value": "party"})
            err = _recv_type(wss[0], "error")
            assert "已经开始" in err["message"]
            assert rm.get_room(code)["game_state"]["rules"]["jump_in"] is False
        finally:
            _close(wss)

    def test_bad_rule_value_gets_an_error_not_silence(self, client):
        """脏值一律**报错**，不静默忽略 —— 静默忽略意味着房主点了芯片、
        界面上却什么也没变（本项目反复踩过「被静默拒绝的动作就是没反应」）。"""
        wss, code, tokens, uids = _lobby(client, players=2)
        try:
            wss[0].send_json({"type": "room_setting", "key": "stack_mode", "value": "nope"})
            assert "叠牌" in _recv_type(wss[0], "error")["message"]
            wss[0].send_json({"type": "room_setting", "key": "nonsense", "value": True})
            assert "设置" in _recv_type(wss[0], "error")["message"]
            wss[0].send_json({"type": "room_setting", "key": "preset", "value": "nope"})
            assert "档位" in _recv_type(wss[0], "error")["message"]
        finally:
            _close(wss)

    def test_the_picked_rules_reach_the_game_state(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "party"})
        try:
            assert starts[0]["game"]["rules"]["jump_in"] is True
            assert rm.get_room(code)["game_state"]["rules"]["must_play"] is True
        finally:
            _close(wss)


class TestJumpInWire:
    """抢出是**唯一一条非本回合动作**，也是唯一会出现「抢输」的竞态。"""

    def _arm(self, room, turn=0, discard="R5", hands=None):
        gs = _force(room, turn=turn, discard=discard)
        gs["hands"] = hands or {0: ["G9", "G8"], 1: ["R5", "B3"]}
        return gs

    def test_jump_is_broadcast_with_the_jump_flag(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"jump_in": True})
        try:
            room = rm.get_room(code)
            self._arm(room, turn=0)
            wss[1].send_json({"type": "uno_play", "card": "R5", "jump": True})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "play" and ev["seat"] == 1 and ev["jump"] is True
            st = _recv_type(wss[1], "uno_state")
            assert st["discard_top"] == "R5"
            assert st["turn"] == 0          # 2 人局：从座位 1 往下走就回到座位 0
        finally:
            _close(wss)

    def test_jump_keeps_hands_private(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"jump_in": True})
        try:
            room = rm.get_room(code)
            self._arm(room, turn=0)
            wss[1].send_json({"type": "uno_play", "card": "R5", "jump": True})
            st = _recv_type(wss[0], "uno_state")
            # 座位 0 收到的状态里，座位 1 的手牌**不在线上**
            assert st["hand"] == ["G9", "G8"]
            assert st["hand_counts"] == {"0": 2, "1": 1}
            assert "hands" not in st
        finally:
            _close(wss)

    def test_jumpable_is_published_only_to_the_holder(self, client):
        """**抢出列表本身也是私有信息**：只有拿着那张牌的人才知道自己能抢。

        发给别人等于把「我手里有哪张牌」漏了出去 —— 与 `hand` 是同一条纪律。
        """
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"jump_in": True})
        try:
            room = rm.get_room(code)
            gs = self._arm(room, turn=0)
            assert uno.game_to_sync(gs, uids[1])["jumpable"] == ["R5"]
            assert uno.game_to_sync(gs, uids[0])["jumpable"] == []
        finally:
            _close(wss)

    def test_losing_the_race_gets_a_message_not_silence(self, client):
        """抢输必须回一句能看懂的错 —— 界面上「点了没反应」是最难查的一类反馈。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"jump_in": True})
        try:
            room = rm.get_room(code)
            gs = self._arm(room, turn=0, hands={0: ["G9", "G8"],
                                                1: ["R5", "B3"]})
            gs["discard_pile"].append("G3")     # 牌堆顶被别人换掉了
            wss[1].send_json({"type": "uno_play", "card": "R5", "jump": True})
            err = _recv_type(wss[1], "error")
            assert "抢" in err["message"]
        finally:
            _close(wss)

    def test_jump_is_refused_when_the_rule_is_off(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            self._arm(room, turn=0)
            wss[1].send_json({"type": "uno_play", "card": "R5", "jump": True})
            err = _recv_type(wss[1], "error")
            assert "抢出" in err["message"]
        finally:
            _close(wss)


class TestSevenZeroWire:
    def test_jump_seven_swaps_hands_and_keeps_them_private(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=3, settings={"jump_in": True, "seven_zero": "swap"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R7")
            gs["hands"] = {0: ["B9", "G4"], 1: ["R7", "G3"], 2: ["Y8", "Y9"]}
            view = uno.game_to_sync(gs, uids[1])
            assert view["swap_targets"] == [0, 2]
            wss[1].send_json({"type": "uno_play", "card": "R7", "jump": True,
                              "target": view["swap_targets"][0]})
            event = _recv_type(wss[1], "uno_event")
            assert event["jump"] is True and event["effect"] == "swap"
            assert event["target"] == 0
            for seat, ws in enumerate(wss):
                state = _recv_type(ws, "uno_state")
                assert state["hand"] == gs["hands"][seat]
                assert "hands" not in state
            assert gs["hands"][0] == ["G3"]
            assert gs["hands"][1] == ["B9", "G4"]
            assert gs["hands"][2] == ["Y8", "Y9"]
        finally:
            _close(wss)

    def test_swap_moves_hands_and_keeps_them_private(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"seven_zero": "swap"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"] = {0: ["R7", "G3", "G4"], 1: ["B9", "B8"]}
            wss[0].send_json({"type": "uno_play", "card": "R7", "target": 1})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["effect"] == "swap" and ev["target"] == 1
            st0 = _recv_type(wss[0], "uno_state")
            st1 = _recv_type(wss[1], "uno_state")
            assert st0["hand"] == ["B9", "B8"]        # 换到的正是对手那手
            assert st1["hand"] == ["G3", "G4"]
            assert "hands" not in st0 and "hands" not in st1
        finally:
            _close(wss)

    def test_swap_without_a_target_is_refused(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"seven_zero": "swap"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"] = {0: ["R7", "G3"], 1: ["B9"]}
            wss[0].send_json({"type": "uno_play", "card": "R7"})
            err = _recv_type(wss[0], "error")
            assert "换手" in err["message"]
            assert gs["hands"][0] == ["R7", "G3"], "被拒的一手不能改状态"
        finally:
            _close(wss)

    def test_zero_rotation_reaches_every_player(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=3, settings={"seven_zero": "rotate"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"] = {0: ["R0", "G3"], 1: ["B9", "B8"], 2: ["Y1"]}
            wss[0].send_json({"type": "uno_play", "card": "R0"})
            ev = _recv_type(wss[1], "uno_event")
            assert ev["effect"] == "rotate"
            st = _recv_type(wss[1], "uno_state")
            assert st["hand"] == ["G3"]               # 收到上家打完 0 之后剩的那张
        finally:
            _close(wss)

    def test_snapshot_carries_the_rule_set_name(self, client, temp_db):
        """战绩要能说清「这局打的是哪套规矩」。

        同一个 UNO 在「热闹」档和「经典」档下完全是两种牌局，只写一个游戏名等于
        什么都没说。名字**由服务端翻译好**随快照下发 —— 战绩页不该为了这一句话去
        抄一份档位表（「游戏名散在七处」那个坑）。
        """
        rm.set_game_end_callback(save_game_record)
        try:
            wss, code, tokens, uids, starts = _setup_game(
                client, players=2, settings={"preset": "party"})
            try:
                room = rm.get_room(code)
                _force(room, turn=0, discard="Y5")
                wss[0].send_json({"type": "surrender"})
                _recv_type(wss[1], "game_over")
            finally:
                _close(wss)
            conn = get_conn()
            try:
                row = conn.execute(
                    "SELECT snapshot_json FROM game_records WHERE game = 'uno'"
                ).fetchone()
            finally:
                conn.close()
            snap = json.loads(row["snapshot_json"])
            assert snap["rules_name"] == "热闹"
            assert snap["rules"]["jump_in"] is True
        finally:
            rm.set_game_end_callback(None)


class TestNoMercyWire:
    """无怜悯档：牌堆真的换了一副、25 张淘汰真的会让人出局。"""

    def test_the_no_mercy_preset_swaps_the_whole_deck(self, client):
        """开局后抽牌堆是 164 − 3×7 − 1 = 142（标准牌堆是 108 那一套）。

        这是**整条链路最硬的一条断言**：大厅选的档位 → 房间房规 → `create_game`
        用哪副牌 → 下发给客户端的 `draw_pile_count`。中间任何一环断了都对不上。
        """
        wss, code, tokens, uids, starts = _setup_game(
            client, players=3, settings={"preset": "no_mercy"})
        try:
            assert starts[0]["game"]["rules"]["deck"] == "no_mercy"
            assert starts[0]["game"]["draw_pile_count"] == 164 - 3 * 7 - 1
            assert starts[1]["game"]["draw_pile_count"] == 142
        finally:
            _close(wss)

    def test_a_25_card_hand_is_knocked_out_and_ends_the_game(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            # 座位 0 手上 24 张，摸一张就被 25 张淘汰 → 只剩座位 1 → 终局
            gs["hands"] = {0: ["B" + str(i) for i in range(24)], 1: ["G3"]}
            gs["draw_pile"] = ["Y1"] * 5
            wss[0].send_json({"type": "uno_draw"})
            over = _recv_type(wss[1], "game_over")
            assert over["reason"] == "last_remaining"
            assert over["winner"] == 1, "剩下的人赢（这就是官方的第二种获胜方式）"
            st = _recv_type(wss[0], "uno_state")
            assert 0 in st["out"]
        finally:
            _close(wss)

    def test_mercy_is_off_in_the_classic_deck(self, client):
        """不开这条房规时，30 张手牌也没人出局 —— 免得它变成一条全局规则。"""
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="R5")
            gs["hands"] = {0: ["B" + str(i) for i in range(30)], 1: ["G3"]}
            gs["draw_pile"] = ["Y1"] * 5
            wss[0].send_json({"type": "uno_draw"})
            ev = _recv_type(wss[1], "uno_event")
            assert ev["kind"] == "draw"
            assert rm.get_room(code)["game_state"]["out"] == []
        finally:
            _close(wss)


class TestRouletteWire:
    """颜色轮盘（W+RO）：**这一张要另一个玩家做一次选择** ——
    本作第一条「等某个座位上的人点名」的状态机。"""

    def _arm(self, room, turn=0):
        gs = _force(room, turn=turn, discard="R5")
        gs["hands"] = {0: ["W+RO", "G3"], 1: ["B9", "B8"]}
        gs["draw_pile"] = ["Y1", "G2", "B4", "R7", "G5"]
        return gs

    def test_playing_it_hands_the_naming_over_to_the_next_seat(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            self._arm(room)
            wss[0].send_json({"type": "uno_play", "card": "W+RO"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "play" and ev["effect"] == "roulette"
            st0 = _recv_type(wss[0], "uno_state")
            st1 = _recv_type(wss[1], "uno_state")
            assert st1["roulette_seat"] == 1, "点名权交给下家"
            assert st0["roulette_seat"] == 1 and st0["your_seat"] == 0
            # 挂起时**谁都出不了牌、也摸不了牌**（只有点名者能发 uno_roulette）
            assert st0["playable"] == [] and st0["can_draw"] is False
            assert st1["playable"] == [] and st1["can_draw"] is False
        finally:
            _close(wss)

    def test_only_the_named_seat_may_name_a_colour(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            self._arm(room)
            wss[0].send_json({"type": "uno_play", "card": "W+RO"})
            _recv_type(wss[0], "uno_state")
            # 出牌的人自己不能点（点名权在下家）
            wss[0].send_json({"type": "uno_roulette", "color": "R"})
            err = _recv_type(wss[0], "error")
            assert "点颜色" in err["message"]
            # 摸牌同样被拒 —— 否则轮盘可以被绕过
            wss[1].send_json({"type": "uno_draw"})
            assert "颜色轮盘" in _recv_type(wss[1], "error")["message"]
        finally:
            _close(wss)

    def test_naming_a_colour_flips_until_that_colour_shows_up(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            gs = self._arm(room)
            before = len(gs["hands"][1])
            wss[0].send_json({"type": "uno_play", "card": "W+RO"})
            _recv_type(wss[0], "uno_state")
            # 座位 1 的那条**出牌后的**状态也要吃掉：不然下面 `_recv_type` 会读到它
            # （队列是先进先出，读到的会是出牌前那一刻的旧盘面）
            _recv_type(wss[1], "uno_state")
            wss[1].send_json({"type": "uno_roulette", "color": "R"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "roulette" and ev["seat"] == 1
            assert ev["color"] == "R" and ev["drawn"] >= 1
            st = _recv_type(wss[1], "uno_state")
            # 翻出来的牌**全归点名者**，最后一张一定是点名的那个颜色
            assert st["hand_counts"]["1"] == before + ev["drawn"]
            assert st["hand"][-1][0] == "R", f"最后翻出来的应当是红色：{st['hand']}"
            assert st["current_color"] == "R"
            assert st["roulette_seat"] is None, "点名完轮盘就结束了"
            assert st["turn"] == 0, "点名者停一轮，回合交回出牌的人"
        finally:
            _close(wss)

    def test_a_bad_colour_is_refused(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            self._arm(room)
            wss[0].send_json({"type": "uno_play", "card": "W+RO"})
            _recv_type(wss[0], "uno_state")
            wss[1].send_json({"type": "uno_roulette", "color": "紫"})
            assert "颜色" in _recv_type(wss[1], "error")["message"]
            assert rm.get_room(code)["game_state"]["roulette"] is not None
        finally:
            _close(wss)

    def test_it_needs_no_chosen_colour_when_played(self, client):
        """其余黑牌都要 `color`，**唯独轮盘不要** —— 颜色由下家点名。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=2, settings={"preset": "no_mercy"})
        try:
            room = rm.get_room(code)
            self._arm(room)
            wss[0].send_json({"type": "uno_play", "card": "W+RO"})   # 不带 color
            ev = _recv_type(wss[0], "uno_event")
            assert ev["kind"] == "play" and ev["effect"] == "roulette"
        finally:
            _close(wss)


class TestTeamsWire:
    """2v2 组队档走线上（`teams=2v2`，v1.16）。

    引擎侧的规则回归在 `test_uno.py::TestTeams`；这里只盯**协议**上容易出错的三处：

    1. 人数不够时房主必须收到**一句能看懂的话**，不是「无法开始游戏」（本项目最
       忌讳的「点了没反应」）。
    2. 一个人出完手牌后局要继续，且 `uno_state` 里 `emptied` / `out` 都要更新 ——
       客户端靠它把那一格画成「已出完」而不是「已出局」。
    3. 终局广播带 `winner_team`：结算是**队伍**，只给一个座位号会让队友看起来
       像输家。
    """

    def test_team_preset_needs_four_players(self, client):
        wss, code, tokens, uids = _lobby(client, players=3)
        try:
            wss[0].send_json({"type": "room_setting", "key": "preset", "value": "team"})
            for ws in wss:
                _recv_type(ws, "lobby")
            wss[0].send_json({"type": "start"})
            err = _recv_type(wss[0], "error")
            assert "4" in err["message"], err["message"]
            assert rm.get_room(code)["status"] == "waiting", "开局失败不能把房间卡在 playing"
        finally:
            _close(wss)

    def test_team_deck_carries_the_two_team_cards(self, client):
        """组队档的牌堆是 **112 张**（108 + 两张专属黑牌各 2）—— 这一个数字同时
        锁住「房规 → `create_game` 用哪副牌 → 下发的 `draw_pile_count`」整条链路。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            assert starts[0]["game"]["draw_pile_count"] == 112 - 4 * 7 - 1
            assert len(uno.build_deck("classic", teams=True)) == 112
            assert len(uno.build_deck("classic")) == 108, "经典牌堆一个字都不该动"
        finally:
            _close(wss)

    def test_team_play_over_the_wire(self, client):
        """`WTP` 走线上：回合跳到队友，对手全被跳过，而且要选色。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["WTP", "R1"]
            gs["hands"][2] = ["G1", "G2"]
            # 不选色必须被拒（同 v1.14 那三张黑牌的教训）
            wss[0].send_json({"type": "uno_play", "card": "WTP"})
            err = _recv_type(wss[0], "error")
            assert "颜色" in err["message"], err
            wss[0].send_json({"type": "uno_play", "card": "WTP", "color": "G"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["effect"] == "team_play"
            assert sorted(ev["skipped"]) == [1, 3]
            state = _recv_type(wss[0], "uno_state")
            assert state["turn"] == 2, "回合落到队友身上"
        finally:
            _close(wss)

    def test_team_start_ships_the_pairing(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            g = starts[0]["game"]
            assert g["seat_teams"] == [0, 1, 0, 1]
            assert g["your_team"] == 0
            assert g["emptied"] == []
            assert starts[2]["game"]["your_team"] == 0
            assert starts[1]["game"]["your_team"] == 1
        finally:
            _close(wss)

    def test_one_member_emptying_keeps_the_round_running(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3"]          # 座位 0 手里只剩一张能出的
            # 队友只剩一张 → 半手传不出来，座位 0 才真的留在场外
            # （队友手里有 ≥2 张时他会接管一半接着打，见 TestTeams）
            gs["hands"][2] = ["Y7"]
            wss[0].send_json({"type": "uno_play", "card": "G3"})
            state = _recv_type(wss[0], "uno_state")
            assert state["emptied"] == [0]
            assert 0 in state["out"]
            assert state["finished"] is False, "队友还没出完，局不能结束"
            assert state["turn"] != 0, "回合必须离开已经出完的人"
            # 手牌私有仍然成立（组队档不该顺手把队友的牌发出来）
            assert state["hand"] == []
            assert set(state["hand_counts"]) == {"0", "1", "2", "3"}
        finally:
            _close(wss)

    def test_emptying_hands_over_half_the_partner_hand(self, client):
        """官方 GOING OUT 走线上：出完手牌 → 接管队友一半 → **接着打**，
        而且要广播一条 `takeover` 让全桌知道手牌张数为什么跳变。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3"]
            gs["hands"][2] = ["Y1", "Y2", "Y3", "Y4"]     # 队友 4 张
            wss[0].send_json({"type": "uno_play", "card": "G3"})
            ev = _recv_type(wss[0], "uno_event")
            # 这一手**照常结算**（不是 `empty`）—— 他手里又有牌了，局还在跑
            assert ev["effect"] == "play"
            assert ev["takeover"] == {"seat": 0, "from": 2, "count": 2,
                                      "reason": "going_out"}
            state = _recv_type(wss[0], "uno_state")
            assert state["finished"] is False, "接管之后局还在跑"
            assert state["emptied"] == [] and 0 not in state["out"]
            assert state["hand"] == ["Y1", "Y2"], "本人手牌是接管来的那两张"
            assert state["hand_counts"]["2"] == 2, "队友留下多的那些"
            assert state["turn"] == 1, "回合照常移交（他不再是「出完了」）"
        finally:
            _close(wss)

    def test_drawing_tags_an_out_teammate_back_in_over_the_wire(self, client):
        """TAGGING BACK IN 最常由**摸牌**触发 —— 那条路径也必须报 `takeover`，
        否则界面上只会看到两边手牌张数莫名跳变。"""
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3"]
            gs["hands"][2] = ["Y7"]           # 队友只剩一张 → 座位 0 出完留在场外
            wss[0].send_json({"type": "uno_play", "card": "G3"})
            _recv_type(wss[0], "uno_state")
            # 出牌是广播，座位 2 那边也压着一条 —— 先读掉，免得下面把它当成 draw
            _recv_type(wss[2], "uno_event")
            _recv_type(wss[2], "uno_state")
            assert 0 in gs["out"]
            # 队友摸一张 → 手里 2 张 → 分一半给 0，把他拉回场上
            gs["turn"] = 2
            gs["draw_pile"] = ["B1"]
            wss[2].send_json({"type": "uno_draw"})
            ev = _recv_type(wss[2], "uno_event")
            assert ev["kind"] == "draw"
            assert ev["takeover"] == {"seat": 0, "from": 2, "count": 1,
                                      "reason": "tag_in"}, ev
            state = _recv_type(wss[2], "uno_state")
            assert 0 not in state["out"] and state["emptied"] == []
        finally:
            _close(wss)

    def test_a_teammate_can_call_uno_for_you_over_the_wire(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3", "G4"]     # 自己两张，喊不了自己
            gs["hands"][2] = ["Y7"]           # 队友只剩一张
            wss[0].send_json({"type": "uno_call"})
            ev = _recv_type(wss[0], "uno_event")
            assert ev["seat"] == 2, "事件里报的是**被喊的座位**，不是发起人"
            state = _recv_type(wss[0], "uno_state")
            assert 2 in state["uno_called"]
            assert state["can_call_uno"] is False, "喊过之后按钮灭掉"
        finally:
            _close(wss)

    def test_calling_uno_for_an_opponent_is_refused(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3", "G4"]
            gs["hands"][1] = ["B7"]           # 对手只剩一张 —— 不归我喊
            gs["hands"][2] = ["Y7"]           # 队友也只剩一张 → 我确实能喊，只是不能喊对手
            wss[0].send_json({"type": "uno_call", "target": 1})
            err = _recv_type(wss[0], "error")
            assert err["message"] == "不能替他喊 UNO"
        finally:
            _close(wss)

    def test_both_members_emptying_broadcasts_the_winning_team(self, client):
        wss, code, tokens, uids, starts = _setup_game(
            client, players=4, settings={"preset": "team"})
        try:
            room = rm.get_room(code)
            gs = _force(room, turn=0, discard="G5")
            gs["hands"][0] = ["G3"]
            gs["hands"][2] = ["G7"]          # 队友只剩一张 → 座位 0 出完就留在场外
            wss[0].send_json({"type": "uno_play", "card": "G3"})
            _recv_type(wss[0], "uno_state")

            gs["turn"] = 2                   # 队友（座位 2，同一队）
            gs["discard_pile"] = ["G5"]
            gs["current_color"] = "G"
            wss[2].send_json({"type": "uno_play", "card": "G7"})
            go = _recv_type(wss[0], "game_over")
            assert go["winner_team"] == 0
            assert go["reason"] == uno.REASON_TEAM_EMPTIED
            assert go["seat_teams"] == [0, 1, 0, 1]
            assert set(go["emptied"]) == {0, 2}
            # 排名里获胜那一队排在最前（而且是并列第一，不是「一个人赢」）
            top = [r for r in go["ranking"] if r["team"] == 0]
            assert {r["seat"] for r in top} == {0, 2}
            assert all(r["rank"] == 1 for r in top)
        finally:
            _close(wss)
