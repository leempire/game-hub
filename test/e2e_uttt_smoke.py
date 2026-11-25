"""终极井字棋双浏览器回归；独立后端/数据库，web须先用scripts启动。"""
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
    from app.game import uttt
    import uvicorn
    config.update(db_path=db_path, admin={}, jwt_secret="isolated-uttt-browser-test-secret-long-enough")
    original, count = uttt.create_game, 0

    def create_game():
        nonlocal count
        count += 1
        gs = original()
        if count == 2:
            gs["boards"][0] = [1, 1, 1, 2, 0, 2, 0, 0, 0]
            gs["boards"][2] = [2, 2, 2, 1, 1, 0, 0, 0, 0]
            gs["local_results"][0], gs["local_results"][2] = 1, 2
            gs["active_board"] = 4
        elif count == 3:
            gs["boards"][0] = [1, 1, 1, 2, 0, 2, 0, 0, 0]
            gs["boards"][1] = [1, 1, 1, 2, 0, 2, 0, 0, 0]
            gs["boards"][2] = [1, 1, 0, 2, 0, 0, 2, 0, 0]
            gs["local_results"][:2] = [1, 1]
            gs["active_board"] = 2
        return gs

    uttt.create_game = create_game
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


def browser_checks(api):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pages, latest, errors, sent = [], [{}, {}], [], [[], []]
        for seat in range(2):
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.route("**/api/**", lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                ("?" + urlsplit(route.request.url).query if urlsplit(route.request.url).query else "")))
            context.add_init_script("""const Original = window.WebSocket;
                window.WebSocket = class extends Original { constructor(url, protocols) {
                  const parsed = new URL(url);
                  super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
                }};""" % json.dumps(api.replace("http://", "ws://")))
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def received(payload, index=seat):
                message = json.loads(payload)
                if message.get("type") in ("sync", "game_start", "uttt_state") and message.get("game"):
                    latest[index] = message["game"]
                if message.get("type") == "error":
                    errors.append(message.get("message"))

            def outgoing(payload, index=seat):
                message = json.loads(payload)
                if message.get("type") == "uttt_move":
                    sent[index].append(message)

            def connected(ws, incoming=received, outgoing=outgoing):
                ws.on("framereceived", incoming)
                ws.on("framesent", outgoing)

            page.on("websocket", connected)
            pages.append(page)
            page.goto(BASE, timeout=90000)
            page.get_by_text("注册", exact=True).first.click()
            page.get_by_placeholder("3-20位字母数字下划线").fill(f"uttt_browser_{seat}")
            page.get_by_placeholder("6-32位").fill("test123456")
            page.get_by_placeholder("再次输入密码").fill("test123456")
            page.get_by_text("注 册", exact=True).click()
            page.get_by_test_id("game-card-uttt").wait_for(timeout=30000)
        host, peer = pages

        def state(seat, predicate):
            for _ in range(300):
                if latest[seat] and predicate(latest[seat]):
                    return latest[seat]
                pages[seat].wait_for_timeout(50)
            raise AssertionError(f"玩家{seat}未更新，revision={latest[seat].get('revision')}，errors={errors}")

        def start():
            latest[:] = [{}, {}]
            peer.get_by_text("准 备", exact=True).click()
            host.get_by_text("开始游戏", exact=True).click()
            for page in pages:
                page.get_by_test_id("uttt-overview").wait_for()
            return state(0, lambda g: g["winner"] is None and not g["moves"])

        def select(seat, board, cell):
            page = pages[seat]
            page.get_by_test_id(f"uttt-board-{board}").click()
            page.get_by_test_id(f"uttt-cell-{board}-{cell}").click()

        def confirm(seat, revision):
            pages[seat].get_by_test_id("uttt-confirm").click()
            return state(seat, lambda g: g["revision"] >= revision)

        def back(page):
            page.get_by_test_id("uttt-back-to-room").click()
            page.get_by_text("房间号", exact=True).wait_for()

        def surrender():
            host.get_by_test_id("uttt-surrender").click()
            host.get_by_test_id("uttt-confirm-danger").click()
            for page in pages:
                page.get_by_test_id("uttt-back-to-room").wait_for()
                back(page)

        host.get_by_test_id("game-card-uttt").click()
        host.get_by_text("创建房间", exact=True).click()
        host.get_by_text("房间号", exact=True).wait_for()
        code = re.search(r"房间号\s*([A-Z0-9]{6})", host.locator("body").inner_text()).group(1)
        peer.get_by_test_id("game-card-uttt").click()
        peer.get_by_text("加入房间", exact=True).click()
        peer.get_by_placeholder("输入6位房间号").fill(code)
        peer.get_by_text("加入", exact=True).click()
        peer.get_by_text("房间号", exact=True).wait_for()
        game = start()
        assert len(game["legal"]) == 81 and game["your_color"] == "black"
        assert state(1, lambda g: bool(g))["your_color"] == "white"
        select(0, 6, 2)
        expect(host.get_by_test_id("uttt-destination")).to_contain_text("右上小盘")
        assert not sent[0] and latest[0]["revision"] == 0
        host.get_by_test_id("uttt-cancel").click()
        assert not sent[0]
        select(0, 6, 2)
        confirm(0, 1)
        assert state(1, lambda g: g["revision"] == 1)["active_board"] == 2
        assert len(sent[0]) == 1 and latest[0]["boards"][6][2] == 1
        select(1, 2, 4)
        confirm(1, 2)
        assert state(0, lambda g: g["revision"] == 2)["active_board"] == 4
        select(0, 4, 0)
        host.reload()
        host.get_by_test_id("uttt-overview").wait_for()
        expect(host.get_by_test_id("uttt-destination")).to_contain_text("先选一个空格")
        assert latest[0]["boards"][2][4] == 2
        for width, height in ((320, 640), (390, 844), (1280, 900)):
            host.set_viewport_size({"width": width, "height": height})
            box = host.get_by_test_id("uttt-actions").bounding_box()
            cell = host.get_by_test_id("uttt-cell-4-0").bounding_box()
            assert box and box["y"] >= 0 and box["y"] + box["height"] <= height
            assert cell and cell["width"] >= 44 and cell["height"] >= 44
            assert host.evaluate("document.documentElement.scrollWidth <= innerWidth")
            host.screenshot(path=str(OUT / f"uttt-{width}.png"))
        host.set_viewport_size({"width": 390, "height": 844})
        print("PASS 1: 双人开局、预览/取消不发包、单次确认、双向指派、刷新续玩、三视口和44px格", flush=True)
        surrender()

        base = start()["revision"]
        select(0, 4, 2)
        expect(host.get_by_test_id("uttt-destination")).to_contain_text("任意未结束的小盘")
        confirm(0, base + 1)
        assert state(1, lambda g: g["revision"] == base + 1)["active_board"] is None
        select(1, 8, 0)
        confirm(1, base + 2)
        assert state(0, lambda g: g["revision"] == base + 2)["active_board"] is None
        surrender()
        print("PASS 2: 已赢小盘封盘、权威目的地预览、自由选盘与认输返回", flush=True)

        base = start()["revision"]
        select(0, 2, 2)
        expect(host.get_by_test_id("uttt-destination")).to_contain_text("结束对局")
        confirm(0, base + 1)
        host.get_by_test_id("game-result-history").click()
        host.locator('[data-testid^="history-record-"]').first.wait_for()
        back(peer)
        # The history route sits above the preserved Room route; no leave was sent.
        host.go_back()
        host.get_by_text("房间号", exact=True).wait_for()
        assert code in host.locator("body").inner_text()
        host.goto(BASE + "/history")
        host.get_by_text("终极井字棋", exact=True).first.click()
        host.get_by_test_id("uttt-history").wait_for()
        host.screenshot(path=str(OUT / "uttt-history.png"))
        peer.get_by_text("退出房间", exact=True).click()
        host.goto(BASE + f"/room/{code}")
        host.get_by_text("添加人机", exact=True).wait_for()
        print("PASS 3: 宏观三连终局、双方回房、最终棋盘战绩、深链恢复房间", flush=True)

        for difficulty in ("easy", "normal", "hard"):
            host.get_by_test_id(f"bot-difficulty-{difficulty}").click()
            host.get_by_text("添加人机", exact=True).click()
            host.locator('[data-testid^="remove-bot-"]').first.wait_for()
            if difficulty == "hard":
                host.locator('[data-testid^="swap-seat-"]').first.click()
                host.wait_for_timeout(400)
            latest[0] = {}
            host.get_by_text("开始游戏", exact=True).click()
            host.get_by_test_id("uttt-overview").wait_for()
            current = state(0, lambda g: g["next"] == g["your_color"])
            expected = current["revision"] + 2
            move = current["legal"][0]
            select(0, move["board"], move["cell"])
            confirm(0, expected - 1)
            current = state(0, lambda g: g["revision"] >= expected)
            assert current["next"] == current["your_color"]
            if difficulty == "hard":
                assert current["your_color"] == "white", "换座后应由人机执X先行"
            host.get_by_test_id("uttt-surrender").click()
            host.get_by_test_id("uttt-confirm-danger").click()
            back(host)
            host.locator('[data-testid^="remove-bot-"]').last.click()
            host.get_by_text("添加人机", exact=True).wait_for()
        print("PASS 4: 三档人机真实应招、换座后人机先手、结算回房可继续", flush=True)
        host.goto(BASE + "/history")
        for label in ("简单", "普通", "困难"):
            expect(host.get_by_text(re.compile(f" · {label}")).first).to_be_visible()
        print("PASS 5: 战绩保留并展示三档人机难度", flush=True)
        for page in pages:
            page.goto(BASE + f"/room/{code}")
            expect(page.get_by_text("房间号", exact=True)).to_be_visible()
        start()
        peer.get_by_test_id("uttt-leave").click()
        peer.get_by_test_id("uttt-confirm-danger").click()
        expect(host.get_by_test_id("uttt-back-to-room")).to_be_visible()
        expect(peer.get_by_test_id("game-card-uttt")).to_be_visible()
        back(host)
        expect(host.get_by_text("添加人机", exact=True)).to_be_visible()
        expect(host.get_by_text("uttt_browser_1", exact=True)).not_to_be_visible()
        print("PASS 6: 中途直接离开，对手立即结算并回房", flush=True)
        assert not errors, errors
        browser.close()


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="gamehub-uttt-") as folder, (OUT / "uttt-e2e-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, __file__, "--serve", str(port), str(Path(folder) / "test.db")],
                                   cwd=ROOT / "server", stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError("隔离测试后端启动失败，见uttt-e2e-server.log")
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


if __name__ == "__main__":
    serve(int(sys.argv[2]), sys.argv[3]) if "--serve" in sys.argv else run()
