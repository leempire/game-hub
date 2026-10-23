"""海战棋两条房规（v1.18.1）：舰船不可相邻 / 击沉不报舰种。

这两条都走统一房间设置注册表。这里锁三件事：
① 设置真的写进开局对局态；② 布阵校验按房规裁决且报错说人话；
③ 击沉不报舰种时，**下发给对手的 sync 里连舰种字段都没有**（不是只让客户端不显示）。

Run: pytest test/test_battleship_rules.py -v
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app.game import battleship as bs

CODE = "TBS1"


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    yield
    rm._rooms.clear()


def _make_room(**settings) -> dict:
    players = [
        {"id": i + 1, "username": f"bs{i + 1}", "nickname": None,
         "ready": True, "is_host": i == 0, "connected": True, "is_bot": False}
        for i in range(2)
    ]
    room = {
        "code": CODE, "game": "battleship", "players": players,
        "host_id": 1, "status": "waiting", "max_players": 2,
        "game_state": None, "created_at": rm._now_iso(),
        "last_activity": time.time(), "empty_since": None, "swap_requests": [],
        "bs_size": 10,
    }
    room.update(settings)
    rm._rooms[CODE] = room
    rm._start_battleship(room)
    return room


def _spread() -> list[dict]:
    """每隔一行放一艘：正常合法、且没有两艘相邻。"""
    return [
        {"id": 1, "x": 0, "y": 0, "horizontal": True},
        {"id": 2, "x": 0, "y": 2, "horizontal": True},
        {"id": 3, "x": 0, "y": 4, "horizontal": True},
        {"id": 4, "x": 0, "y": 6, "horizontal": True},
        {"id": 5, "x": 0, "y": 8, "horizontal": True},
    ]


def _tight() -> list[dict]:
    ships = _spread()
    ships[1] = {"id": 2, "x": 0, "y": 1, "horizontal": True}
    return ships


class TestRulesWiring:
    def test_settings_reach_the_game_state(self):
        room = _make_room(bs_adjacent=True, bs_hide_sunk_type=True)
        gs = room["game_state"]
        assert gs["adjacent_placement"] is True
        assert gs["hide_sunk_ship_type"] is True

    def test_no_touch_rejects_touching_placement_with_chinese_error(self):
        _make_room(bs_adjacent=True)
        res = rm._battleship_set_placement(CODE, 1, _tight())
        assert res["ok"] is False
        assert "相邻" in res["error"]
        ok = rm._battleship_set_placement(CODE, 1, _spread())
        assert ok["ok"] is True

    def test_default_rules_keep_adjacent_placement_legal(self):
        _make_room()
        assert rm._battleship_set_placement(CODE, 1, _tight())["ok"] is True

    def test_sync_omits_ship_type_when_hidden(self):
        room = _make_room(bs_hide_sunk_type=True)
        gs = room["game_state"]
        # 对手（id=2）按 spread 布阵；把 5 号舰打沉
        ships = _spread()
        gs["placements"][2] = ships
        bs.apply_placement(gs["boards"][2], ships)
        # 1 号玩家打向对手（2 号）的炮：game_to_sync 里 own_attack = attack_boards[opp]
        attack = gs["attack_boards"][2]
        for cx, cy in bs.get_ship_cells(gs["boards"][2], 5):
            attack[cy][cx] = bs.HIT
        sync = bs.game_to_sync(gs, 1)
        sunk = [s for s in sync["opp_ships"] if s.get("sunk")]
        assert len(sunk) == 1
        assert "id" not in sunk[0], "击沉不报舰种：sync 里不能带舰种"
        assert len(sunk[0]["cells"]) == 2, "位置仍要下发（用于标出沉船）"
        assert sync["rules"]["hide_sunk_ship_type"] is True

    def test_weapon_shots_strip_ship_id_when_hidden(self):
        """武器的逐格结果也走同一条「不报舰种」规则（不是只在单发 shot 上做）。"""
        from app.ws import _public_weapon_shots
        shots = [{"x": 1, "y": 2, "result": "sunk", "ship_id": 3, "sunk_cells": [[1, 2]]}]
        hidden = _public_weapon_shots(shots, True)
        assert "ship_id" not in hidden[0]
        assert hidden[0]["result"] == "sunk" and hidden[0]["sunk_cells"] == [[1, 2]]
        assert _public_weapon_shots(shots, False)[0]["ship_id"] == 3

    def test_sync_keeps_ship_type_by_default(self):
        room = _make_room()
        gs = room["game_state"]
        ships = _spread()
        gs["placements"][2] = ships
        bs.apply_placement(gs["boards"][2], ships)
        # 1 号玩家打向对手（2 号）的炮：game_to_sync 里 own_attack = attack_boards[opp]
        attack = gs["attack_boards"][2]
        for cx, cy in bs.get_ship_cells(gs["boards"][2], 5):
            attack[cy][cx] = bs.HIT
        sync = bs.game_to_sync(gs, 1)
        sunk = [s for s in sync["opp_ships"] if s.get("sunk")]
        assert sunk and sunk[0]["id"] == 5
        assert sync["rules"]["hide_sunk_ship_type"] is False
