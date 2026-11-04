"""Timed rooms, authoritative takeback affordances and private military deadlines."""

from contextlib import ExitStack
from copy import deepcopy
import threading

import pytest

from app import database, persistence, room_manager as rm, room_timing, security, ws as ws_module
from app.game import xiangqi as xq


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "timing.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "timing-test-secret-that-is-at-least-32-bytes")
    for name in ("_rooms", "_dc_tasks", "_bot_tasks"):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(room_timing, "_anchors", {})
    monkeypatch.setattr(room_timing, "_tasks", {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(rm, "schedule_bot_turn", lambda *args: None)
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    return ws_client


def user(client, name):
    response = client.post("/api/auth/register", json={"username": name, "password": "test123456"})
    assert response.status_code == 200
    data = response.json()
    return data["user"]["id"], data["token"], {"Authorization": f"Bearer {data['token']}"}


def recv(ws, kind):
    for _ in range(40):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"missing {kind}")


def pair(client, stack, game="xiangqi", *, bot=False, clock=300, undo=False, byoyomi=0, setup_time=0):
    host = user(client, f"clock_{game}_host")
    room = client.post("/api/rooms", headers=host[2], json={"game": game}).json()
    code = room["code"]
    state = rm.get_room(code)
    state.update(chess_clock_seconds=clock, xiangqi_byoyomi_seconds=byoyomi,
                 xiangqi_allow_undo=undo, luzhanqi_setup_timeout=setup_time,
                 luzhanqi_mode="hidden")
    if bot:
        rm.add_bot(code, host[0], "easy")
        guest = None
    else:
        guest = user(client, f"clock_{game}_guest")
        assert client.post(f"/api/rooms/{code}/join", headers=guest[2], json={}).status_code == 200
    people = [host] if guest is None else [host, guest]
    sockets = [stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={p[1]}")) for p in people]
    for socket in sockets:
        recv(socket, "sync")
    if guest is not None:
        sockets[1].send_json({"type": "ready"})
    sockets[0].send_json({"type": "start"})
    starts = [recv(socket, "game_start")["game"] for socket in sockets]
    return code, state, people, sockets, starts


@pytest.mark.parametrize("game", ["gomoku", "reversi", "xiangqi"])
def test_enabled_clock_is_in_start_move_and_sync_payloads(client, game):
    with ExitStack() as stack:
        code, room, people, sockets, starts = pair(client, stack, game)
        first = starts[0]["clock"]["turn"]
        assert starts[0]["clock"]["remaining_ms"][first] <= 300000
        if game == "xiangqi":
            sockets[0].send_json({"type": "xiangqi_move", "from_x": 0, "from_y": 6, "to_x": 0, "to_y": 5})
        else:
            x, y = starts[0]["legal"][0]
            sockets[0].send_json({"type": "move", "x": x, "y": y})
        move = recv(sockets[0], "move")
        assert move["clock"]["turn"] != first
        assert move["clock"]["remaining_ms"][first] <= starts[0]["clock"]["remaining_ms"][first]
        assert ws_module._build_sync(room, people[0][0])["game"]["clock"]["turn"] == move["clock"]["turn"]


def test_clock_expires_without_any_new_message_and_records_result(client):
    with ExitStack() as stack:
        code, room, people, sockets, starts = pair(client, stack)
        gs = room["game_state"]
        gs["clock"]["remaining_ms"]["red"] = 30
        client.portal.call(room_timing.schedule, room)
        for socket in sockets:
            result = recv(socket, "game_over")
            assert result["winner"] == "black" and result["reason"] == "timeout"
        assert room["status"] == "waiting"
        assert database.get_user_history(people[0][0])["items"][0]["snapshot"]["reason"] == "timeout"


def test_late_move_is_rejected_before_it_can_change_board(client):
    with ExitStack() as stack:
        code, room, _, sockets, _ = pair(client, stack)
        gs = room["game_state"]
        original = deepcopy(gs["board"])
        gs["clock"]["remaining_ms"]["red"] = 10
        room_timing._anchors[code] = (gs, room_timing._now() - 0.02)
        sockets[0].send_json({"type": "xiangqi_move", "from_x": 0, "from_y": 6, "to_x": 0, "to_y": 5})
        assert recv(sockets[0], "game_over")["reason"] == "timeout"
        assert gs["board"] == original and gs["moves"] == []


def test_overtime_is_actual_room_rule_and_not_disabled_at_main_time_zero(client):
    with ExitStack() as stack:
        code, room, _, sockets, starts = pair(client, stack, byoyomi=10)
        gs = room["game_state"]
        assert starts[0]["clock"]["period_ms"] == 10000
        gs["clock"]["remaining_ms"]["red"] = 1
        room_timing._anchors[code] = (gs, room_timing._now() - 0.1)
        sockets[0].send_json({"type": "xiangqi_move", "from_x": 0, "from_y": 6, "to_x": 0, "to_y": 5})
        move = recv(sockets[0], "move")
        assert move["clock"]["remaining_ms"]["red"] == 0
        assert gs["winner"] is None
        assert move["clock"]["turn"] == "black"


def test_undo_is_server_authorized_rewinds_both_moves_and_does_not_refund_clock(client, monkeypatch):
    monkeypatch.setattr(ws_module.xiangqi_ai, "best_move", lambda board, color, **kw: xq.all_legal_moves(board, color)[0])
    with ExitStack() as stack:
        code, room, people, sockets, starts = pair(client, stack, bot=True, undo=True)
        assert starts[0]["can_undo"] is False
        original = deepcopy(room["game_state"]["board"])
        sockets[0].send_json({"type": "xiangqi_move", "from_x": 0, "from_y": 6, "to_x": 0, "to_y": 5})
        assert recv(sockets[0], "move")["can_undo"] is False
        bot = next(player for player in room["players"] if player.get("is_bot"))
        client.portal.call(ws_module._bot_xiangqi_move, code, bot["id"], room, room["game_state"])
        assert recv(sockets[0], "move")["can_undo"] is True
        before = dict(room["game_state"]["clock"]["remaining_ms"])
        sockets[0].send_json({"type": "xiangqi_undo"})
        state = recv(sockets[0], "xiangqi_state")["game"]
        assert state["board"] == original and state["moves"] == []
        assert state["revision"] == 3 and state["can_undo"] is False
        assert all(state["clock"]["remaining_ms"][side] <= value for side, value in before.items())


def test_human_room_cannot_use_bot_takeback(client):
    with ExitStack() as stack:
        _, _, _, sockets, starts = pair(client, stack, undo=True)
        assert starts[0]["can_undo"] is False
        sockets[0].send_json({"type": "xiangqi_undo"})
        assert "人机" in recv(sockets[0], "error")["message"]


def test_military_timeout_begins_with_private_views_and_terminal_truth(client):
    with ExitStack() as stack:
        code, room, _, sockets, starts = pair(client, stack, "luzhanqi", setup_time=30)
        assert starts[0]["setup_remaining_ms"] > 0
        room["game_state"]["setup_remaining_ms"] = 20
        client.portal.call(room_timing.schedule, room)
        for index, socket in enumerate(sockets):
            view = recv(socket, "luzhanqi_begin")["game"]
            mine = "red" if index == 0 else "black"
            assert view["phase"] == "playing"
            assert all(cell["p"] is None for row in view["cells"] for cell in row
                       if cell and cell["c"] != mine)
        sockets[0].send_json({"type": "surrender"})
        for socket in sockets:
            recv(socket, "game_over")
            view = recv(socket, "luzhanqi_state")["game"]
            assert view["truth_available"]
            assert all(cell["p"] is not None for row in view["cells"] for cell in row if cell)


def test_disconnect_does_not_pause_clock(client):
    with ExitStack() as stack:
        code, room, _, sockets, _ = pair(client, stack)
        room["game_state"]["clock"]["remaining_ms"]["red"] = 40
        client.portal.call(room_timing.schedule, room)
        sockets[0].close()
        result = recv(sockets[1], "game_over")
        assert result["reason"] == "timeout" and result["winner"] == "black"


def test_search_result_cannot_reapply_after_same_length_takeback(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed(board, color, **kwargs):
        candidate = xq.all_legal_moves(board, color)[0]
        entered.set()
        assert release.wait(3)
        return candidate

    monkeypatch.setattr(ws_module.xiangqi_ai, "best_move", delayed)
    with ExitStack() as stack:
        code, room, _, sockets, _ = pair(client, stack, bot=True, undo=True, clock=0)
        sockets[0].send_json({"type": "xiangqi_move", "from_x": 0, "from_y": 6, "to_x": 0, "to_y": 5})
        recv(sockets[0], "move")
        gs = room["game_state"]
        bot = next(player for player in room["players"] if player.get("is_bot"))
        future = client.portal.start_task_soon(ws_module._bot_xiangqi_move, code, bot["id"], room, gs)
        try:
            assert entered.wait(2)
            assert xq.make_move(gs, *xq.all_legal_moves(gs["board"], "black")[0])["ok"]
            assert xq.make_move(gs, *xq.all_legal_moves(gs["board"], "red")[0])["ok"]
            assert xq.undo_last_pair(gs, "black")["ok"]
        finally:
            release.set()
        future.result(timeout=4)
        assert len(gs["moves"]) == 1
        assert gs["next"] == "black"


def test_military_terminal_order_is_game_over_truth_then_lobby(client):
    rm.add_game_end_listener(ws_module._broadcast_lobby_after_game_end)
    with ExitStack() as stack:
        _, _, _, sockets, _ = pair(client, stack, "luzhanqi", clock=0)
        sockets[0].send_json({"type": "surrender"})
        for socket in sockets:
            kinds = []
            for _ in range(20):
                message = socket.receive_json()
                kinds.append(message["type"])
                if message["type"] == "lobby" and message["room"]["status"] == "waiting":
                    break
            assert kinds.index("game_over") < kinds.index("luzhanqi_state") < kinds.index("lobby")


def test_out_of_band_http_style_leave_flushes_latest_members_even_without_game_over(client):
    rm.add_game_end_listener(ws_module._broadcast_lobby_after_game_end)
    with ExitStack() as stack:
        code, room, people, sockets, _ = pair(client, stack, clock=0)
        client.portal.call(rm.leave_room, code, people[0][0])
        lobby = recv(sockets[1], "lobby")["room"]
        assert lobby["status"] == "waiting"
        assert [player["id"] for player in lobby["players"]] == [people[1][0]]
