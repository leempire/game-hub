"""Four-seat and responsive-layout smoke test with run_e2e_isolated.py."""

import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("GAMEHUB_WEB_URL", "http://localhost:50540").rstrip("/")
HAND = '[data-testid^="guandan-card-"]'


class Player:
    def __init__(self, browser, errors):
        context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        self.page = page = context.new_page()
        self.messages = []
        page.set_default_timeout(15000)

        def connect(socket):
            if urlsplit(socket.url).path.startswith("/ws/room/"):
                socket.on("framereceived", lambda payload: self.messages.append(json.loads(payload)))

        page.on("websocket", connect)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE, timeout=120000)
        page.get_by_text("注册", exact=True).first.click()
        page.get_by_placeholder("3-20位字母数字下划线").fill("gdweb_" + uuid4().hex[:9])
        page.get_by_placeholder("6-32位").fill("test123456")
        page.get_by_placeholder("再次输入密码").fill("test123456")
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and urlsplit(response.url).path == "/api/auth/register") as registration:
            page.get_by_text("注 册", exact=True).click()
        assert registration.value.ok
        self.uid = registration.value.json()["user"]["id"]
        page.get_by_role("button", name="多人桌游", exact=True).click()
        page.get_by_test_id("game-card-guandan").click()

    def received(self, kind, predicate=lambda _: True, offset=0):
        for _ in range(300):
            for message in self.messages[offset:]:
                if message["type"] == kind and predicate(message):
                    return message
            self.page.wait_for_timeout(50)
        raise AssertionError(f"Expected {kind}, recent messages: {self.messages[-4:]}")


def main():
    if not os.environ.get("GAMEHUB_TEST_API"):
        raise SystemExit("Run with test/run_e2e_isolated.py e2e_guandan_smoke")
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("GAMEHUB_TEST_CHROMIUM"))
        players = [Player(browser, errors) for _ in range(4)]
        by_id = {player.uid: player for player in players}
        host = players[0].page
        host.get_by_text("创建房间", exact=True).click()
        host.get_by_text("房间号", exact=True).wait_for()
        code = re.search(r"房间号\s*([A-Z0-9]{6})", host.locator("body").inner_text())[1]
        for player in players[1:]:
            page = player.page
            page.get_by_text("加入房间", exact=True).click()
            page.get_by_placeholder("输入6位房间号").fill(code)
            page.get_by_text("加入", exact=True).click()
            page.get_by_text("准 备", exact=True).click()
        host.get_by_text("开始游戏", exact=True).click()
        starts = [player.received("game_start")["game"] for player in players]
        assert len({card["id"] for state in starts for card in state["hand"]}) == 108
        assert all("hands" not in state and state["remaining"].count(None) == 3 for state in starts)
        for player in players:
            expect(player.page.locator(HAND)).to_have_count(27)

        state = starts[0]
        match = state["match_id"]
        leader = by_id[state["current_id"]]
        page = leader.page
        for width, height, rows in ((320, 568, 3), (390, 844, 2), (667, 375, 2), (1440, 900, 1)):
            page.set_viewport_size({"width": width, "height": height})
            expect(page.locator('[data-testid^="guandan-hand-row-"]')).to_have_count(rows)
            expect(page.get_by_test_id("guandan-play")).to_be_in_viewport()
            for slot in page.locator(HAND).all():
                expect(slot).to_be_in_viewport(ratio=0.99)
                assert slot.bounding_box()["width"] >= 24
            assert page.locator("html").evaluate("el => el.scrollWidth === el.clientWidth")

        page.set_viewport_size({"width": 320, "height": 568})
        pick = starts[players.index(leader)]["hand"][0]["id"]
        card = page.get_by_test_id(f"guandan-card-{pick}")
        card.focus()
        page.keyboard.press("Space")
        expect(card).to_have_attribute("aria-pressed", "true")
        page.get_by_test_id("guandan-expand").click()
        expect(page.get_by_test_id(f"guandan-large-card-{pick}")).to_have_attribute("aria-pressed", "true")
        page.get_by_test_id("guandan-picker-close").click()
        expect(page.get_by_test_id("guandan-picker")).to_have_count(0)
        page.set_viewport_size({"width": 667, "height": 375})
        expect(card).to_have_attribute("aria-pressed", "true")
        page.get_by_test_id("guandan-play").click()
        state = leader.received("guandan_state", lambda m: m["game"]["revision"] > state["revision"])["game"]
        assert state["last_play"]["uid"] == leader.uid and state["last_play"]["cards"][0]["id"] == pick
        expect(page.locator(HAND)).to_have_count(26)
        offset = len(leader.messages)
        page.reload(timeout=90000)
        restored = leader.received("sync", lambda m: (m.get("game") or {}).get("match_id") == match, offset)["game"]
        assert restored == state

        for _ in range(3):
            by_id[state["current_id"]].page.get_by_test_id("guandan-pass").click()
            state = leader.received("guandan_state", lambda m: m["game"]["revision"] > state["revision"])["game"]
        assert state["current_id"] == leader.uid and state["last_play"] is None
        page.go_back()
        expect(page.get_by_test_id("guandan-confirm")).to_be_visible()
        page.get_by_text("取消", exact=True).click()
        page.get_by_text("认输", exact=True).click()
        page.get_by_text("确认认输", exact=True).click()
        for player in players:
            assert player.received("game_over")["summary"]["reason"] == "surrender"
            player.page.get_by_test_id("guandan-back-to-room").click()
            expect(player.page.get_by_text(code, exact=True)).to_be_visible()
        assert not errors, errors
        print("PASS: four seats, private hands, four viewport sizes, picker, play/pass, reload and result")

        solo = Player(browser, errors)
        page = solo.page
        page.get_by_text("创建房间", exact=True).click()
        page.get_by_text("房间号", exact=True).wait_for()
        for count, difficulty in enumerate(("简单", "普通", "困难"), 1):
            page.get_by_text(difficulty, exact=True).click()
            page.get_by_text("添加人机", exact=True).click()
            solo.received("lobby", lambda m: sum(p.get("is_bot", False) for p in m["room"]["players"]) == count)
            expect(page.get_by_text(f"{difficulty}难度", exact=True)).to_be_visible()
        page.get_by_text("开始游戏", exact=True).click()
        state = solo.received("game_start")["game"]
        assert len([uid for uid in state["players"] if uid < 0]) == 3
        if state["current_id"] != solo.uid:
            state = solo.received("guandan_state", lambda m: m["game"]["current_id"] == solo.uid)["game"]
        revision = state["revision"]
        page.get_by_test_id("guandan-hint").click()
        candidate = solo.received("guandan_hint", lambda m: m["revision"] == revision)["combo"]
        page.get_by_test_id("guandan-play" if candidate else "guandan-pass").click()
        solo.received("guandan_state", lambda m: m["game"]["revision"] >= revision + 2)
        page.get_by_test_id("guandan-surrender").click()
        page.get_by_test_id("guandan-confirm-submit").click()
        expect(page.get_by_test_id("guandan-back-to-room")).to_be_visible()
        page.get_by_test_id("game-result-history").click()
        record = page.get_by_test_id(re.compile(r"^history-record-")).filter(has_text="掼蛋")
        record.click()
        history = page.get_by_test_id("guandan-history")
        expect(history).to_contain_text("人机·棋手3")
        expect(record.get_by_text(re.compile(r"^bot_[A-Z0-9]{6}$"))).to_have_count(0)
        assert not errors, errors
        print("PASS: one human, three bot difficulties, automatic turn and result")


if __name__ == "__main__":
    main()
