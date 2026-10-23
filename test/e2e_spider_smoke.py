"""蜘蛛纸牌真实浏览器回归；独立临时后端/数据库，不修改开发环境存档。

从仓库根运行 server/.venv/bin/python test/e2e_spider_smoke.py；前端须已启动。
第二局使用测试进程内的确定性残局，真实走子通关，生产代码无测试入口。
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp/checks"
BASE = os.environ.get("GAMEHUB_WEB_URL", "http://localhost:50540")


def serve(port, db_path):
    sys.path.insert(0, str(ROOT / "server"))
    from app.cfg import config
    from app.game import spider
    import uvicorn
    config.update(db_path=db_path, admin={}, jwt_secret="isolated-spider-browser-test-secret")
    original = spider.create_game
    count = 0

    def create_game():
        nonlocal count
        count += 1
        game = original(7)
        if count == 2:
            game.update(columns=[list(range(13, 1, -1)), [1]] + [[] for _ in range(8)],
                        face_up=[0] * 10, stock=[], completed=7)
        return game

    spider.create_game = create_game
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="gamehub-spider-") as temp, \
            (OUT / "spider-e2e-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, __file__, "--serve", str(port), str(Path(temp) / "test.db")],
                                   cwd=ROOT / "server", stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError("测试后端启动失败，见 spider-e2e-server.log")
                try:
                    if requests.get(api + "/docs", timeout=.5).ok:
                        break
                except requests.RequestException:
                    time.sleep(.1)
            else:
                raise AssertionError("测试后端未就绪")
            browser_checks(api)
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def browser_checks(api):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        # 只替换测试浏览器的后端地址，网页与所有业务代码使用真实实现。
        context.route("**/api/**", lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                      ("?" + urlsplit(route.request.url).query if urlsplit(route.request.url).query else "")))
        ws_base = api.replace("http://", "ws://")
        context.add_init_script("""
            const OriginalWebSocket = window.WebSocket;
            window.WebSocket = class extends OriginalWebSocket {
              constructor(url, protocols) {
                const parsed = new URL(url);
                super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
              }
            };
        """ % json.dumps(ws_base))
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        latest = {}

        def received(payload):
            data = json.loads(payload)
            if data.get("type") in ("sync", "game_start", "spider_state") and data.get("game"):
                latest.clear()
                latest.update(data["game"])

        page.on("websocket", lambda ws: ws.on("framereceived", received))

        def state(predicate):
            for _ in range(200):
                if latest and predicate(latest):
                    return dict(latest)
                page.wait_for_timeout(50)
            raise AssertionError("蜘蛛纸牌状态没有按预期更新")

        def button(testid):
            return page.get_by_test_id("spider-" + testid)

        def open_game():
            page.get_by_test_id("game-card-spider").click()
            # 单人游戏没有房间概念：入口弹窗里不能出现「加入房间」（v1.18.1 主页的
            # 快捷入口叫「输入房间号」，所以这里断言 0 而不是数页面上有几处）。
            assert page.get_by_text("加入房间", exact=True).count() == 0
            page.get_by_text("开始 / 继续牌局", exact=True).click()
            page.get_by_test_id("spider-board").wait_for()

        page.goto(BASE, timeout=60000)
        page.get_by_text("注册", exact=True).first.click()
        page.get_by_placeholder("3-20位字母数字下划线").fill("spider_browser")
        page.get_by_placeholder("6-32位").fill("test123456")
        page.get_by_placeholder("再次输入密码").fill("test123456")
        page.get_by_text("注 册", exact=True).click()
        try:
            page.get_by_test_id("game-card-spider").wait_for(timeout=30000)
        except Exception:
            page.screenshot(path=str(OUT / "spider-failure.png"))
            print(page.locator("body").inner_text()[:1800], errors, flush=True)
            raise
        open_game()
        initial = state(lambda g: g["revision"] == 0)
        assert sum(v is None for c in initial["columns"] for v in c) == 44
        assert len(page.locator('[data-testid^="spider-card-"]').all()) == 10
        page.wait_for_timeout(400)  # 等待入口弹窗的淡出动画结束再截图。
        page.screenshot(path=str(OUT / "spider-mobile.png"))
        print("PASS 1: 单人入口、标准发牌与手机布局", flush=True)

        move = initial["legal_moves"][0]
        page.get_by_test_id(f"spider-card-{move['source']}-{move['index']}").click()
        assert "可放" in page.get_by_test_id(f"spider-column-{move['target']}").inner_text()
        page.get_by_test_id(f"spider-column-{move['target']}").click()
        state(lambda g: g["move_count"] == 1)
        button("undo").click()
        reverted = state(lambda g: g["revision"] == 2)
        assert reverted["columns"] == initial["columns"]
        button("hint").click()
        expect(button("message")).to_contain_text("可以把")
        print("PASS 2: 点击牌组与目标列、撤销、提示", flush=True)

        button("deal").click()
        dealt = state(lambda g: g["stock_count"] == 40)
        page.reload()
        button("deal").wait_for()
        state(lambda g: g["revision"] == dealt["revision"])
        assert latest["columns"] == dealt["columns"]
        button("undo").click()
        state(lambda g: g["stock_count"] == 50)
        assert latest["columns"] == initial["columns"]
        print("PASS 3: 发牌、刷新续玩、刷新后仍可撤销", flush=True)

        page.get_by_test_id("help-btn-spider").click()
        page.get_by_text("蜘蛛纸牌规则", exact=True).wait_for()
        page.get_by_test_id("help-close-btn").click()
        button("save").click()
        page.get_by_text("继续游戏", exact=True).click()
        button("save").click()
        page.get_by_text("确认返回", exact=True).click()
        page.get_by_test_id("game-card-spider").wait_for()
        previous_revision = latest["revision"]
        open_game()
        state(lambda g: g["revision"] == previous_revision)
        assert latest["columns"] == initial["columns"]
        print("PASS 4: 帮助、取消返回、保存离开与再次进入", flush=True)

        # 浏览器返回必须弹确认；确认后不被房间页拉回对局。
        page.go_back(wait_until="commit")
        page.get_by_text("保存并返回？", exact=True).wait_for()
        page.get_by_text("继续游戏", exact=True).click()
        button("new").click()
        page.get_by_text("继续游戏", exact=True).click()
        assert latest["revision"] == previous_revision
        button("new").click()
        page.get_by_text("确认新牌局", exact=True).click()
        state(lambda g: g["completed"] == 7)
        page.get_by_test_id("spider-card-1-0").click()
        page.get_by_test_id("spider-column-0").click()
        state(lambda g: g["finished"])
        page.get_by_test_id("spider-win").wait_for()
        page.get_by_test_id("spider-win").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / "spider-win.png"))
        print("PASS 5: 浏览器返回保护、新局确认、真实走子收齐八组", flush=True)

        for width, height, name in [(320, 568, "small"), (1280, 800, "desktop")]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            box = button("save").bounding_box()
            assert box and box["height"] >= 44 and box["y"] + box["height"] <= height
            page.screenshot(path=str(OUT / f"spider-{name}.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        button("save").click()
        page.get_by_text("确认返回", exact=True).click()
        page.get_by_text("我的", exact=True).last.click()
        page.get_by_text("历史战绩", exact=True).click()
        try:
            page.locator('[data-testid^="history-record-"]').first.click()
        except Exception:
            page.screenshot(path=str(OUT / "spider-history-failure.png"))
            print(page.locator("body").inner_text()[-2200:], errors, flush=True)
            raise
        page.get_by_text("已收齐 8 / 8 组 · 1 步", exact=False).wait_for()
        assert not errors, errors
        page.screenshot(path=str(OUT / "spider-history.png"))
        print("PASS 6: 小屏/桌面无页面横溢，战绩与牌面快照，无浏览器异常", flush=True)
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        run()
