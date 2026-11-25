"""蜘蛛纸牌双浏览器竖屏回归；独立临时后端/数据库，前端须已启动。

从根目录用安装了 requirements-dev.txt 的 Python 运行本文件。
第二局由测试后端构造确定性残局，生产代码没有测试入口。
"""
import json
import os
from pathlib import Path
import re
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
    original, count = spider.create_game, 0

    def create_game():
        nonlocal count
        count += 1
        game = original(5)
        if count == 2:
            width = len(game["columns"])
            game.update(columns=[list(range(13, 1, -1)), [1]] + [[] for _ in range(width - 2)],
                        face_up=[0] * width, stock=[], completed=game["total_runs"] - 1)
        return game

    spider.create_game = create_game
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="gamehub-spider-", dir=OUT) as temp, \
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
        pages, latest, errors = [], [{}, {}], []
        for seat in range(2):
            context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            context.route("**/api/**", lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                          ("?" + urlsplit(route.request.url).query if urlsplit(route.request.url).query else "")))
            context.add_init_script("""
                const OriginalWebSocket = window.WebSocket;
                window.WebSocket = class extends OriginalWebSocket {
                  constructor(url, protocols) {
                    const parsed = new URL(url);
                    super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
                  }
                };
            """ % json.dumps(api.replace("http://", "ws://")))
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

            def received(payload, index=seat):
                data = json.loads(payload)
                if data.get("type") in ("sync", "game_start", "spider_state") and data.get("game"):
                    latest[index] = data["game"]

            page.on("websocket", lambda ws, handler=received: ws.on("framereceived", handler))
            pages.append(page)
            page.goto(BASE, timeout=90000)
            page.get_by_text("注册", exact=True).first.click()
            page.get_by_placeholder("3-20位字母数字下划线").fill(f"spider_browser_{seat}")
            page.get_by_placeholder("6-32位").fill("test123456")
            page.get_by_placeholder("再次输入密码").fill("test123456")
            page.get_by_text("注 册", exact=True).click()
            page.get_by_test_id("game-card-spider").wait_for(timeout=30000)
        host, peer = pages

        def state(index, predicate):
            for _ in range(300):
                if latest[index] and predicate(latest[index]):
                    return latest[index]
                pages[index].wait_for_timeout(50)
            raise AssertionError(f"玩家 {index} 状态没有更新：{latest[index]}")

        def move(index, action):
            page = pages[index]
            page.get_by_test_id(f"spider-card-{action['source']}-{action['index']}").tap()
            expect(page.get_by_test_id(f"spider-column-{action['target']}")).to_contain_text("可放")
            page.get_by_test_id(f"spider-column-{action['target']}").tap()

        host.get_by_role("button", name="合作", exact=True).click()
        expect(host.get_by_test_id("game-card-spider")).to_have_count(0)
        host.get_by_role("button", name="各玩各的", exact=True).click()
        expect(host.get_by_test_id("game-card-spider")).to_be_visible()

        host.get_by_test_id("game-card-spider").click()
        host.get_by_text("加入房间", exact=True).wait_for()
        host.get_by_text("创建房间", exact=True).click()
        host.get_by_text("房间号", exact=True).wait_for()
        assert host.get_by_test_id("spider-board").count() == 0
        code = re.search(r"房间号\s*([A-Z0-9]{6})", host.locator("body").inner_text()).group(1)
        peer.get_by_test_id("game-card-spider").click()
        peer.get_by_text("加入房间", exact=True).click()
        peer.get_by_placeholder("输入6位房间号").fill(code)
        peer.get_by_text("加入", exact=True).click()
        peer.get_by_text("准 备", exact=True).click()
        host.get_by_text("开始游戏", exact=True).click()
        for page in pages:
            page.get_by_test_id("spider-board").wait_for()
        initial = state(0, lambda game: game["revision"] == 0)
        assert state(1, lambda game: game["revision"] == 0) == initial
        assert len(initial["columns"]) == 10 and initial["total_runs"] == 8 and initial["stock_count"] == 50
        print("PASS 1: 分类入口、创建和加入、准备、相同的十列起手牌", flush=True)

        action = initial["legal_moves"][0]
        host.get_by_test_id(f"spider-card-{action['source']}-{action['index']}").tap()
        peer.get_by_test_id("spider-deal").tap()
        dealt = state(1, lambda game: game["revision"] == 1)
        assert dealt["stock_count"] == 40 and latest[0] == initial
        expect(host.get_by_test_id(f"spider-column-{action['target']}")).to_contain_text("可放")
        host.get_by_test_id(f"spider-column-{action['target']}").tap()
        assert state(0, lambda game: game["revision"] == 1)["columns"] != initial["columns"]
        host.get_by_test_id("spider-undo").tap()
        host.get_by_text("确认撤销", exact=True).tap()
        expect(host.get_by_text("撤销上一步？", exact=True)).to_have_count(0)
        assert state(0, lambda game: game["revision"] == 2)["columns"] == initial["columns"]
        assert latest[1] == dealt
        print("PASS 2: 移动、发牌和撤销各自生效，选牌保持", flush=True)

        latest[1] = {}
        peer.reload()
        assert state(1, lambda game: game["revision"] == 1) == dealt
        expect(peer.get_by_test_id("spider-deal")).to_have_text("发牌 · 剩 4 轮")
        for width, height in ((320, 568), (390, 844)):
            host.set_viewport_size({"width": width, "height": height})
            assert host.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert host.get_by_test_id("spider-board").evaluate("el => el.scrollWidth > el.clientWidth")
            expect(host.get_by_test_id("spider-card-0-5")).to_be_in_viewport(ratio=.4)
            for control in ("spider-deal", "spider-undo", "spider-end", "spider-end-round", "spider-back"):
                box = host.get_by_test_id(control).bounding_box()
                # RNW 的 44pt 目标会出现 43.999… 的亚像素测量，留 0.5px 容差。
                assert box and box["height"] >= 43.5 and 0 <= box["y"] <= height - box["height"]
            host.screenshot(path=str(OUT / f"spider-portrait-{width}.png"))
        host.set_viewport_size({"width": 390, "height": 844})
        full = host.get_by_test_id("spider-fullscreen")
        full.click()
        expect(full).to_have_attribute("aria-label", "退出横屏全屏")
        host.wait_for_timeout(200)
        host.keyboard.press("Escape")
        expect(full).to_have_attribute("aria-label", "横屏全屏")
        print("PASS 3: 刷新恢复个人进度，320px 和 390px 竖屏可滚动、操作按钮完整、全屏可进可退", flush=True)

        host.get_by_test_id("game-shell-spider").get_by_test_id("help-btn-spider").click()
        host.get_by_text("蜘蛛纸牌规则", exact=True).wait_for()
        host.get_by_test_id("help-close-btn").click()
        host.get_by_test_id("spider-back").click()
        host.get_by_text("继续游戏", exact=True).click()
        host.get_by_test_id("spider-back").click()
        host.get_by_text("确认离开", exact=True).click()
        try:
            host.get_by_text("房间号", exact=True).wait_for()
        except Exception:
            host.screenshot(path=str(OUT / "spider-leave-failure.png"))
            print('leave failure', host.url, host.locator('body').inner_text()[-2500:], errors, flush=True)
            raise
        host.wait_for_timeout(500)
        assert host.get_by_test_id("spider-board").count() == 0
        expect(host.get_by_text("本局进行中", exact=True)).to_be_visible()
        expect(host.get_by_text("准 备", exact=True)).to_have_count(0)
        expect(host.get_by_text("取消准备", exact=True)).to_have_count(0)
        expect(peer.get_by_text("结束 0/8", exact=True)).to_be_visible()
        assert latest[1] == dealt
        move(1, latest[1]["legal_moves"][0])
        peer.get_by_test_id("spider-end-round").click()
        peer.get_by_text("确认结束本局", exact=True).click()
        state(1, lambda game: game["finished"])
        peer.get_by_test_id("spider-back").click()
        peer.get_by_text("房间号", exact=True).wait_for()
        print("PASS 4: 退出只结束本人，房主移交；新房主结束本局并结算", flush=True)

        host.get_by_text("准 备", exact=True).click()
        peer.get_by_text("开始游戏", exact=True).click()
        for index in range(2):
            state(index, lambda game: game["completed"] == 7 and not game["finished"])
        move(1, {"source": 1, "index": 0, "target": 0})
        state(1, lambda game: game["completed"] == 8 and game["finished"])
        expect(peer.get_by_test_id("spider-win")).to_contain_text("个人通关")
        expect(host.get_by_text("通关 8/8", exact=True)).to_be_visible()
        assert latest[0]["completed"] == 7 and not latest[0]["finished"]
        peer.get_by_test_id("spider-back").click()
        peer.get_by_text("房间号", exact=True).wait_for()
        expect(peer.get_by_test_id("spider-board")).to_have_count(0)
        expect(peer.get_by_text("本局进行中", exact=True)).to_be_visible()
        expect(peer.get_by_text("开始游戏", exact=True)).to_have_count(0)
        expect(peer.get_by_text("7/8 组", exact=True).first).to_be_visible()
        host.get_by_test_id("spider-end").tap()
        host.get_by_text("确认结束", exact=True).tap()
        expect(host.get_by_test_id("game-result")).to_contain_text("你的牌局已结束")
        host.get_by_test_id("game-result-history").tap()
        host.locator('[data-testid^="history-record-"]').first.click()
        expect(host.get_by_text("已收齐 7 / 8 组 · 0 步")).to_be_visible()
        assert not errors, errors
        print("PASS 5: 回房再开、个人通关可提前返回、全员结束后查看个人战绩", flush=True)
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        run()
