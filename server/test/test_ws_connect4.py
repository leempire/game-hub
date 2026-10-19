"""重力四子棋 WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房/加入、开局下发（cols/rows 而不是五子棋的 board_size）、
按列落子与落点行广播、非法落子报错、四连终局、认输、断线重连 sync、
人机应招、战绩快照、结束后续开。

坐标约定：客户端只发 `x = 列号`，服务端回 `x = 列 / y = 落点行`。

Run: pytest test/test_ws_connect4.py -v
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


def _register(client, username):
    r = client.post("/api/auth/register", json={
        "username": username, "password": "test123456",
    })
    assert r.status_code == 200
    data = r.json()
    return data["token"], data["user"]["id"]


def _create_room(client, token, game="connect4"):
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
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setup_playing_game(client):
    """建房 → 加入 → 双 WS → ready → start。返回 (ws1, ws2, code, t1, t2, g1, g2)。"""
    ts = int(time.time() * 1000) % 100000
    t1, _ = _register(client, f"c4_h{ts}a")
    t2, _ = _register(client, f"c4_h{ts}b")
    room = _create_room(client, t1)
    code = room["code"]
    _join_room(client, t2, code)
    ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
    ws2 = client.websocket_connect(f"/ws/room/{code}?token={t2}")
    ws1.__enter__()
    ws2.__enter__()
    try:
        _recv_type(ws1, "sync")
        _recv_type(ws2, "sync")
        ws2.send_json({"type": "ready"})
        _recv_type(ws1, "lobby")
        _recv_type(ws2, "lobby")
        ws1.send_json({"type": "start"})
        g1 = _recv_type(ws1, "game_start")
        g2 = _recv_type(ws2, "game_start")
    except Exception:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)
        raise
    return ws1, ws2, code, t1, t2, g1, g2


def _close(ws1, ws2):
    ws1.__exit__(None, None, None)
    ws2.__exit__(None, None, None)


def _drop(ws, col):
    ws.send_json({"type": "move", "x": col})


class TestStart:
    def test_room_creation(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"c4_r{ts}")
        room = _create_room(client, t1)
        assert room["game"] == "connect4"
        assert room["max_players"] == 2
        assert room["status"] == "waiting"

    def test_game_start_payload(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            # 载荷是 cols/rows，不是五子棋那套写死的 board_size: 15
            assert "board_size" not in g1["game"]
            assert g1["game"]["cols"] == 7
            assert g1["game"]["rows"] == 6
            assert g1["game"]["next"] == "black"
            assert g1["game"]["your_color"] == "black"      # 房主执黑先行
            assert g2["game"]["your_color"] == "white"
            board = g1["game"]["board"]
            assert len(board) == 6 and all(len(row) == 7 for row in board)
            assert all(cell == 0 for row in board for cell in row)
            assert g1["game"]["winner"] is None
        finally:
            _close(ws1, ws2)


class TestMoves:
    def test_drop_broadcasts_the_landing_row(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            _drop(ws1, 3)
            m1 = _recv_type(ws1, "move")
            m2 = _recv_type(ws2, "move")
            # 落点在列底（row 5），回合交给白方
            assert (m1["x"], m1["y"]) == (3, 5)
            assert m1["color"] == "black"
            assert m1["next"] == "white"
            assert m2 == m1
            # 白方落在同一列的上一格
            _drop(ws2, 3)
            m = _recv_type(ws1, "move")
            assert (m["x"], m["y"]) == (3, 4)
            assert m["color"] == "white"
            assert m["next"] == "black"
        finally:
            _close(ws1, ws2)

    def test_out_of_turn_is_rejected(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            _drop(ws2, 0)          # 白方抢先
            err = _recv_type(ws2, "error")
            assert "轮到你" in err["message"]
            # 黑方照常走，不受影响
            _drop(ws1, 0)
            assert _recv_type(ws1, "move")["y"] == 5
        finally:
            _close(ws1, ws2)

    def test_full_column_is_rejected(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            for _ in range(3):
                _drop(ws1, 6)
                _recv_type(ws1, "move")
                _drop(ws2, 6)
                _recv_type(ws2, "move")
            # 第 6 列现在 6 格满（双方各 3）
            _drop(ws1, 6)
            err = _recv_type(ws1, "error")
            assert "满" in err["message"]
            # 换一列立刻就好了
            _drop(ws1, 0)
            assert _recv_type(ws1, "move")["y"] == 5
        finally:
            _close(ws1, ws2)

    def test_missing_column_is_ignored(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            ws1.send_json({"type": "move"})          # 没有 x
            _drop(ws1, 2)
            assert _recv_type(ws1, "move")["x"] == 2  # 无事发生，照常走子
        finally:
            _close(ws1, ws2)


class TestGameOver:
    def test_four_in_a_row(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            # 每走一手两个客户端都要各收一次：只取自己那条的话，对方那条会一直
            # 堆在收件箱里，下一次 _recv_type 拿到的就是上一步的旧消息
            for col in (0, 1, 2):
                _drop(ws1, col)
                _recv_type(ws1, "move")
                _recv_type(ws2, "move")
                _drop(ws2, 6)
                _recv_type(ws1, "move")
                _recv_type(ws2, "move")
            _drop(ws1, 3)
            last = _recv_type(ws1, "move")
            assert (last["x"], last["y"]) == (3, 5)
            _recv_type(ws2, "move")
            go1 = _recv_type(ws1, "game_over")
            go2 = _recv_type(ws2, "game_over")
            assert go1["winner"] == "black"
            assert go1["reason"] == "four"
            assert go2 == go1
            # 房间回到等待态，可以续开
            assert rm.get_room(code)["status"] == "waiting"
        finally:
            _close(ws1, ws2)

    def test_surrender(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            ws1.send_json({"type": "surrender"})
            go = _recv_type(ws1, "game_over")
            assert go["winner"] == "white"
            assert go["reason"] == "surrender"
            _recv_type(ws2, "game_over")
        finally:
            _close(ws1, ws2)

    def test_vertical_four_also_ends_the_game(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            # 黑一直砸 5 列，白在 0 / 1 / 2 列交替（避免自己也连成）
            white_cols = [0, 0, 1, 1, 2]
            for i in range(3):
                _drop(ws1, 5)
                _recv_type(ws1, "move")
                _drop(ws2, white_cols[i])
                _recv_type(ws2, "move")
            _drop(ws1, 5)
            _recv_type(ws1, "move")
            go = _recv_type(ws1, "game_over")
            assert go["winner"] == "black" and go["reason"] == "four"
        finally:
            _close(ws1, ws2)


class TestReconnect:
    def test_sync_carries_connect4_state(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            _drop(ws1, 3)
            _recv_type(ws1, "move")
            _recv_type(ws2, "move")
            _close(ws1, ws2)
            # 重连拿 sync
            ws = client.websocket_connect(f"/ws/room/{code}?token={t1}")
            ws.__enter__()
            try:
                sync = _recv_type(ws, "sync")
                game = sync["game"]
                assert game is not None, "对局还在进行，sync 必须带 game"
                assert game["cols"] == 7 and game["rows"] == 6
                assert game["your_color"] == "black"
                assert game["board"][5][3] == 1          # 黑子在列底
                assert game["next"] == "white"
                assert sync["in_game"] is True
            finally:
                ws.__exit__(None, None, None)
        finally:
            pass

    def test_sync_after_game_end_has_no_game(self, client):
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            ws1.send_json({"type": "surrender"})
            _recv_type(ws1, "game_over")
            _close(ws1, ws2)
            ws = client.websocket_connect(f"/ws/room/{code}?token={t1}")
            ws.__enter__()
            try:
                sync = _recv_type(ws, "sync")
                # 房间回到 waiting → 不下发 game，否则点「返回房间」会被弹回对局页
                assert sync["game"] is None
                assert sync["room"]["status"] == "waiting"
            finally:
                ws.__exit__(None, None, None)
        finally:
            pass


class TestBot:
    def test_bot_replies(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, uid1 = _register(client, f"c4_bot{ts}")
        room = _create_room(client, t1)
        code = room["code"]
        ws1 = client.websocket_connect(f"/ws/room/{code}?token={t1}")
        ws1.__enter__()
        try:
            _recv_type(ws1, "sync")
            ws1.send_json({"type": "add_bot"})
            lobby = _recv_type(ws1, "lobby")
            bot = [p for p in lobby["room"]["players"] if p.get("is_bot")][0]
            assert bot["ready"] is True
            ws1.send_json({"type": "start"})
            _recv_type(ws1, "game_start")
            _drop(ws1, 0)
            _recv_type(ws1, "move")
            # 人机应招：0.8~1.2s 的思考延迟
            bot_move = _recv_type(ws1, "move", max_skip=20)
            assert bot_move["color"] == "white"
            assert bot_move["next"] == "black"
            assert bot_move["y"] == 5
        finally:
            ws1.__exit__(None, None, None)

    def test_room_accepts_bot(self, client):
        ts = int(time.time() * 1000) % 100000
        t1, _ = _register(client, f"c4_botr{ts}")
        room = _create_room(client, t1)
        # add_bot 的白名单里必须有 connect4，否则这里会抛「该游戏不支持人机」
        updated = rm.add_bot(room["code"], room["host_id"])
        assert any(p.get("is_bot") for p in updated["players"])


class TestHistory:
    def test_record_and_snapshot(self, client, tmp_path):
        """认输要落库，且快照带 cols/rows/board——否则战绩页展开是空白。"""
        import json
        import app.database as db_mod
        from app.database import init_db, save_game_record, get_conn
        init_db(str(tmp_path / "test.db"))
        rm.set_game_end_callback(save_game_record)
        try:
            ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
            try:
                _drop(ws1, 3)
                _recv_type(ws1, "move")
                _recv_type(ws2, "move")
                ws1.send_json({"type": "surrender"})
                _recv_type(ws1, "game_over")
                _recv_type(ws2, "game_over")
            finally:
                _close(ws1, ws2)
            conn = get_conn()
            try:
                rows = conn.execute(
                    "SELECT results_json, snapshot_json FROM game_records "
                    "WHERE game = 'connect4' AND room_code = ?",
                    (code,),
                ).fetchall()
                assert len(rows) == 1, "重力四子棋战绩没落库"
                results = {r["result"] for r in json.loads(rows[0]["results_json"])}
                assert results == {"win", "lose"}
                snap = json.loads(rows[0]["snapshot_json"])
            finally:
                conn.close()
            assert snap["game"] == "connect4"
            assert snap["cols"] == 7 and snap["rows"] == 6
            assert len(snap["board"]) == 6 and len(snap["board"][0]) == 7
            assert snap["board"][5][3] == 1          # 黑子那一步在快照里
            assert snap["winner"] == "white"
            assert snap["reason"] == "surrender"
        finally:
            rm.set_game_end_callback(None)
            db_mod.init_db(db_mod._db_path)


class TestNextRound:
    def test_start_new_round_from_lobby(self, client):
        """结束后房间回到等待态，棋盘复位；对方重新准备后房主可续开。"""
        ws1, ws2, code, t1, t2, g1, g2 = _setup_playing_game(client)
        try:
            _drop(ws1, 4)
            _recv_type(ws1, "move")
            _recv_type(ws2, "move")
            ws1.send_json({"type": "surrender"})
            _recv_type(ws1, "game_over")
            _recv_type(ws2, "game_over")
            # 终局把真人非房主复位为未准备 → 先重新准备
            ws2.send_json({"type": "ready"})
            _recv_type(ws1, "lobby")
            _recv_type(ws2, "lobby")
            ws1.send_json({"type": "start"})
            rs1 = _recv_type(ws1, "game_start")
            board = rs1["game"]["board"]
            assert all(cell == 0 for row in board for cell in row), "新一局棋盘没复位"
            assert rs1["game"]["next"] == "black"
            assert rs1["game"]["winner"] is None
        finally:
            _close(ws1, ws2)