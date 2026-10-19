"""俄罗斯方块对战 WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房/加入、逐人下发开局（各玩各的盘）、动作序列重放、逐人裁剪广播、
攻击行顶给对手并推送新盘面、认输只结束本人、全员结束才结算、断线重连 sync、
战绩快照。

坐标约定：`board[row][col]`，row 0 在最上面；1..7 方块 id、8 攻击行填充块。
**攻击行要让对手收得到**：走完整链路（消行 → deliver_garbage → WS 推送），
所以用例直接改房间里的 gs 造出「差一列就满」的局面，而不是靠随机硬降
（随机硬降铺不满一整行，一行都消不掉——引擎单测里已经确认过）。

Run: pytest test/test_ws_tetris.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.game import tetris as tetris_engine


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


def _create_room(client, token, game="tetris"):
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=15):
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setup_game(client, players=2):
    """建房 → 加入 → 双 WS → ready → start。返回 (wss, code, tokens, uids, starts)。"""
    ts = int(time.time() * 1000) % 100000
    tokens, uids = [], []
    for i in range(players):
        t, uid = _register(client, f"tt_{ts}_{i}")
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
    for ws in wss[1:]:
        ws.send_json({"type": "ready"})
    for ws in wss:
        _recv_type(ws, "lobby")
    wss[0].send_json({"type": "start"})
    starts = [_recv_type(ws, "game_start") for ws in wss]
    return wss, code, tokens, uids, starts


def _close(wss):
    for ws in wss:
        ws.__exit__(None, None, None)


class TestStart:
    def test_room_creation(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"tt_r{ts}")
        room = _create_room(client, t1)
        assert room["game"] == "tetris"
        assert room["max_players"] == 4, "俄罗斯方块最多 4 人"
        assert room["status"] == "waiting"

    def test_game_start_is_per_player(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            g1, g2 = starts[0]["game"], starts[1]["game"]
            # 载荷是 cols/rows，不是五子棋那套 board_size: 15
            assert "board_size" not in g1
            assert g1["cols"] == 10 and g1["rows"] == 20
            assert len(g1["board"]) == 20 and len(g1["board"][0]) == 10
            assert g1["current"] is not None, "开局就该有正在下落的方块"
            assert g1["next_piece"] in tetris_engine.PIECES
            # 各自的盘面是**不同对象**（各玩各的）
            assert g1["board"] is not g2["board"]
            # 排行榜是公开信息，两边一致
            assert len(g1["leaderboard"]) == 2
            assert uids[0] in [r["user_id"] for r in g1["leaderboard"]]
        finally:
            _close(wss)

    def test_sync_carries_tetris_state(self, client):
        wss, code, tokens, uids, starts = _setup_game(client, players=2)
        try:
            _close(wss)
            ws = client.websocket_connect(f"/ws/room/{code}?token={tokens[0]}")
            ws.__enter__()
            try:
                sync = _recv_type(ws, "sync")
                game = sync["game"]
                assert game is not None
                assert game["cols"] == 10 and game["rows"] == 20
                assert game["current"] is not None
                assert sync["in_game"] is True
            finally:
                ws.__exit__(None, None, None)
        finally:
            pass


class TestActions:
    def test_hard_drop_locks_and_returns_state(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            msg = _recv_type(wss[0], "tetris_state")
            assert msg["user_id"] == uids[0]
            assert msg["last_action"] == "lock"
            assert msg["current"] is not None, "锁定后应立刻发下一个方块"
            filled = sum(1 for row in msg["board"] for c in row if c)
            assert filled == 4, f"硬降应落下一块（4 格），实际 {filled}"
            # 对手只收到排行榜更新，**看不到我的盘**
            other = _recv_type(wss[1], "tetris_leaderboard")
            assert "board" not in other
            assert len(other["leaderboard"]) == 2
        finally:
            _close(wss)

    def test_move_and_rotate_then_drop(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({
                "type": "tetris_actions",
                "actions": ["left", "left", "rotate_cw", "hard_drop"],
            })
            msg = _recv_type(wss[0], "tetris_state")
            assert msg["last_action"] == "lock"
            filled = sum(1 for row in msg["board"] for c in row if c)
            assert filled == 4
            cols = [c for row in msg["board"] for c, v in enumerate(row) if v]
            assert min(cols) <= 3, "左移了两格，方块应该比出生位置更靠左"
        finally:
            _close(wss)

    def test_empty_actions_are_silent(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "tetris_actions", "actions": []})
            # 静默：不该有 error，也不该有 state
            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            msg = _recv_type(wss[0], "tetris_state")
            assert msg["last_action"] == "lock"
        finally:
            _close(wss)

    def test_non_locking_sequence_resyncs_instead_of_silence(self, client):
        """**分叉兜底**：客户端只在「方块下不动了」时才 flush（或点了硬降），
        所以服务端重放却没锁定 = 两边盘面已经分叉（漏收过一条广播等）。

        这时不回包的话，客户端的 `awaiting` 永远等不到回包——重力与按键全部
        失效，界面上就是「盘面冻住、再也不出新的方块」。必须回一份权威盘面。
        """
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "tetris_actions", "actions": ["left"]})
            msg = _recv_type(wss[0], "tetris_state")
            assert msg["last_action"] == "resync"
            assert msg["user_id"] == uids[0]
            assert msg["current"] is not None, "兜底包也要带权威盘面与方块"
            # 兜底之后继续正常走子（回包即权威盘面）
            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            again = _recv_type(wss[0], "tetris_state")
            assert again["last_action"] == "lock"
            assert again["current"] is not None
        finally:
            _close(wss)

    def test_oversized_action_list_is_rejected(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({
                "type": "tetris_actions",
                "actions": ["left"] * (rm.TETRIS_MAX_ACTIONS + 10),
            })
            err = _recv_type(wss[0], "error")
            assert "过长" in err["message"]
        finally:
            _close(wss)

    def test_wrong_message_shape_is_ignored(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "tetris_actions"})          # 没有 actions
            wss[0].send_json({"type": "tetris_actions", "actions": "hard_drop"})
            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            assert _recv_type(wss[0], "tetris_state")["last_action"] == "lock"
        finally:
            _close(wss)


class TestGarbage:
    def _make_row_ready(self, code, uid, rows=4):
        """把 uid 的底部 rows 行铺满、只留第 9 列空，并把当前方块换成竖 I。

        这样一次 hard_drop 就能消 4 行 → 触发 4 行攻击行。
        """
        gs = rm.get_room(code)["game_state"]
        board = gs["boards"][uid]
        for r in range(tetris_engine.ROWS - rows, tetris_engine.ROWS):
            for c in range(tetris_engine.COLS - 1):
                board[r][c] = 8
        gs["current"][uid] = {"piece": "I", "rotation": 1, "x": 7, "y": 0}
        return gs

    def test_clearing_sends_garbage_and_pushes_state_to_victim(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            gs = self._make_row_ready(code, uids[0], rows=4)
            for uid in (uids[0], uids[1]):
                before = gs["garbage_received"].get(uid, 0)
                assert before == 0

            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            mine = _recv_type(wss[0], "tetris_state")
            assert mine["cleared"] == 4
            assert mine["score_gained"] == 800
            assert mine["garbage_sent"] == 4

            # 受害者（不是他动的）必须收到自己的新盘面
            victim = _recv_type(wss[1], "tetris_state")
            assert victim["user_id"] == uids[1]
            assert victim["last_action"] == "garbage"
            assert victim["garbage_from"] == uids[0]
            assert victim["garbage_received"] >= 4
            bottom = victim["board"][tetris_engine.ROWS - 1]
            assert sum(1 for c in bottom if c == 8) == 9, \
                f"底行应是一整行攻击行（含一个洞）：{bottom}"
        finally:
            _close(wss)

    def test_victim_keeps_playing_after_garbage(self, client):
        """**v1.9.1 回归**：被顶之后必须还能继续玩。

        早先「方块在第 count 行以内就判死」的判据会把出生在第 0 行的方块直接
        顶死，而且判死分支跳过了 `add_garbage`——受害者盘面纹丝不动、方块凭空
        消失、后续动作全部被「你已结束本局」拒掉。客户端那边就是「被顶之后
        卡住、再也不出方块」。这里锁住三件事：方块还在、盘面真的被顶起来、
        继续走子服务端仍然回包。
        """
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            self._make_row_ready(code, uids[0], rows=4)
            gs = rm.get_room(code)["game_state"]
            assert gs["current"][uids[1]]["y"] == 0, "对手方块应在出生点"

            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            mine = _recv_type(wss[0], "tetris_state")
            assert mine["garbage_killed"] == [], "空盘上的方块不该被顶死"

            victim = _recv_type(wss[1], "tetris_state")
            assert victim["finished"] is False
            assert victim["current"] is not None, "被顶之后当前方块不能凭空消失"
            assert victim["garbage_received"] == 4
            assert sum(1 for c in victim["board"][tetris_engine.ROWS - 1] if c == 8) == 9, \
                "盘面必须真的被顶起来"

            # 继续走子：服务端重放并回包（回不上包 = 客户端 awaiting 卡死 = 界面卡住）
            wss[1].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            again = _recv_type(wss[1], "tetris_state")
            assert again["user_id"] == uids[1]
            assert again["last_action"] == "lock"
            assert again["current"] is not None, "锁定后要出下一块"
        finally:
            _close(wss)

    def test_garbage_can_top_out_the_opponent(self, client):
        """堆叠已经压到天花板时收攻击行 → 顶死（攻击取胜的正常方式）。"""
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            self._make_row_ready(code, uids[0], rows=4)
            gs = rm.get_room(code)["game_state"]
            # 对手自己把盘堆到天花板（只留第 0 行，方块也要塞得下）
            board = gs["boards"][uids[1]]
            for r in range(3, tetris_engine.ROWS):
                for c in range(tetris_engine.COLS):
                    board[r][c] = 8
            gs["current"][uids[1]] = {"piece": "I", "rotation": 0, "x": 3, "y": 1}
            assert gs["current"][uids[1]]["y"] == 1

            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            mine = _recv_type(wss[0], "tetris_state")
            assert mine["garbage_killed"] == [uids[1]]
            victim = _recv_type(wss[1], "tetris_state")
            assert victim["finished"] is True
            assert victim["finish_reason"] == "garbage"
            assert victim["current"] is None
        finally:
            _close(wss)


class TestFinish:
    def test_surrender_only_finishes_me(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "surrender"})
            # 单人认输不该整局结束（对手还在玩）
            wss[1].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            msg = _recv_type(wss[1], "tetris_state")
            assert msg["last_action"] == "lock"
            gs = rm.get_room(code)["game_state"]
            assert gs["game_over_flags"][uids[0]] is True
            assert gs["game_over_flags"][uids[1]] is False
            assert gs["winner"] is None, "还有人没结束，不该结算"
        finally:
            _close(wss)

    def test_all_finished_settles_with_ranking(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            gs = rm.get_room(code)["game_state"]
            gs["scores"][uids[0]] = 500
            gs["scores"][uids[1]] = 900
            wss[0].send_json({"type": "surrender"})
            _recv_type(wss[0], "lobby", max_skip=5) if False else None
            wss[1].send_json({"type": "surrender"})
            go = _recv_type(wss[0], "game_over")
            assert go["reason"] == "ranking"
            assert len(go["ranking"]) == 2
            assert go["ranking"][0]["user_id"] == uids[1], "分高的排第一"
            assert [r["rank"] for r in go["ranking"]] == [1, 2]
            assert rm.get_room(code)["status"] == "waiting"
        finally:
            _close(wss)

    def test_actions_after_finish_are_rejected(self, client):
        wss, code, tokens, uids, starts = _setup_game(client)
        try:
            wss[0].send_json({"type": "surrender"})
            wss[0].send_json({"type": "tetris_actions", "actions": ["hard_drop"]})
            err = _recv_type(wss[0], "error")
            assert "结束" in err["message"]
        finally:
            _close(wss)


class TestHistory:
    def test_record_and_snapshot(self, client, tmp_path):
        import json
        import app.database as db_mod
        from app.database import init_db, save_game_record, get_conn
        init_db(str(tmp_path / "test.db"))
        rm.set_game_end_callback(save_game_record)
        try:
            wss, code, tokens, uids, starts = _setup_game(client)
            try:
                gs = rm.get_room(code)["game_state"]
                gs["scores"] = {uids[0]: 300, uids[1]: 700}
                wss[0].send_json({"type": "surrender"})
                wss[1].send_json({"type": "surrender"})
                _recv_type(wss[0], "game_over")
            finally:
                _close(wss)
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT results_json, snapshot_json FROM game_records "
                    "WHERE game = 'tetris' AND room_code = ?", (code,),
                ).fetchall()
                assert len(rows) == 1, "俄罗斯方块战绩没落库"
                results = {r["result"] for r in json.loads(rows[0]["results_json"])}
                assert results == {"win", "lose"}, results
                snap = json.loads(rows[0]["snapshot_json"])
            finally:
                conn.close()
            assert snap["game"] == "tetris"
            assert len(snap["boards"]) == 2
            assert len(snap["boards"][str(uids[0])]) == 20
            assert snap["ranking"][0]["rank"] == 1
            assert snap["players"][str(uids[0])]["username"].startswith("tt_")
        finally:
            rm.set_game_end_callback(None)
            db_mod.init_db(db_mod._db_path)