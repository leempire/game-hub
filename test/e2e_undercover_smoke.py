"""谁是卧底四人浏览器冒烟；需先启动 Web，后端和数据库由本脚本隔离启动。"""
from collections import Counter
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
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp/checks"
BASE = os.environ.get("GAMEHUB_WEB_URL", "http://localhost:50540").rstrip("/")


def serve(port, db_path):
    sys.path.insert(0, str(ROOT / "server"))
    os.environ["JWT_SECRET"] = "isolated-undercover-browser-test-secret-long-enough"
    from app.cfg import config
    import uvicorn

    config.update(db_path=db_path, admin={})
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


def browser_checks(api):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pages, players, errors = [], [], []
        received = [[] for _ in range(4)]
        sent = [[] for _ in range(4)]

        def redirect_api(route):
            url = urlsplit(route.request.url)
            route.continue_(url=api + url.path + ("?" + url.query if url.query else ""))

        for seat in range(4):
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.route(re.compile(r"/api/"), redirect_api)
            context.add_init_script("""const Original = window.WebSocket;
                window.WebSocket = class extends Original { constructor(url, protocols) {
                  const parsed = new URL(url);
                  super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
                }};""" % json.dumps(api.replace("http://", "ws://")))
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on("pageerror", lambda error: errors.append(str(error)))

            def connected(ws, index=seat):
                if not urlsplit(ws.url).path.startswith("/ws/room/"):
                    return

                def receive(payload):
                    message = json.loads(payload)
                    received[index].append(message)
                    if message.get("type") == "error":
                        errors.append(f"玩家 {index + 1}: {message.get('message')}")

                ws.on("framereceived", receive)
                ws.on("framesent", lambda payload: sent[index].append(json.loads(payload)))

            page.on("websocket", connected)
            pages.append(page)
            page.goto(BASE, timeout=90000)
            page.get_by_text("注册", exact=True).first.click()
            page.get_by_placeholder("3-20位字母数字下划线").fill(f"undercover_web_{seat}")
            page.get_by_placeholder("6-32位").fill("test123456")
            page.get_by_placeholder("再次输入密码").fill("test123456")
            with page.expect_response(lambda response: response.request.method == "POST"
                                      and urlsplit(response.url).path == "/api/auth/register") as registration:
                page.get_by_text("注 册", exact=True).click()
            assert registration.value.ok, registration.value.status
            players.append(registration.value.json())
            page.get_by_role("button", name="多人桌游", exact=True).click()
            expect(page.get_by_test_id("game-card-uttt")).to_have_count(0)
            card = page.get_by_test_id("game-card-undercover")
            expect(card).to_contain_text("谁是卧底")
            card.click()

        host = pages[0]
        seats = {player["user"]["id"]: seat for seat, player in enumerate(players)}

        def wait_message(seat, predicate, after=0):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                for message in reversed(received[seat][after:]):
                    if predicate(message):
                        return message
                pages[seat].wait_for_timeout(50)
            raise AssertionError(f"玩家 {seat + 1} 未收到预期消息，最近的消息类型："
                                 f"{[message['type'] for message in received[seat][-8:]]}，错误：{errors}")

        def state(seat, predicate, *, kind=None, after=0):
            def matches(message):
                if message["type"] not in ("game_start", "sync", "undercover_state"):
                    return False
                game = message.get("game")
                return (kind is None or message["type"] == kind) and bool(game) and predicate(game)

            return wait_message(seat, matches, after)["game"]

        def start(previous_id=None):
            for page in pages[1:]:
                page.get_by_text("准 备", exact=True).click()
            host.get_by_text("开始游戏", exact=True).click()
            for page in pages:
                expect(page.get_by_test_id("undercover-word-toggle")).to_be_visible()
            return [state(seat, lambda game: game["match_id"] != previous_id and game["phase"] == "clue",
                          kind="game_start") for seat in range(4)]

        host.get_by_text("创建房间", exact=True).click()
        host.get_by_text("房间号", exact=True).wait_for()
        code = re.search(r"房间号\s*([A-Z0-9]{6})", host.locator("body").inner_text()).group(1)
        for page in pages[1:]:
            page.get_by_text("加入房间", exact=True).click()
            page.get_by_placeholder("输入6位房间号").fill(code)
            page.get_by_text("加入", exact=True).click()
            page.get_by_text("房间号", exact=True).wait_for()
        expect(host.get_by_text("添加人机", exact=True)).to_be_visible()
        opening = start()
        match_id = opening[0]["match_id"]
        words = [game["your_word"] for game in opening]
        assert all(isinstance(word, str) and word for word in words)
        assert sorted(Counter(words).values()) == [1, 3]
        assert {game["match_id"] for game in opening} == {match_id}
        assert set(opening[0]["player_ids"]) == set(seats)
        undercover = next(uid for uid, seat in seats.items() if words.count(words[seat]) == 1)
        civilian = next(uid for uid in seats if uid != undercover)
        for player in players:
            response = requests.get(api + f"/api/rooms/{code}",
                                    headers={"Authorization": f"Bearer {player['token']}"}, timeout=5)
            response.raise_for_status()
            assert all(word not in json.dumps(response.json(), ensure_ascii=False) for word in set(words))

        for seat, page in enumerate(pages):
            own_word = page.get_by_test_id("undercover-word")
            expect(page.get_by_text(words[seat], exact=True)).not_to_be_visible()
            page.get_by_test_id("undercover-word-toggle").click()
            expect(own_word).to_have_text(words[seat])
            other_word = next(word for word in words if word != words[seat])
            expect(page.get_by_text(other_word, exact=True)).not_to_be_visible()
            page.get_by_test_id("undercover-word-toggle").click()
            expect(page.get_by_text(words[seat], exact=True)).not_to_be_visible()

        host.get_by_label("返回", exact=True).click()
        leave_title = host.get_by_text("确认离开房间？", exact=True)
        expect(leave_title).to_be_visible()
        host.get_by_text("取消", exact=True).click()
        expect(leave_title).to_be_hidden()
        host.set_viewport_size({"width": 320, "height": 640})
        toggle = host.get_by_test_id("undercover-word-toggle")
        assert host.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert toggle.bounding_box()["height"] >= 44
        host.screenshot(path=str(OUT / "undercover-320.png"))
        print("PASS 1: 多人桌游入口、四人开局、逐人看词及 320px 布局", flush=True)

        def clues(round_number, active):
            for turn in range(len(active)):
                game = state(0, lambda g: g["round"] == round_number and g["phase"] == "clue"
                             and sum(clue["round"] == round_number for clue in g["clues"]) == turn)
                seat = seats[game["speaker_id"]]
                assert game["speaker_id"] in active
                for other, page in enumerate(pages):
                    if other != seat:
                        expect(page.get_by_test_id("undercover-clue-input")).not_to_be_visible()
                page = pages[seat]
                clue = f"这是第{round_number}轮线索{turn + 1}"
                page.get_by_test_id("undercover-clue-input").fill(clue)
                page.get_by_test_id("undercover-clue-submit").click()
                state(0, lambda g: any(item["text"] == clue for item in g["clues"]))
            return state(0, lambda g: g["round"] == round_number and g["phase"] == "vote")

        def vote(uid, target, phase_id):
            seat = seats[uid]
            page = pages[seat]
            state(seat, lambda game: game["phase_id"] == phase_id)
            expect(page.get_by_test_id(f"undercover-vote-{uid}")).not_to_be_visible()
            before = sum(message["type"] == "undercover_vote" for message in sent[seat])
            page.get_by_test_id(f"undercover-vote-{target}").click()
            assert sum(message["type"] == "undercover_vote" for message in sent[seat]) == before
            page.get_by_test_id("undercover-vote-confirm").click()
            outgoing = [message for message in sent[seat] if message["type"] == "undercover_vote"]
            assert len(outgoing) == before + 1
            assert outgoing[-1]["target_id"] == target and outgoing[-1]["phase_id"] == phase_id
            assert outgoing[-1]["match_id"] == match_id

        phase_id = clues(1, set(seats))["phase_id"]
        first_voter = next(uid for uid in seats if uid != civilian)
        voter_seat = seats[first_voter]
        voter_page = pages[voter_seat]
        vote(first_voter, civilian, phase_id)
        state(voter_seat, lambda g: g["phase_id"] == phase_id and g["your_vote"] == civilian)
        for uid in seats:
            if uid != first_voter:
                game = state(seats[uid], lambda g: g["phase_id"] == phase_id and g["votes_cast"] == 1)
                assert game["your_vote"] is None and game["tallies"] == []
        before_reload = len(received[voter_seat])
        voter_page.get_by_test_id("undercover-word-toggle").click()
        expect(voter_page.get_by_test_id("undercover-word")).to_have_text(words[voter_seat])
        voter_page.reload()
        expect(voter_page.get_by_test_id("undercover-word-toggle")).to_be_visible()
        expect(voter_page.get_by_text(words[voter_seat], exact=True)).not_to_be_visible()
        synced = state(voter_seat, lambda g: g["match_id"] == match_id, kind="sync", after=before_reload)
        assert (synced["phase_id"], synced["your_vote"], synced["your_word"]) == (phase_id, civilian, words[voter_seat])
        expect(voter_page.get_by_text(re.compile(r"^已锁票"))).to_be_visible()
        expect(voter_page.get_by_test_id("undercover-vote-confirm")).not_to_be_visible()
        for uid in seats:
            if uid != first_voter:
                vote(uid, first_voter if uid == civilian else civilian, phase_id)

        round_two = state(0, lambda g: g["round"] == 2 and g["phase"] == "clue")
        assert round_two["eliminations"][-1]["user_id"] == civilian
        assert round_two["eliminations"][-1]["role"] == "civilian"
        assert any(item["user_id"] == civilian and item["count"] == 3
                   for item in round_two["tallies"][-1]["votes"])
        active = set(seats) - {civilian}
        phase_id = clues(2, active)["phase_id"]
        eliminated = pages[seats[civilian]]
        expect(eliminated.get_by_test_id("undercover-eliminated")).to_be_visible()
        expect(eliminated.get_by_test_id("undercover-vote-confirm")).not_to_be_visible()
        assert state(seats[civilian], lambda g: g["phase_id"] == phase_id)["candidate_ids"] == []
        assert not any(message.get("phase_id") == phase_id for message in sent[seats[civilian]])
        print("PASS 2: 轮流提交、确认锁票、逐人票况、刷新恢复、出局后只读", flush=True)

        for uid in active:
            vote(uid, undercover if uid != undercover else next(peer for peer in active if peer != uid), phase_id)
        summary = None
        for seat, page in enumerate(pages):
            game_over = wait_message(seat, lambda msg: msg["type"] == "game_over" and msg.get("match_id") == match_id)
            assert game_over["game"] == "undercover" and game_over["winner"] == "civilian"
            assert summary is None or game_over["summary"] == summary
            summary = game_over["summary"]
            expect(page.get_by_test_id("undercover-result")).to_be_visible()
            for word in set(words):
                expect(page.get_by_test_id("undercover-result")).to_contain_text(word)
            other_word = next(word for word in words if word != words[seat])
            for message in received[seat]:
                if message == game_over:
                    break
                assert other_word not in json.dumps(message, ensure_ascii=False)
                game = message.get("game")
                if isinstance(game, dict) and game.get("match_id") == match_id:
                    assert game["your_word"] == words[seat]
                    assert not (set(game) & {"roles", "role", "your_role", "word_pair", "word_pair_id", "words", "votes"})
        assert set(summary["word_pair"].values()) == set(words)
        assert {entry["user_id"]: entry["role"] for entry in summary["roles"]} == {
            uid: "undercover" if uid == undercover else "civilian" for uid in seats
        }

        record_ids = set()
        results = {player["user"]["username"]: "lose" if player["user"]["id"] == undercover else "win"
                   for player in players}
        for player in players:
            response = requests.get(api + "/api/history/mine",
                                    headers={"Authorization": f"Bearer {player['token']}"}, timeout=5)
            response.raise_for_status()
            history = response.json()
            assert history["total"] == 1
            record = history["items"][0]
            assert record["game"] == "undercover" and record["room_code"] == code
            assert record["snapshot"] == summary
            assert {entry["username"]: entry["result"] for entry in record["results"]} == results
            record_ids.add(record["id"])
        assert len(record_ids) == 1
        record_id = record_ids.pop()
        share = requests.post(api + f"/api/history/{record_id}/share",
                              headers={"Authorization": f"Bearer {players[0]['token']}"}, timeout=5)
        share.raise_for_status()
        public = requests.get(api + f"/api/history/shared/{share.json()['token']}", timeout=5)
        public.raise_for_status()
        public_record = public.json()
        assert public_record["room_code"] == "" and public_record["snapshot"]["winner"] == "civilian"
        assert set(public_record["snapshot"]) == {"game", "round", "winner", "reason"}
        assert public_record["results"] == []
        host.screenshot(path=str(OUT / "undercover-result.png"))
        for page in pages:
            page.get_by_test_id("undercover-back-to-room").click()
            page.get_by_text("房间号", exact=True).wait_for()
            assert code in page.locator("body").inner_text()
        host.goto(BASE + "/history")
        history_record = host.get_by_test_id(f"history-record-{record_id}")
        expect(history_record).to_contain_text("谁是卧底")
        history_record.click()
        for word in set(words):
            expect(host.get_by_test_id("undercover-history")).to_contain_text(word)
        host.goto(BASE + f"/room/{code}")
        host.get_by_text("房间号", exact=True).wait_for()

        next_opening = start(previous_id=match_id)
        assert len({game["match_id"] for game in next_opening}) == 1
        assert set(game["your_word"] for game in next_opening) != set(words)
        assert all(not game["clues"] and not game["eliminations"] for game in next_opening)
        assert not errors, errors
        print("PASS 3: 自然终局、四人战绩、公开分享裁剪、原房续开、新对局标识与新词对", flush=True)
        browser.close()


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="gamehub-undercover-") as folder, (OUT / "undercover-e2e-server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, __file__, "--serve", str(port), str(Path(folder) / "test.db")],
                                   cwd=ROOT / "server", stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError("隔离测试后端启动失败，见 undercover-e2e-server.log")
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
