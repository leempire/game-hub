"""2048 断线超时：逐人结束、完整终局广播及战绩。"""

import asyncio
from contextlib import ExitStack

import pytest

from app import database, persistence, room_manager as rm, security, ws as ws_module


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "2048.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, "_jwt_secret", "isolated-2048-test-secret-at-least-32-bytes")
    for name in ("_rooms", "_dc_tasks", "_bot_tasks"):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(rm, "_game_end_callback", database.save_game_record)
    monkeypatch.setattr(rm, "_game_end_listeners", [])
    monkeypatch.setattr(rm, "_dc_timeout_callback", None)
    monkeypatch.setattr(ws_module, "_connections", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    ws_module._register_dc_timeout_callback()
    return ws_client


def recv(socket, kind):
    for _ in range(30):
        message = socket.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"missing {kind}")


def start(client, stack, count):
    users, sockets = [], []
    for index in range(count):
        response = client.post("/api/auth/register", json={
            "username": f"timeout_{index}", "password": "test123456",
        })
        assert response.status_code == 200
        users.append(response.json())
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    response = client.post("/api/rooms", json={"game": "2048"}, headers=headers[0])
    assert response.status_code == 200
    code = response.json()["code"]
    for header in headers[1:]:
        assert client.post(f"/api/rooms/{code}/join", json={}, headers=header).status_code == 200
    for user in users:
        socket = stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={user['token']}"))
        recv(socket, "sync")
        sockets.append(socket)
    for socket in sockets[1:]:
        socket.send_json({"type": "ready"})
    sockets[0].send_json({"type": "start"})
    for socket in sockets:
        recv(socket, "game_start")
    return code, [user["user"]["id"] for user in users], sockets


@pytest.mark.parametrize("count", [1, 2, 4])
def test_timeout_finishes_only_player_then_broadcasts_complete_ranking(client, count):
    with ExitStack() as stack:
        code, ids, sockets = start(client, stack, count)
        room = rm.get_room(code)
        gs = room["game_state"]
        for index, user_id in enumerate(ids):
            client.portal.call(rm._on_dc_timeout, code, user_id)
            for socket in sockets:
                message = recv(socket, "move2048")
                assert message["user_id"] == user_id
                assert message["player_finished"] == "disconnect"
                assert not {"board", "spawned", "trace"} & message.keys()
                finished = {row["user_id"] for row in message["leaderboard"] if row["finished"]}
                assert finished == set(ids[:index + 1])
            assert gs["finish_order"] == ids[:index + 1]
            if index < count - 1:
                assert room["status"] == "playing" and gs["winner"] is None
                assert database.get_user_history(user_id)["total"] == 0
                # 未超时的玩家仍能真实发包走子；私有盘面只给操作者。
                next_id = ids[index + 1]
                gs["boards"][next_id] = [[0, 0, 2, 0]] + [[0] * 4 for _ in range(3)]
                sockets[index + 1].send_json({"type": "move2048", "direction": "left"})
                for seat, socket in enumerate(sockets):
                    moved = recv(socket, "move2048")
                    assert moved["user_id"] == next_id
                    assert ("board" in moved) == (seat == index + 1)
                # 已结束者再次触发超时不改变结束顺序，也不重发终局。
                client.portal.call(rm._on_dc_timeout, code, user_id)
                sockets[-1].send_json({"type": "ping"})
                assert sockets[-1].receive_json()["type"] == "pong"
                assert gs["finish_order"] == ids[:index + 1]
        assert room["status"] == "waiting"
        assert all(gs["game_over_flags"].values())
        for socket in sockets:
            final = recv(socket, "game_over")
            assert final["reason"] == "ranking"
            assert final["ranking"] == gs["ranking"]
            assert len(final["ranking"]) == count
            assert {row["finish_reason"] for row in final["ranking"]} == {"disconnect"}
        for user_id in ids:
            history = database.get_user_history(user_id)
            assert history["total"] == 1
            assert history["items"][0]["snapshot"]["ranking"] == gs["ranking"]
            assert all(row["result"] == "end" for row in history["items"][0]["results"])


def test_timeout_preserves_already_completed_player(client):
    with ExitStack() as stack:
        code, ids, sockets = start(client, stack, 2)
        gs = rm.get_room(code)["game_state"]
        gs["boards"][ids[0]] = [[1024, 1024, 0, 0]] + [[0] * 4 for _ in range(3)]
        sockets[0].send_json({"type": "move2048", "direction": "left"})
        for socket in sockets:
            recv(socket, "move2048")
        client.portal.call(rm._on_dc_timeout, code, ids[0])
        assert gs["finish_reasons"][ids[0]] == "reach_2048"
        assert gs["finish_order"] == [ids[0]]
        client.portal.call(rm._on_dc_timeout, code, ids[1])
        final = recv(sockets[0], "game_over")
        assert final["ranking"][0]["user_id"] == ids[0]
        assert final["ranking"][0]["finish_reason"] == "reach_2048"
        assert final["ranking"][1]["finish_reason"] == "disconnect"


def test_same_turn_timeouts_capture_each_state_and_broadcast_one_game_over(client):
    with ExitStack() as stack:
        code, ids, sockets = start(client, stack, 2)

        def timeout_both():
            # 同一个事件循环回合内连续改变状态，两个发送任务此时都尚未执行。
            for user_id in ids:
                rm._on_dc_timeout(code, user_id)

        client.portal.call(timeout_both)
        for socket in sockets:
            first = recv(socket, "move2048")
            second = recv(socket, "move2048")
            assert [first["user_id"], second["user_id"]] == ids
            assert [row["finished"] for row in first["leaderboard"]] == [True, False]
            assert all(row["finished"] for row in second["leaderboard"])
            assert len(recv(socket, "game_over")["ranking"]) == 2
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "pong", "每局只能广播一次终局"


def test_delayed_timeout_callbacks_do_not_reach_next_round(client, monkeypatch):
    with ExitStack() as stack:
        code, ids, sockets = start(client, stack, 2)
        callback = rm._dc_timeout_callback
        gate = client.portal.call(asyncio.Event)
        tasks = []

        async def delayed(*args):
            tasks.append(asyncio.current_task())
            await gate.wait()
            await callback(*args)

        monkeypatch.setattr(rm, "_dc_timeout_callback", delayed)
        old_state = rm.get_room(code)["game_state"]
        for user_id in ids:
            client.portal.call(rm._on_dc_timeout, code, user_id)
        assert rm.get_room(code)["status"] == "waiting"
        sockets[1].send_json({"type": "ready"})
        sockets[0].send_json({"type": "start"})
        for socket in sockets:
            fresh = recv(socket, "game_start")["game"]
            assert fresh["game_over"] is False
        room = rm.get_room(code)
        assert room["game_state"] is not old_state

        async def release():
            await asyncio.sleep(0)
            gate.set()
            await asyncio.gather(*tasks)

        client.portal.call(release)
        for socket in sockets:
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "pong", "旧局事件不得发到续局"
        assert room["status"] == "playing"
        assert not any(room["game_state"]["game_over_flags"].values())
