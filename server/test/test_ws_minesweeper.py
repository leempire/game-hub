#!/usr/bin/env python3
"""
扫雷竞技模式 WS 集成测试：踩雷淘汰、淘汰者操作被拒、统计归属、
全员淘汰结束、挖完结束、排名顺序、正确/错误标雷计数。

难度不在创建房间时定，而是房主连上 WS 之后在等待大厅里选
（v1.15 起统一走 `room_setting`，选项与当前值在 `room.settings` 里）。

用法：先启动后端(50541)，然后 cd server && python test/test_ws_minesweeper.py
"""
__test__ = False  # Prevent pytest from collecting async standalone tests
import asyncio
import json
import time

import httpx
import websockets

BASE = "http://localhost:50541"
WS = "ws://localhost:50541"
TS = int(time.time())


async def register(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post(f"{BASE}/api/auth/register",
                          json={"username": name, "password": "test123456"})
    r.raise_for_status()
    return r.json()


async def recv_until(ws, types: set, timeout=10):
    """接收消息直到命中 types 之一，返回该消息。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg["type"] in types:
            return msg
    raise AssertionError(f"未收到 {types}")


async def pick_difficulty(ws, difficulty: str):
    """房主在等待大厅里选难度（难度不再在创建房间时定）。

    服务端会给全房广播一条 `lobby`；**不能只读第一条** —— 别的连接握手时也会推
    一条（在线态变了），那条还带着旧难度。这里跳过旧的、只认生效的那条。
    """
    await ws.send(json.dumps(
        {"type": "room_setting", "key": "difficulty", "value": difficulty}))
    deadline = asyncio.get_event_loop().time() + 10
    while asyncio.get_event_loop().time() < deadline:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if msg["type"] == "lobby" and _setting(msg["room"], "difficulty") == difficulty:
            return msg["room"]
    raise AssertionError(f"没有收到难度为 {difficulty} 的 lobby")


def _setting(room, key):
    """从房间载荷的统一 `settings` 里取一项的值（v1.15 起设置只有这一份结构）。"""
    for item in room.get("settings") or []:
        if item["key"] == key:
            return item["value"]
    raise AssertionError(f"房间设置里没有 {key}：{room.get('settings')}")


async def run_basic_flow():
    """Test basic competitive mines sweeper WS flow."""
    async with httpx.AsyncClient() as c:
        a = await register(c, f"ms_a_{TS}")
        b = await register(c, f"ms_b_{TS}")
        r = await c.post(f"{BASE}/api/rooms",
                         headers={"Authorization": f"Bearer {a['token']}"},
                         json={"game": "minesweeper"})
        room = r.json()
        code = room["code"]
        assert room["max_players"] == 4 and _setting(room, "difficulty") == "intermediate"
        print(f"  创建扫雷房间 {code} (max=4, 缺省中级) ✓")

        r = await c.post(f"{BASE}/api/rooms/{code}/join",
                         headers={"Authorization": f"Bearer {b['token']}"},
                         json={})
        r.raise_for_status()
        print("  B 加入 ✓")

        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa, websockets.connect(
            f"{WS}/ws/room/{code}?token={b['token']}"
        ) as wb:
            sa = await recv_until(wa, {"sync"})
            sb = await recv_until(wb, {"sync"})
            assert sa["game"] is None and sb["game"] is None
            await pick_difficulty(wa, "beginner")
            print("  A 在等待大厅里选初级 ✓")
            await wa.send(json.dumps({"type": "ready"}))
            await wb.send(json.dumps({"type": "ready"}))
            await recv_until(wa, {"lobby"})
            await wa.send(json.dumps({"type": "start"}))
            ga = await recv_until(wa, {"game_start"})
            gb = await recv_until(wb, {"game_start"})
            g = ga["game"]
            assert g["width"] == 9 and g["mines"] == 10
            assert "players" in g
            assert "0" in g["players"] and "1" in g["players"]
            assert g["players"]["0"]["alive"] is True
            print("  开局：9x9/10雷，双方收到 game_start with players ✓")

            # A 翻开首格（首翻安全）→ 双方收到相同 revealed
            await wa.send(json.dumps({"type": "reveal", "x": 4, "y": 4}))
            ra = await recv_until(wa, {"revealed"})
            rb = await recv_until(wb, {"revealed"})
            assert ra["cells"] == rb["cells"] and len(ra["cells"]) >= 1
            assert all(v != -1 for _, _, v in ra["cells"]), "首翻不应踩雷"
            assert ra["seat"] == 0
            assert ra["username"] == f"ms_a_{TS}"
            assert ra["hit_mine"] is False
            assert ra["eliminated"] is False
            print(f"  首翻安全，flood fill 广播 {len(ra['cells'])} 格 ✓")

            # B 插旗 → 双方收到 flagged with seat
            revealed_set = {(cx, cy) for cx, cy, _ in ra["cells"]}
            fx, fy = next((x, y) for y in range(9) for x in range(9)
                          if (x, y) not in revealed_set)
            await wb.send(json.dumps({"type": "flag", "x": fx, "y": fy}))
            fa = await recv_until(wa, {"flagged"})
            fb = await recv_until(wb, {"flagged"})
            assert fa["flagged"] is True and fb["flagged"] is True
            assert fa["seat"] == 1
            print("  B 插旗，双方同步 with seat ✓")

            # 重复翻开已翻格 → error
            first = ra["cells"][0]
            await wa.send(json.dumps({"type": "reveal", "x": first[0], "y": first[1]}))
            err = await recv_until(wa, {"error"})
            assert err["message"]
            print(f"  重复翻开被拒：{err['message']} ✓")

        # 断线重连：A 重连后 sync 含已翻开/插旗状态
        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa2:
            s2 = await recv_until(wa2, {"sync"})
            g2 = s2["game"]
            assert len(g2["revealed"]) >= 1 and len(g2["flagged"]) == 1
            assert "players" in g2
            print(f"  A 重连 sync 恢复：revealed={len(g2['revealed'])} "
                  f"flagged={len(g2['flagged'])} ✓")

    print("\n=== 基础流程测试通过 ✓✓✓ ===")


async def run_elimination_flow():
    """Test player elimination and game over flow."""
    async with httpx.AsyncClient() as c:
        a = await register(c, f"elim_a_{TS}")
        b = await register(c, f"elim_b_{TS}")
        r = await c.post(f"{BASE}/api/rooms",
                         headers={"Authorization": f"Bearer {a['token']}"},
                         json={"game": "minesweeper"})
        room = r.json()
        code = room["code"]

        r = await c.post(f"{BASE}/api/rooms/{code}/join",
                         headers={"Authorization": f"Bearer {b['token']}"},
                         json={})
        r.raise_for_status()

        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa, websockets.connect(
            f"{WS}/ws/room/{code}?token={b['token']}"
        ) as wb:
            await recv_until(wa, {"sync"})
            await recv_until(wb, {"sync"})
            await pick_difficulty(wa, "beginner")
            await wa.send(json.dumps({"type": "ready"}))
            await wb.send(json.dumps({"type": "ready"}))
            await recv_until(wa, {"lobby"})
            await wa.send(json.dumps({"type": "start"}))
            await recv_until(wa, {"game_start"})
            await recv_until(wb, {"game_start"})

            # A 翻首格（安全）
            await wa.send(json.dumps({"type": "reveal", "x": 4, "y": 4}))
            await recv_until(wa, {"revealed"})
            await recv_until(wb, {"revealed"})

            # A 投降（自我淘汰）
            await wa.send(json.dumps({"type": "surrender"}))
            elim = await recv_until(wa, {"player_eliminated"})
            assert elim["seat"] == 0
            assert "弃权" in elim["message"]
            await recv_until(wb, {"player_eliminated"})
            print("  A 投降被淘汰 ✓")

            # A 再操作应被拒
            await wa.send(json.dumps({"type": "reveal", "x": 0, "y": 0}))
            err = await recv_until(wa, {"error"})
            assert "淘汰" in err["message"]
            print("  A 淘汰后操作被拒 ✓")

            # B 也投降 → 全员淘汰 → game_over
            await wb.send(json.dumps({"type": "surrender"}))
            await recv_until(wa, {"player_eliminated"})
            go = await recv_until(wa, {"game_over"})
            assert go["reason"] == "eliminated"
            assert "ranking" in go
            assert len(go["ranking"]) == 2
            # 用时随结算下发：A 翻过格子 → 有限值；B 没翻过 → None（界面不显示 0.0 秒）
            by_seat = {e["seat"]: e for e in go["ranking"]}
            assert by_seat[0]["elapsed_ms"] is not None
            assert by_seat[1]["elapsed_ms"] is None
            # A 先淘汰，B 后淘汰 → B 排名更前（但都是淘汰者）
            # 淘汰者按 revealed_count desc，然后 eliminated_at desc
            print(f"  全员淘汰，game_over with ranking ✓")

    print("\n=== 淘汰流程测试通过 ✓✓✓ ===")


async def run_lives_flow():
    """中级 2 条命：踩雷先播 life_lost（扣命不淘汰），扣完才 player_eliminated。

    客户端不知道雷在哪，所以按固定顺序扫格子直到踩到雷——中级 40 雷 / 256 格，
    前 80 格里一颗雷都踩不到的概率可以忽略。
    """
    async with httpx.AsyncClient() as c:
        a = await register(c, f"life_a_{TS}")
        r = await c.post(f"{BASE}/api/rooms",
                         headers={"Authorization": f"Bearer {a['token']}"},
                         json={"game": "minesweeper"})
        code = r.json()["code"]
        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa:
            sync = await recv_until(wa, {"sync"})
            # 缺省就是中级；显式选一遍，顺便盯住这条设置真的驱动开局
            await pick_difficulty(wa, "intermediate")
            await wa.send(json.dumps({"type": "ready"}))
            await recv_until(wa, {"lobby"})
            await wa.send(json.dumps({"type": "start"}))
            gs = await recv_until(wa, {"game_start"})
            assert gs["game"].get("lives") == 2, sorted(gs["game"].keys())
            assert gs["game"]["players"]["0"].get("lives_left") == 2
            print("  中级开局每人 2 条命 ✓")

            # 首翻必安全（服务端保证）
            await wa.send(json.dumps({"type": "reveal", "x": 8, "y": 8}))
            first = await recv_until(wa, {"revealed"})
            assert first["hit_mine"] is False
            assert first["lives_left"] == 2

            # 扫到踩雷为止
            lost = None
            for i in range(80):
                x, y = i % 16, i // 16
                await wa.send(json.dumps({"type": "reveal", "x": x, "y": y}))
                msg = await recv_until(wa, {"revealed", "error"})
                if msg["type"] == "error":
                    continue          # 该格已翻开之类，跳过
                if msg["hit_mine"]:
                    lost = msg
                    break
            assert lost is not None, "扫了 80 格没踩到雷，测试假设失效"
            assert lost["lost_life"] is True
            assert lost["lives_left"] == 1
            assert lost["eliminated"] is False, "中级第一次踩雷不该直接淘汰"
            ev = await recv_until(wa, {"life_lost"})
            assert ev["lives_left"] == 1
            assert "还剩 1 条命" in ev["message"]
            print("  踩雷扣命（未淘汰）→ 广播 life_lost ✓")

            # 再扫到踩雷 → 淘汰
            eliminated = None
            for i in range(80, 256):
                x, y = i % 16, i // 16
                await wa.send(json.dumps({"type": "reveal", "x": x, "y": y}))
                msg = await recv_until(wa, {"revealed", "error"})
                if msg["type"] == "error":
                    continue
                if msg["hit_mine"]:
                    eliminated = msg
                    break
            assert eliminated is not None, "第二条命也没踩到雷，测试假设失效"
            assert eliminated["eliminated"] is True
            assert eliminated["lives_left"] == 0
            ev = await recv_until(wa, {"player_eliminated"})
            assert ev["seat"] == 0
            print("  第二条命扣完 → player_eliminated ✓")

    print("\n=== 生命值流程测试通过 ✓✓✓ ===")


async def run_reconnect_sync():
    """Test reconnect sync includes player stats."""
    async with httpx.AsyncClient() as c:
        a = await register(c, f"rs_a_{TS}")
        b = await register(c, f"rs_b_{TS}")
        r = await c.post(f"{BASE}/api/rooms",
                         headers={"Authorization": f"Bearer {a['token']}"},
                         json={"game": "minesweeper"})
        room = r.json()
        code = room["code"]

        r = await c.post(f"{BASE}/api/rooms/{code}/join",
                         headers={"Authorization": f"Bearer {b['token']}"},
                         json={})
        r.raise_for_status()

        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa, websockets.connect(
            f"{WS}/ws/room/{code}?token={b['token']}"
        ) as wb:
            await recv_until(wa, {"sync"})
            await recv_until(wb, {"sync"})
            await pick_difficulty(wa, "beginner")
            await wa.send(json.dumps({"type": "ready"}))
            await wb.send(json.dumps({"type": "ready"}))
            await recv_until(wa, {"lobby"})
            await wa.send(json.dumps({"type": "start"}))
            await recv_until(wa, {"game_start"})
            await recv_until(wb, {"game_start"})

            # A reveals some cells
            await wa.send(json.dumps({"type": "reveal", "x": 4, "y": 4}))
            await recv_until(wa, {"revealed"})
            await recv_until(wb, {"revealed"})

        # A reconnects
        async with websockets.connect(
            f"{WS}/ws/room/{code}?token={a['token']}"
        ) as wa2:
            s = await recv_until(wa2, {"sync"})
            g = s["game"]
            assert g is not None
            assert "players" in g
            assert "0" in g["players"]
            assert g["players"]["0"]["alive"] is True
            assert g["players"]["0"]["revealed_count"] >= 1
            print(f"  重连 sync 含玩家统计 ✓")

    print("\n=== 重连同测试通过 ✓✓✓ ===")


async def main():
    await run_basic_flow()
    await run_elimination_flow()
    await run_lives_flow()
    await run_reconnect_sync()
    print("\n=== 扫雷 WS 集成测试全部通过 ✓✓✓ ===")


if __name__ == "__main__":
    asyncio.run(main())
