"""深空休眠 tick 的两条时序保证（v1.18，卡顿排查）。

1. **一个卡住的连接拖不住整个房间**：tick 广播挂到逐连接的发送链上，链上还有
   没发完的快照就丢掉这一帧；状态 / 事件 / 终局必达且保序。
2. **tick 按截止时间排**：处理耗时不再累加进间隔（原来每轮固定 sleep 200ms，
   Windows 上实测平均 205ms，游戏时间比真实时间慢 2.6%）。
"""

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app import stasis as stasis_engine
from app import ws as wsmod


class _FakeWS:
    """记录收到的消息；`stuck=True` 时 `send_json` 永远不返回（对端不读、缓冲满）。"""

    def __init__(self, stuck=False):
        self.stuck = stuck
        self.got = []
        self.release = asyncio.Event()

    async def send_json(self, msg):
        if self.stuck:
            await self.release.wait()
        self.got.append(msg["type"])


def _run(coro):
    """跑在**私有**事件循环里，不用 `asyncio.run`：后者退出时会把主线程的当前
    循环置空，之后别的用例在同步代码里 `asyncio.get_event_loop()` 会直接抛错
    （实测把排在后面的军棋 WS 用例搞挂、还连带卡死整个套件）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _clean():
    rm._rooms.clear()
    wsmod._connections.clear()
    wsmod._send_tails.clear()
    yield
    rm._rooms.clear()
    wsmod._connections.clear()
    wsmod._send_tails.clear()


def _room(code, n_humans=2):
    uids = list(range(1, n_humans + 1))
    gs = stasis_engine.create_game(uids, seed=1, map_id="dorm")
    room = {
        "code": code, "game": "stasis", "status": "playing", "host_id": 1,
        "players": [{"id": u, "username": f"u{u}", "connected": True, "ready": True}
                    for u in uids],
        "game_state": gs, "empty_since": None, "last_activity": time.time(),
    }
    rm._rooms[code] = room
    return room


def test_stuck_connection_does_not_block_tick_and_drops_only_ticks():
    async def run():
        room = _room("SND001")
        ok, stuck = _FakeWS(), _FakeWS(stuck=True)
        wsmod._connections["SND001"] = {1: ok, 2: stuck}

        # 10 帧 tick + 中间夹一条事件：整个过程**不能**因为卡住的连接而停住
        for i in range(10):
            await asyncio.wait_for(wsmod.broadcast_stasis_tick("SND001", room), 0.5)
            if i == 3:
                await asyncio.wait_for(
                    wsmod.broadcast_stasis_events("SND001", [{"type": "entity_evolve"}]), 0.5)
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)

        assert ok.got.count("stasis_tick") == 10, "健康的连接一帧都不该丢"
        assert ok.got.index("stasis_events") == 4, "事件要排在第 4 帧 tick 之后"
        assert stuck.got == [], "卡住的连接什么都还没发出去"

        # 恢复：链上只有「卡住的第一帧 tick + 那条事件」，中间的快照都被丢了
        stuck.stuck = False
        stuck.release.set()
        await asyncio.sleep(0.01)
        assert stuck.got == ["stasis_tick", "stasis_events"], stuck.got

    _run(run())


def test_direct_send_queues_behind_pending_chain():
    """终局挂上链后紧接着直接发 lobby：lobby 不能抢在 game_over 前面。"""
    async def run():
        room = _room("SND002")
        ws1 = _FakeWS()
        wsmod._connections["SND002"] = {1: ws1}
        await wsmod.broadcast_stasis_game_over(room, [])
        await wsmod._broadcast("SND002", {"type": "lobby"})
        await asyncio.sleep(0.01)
        assert ws1.got == ["game_over", "lobby"], ws1.got

    _run(run())


def test_tick_interval_does_not_accumulate_processing_time(monkeypatch):
    """每轮处理故意耗时 40ms：按截止时间排的话平均间隔仍是 200ms（原来是 240ms）。"""
    calls = []

    def slow_advance(gs, dt_ms, rng):
        calls.append(time.perf_counter())
        time.sleep(0.04)
        return []

    monkeypatch.setattr(rm.stasis_engine, "advance", slow_advance)
    monkeypatch.setattr(rm, "_stasis_bot_think", lambda room, gs: None)
    monkeypatch.setattr(rm.persistence, "save_room", lambda room: None)

    async def run():
        _room("SND003")
        task = asyncio.get_running_loop().create_task(rm._stasis_tick_loop("SND003"))
        await asyncio.sleep(2.25)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(run())
    assert len(calls) >= 9, f"2.25 秒里只跑了 {len(calls)} 个 tick"
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    mean_ms = sum(gaps) / len(gaps) * 1000
    assert mean_ms < 212, f"平均 tick 间隔 {mean_ms:.1f}ms：处理耗时又累加进去了"
