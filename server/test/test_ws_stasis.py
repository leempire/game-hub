"""深空休眠 WebSocket 集成测试（FastAPI TestClient）。

覆盖：建房/人数校验（v2 是 2-6 人）、逐人下发 game_start、**服务端 tick 真的在推进**、
移动输入被 tick 积分、升级/修补的广播与错误文案、认输→遗产转移、sync 形状、
tick 的生命周期（终局自净 / 全员离线暂停 / 热重启重排）。

**这是本项目第一个有服务端 tick 的游戏**，所以有两条别处没有的不变量要盯住：
1. **tick 必须在跑**（房间进 playing 就有任务），且**房间回到 waiting 后要自净**
   ——漏掉就是永不退出的协程。
2. **tick 只在有真人在线时推进**（游戏时钟冻结），否则「挂着 10 分钟回来发现
   救援舰已经到了」。

v2 的变化：跑动阶段不再需要客户端发 `pick_room`（引擎按 `run_ms` 倒计时自己走完），
玩家位置由 `stasis_move` 的方向 + tick 积分决定。

Run: pytest test/test_ws_stasis.py -v
"""

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app import ws as wsmod


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    for task in list(rm._tick_tasks.values()):
        task.cancel()
    rm._tick_tasks.clear()
    yield
    for task in list(rm._tick_tasks.values()):
        task.cancel()
    rm._tick_tasks.clear()


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


def _name(prefix="st", suffix="") -> str:
    """用户名**必须每次调用都不同**：DB 是持久的，重复注册会 409。

    其它 WS 用例文件用的是 `int(time.time()*1000) % 100000`，那个每 100 秒就绕回来
    —— 同一个文件在 100 秒内跑两遍必然 409（本地反复调试时天天踩）。
    """
    return f"{prefix}_{int(time.time() * 1000) % 100000}_{uuid.uuid4().hex[:6]}{suffix}"


