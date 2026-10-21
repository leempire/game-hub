"""2048 撤销：只在单人局开放，回滚盘面 / 分数 / 步数（RNG 不回滚）。

撤销是房间层的行为（引擎 `move()` 是无状态纯函数），所以用例直接搭一个最小房间
调 `_start_2048` / `_game2048_make_move` / `_game2048_undo`——不起 WS，毫秒级。

Run: pytest test/test_2048_undo.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm

CODE = "T2048"


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    yield
    rm._rooms.clear()


def _make_room(n_players: int) -> dict:
    """搭一个够 `_start_2048` 用的房间（不经过 create_room，避免随机房号）。"""
    players = [
        {"id": i + 1, "username": f"p{i + 1}", "nickname": None,
         "ready": True, "is_host": i == 0, "connected": True, "is_bot": False}
        for i in range(n_players)
    ]
    room = {
        "code": CODE, "game": "2048", "players": players,
        "host_id": 1, "status": "waiting", "max_players": n_players,
        "game_state": None, "created_at": rm._now_iso(),
        "last_activity": time.time(), "empty_since": None, "swap_requests": [],
    }
    rm._rooms[CODE] = room
    rm._start_2048(room)
    return room


class TestUndo:
    def test_undo_restores_board_score_and_move_count(self):
        room = _make_room(1)
        gs = room["game_state"]
        pid = 1
        before = [row[:] for row in gs["boards"][pid]]
        assert rm._game2048_make_move(CODE, pid, "left").get("ok") or \
            rm._game2048_make_move(CODE, pid, "right").get("ok") or \
            rm._game2048_make_move(CODE, pid, "up").get("ok") or \
            rm._game2048_make_move(CODE, pid, "down").get("ok"), "开局应至少有一个方向能走"
        assert gs["move_counts"][pid] == 1
        res = rm._game2048_undo(CODE, pid)
        assert res["ok"], res
        assert res["board"] == before, "撤销应回到走子前的盘面"
        assert res["score"] == 0
        assert res["move_count"] == 0
        assert res["undo_available"] is False

    def test_undo_with_empty_stack_returns_chinese_error(self):
        _make_room(1)
        res = rm._game2048_undo(CODE, 1)
        assert res["ok"] is False
        assert "撤销" in res["error"]

    def test_undo_is_disabled_in_multiplayer(self):
        room = _make_room(2)
        gs = room["game_state"]
        # 两个人都各走一步（尽力找一个能走的方向）
        for pid in (1, 2):
            for direction in ("left", "right", "up", "down"):
                if rm._game2048_make_move(CODE, pid, direction).get("ok"):
                    break
            assert gs["undo_stacks"][pid] == [], "多人局不维护撤销栈"
        res = rm._game2048_undo(CODE, 1)
        assert res["ok"] is False
        assert "多人" in res["error"]

    def test_undo_stack_is_capped(self):
        room = _make_room(1)
        gs = room["game_state"]
        pid = 1
        # 反复走能走的方向，直到栈满（上限 20）
        for _ in range(rm.G2048_MAX_UNDO + 5):
            for direction in ("left", "right", "up", "down"):
                if rm._game2048_make_move(CODE, pid, direction).get("ok"):
                    break
        assert len(gs["undo_stacks"][pid]) == rm.G2048_MAX_UNDO
