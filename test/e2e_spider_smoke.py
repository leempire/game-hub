"""蜘蛛纸牌双浏览器合作回归；独立临时后端/数据库，前端须已启动。

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

    def create_game(*, num_players=1):
        nonlocal count
        count += 1
        game = original(5, num_players=num_players)
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
        pages, latest, errors = [], [{}, {}], []
        for seat in range(2):
            context = browser.new_context(viewport={"width": 390, "height": 844})
            if seat == 1:
                context.add_init_script("Object.defineProperty(Element.prototype, 'requestFullscreen', { value: undefined, configurable: true });")
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

        def both(predicate):
            first, second = [state(i, predicate) for i in range(2)]
            assert first == second
            return first

        def move(index, action):
            page = pages[index]
            page.get_by_test_id(f"spider-card-{action['source']}-{action['index']}").click()
            expect(page.get_by_test_id(f"spider-column-{action['target']}")).to_contain_text("可放")
            page.get_by_test_id(f"spider-column-{action['target']}").click()

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
        initial = both(lambda game: game["revision"] == 0)
        assert len(initial["columns"]) == 15 and initial["total_runs"] == 12 and initial["stock_count"] == 75
        host.screenshot(path=str(OUT / "spider-mobile.png"))
        print("PASS 1: 统一创建/加入房间、准备、双端同时进入 15 列合作牌桌", flush=True)

        full = host.get_by_test_id("spider-fullscreen")
        frame = host.get_by_test_id("spider-play-area").bounding_box()
        button = full.bounding_box()
        assert frame and button and button["height"] >= 44
        assert abs(frame["x"] + frame["width"] - button["x"] - button["width"] - 12) < 2
        assert abs(frame["y"] + frame["height"] - button["y"] - button["height"] - 12) < 2
        full.click()
        expect(full).to_have_attribute("aria-label", "退出横屏全屏")
        host.wait_for_function("document.fullscreenElement !== null")
        host.wait_for_function("getComputedStyle(document.querySelector('[data-testid=spider-stage]')).width === '844px'")
        rect = host.get_by_test_id("spider-stage").bounding_box()
        assert rect and abs(rect["x"]) < 1 and abs(rect["y"]) < 1
        assert abs(rect["width"] - 390) < 1 and abs(rect["height"] - 844) < 1
        assert latest[0]["revision"] == 0
        peer.get_by_test_id("spider-fullscreen").click()
        expect(peer.get_by_test_id("spider-fullscreen")).to_have_attribute("aria-label", "退出横屏全屏")
        assert peer.evaluate("document.fullscreenElement === null")
        peer.set_viewport_size({"width": 844, "height": 390})
        peer.screenshot(path=str(OUT / "spider-fullscreen-landscape.png"))

        move(0, initial["legal_moves"][0])
        after_host = both(lambda game: game["revision"] == 1)
        move(1, after_host["legal_moves"][0])
        both(lambda game: game["revision"] == 2)
        host.get_by_test_id("spider-undo").click()
        host.get_by_text("确认撤销", exact=True).click()
        undone = both(lambda game: game["revision"] == 3)
        assert undone["columns"] == after_host["columns"]
        print("PASS 2: 两人均可移动任意牌组，撤销全队最近一步并同步", flush=True)

        full.click()
        expect(full).to_have_attribute("aria-label", "横屏全屏")
        host.wait_for_function("document.fullscreenElement === null")
        host.set_viewport_size({"width": 390, "height": 844})
        full.click()
        host.wait_for_function("document.fullscreenElement !== null")
        host.evaluate("document.exitFullscreen()")
        expect(full).to_have_attribute("aria-label", "横屏全屏")
        peer.keyboard.press("Escape")
        expect(peer.get_by_test_id("spider-fullscreen")).to_have_attribute("aria-label", "横屏全屏")
        peer.set_viewport_size({"width": 390, "height": 844})
        peer.get_by_test_id("spider-fullscreen").click()
        peer.go_back(wait_until="commit")
        expect(peer.get_by_test_id("spider-fullscreen")).to_have_attribute("aria-label", "横屏全屏")
        peer.get_by_test_id("spider-board").wait_for()
        assert latest[0]["revision"] == latest[1]["revision"] == 3
        print("PASS 2a: 右下角全屏、横屏操作与弹窗、无全屏 API 回退、退出/Escape/返回恢复且不丢牌", flush=True)

        disjoint = next((a, b) for a in undone["legal_moves"] for b in undone["legal_moves"]
                        if not {a["source"], a["target"]} & {b["source"], b["target"]})
        a, b = disjoint
        host.get_by_test_id(f"spider-card-{a['source']}-{a['index']}").click()
        move(1, b)
        both(lambda game: game["revision"] == 4)
        expect(host.get_by_test_id(f"spider-column-{a['target']}")).to_contain_text("可放")
        host.get_by_test_id(f"spider-column-{a['target']}").click()
        both(lambda game: game["revision"] == 5)
        peer.get_by_test_id("spider-deal").click()
        dealt = both(lambda game: game["revision"] == 6)
        assert dealt["stock_count"] == 60
        host.reload()
        state(0, lambda game: game["revision"] == 6)
        host.get_by_test_id("spider-undo").click()
        host.get_by_text("确认撤销", exact=True).click()
        both(lambda game: game["revision"] == 7 and game["stock_count"] == 75)
        print("PASS 3: 队友无关操作保留选牌、共享发牌、刷新续玩和撤销", flush=True)

        host.get_by_test_id("help-btn-spider").click()
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
        state(1, lambda game: len(game["departed"]) == 1)
        move(1, latest[1]["legal_moves"][0])
        peer.get_by_test_id("spider-end").click()
        peer.get_by_text("确认结束", exact=True).click()
        state(1, lambda game: game["finished"])
        peer.get_by_test_id("spider-back").click()
        peer.get_by_text("房间号", exact=True).wait_for()
        print("PASS 4: 退出只结束本人，房主转移、队友继续，结束后返回房间", flush=True)

        host.get_by_text("准 备", exact=True).click()
        peer.get_by_text("开始游戏", exact=True).click()
        both(lambda game: game["completed"] == 11 and not game["finished"])
        move(1, {"source": 1, "index": 0, "target": 0})
        both(lambda game: game["completed"] == 12 and game["finished"])
        for page in pages:
            page.get_by_test_id("spider-win").wait_for()
        for width, height, name in [(320, 568, "small"), (1280, 800, "desktop")]:
            peer.set_viewport_size({"width": width, "height": height})
            assert peer.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            box = peer.get_by_test_id("spider-back").bounding_box()
            assert box and box["height"] >= 44 and box["y"] + box["height"] <= height
            peer.screenshot(path=str(OUT / f"spider-{name}.png"))
        peer.get_by_test_id("spider-back").click()
        peer.get_by_text("退出房间", exact=True).click()
        peer.get_by_text("我的", exact=True).last.click()
        peer.get_by_text("历史战绩", exact=True).click()
        peer.locator('[data-testid^="history-record-"]').first.click()
        peer.get_by_text("已收齐 12 / 12 组 · 1 步", exact=False).wait_for()
        assert not errors, errors
        print("PASS 5: 回房重新准备、全队通关、动态目标战绩、小屏与桌面布局", flush=True)
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        run()
