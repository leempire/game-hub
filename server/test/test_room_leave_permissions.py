"""退出权限必须在游戏分发前校验，避免非成员结算他人对局。"""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import persistence, room_manager as rm
from app.routers.rooms import router
from app.security import get_current_user_id


GAMES = (
    "gomoku", "reversi", "minesweeper", "battleship", "dots", "ludo",
    "2048", "xiangqi", "connect4", "tetris", "uno", "stasis",
    "luzhanqi", "spider", "uttt",
)
HOST = {"id": 1, "username": "host", "nickname": None}
GUEST = {"id": 2, "username": "guest", "nickname": None}
OUTSIDER_ID = 3


@pytest.fixture(autouse=True)
def isolated_rooms(monkeypatch):
    # 不启动完整应用、不访问在线数据库；记录结算调用以检查拒绝没有副作用。
    monkeypatch.setattr(rm, "_rooms", {})
    monkeypatch.setattr(persistence, "_enabled", False)
    ended = []
    monkeypatch.setattr(rm, "_notify_game_end", lambda room: ended.append(room["code"]))
    yield ended


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: OUTSIDER_ID
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("game", GAMES)
@pytest.mark.parametrize("status", ["waiting", "playing"])
def test_outsider_cannot_leave_any_game(client, isolated_rooms, game, status):
    room = rm.create_room(HOST, None, None, game)
    rm.join_room(room["code"], GUEST, None)
    room["status"] = status
    # 权限判断必须早于任何游戏状态解析：即使在开局/恢复的中间态也不能放行。
    if status == "playing":
        room["game_state"] = {"winner": None, "finished": False}
    before = deepcopy(room)

    response = client.post(f"/api/rooms/{room['code']}/leave")
    assert response.status_code == 403
    assert response.json() == {"detail": "你不在房间中"}
    assert room == before

    # 内部调用和旧 WS 连接同样不能绕过 HTTP 守卫；无房间结果避免假广播。
    assert rm.leave_room(room["code"], OUTSIDER_ID) == ("removed", None)
    assert room == before
    assert isolated_rooms == []


def test_member_leaves_waiting_and_repeated_leave_is_noop(client, isolated_rooms):
    room = rm.create_room(HOST, None, None, "gomoku")
    rm.join_room(room["code"], GUEST, None)
    client.app.dependency_overrides[get_current_user_id] = lambda: GUEST["id"]

    assert client.post(f"/api/rooms/{room['code']}/leave").status_code == 200
    assert [p["id"] for p in room["players"]] == [HOST["id"]]
    before = deepcopy(room)
    assert rm.leave_room(room["code"], GUEST["id"]) == ("removed", None)
    assert room == before
    assert isolated_rooms == []


def test_member_leaves_playing_settles_once(client, isolated_rooms):
    room = rm.create_room(HOST, None, None, "gomoku")
    rm.join_room(room["code"], GUEST, None)
    room["players"][1]["ready"] = True
    assert rm.start_game(room["code"], HOST["id"]) is not None
    client.app.dependency_overrides[get_current_user_id] = lambda: HOST["id"]

    assert client.post(f"/api/rooms/{room['code']}/leave").status_code == 200
    assert room["status"] == "waiting"
    assert room["game_state"]["winner"] == "white"
    assert room["host_id"] == GUEST["id"]
    assert isolated_rooms == [room["code"]]
    before = deepcopy(room)
    assert rm.leave_room(room["code"], HOST["id"]) == ("removed", None)
    assert room == before
    assert isolated_rooms == [room["code"]]
