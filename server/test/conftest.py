"""pytest 收集配置：test_ws_game.py 是需在线后端的独立脚本（直接 python 运行），不进 pytest。

另提供一个共享事件循环的 TestClient fixture，见下。
"""

import os
import sys

import anyio
import pytest
from fastapi.testclient import TestClient

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
    with anyio.from_thread.start_blocking_portal() as portal:
        client = TestClient(app)
        client.portal = portal
        yield client