def _create_room(client, token, game="stasis"):
    r = client.post("/api/rooms", json={"game": game},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _join_room(client, token, code):
    r = client.post(f"/api/rooms/{code}/join", json={},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return r.json()


def _recv_type(ws, msg_type, max_skip=400):
    """收到指定类型为止。

    `max_skip` 给得很大：这个游戏每 200ms 一条 `stasis_tick`，慢路径上会淹掉
    几十条其它消息。
    """
    for _ in range(max_skip):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive {msg_type} after {max_skip} messages")


def _setup(client, players=2, ready=True):
    """建房 → 加人 → 连 WS → 准备 → 开始（返回停在**跑动阶段**的局）。"""
    tokens, uids = [], []
    for i in range(players):
        t, uid = _register(client, _name("st", str(i)))
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
        while True:
            lobby = _recv_type(ws, "lobby")["room"]
            if all(p["id"] == lobby["host_id"] or p["ready"] for p in lobby["players"]):
                break
    if ready:
        wss[0].send_json({"type": "start"})
        deadline = time.monotonic() + 3
        while code not in rm._tick_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert code in rm._tick_tasks, "服务端尚未完成开局"
    return wss, code, tokens, uids


def _close(wss):
    for ws in wss:
        try:
            ws.__exit__(None, None, None)
        except Exception:
            pass


def _skip_run(room):
    """把跑动倒计时压到 1ms，下一 tick 就进 playing（不用真等 30 秒）。"""
    room["game_state"]["run_ms"] = 1


def _make_lying(gs, uid):
    """把玩家置成「已躺下」。

    **v4：建造 / 升级 / 拆除 / 修补都要求已经躺下**（`stasis._require_lying`），
    否则引擎一律抛「要先躺进休眠舱才能建造与升级」。这里直接改状态（不走 wire）
    是为了让用例只盯它自己要测的那件事；「lie 走 wire 会不会广播」另有专门的用例。
    """
    gs["players"][uid]["lying"] = True
    gs["players"][uid]["in_bed"] = True


def _payload_of(msg: dict) -> dict | None:
    """取消息里的对局载荷。

    `game_start` 套在 `msg.game` 里（全项目惯例）；`stasis_state` / `stasis_tick`
    是**扁平**的，载荷就是消息本身（注意它自带一个字符串字段 `game: "stasis"`）。
    """
    if msg["type"] == "game_start":
        return msg.get("game")
    if msg["type"] in ("stasis_state", "stasis_tick"):
        return msg
    return None


def _wait_phase(ws, phase="playing", max_msgs=200):
    """等到 phase 变成指定值（收 tick 或 state 都行）。"""
    for _ in range(max_msgs):
        msg = ws.receive_json()
        payload = _payload_of(msg)
        if payload and payload.get("phase") == phase:
            return payload
    raise AssertionError(f"phase 没有在超时内变成 {phase}")


def _to_playing(wss, code):
    """跳过跑动阶段并等到 playing（返回 room）。"""
    room = rm.get_room(code)
    _skip_run(room)
    _wait_phase(wss[0], "playing")
    return room


# ── 建房与开局 ──────────────────────────────────────────────


class TestStart:
    def test_room_creation(self, client):
        t, _ = _register(client, _name("sc"))
        room = _create_room(client, t)
        assert room["game"] == "stasis"
        assert room["max_players"] == 6          # v2 是 2-6 人

    def test_game_start_is_per_person(self, client):
        """逐人下发：your_seat / your_uid 必须各人对各人。

        全员同一份会让客户端认不出自己（同飞行棋 your_seat、2048 your_color 的教训）。
        """
        wss, code, tokens, uids = _setup(client, players=3)
        try:
            starts = [_recv_type(ws, "game_start") for ws in wss]
            seats = [s["game"]["your_seat"] for s in starts]
            assert seats == [0, 1, 2]
            assert [s["game"]["your_uid"] for s in starts] == uids
            g = starts[0]["game"]
            assert g["phase"] == "run"           # 开局是跑动抢舱室，不再是 "pick"
            assert g["run_ms"] == rm.stasis_engine.RUN_MS
            assert g["game_ms"] == rm.stasis_engine.GAME_MS
            # v9：几何整张由服务端下发（`rows` 是格子网格），房型分配不再是
            # 客户端唯一能拿到的地图信息。
            assert g["map"]["id"] == "dorm"
            assert len(g["map"]["rows"]) == 30 and len(g["map"]["rows"][0]) == 25
            assert len(g["map"]["rooms"]) == 12
            assert g["map"]["airlock"] == [7, 0]
            assert len(g["seat_colors"]) == 3
            # 玩家带位置（v2 才有），出生在气闸下方的通道里
            for uid in uids:
                p = g["players"][str(uid)]
                assert p["room"] is None
                assert p["pos"][0] % 1 == 0.5 and p["pos"][1] % 1 == 0.5
        finally:
            _close(wss)

    def test_solo_cannot_start(self, client):
        """合作塔防至少要 2 人（人机也算）——1 个人守一层楼没有意义。"""
        t, _ = _register(client, _name("ss"))
        room = _create_room(client, t)
        code = room["code"]
        ws = client.websocket_connect(f"/ws/room/{code}?token={t}")
        ws.__enter__()
        try:
            _recv_type(ws, "sync")
            ws.send_json({"type": "start"})
            msg = _recv_type(ws, "error")
            assert "无法开始" in msg["message"]
        finally:
            ws.__exit__(None, None, None)


# ── 服务端 tick（本作独有）──────────────────────────────────


class TestTick:
    def test_tick_advances_the_game_clock(self, client):
        wss, code, _, _ = _setup(client)
        try:
            _skip_run(rm.get_room(code))
            first = _wait_phase(wss[0], "playing")
            assert first["clock_ms"] == 0
            time.sleep(1.2)
            gs = rm.get_room(code)["game_state"]
            assert gs["clock_ms"] > first["clock_ms"]
            assert gs["phase"] == "playing"
        finally:
            _close(wss)

    def test_run_phase_ends_by_itself_without_client_input(self, client):
        """跑动阶段是**引擎自己按 run_ms 倒计时**走完的 —— v1 那个
        「客户端发 pick_room、服务端 resolve_pick」的协议已经删了。"""
        wss, code, _, uids = _setup(client)
        try:
            gs = rm.get_room(code)["game_state"]
            assert gs["phase"] == "run"
            _skip_run(rm.get_room(code))
            payload = _wait_phase(wss[0], "playing")
            assert payload["run_ms"] == 0
            gs = rm.get_room(code)["game_state"]
            assert gs["clock_ms"] == 0
            for uid in uids:
                assert gs["players"][uid]["room"], "没占到房的人会被自动分配"
        finally:
            _close(wss)

    def test_tick_task_exists_while_playing_and_dies_after(self, client):
        """**tick 的生命周期**：playing 时有任务，回 waiting 后要自净。

        漏掉自净就是「永不退出的协程」——正是 tetris.py 点名不做 tick 的理由之一。
        """
        wss, code, _, _ = _setup(client)
        try:
            assert code in rm._tick_tasks
            assert not rm._tick_tasks[code].done()
            _to_playing(wss, code)
            time.sleep(0.4)
            # 全员弃守 → 全灭 → 房间回 waiting
            for ws in wss:
                ws.send_json({"type": "surrender"})
            deadline = time.time() + 3.0
            while time.time() < deadline and rm.get_room(code)["status"] == "playing":
                time.sleep(0.1)
            assert rm.get_room(code)["status"] == "waiting"
            deadline = time.time() + 2.0
            while time.time() < deadline and code in rm._tick_tasks:
                time.sleep(0.05)
            assert code not in rm._tick_tasks, "终局后 tick 任务必须自净"
        finally:
            _close(wss)

    def test_clock_freezes_when_everyone_is_offline(self, client):
        """全员离线时只睡不推进 —— 游戏时钟冻结，重连后无缝继续。"""
        wss, code, _, _ = _setup(client)
        try:
            _to_playing(wss, code)
            time.sleep(0.5)
            # 关掉所有连接（保座计时照走，但 tick 应当暂停）
            for ws in wss:
                ws.__exit__(None, None, None)
            time.sleep(0.3)
            frozen = rm.get_room(code)["game_state"]["clock_ms"]
            time.sleep(0.8)
            after = rm.get_room(code)["game_state"]["clock_ms"]
            assert after == frozen, "全员离线时游戏时钟不该走"
        finally:
            _close(wss)

    def test_claiming_and_releasing_a_room_pushes_a_state(self, client):
        """跑动阶段**占房 / 让房要补推一份 `stasis_state`**。

        归属（`rooms[].owner`）只在离散层下发，而 tick 只带「谁站在哪」——
        不补这一份，抢到的舱室在客户端**不会亮**（那盏灯是「这间是我的」唯一的
        反馈），要等到躺下才亮。v11 起舱室是「跟着人走」的，这条反馈更重要了。
        """
        wss, code, _, uids = _setup(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            gs["players"][uid]["pos"] = [8.5, 2.5]        # B1 门外那格通道
            wss[0].send_json({"type": "stasis_move", "dx": 1.0, "dy": 0.0})
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["map"]["rooms"]["B1"]["owner"] == uid
            assert msg["players"][str(uid)]["room"] == "B1"

            # 走回通道 → 让出（让不出就换不了房，用户报的就是这条）
            gs["players"][uid]["dir"] = [0.0, 0.0]
            gs["players"][uid]["pos"] = [8.5, 2.5]
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["map"]["rooms"]["B1"]["owner"] is None
            assert msg["players"][str(uid)]["room"] is None
        finally:
            _close(wss)


# ── 动作 ────────────────────────────────────────────────────


class TestActions:
    def _playing(self, client, players=2):
        wss, code, tokens, uids = _setup(client, players=players)
        _to_playing(wss, code)
        time.sleep(0.3)
        return wss, code, tokens, uids

    def test_move_is_integrated_by_the_tick(self, client):
        """移动输入**没有回包**（位置真值走 `stasis_tick`），但服务端真的在积分。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            gs["players"][uid]["pos"] = [7.5, 12.5]       # 挪到纵向通道里
            before = list(gs["players"][uid]["pos"])
            wss[0].send_json({"type": "stasis_move", "dx": 0.0, "dy": 1.0})
            time.sleep(0.6)
            after = gs["players"][uid]["pos"]
            assert after[1] > before[1], f"位置没被 tick 推进：{before} → {after}"
            assert after[0] == pytest.approx(before[0], abs=1e-6)
        finally:
            _close(wss)

    def test_move_payload_is_validated_silently(self, client):
        """乱发的 dx/dy 直接被丢掉（移动是瞬态输入，不值得回一条 error 刷屏）。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            gs["players"][uids[0]]["pos"] = [7.5, 12.5]
            wss[0].send_json({"type": "stasis_move", "dx": "左边", "dy": None})
            time.sleep(0.3)
            assert gs["players"][uids[0]]["dir"] == [0.0, 0.0]
            assert gs["players"][uids[0]]["pos"] == [7.5, 12.5]
        finally:
            _close(wss)

    def test_door_upgrade_refills_and_broadcasts(self, client):
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            gs["players"][uid]["bio"] = 500.0
            _make_lying(gs, uid)                  # v4：升级要求已躺下
            rid = gs["players"][uid]["room"]
            gs["map"]["rooms"][rid]["door_hp"] = 10.0
            wss[0].send_json({"type": "stasis_upgrade", "target": "door"})
            msg = _recv_type(wss[0], "stasis_state")
            after = msg["map"]["rooms"][rid]
            assert after["door_level"] == 2
            assert after["door_hp"] == after["door_max"]      # 升级即满血
        finally:
            _close(wss)

    def test_bed_upgrade_broadcasts(self, client):
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            gs["players"][uids[0]]["bio"] = 500.0
            _make_lying(gs, uids[0])              # v4：升级要求已躺下
            wss[0].send_json({"type": "stasis_upgrade", "target": "bed"})
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["players"][str(uids[0])]["bed_level"] == 2
        finally:
            _close(wss)

    def test_lie_broadcasts_and_stand_is_gone(self, client):
        """`stasis_lie` 走 wire 广播全量状态（用户报「进房间没法躺下」的修复）。

        **v4：`stasis_stand`（起身）已从协议里删除** —— 躺下不可逆。ws.py 的消息
        分发链没有 else 分支，未知类型是**静默忽略**，所以发出去既不改状态、也
        不报错（与「发一条不存在的类型」同一条路径）。这里就按这个真实行为断言。
        """
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            gs["players"][uid]["lying"] = False
            gs["players"][uid]["in_bed"] = False
            wss[0].send_json({"type": "stasis_lie"})
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["players"][str(uid)]["lying"] is True
            assert msg["players"][str(uid)]["in_bed"] is True

            # v4：没有起身这条路 —— 发 stasis_stand 被静默忽略，状态纹丝不动
            wss[0].send_json({"type": "stasis_stand"})
            time.sleep(0.3)
            assert gs["players"][uid]["lying"] is True, "躺下不可逆，不该被站起来"
            assert gs["players"][uid]["in_bed"] is True
        finally:
            _close(wss)

    def test_move_is_ignored_while_lying(self, client):
        """躺下就不能移动（用户明确要求）—— 方向必须被丢掉，不是「发了但不生效」，
        否则起身那一刻会立刻被陈旧方向推离床心。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            wss[0].send_json({"type": "stasis_lie"})
            _recv_type(wss[0], "stasis_state")
            before = list(gs["players"][uid]["pos"])
            wss[0].send_json({"type": "stasis_move", "dx": 1.0, "dy": 0.0})
            time.sleep(0.6)
            assert gs["players"][uid]["dir"] == [0.0, 0.0]
            assert gs["players"][uid]["pos"] == before
        finally:
            _close(wss)

    def test_bed_gate_is_reported_over_the_wire(self, client):
        """升床的门槛（要气密门跟上）必须是一条**可读的 error**，不是静默拒绝 ——
        客户端要把原因写进升级弹窗（用户报「升级弹窗中看不出来」）。

        v4：门槛表换成间断的 `BED_DOOR_REQ`，文案从「气密门 Lv2」改成
        「休眠舱升到 Lv3 需要铁门 2（现在 铁门 1）」。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            gs["players"][uid]["bio"] = 500.0
            _make_lying(gs, uid)                  # v4：否则先报的是「要先躺进休眠舱」
            gs["players"][uid]["bed_level"] = 2
            gs["map"]["rooms"][gs["players"][uid]["room"]]["door_level"] = 1
            wss[0].send_json({"type": "stasis_upgrade", "target": "bed"})
            msg = _recv_type(wss[0], "error")
            assert "休眠舱升到 Lv3" in msg["message"] and "铁门 2" in msg["message"]
            assert gs["players"][uid]["bed_level"] == 2, "被拒绝就不能改状态"
        finally:
            _close(wss)

    def test_remote_patch_costs_more(self, client):
        """远程修补是核心合作动作，价格 1.5 倍。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            a, b = gs["players"][uids[0]], gs["players"][uids[1]]
            a["bio"] = 500.0
            _make_lying(gs, uids[0])              # v4：修补要求已躺下
            rid_b = b["room"]
            room_b = gs["map"]["rooms"][rid_b]
            room_b["door_hp"] = room_b["door_hp"] * 0.5
            before = room_b["door_hp"]
            wss[0].send_json({"type": "stasis_patch", "target_uid": uids[1]})
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["map"]["rooms"][rid_b]["door_hp"] > before
            cost = 500.0 - msg["players"][str(uids[0])]["bio"]
            assert cost == pytest.approx(
                rm.stasis_engine.PATCH_COST_BIO * rm.stasis_engine.PATCH_COST_MULT,
                abs=1.5)
        finally:
            _close(wss)

    def test_patch_a_full_door_is_reported(self, client):
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            gs["players"][uids[0]]["bio"] = 500.0
            _make_lying(gs, uids[0])              # v4：修补要求已躺下
            wss[0].send_json({"type": "stasis_patch", "target_uid": None})
            msg = _recv_type(wss[0], "error")
            assert "满血" in msg["message"]
        finally:
            _close(wss)

    def test_upgrade_a_cell_without_a_building_is_reported(self, client):
        wss, code, _, uids = self._playing(client)
        try:
            _make_lying(rm.get_room(code)["game_state"], uids[0])  # v4：升级要求已躺下
            wss[0].send_json({"type": "stasis_upgrade", "target": "999,999"})
            msg = _recv_type(wss[0], "error")
            assert "没有建筑" in msg["message"]
        finally:
            _close(wss)

    def test_upgrade_without_resources_is_reported(self, client):
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = uids[0]
            _make_lying(gs, uid)                      # v4：升级要求已躺下
            gs["players"][uid]["bed_level"] = 2       # 2 级以后的升级要钱
            gs["players"][uid]["bio"] = 0.0
            # 门槛先过掉（升床要气密门跟上），否则报的是门槛而不是「资源不够」
            gs["map"]["rooms"][gs["players"][uid]["room"]]["door_level"] = 2
            wss[0].send_json({"type": "stasis_upgrade", "target": "bed"})
            msg = _recv_type(wss[0], "error")
            # v6：两种资源**分别报错**（弹窗要把缺的那样说出来），所以这里断的是
            # 「生物能不够」而不是笼统的「资源不够」。
            assert "生物能不够" in msg["message"]
        finally:
            _close(wss)

    def test_bad_payload_is_rejected(self, client):
        wss, code, _, _ = self._playing(client)
        try:
            wss[0].send_json({"type": "stasis_upgrade", "target": ["bed"]})
            msg = _recv_type(wss[0], "error")
            assert "参数" in msg["message"]
        finally:
            _close(wss)

    def test_build_broadcasts_the_layout(self, client):
        """建造的 `cell` 是 **`"x,y"` 字符串**（v2 的格子是坐标不是抽象下标）。

        回归：ws 层的参数校验曾经漏改成字符串，于是每一次建造都被静默拒掉、
        界面上「点了没反应」（E2E 抓到）。
        """
        wss, code, _, _ = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = gs["seat_uids"][0]
            gs["players"][uid]["bio"] = 500.0
            _make_lying(gs, uid)                  # v4：建造要求已躺下
            key = rm.stasis_engine.buildable_cells(gs, uid)[0]
            wss[0].send_json({"type": "stasis_build", "kind": "turret", "cell": key})
            msg = _recv_type(wss[0], "stasis_state")
            buildings = msg["map"]["rooms"][gs["players"][uid]["room"]]["buildings"]
            assert {"cell": key, "kind": "turret", "level": 1} in buildings
            assert msg["players"][str(uid)]["bio"] == 492      # 8 生物能一座
        finally:
            _close(wss)

    def test_demolish_refunds_and_broadcasts(self, client):
        wss, code, _, _ = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = gs["seat_uids"][0]
            gs["players"][uid]["bio"] = 500.0
            _make_lying(gs, uid)                  # v4：建造/拆除要求已躺下
            key = rm.stasis_engine.buildable_cells(gs, uid)[0]
            wss[0].send_json({"type": "stasis_build", "kind": "turret", "cell": key})
            _recv_type(wss[0], "stasis_state")
            wss[0].send_json({"type": "stasis_demolish", "cell": key})
            msg = _recv_type(wss[0], "stasis_state")
            assert msg["map"]["rooms"][gs["players"][uid]["room"]]["buildings"] == []
            assert msg["players"][str(uid)]["bio"] == 496      # 返还一半
        finally:
            _close(wss)

    def test_v1_int_cell_is_rejected_with_a_clear_error(self, client):
        """老客户端（v1 发整数格号）会拿到一句明确的错误，而不是静默掉线。"""
        wss, code, _, uids = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            gs["players"][uids[0]]["bio"] = 500.0
            wss[0].send_json({"type": "stasis_build", "kind": "turret", "cell": 1})
            msg = _recv_type(wss[0], "error")
            assert "建造参数不对" in msg["message"]
            assert uids[0] in wsmod._connections.get(code, {}), "连接不该被搞掉"
        finally:
            _close(wss)

    def test_unknown_building_kind_is_reported(self, client):
        """建造的**参数校验**在这一层（格子合法性由引擎判）。"""
        wss, code, _, _ = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = gs["seat_uids"][0]
            gs["players"][uid]["bio"] = 500.0
            _make_lying(gs, uid)                  # v4：引擎先查「有没有躺下」，再查种类
            key = rm.stasis_engine.buildable_cells(gs, uid)[0]
            wss[0].send_json({"type": "stasis_build", "kind": "death_star",
                              "cell": key})
            msg = _recv_type(wss[0], "error")
            assert "不能建造" in msg["message"]
        finally:
            _close(wss)

    def test_build_without_resources_is_reported(self, client):
        wss, code, _, _ = self._playing(client)
        try:
            gs = rm.get_room(code)["game_state"]
            uid = gs["seat_uids"][0]
            gs["players"][uid]["bio"] = 0.0
            _make_lying(gs, uid)                  # v4：建造要求已躺下
            key = rm.stasis_engine.buildable_cells(gs, uid)[0]
            wss[0].send_json({"type": "stasis_build", "kind": "turret", "cell": key})
            msg = _recv_type(wss[0], "error")
            assert "资源不够" in msg["message"]
        finally:
            _close(wss)


# ── 淘汰 / 终局 / sync ──────────────────────────────────────


class TestElimination:
    def test_surrender_transfers_legacy_and_keeps_game_running(self, client):
        """**认输只出局，队友继续**（同扫雷/UNO 的语义），遗产平分给队友。"""
        wss, code, _, uids = _setup(client, players=3)
        try:
            _to_playing(wss, code)
            time.sleep(0.3)
            gs = rm.get_room(code)["game_state"]
            gs["players"][uids[0]]["bio"] = 100.0
            wss[0].send_json({"type": "surrender"})
            deadline = time.time() + 2.0
            while time.time() < deadline and gs["players"][uids[0]]["alive"]:
                time.sleep(0.05)
            assert not gs["players"][uids[0]]["alive"]
            assert gs["players"][uids[0]]["bio"] == 0.0
            assert not gs["finished"], "还有人活着，局必须继续"
            assert gs["players"][uids[1]]["alive"]
        finally:
            _close(wss)

    def test_all_surrendering_ends_the_game(self, client):
        wss, code, _, _ = _setup(client, players=2)
        try:
            _to_playing(wss, code)
            time.sleep(0.3)
            for ws in wss:
                ws.send_json({"type": "surrender"})
            msg = _recv_type(wss[0], "game_over")
            assert msg["reason"] == rm.stasis_engine.REASON_WIPED
            assert msg["winner"] == "entity"
            assert msg["ranking"]
        finally:
            _close(wss)


class TestLeaveRoom:
    """中途退出房间（REST `/leave`）—— **跑动阶段也要能退**。

    用户实测报的「一些游戏在游戏中点返回想强制退出时没反应、返回后闪一下还在原处」，
    深空休眠这一半的根因就在这里：`stasis.surrender` 原来要求 `phase == "playing"`，
    跑动阶段点「退出房间」抛 `ValueError("对局还没开始")` → REST 500。
    **500 响应不带 CORS 头，浏览器把它报成 CORS 错误**，客户端静默吞掉、人没被淘汰，
    房间页看到「房间在游戏中且自己还在局中」又把人弹回对局页 —— 表现就是闪一下。
    """

    def test_leave_room_during_run_phase_eliminates_player(self, client):
        wss, code, tokens, uids = _setup(client)
        try:
            gs = rm.get_room(code)["game_state"]
            assert gs["phase"] == "run", "这个用例要盯的就是跑动阶段"

            r = client.post(f"/api/rooms/{code}/leave",
                            headers={"Authorization": f"Bearer {tokens[0]}"})
            assert r.status_code == 200, r.text

            assert not gs["players"][uids[0]]["alive"], "退出的人必须被淘汰"
            assert not gs["finished"], "队友还在守，局要继续"
            assert gs["players"][uids[1]]["alive"]
            # 房间页据此决定要不要把人弹回对局页：他必须已经不算在局玩家
            assert rm.user_in_game(rm.get_room(code), uids[0]) is False
            assert rm.user_in_game(rm.get_room(code), uids[1]) is True
        finally:
            _close(wss)

    def test_everyone_leaving_during_run_ends_the_game(self, client):
        """跑动阶段全退 → 全员失压终局，房间回 waiting（不能卡在 playing）。"""
        wss, code, tokens, uids = _setup(client)
        try:
            for t in tokens:
                r = client.post(f"/api/rooms/{code}/leave",
                                headers={"Authorization": f"Bearer {t}"})
                assert r.status_code == 200, r.text
            room = rm.get_room(code)
            assert room is None or room["status"] == "waiting", room and room["status"]
        finally:
            _close(wss)


class TestSync:
    def test_sync_carries_stasis_state_while_playing(self, client):
        wss, code, tokens, _ = _setup(client)
        try:
            _to_playing(wss, code)
            # 新连接进房 → sync 里应带 stasis 全量状态
            late = client.websocket_connect(f"/ws/room/{code}?token={tokens[1]}")
            late.__enter__()
            try:
                msg = _recv_type(late, "sync")
                assert msg["game"]["game"] == "stasis"   # game_start 套在 msg.game 里
                assert msg["game"]["phase"] in ("run", "playing")
                assert msg["game"]["map"]["id"] == "dorm"
                assert len(msg["game"]["map"]["rows"]) == 30
                assert msg["in_game"] is True
            finally:
                late.__exit__(None, None, None)
        finally:
            _close(wss)

    def test_sync_has_no_game_after_finish(self, client):
        """终局后房间回 waiting，sync 不能再带对局数据 —— 否则点「返回房间」
        会被立刻弹回对局页（v1.5.0 点格棋踩过）。"""
        wss, code, tokens, _ = _setup(client)
        try:
            _to_playing(wss, code)
            time.sleep(0.3)
            for ws in wss:
                ws.send_json({"type": "surrender"})
            _recv_type(wss[0], "game_over")
            time.sleep(0.3)
            late = client.websocket_connect(f"/ws/room/{code}?token={tokens[1]}")
            late.__enter__()
            try:
                msg = _recv_type(late, "sync")
                assert msg["game"] is None
                assert msg["in_game"] is False
            finally:
                late.__exit__(None, None, None)
        finally:
            _close(wss)


class TestHotRestart:
    def test_tick_is_rescheduled_after_restore(self, client):
        """热重启：playing 的 stasis 房必须被重新起 tick 任务。

        状态全在 game_state 里（引擎是纯函数 + 显式 dt），恢复只需要重起任务；
        因为用的是**游戏内时钟**，时间不会跳变。
        """
        wss, code, _, _ = _setup(client)
        try:
            _to_playing(wss, code)
            time.sleep(0.3)
            # 模拟「重启后任务没了」
            for task in list(rm._tick_tasks.values()):
                task.cancel()
            rm._tick_tasks.clear()
            # 对账兜底（sweeper 每轮会做这件事）
            rm.sweep_idle_rooms()
            assert code in rm._tick_tasks
            assert not rm._tick_tasks[code].done()
        finally:
            _close(wss)


# ── 已知缺陷（WS 层）────────────────────────────────────────
#
# 交付时的实测记录（**已被并行修复，勿回退**）：v2 迁移中 ws.py 的 `stasis_build` /
# `stasis_demolish` 参数校验曾漏改成字符串（仍要求 `isinstance(cell, int)`），于是
# 「v2 客户端发 "x,y" 字符串」被静默拒掉、界面表现为「点了没反应」；而「v1 客户端
# 发整数」会一路走到引擎的 `M.cell_key(*cell)`（`stasis_map` 里没有这个名字）→
# TypeError 逃出 `_handle_message`、被 WS 端点的 `except (Exception)` 吞掉 →
# **连接被静默关闭**。两条回归现在都在 `TestActions` 里锁着
# （`test_build_broadcasts_the_layout` / `test_v1_int_cell_is_rejected_with_a_clear_error`）。
