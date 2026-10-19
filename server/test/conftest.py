"""pytest 收集配置：test_ws_game.py 是需在线后端的独立脚本（直接 python 运行），不进 pytest。

另提供一个共享事件循环的 TestClient fixture，见下。
"""

import os
import sys
import time

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app

collect_ignore = ['test_ws_game.py', 'test_ws_battleship_live.py', 'test_bot_live.py']


@pytest.fixture
def ws_client():
    """**所有 websocket 连接共用同一个事件循环**的 TestClient。

    为什么必须显式给 portal：`TestClient` 不写成 `with TestClient(app) as c:` 时
    `self.portal` 是 None，starlette 的 `_portal_factory` 就**给每个 websocket 连接
    各起一个 blocking portal（独立线程 + 独立事件循环）**。于是「A 的处理协程里给
    B 发消息」变成跨事件循环唤醒：anyio memory stream 的 `send_nowait` 会把结果塞进
    B 那边**正在等待**的 future 并 `set_result`，但 B 的循环在 select 里睡着，没人
    写 self-pipe 叫醒它 —— 用例就永久阻塞在 `receive_json()` 上。

    命中条件很常见：`wss[0]` 与 `wss[1]` 各自挂一个循环，最后动作由 `wss[1]` 发出、
    广播给 `wss[0]`，而主线程正阻塞在读 `wss[0]`（俄罗斯方块「两人都认输 → 读
    game_over」就踩了这个，`test_ws_2048`/`test_ws_xiangqi` 那类写法同样在悬崖边）。

    共享 portal 后所有连接跑在同一个循环里，语义也更接近真实的 uvicorn（单循环）。
    上线（uvicorn）没有这个问题——它本来就是一个事件循环。
    """
    with anyio.from_thread.start_blocking_portal() as portal, _ordered_ws_sends(portal):
        client = TestClient(app)
        client.portal = portal
        yield client


async def _settle():
    for _ in range(3):
        await anyio.sleep(0)


class _ordered_ws_sends:
    """让客户端发出的 WS 消息**按发送顺序**被服务端处理完，再返回给用例。

    为什么需要：TestClient 的服务端侧是「每个连接一个协程、`sleep(0)` 忙轮询
    各自的收件队列」。用例连着从 ws1、ws2 各发一条（中间那次 `_recv_type` 往往
    读到的是**对方上一手的陈旧广播**，根本没等），两条消息几乎同时进队，谁先被
    处理取决于事件循环本轮先轮询到哪个协程 —— 服务端就可能先处理后发的那条
    （「还没轮到你走」、开局时对方还没准备好），期待的广播永远不来，用例永久阻塞。
    Linux 上恰好几乎总按发送顺序处理，Windows 上多个用例稳定复现。

    做法：每条 `websocket.receive` 入队后，等服务端把它取走（队列变空），再借
    portal 跑一次 `_settle`——它排在处理协程当前这一步之后执行，返回即说明这条
    消息的同步处理部分已跑完。连接已关闭、消息永远没人取时最多等 1 秒。
    语义上等价于「一个客户端发完一条、服务端处理完再发下一条」，用例本来就是
    这么假设的。
    """

    def __init__(self, portal):
        self.portal = portal

    def __enter__(self):
        self._orig = WebSocketTestSession.send
        portal, orig = self.portal, self._orig

        def send(session, message):
            orig(session, message)
            if message.get("type") != "websocket.receive" or session.portal is not portal:
                return
            deadline = time.monotonic() + 1.0
            while not session._receive_queue.empty() and time.monotonic() < deadline:
                time.sleep(0.0005)
            portal.call(_settle)

        WebSocketTestSession.send = send
        return self

    def __exit__(self, *exc):
        WebSocketTestSession.send = self._orig
