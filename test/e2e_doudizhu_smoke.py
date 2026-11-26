"""斗地主三人浏览器冒烟：运行 test/run_e2e_isolated.py e2e_doudizhu_smoke。"""

import json
import os
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("GAMEHUB_WEB_URL", "http://localhost:50540").rstrip("/")
HAND_CARDS = '[data-testid^="doudizhu-card-"]'
LARGE_CARDS = '[data-testid^="doudizhu-large-card-"]'


def visible_in_viewport(locator):
    for item in locator.all():
        expect(item).to_be_in_viewport(ratio=0.99)


def selected_cards(cards, prefix):
    ids = cards.evaluate_all("elements => elements.filter(element => element.getAttribute('aria-pressed') === 'true')"
                             ".map(element => element.dataset.testid)")
    return [test_id.removeprefix(prefix) for test_id in ids]


class Player:
    def __init__(self, browser, index, errors):
        context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        self.page = page = context.new_page()
        self.incoming, self.outgoing = [], []
        page.set_default_timeout(15000)

        def receive(payload):
            message = json.loads(payload)
            self.incoming.append(message)
            if message.get("type") == "error":
                errors.append(f"玩家 {index + 1}: {message['message']}")

        def connect(socket):
            if urlsplit(socket.url).path.startswith("/ws/room/"):
                socket.on("framereceived", receive)
                socket.on("framesent", lambda payload: self.outgoing.append(json.loads(payload)))

        page.on("pageerror", lambda error: errors.append(f"玩家 {index + 1}: {error}"))
        page.on("websocket", connect)
        page.goto(BASE, timeout=90000)
        page.get_by_text("注册", exact=True).first.click()
        page.get_by_placeholder("3-20位字母数字下划线").fill(f"doudizhu_web_{index}")
        page.get_by_placeholder("6-32位").fill("test123456")
        page.get_by_placeholder("再次输入密码").fill("test123456")
        with page.expect_response(lambda response: response.request.method == "POST"
                                  and urlsplit(response.url).path == "/api/auth/register") as registration:
            page.get_by_text("注 册", exact=True).click()
        assert registration.value.ok, registration.value.status
        self.uid = registration.value.json()["user"]["id"]
        page.get_by_role("button", name="多人桌游", exact=True).click()
        card = page.get_by_test_id("game-card-doudizhu")
        expect(card).to_contain_text("斗地主")
        card.click()

    def wait_message(self, kind, predicate=lambda _: True, *, after=0):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for message in self.incoming[after:]:
                if message.get("type") == kind and predicate(message):
                    return message
            self.page.wait_for_timeout(50)
        raise AssertionError(f"玩家 {self.uid} 等待 {kind} 超时，最近消息 "
                             f"{[message.get('type') for message in self.incoming[-8:]]}")


def check_room_settings(players):
    host, guest = (player.page for player in players[:2])
    rows = [player.page.get_by_test_id("room-settings-row") for player in players]
    for row in rows:
        expect(row).to_contain_text("经典")

    rows[0].click()
    rows[1].click()
    guest_sheet = guest.get_by_test_id("room-settings-sheet")
    expect(guest_sheet).to_contain_text("只有房主能改")
    expect(guest.get_by_test_id("room-settings-preset-strict")).to_have_count(0)

    host.get_by_test_id("room-settings-preset-strict").click()
    expect(guest_sheet.get_by_text("严格带牌", exact=True)).to_be_visible()
    expect(rows[2]).to_contain_text("严格带牌")
    for key in ("airplane_pair_as_singles", "four_pair_as_singles"):
        expect(host.get_by_test_id(f"room-settings-{key}-off")).to_have_attribute("aria-label", re.compile(r"（当前）$"))
    host.get_by_test_id("room-settings-airplane_pair_as_singles-on").click()
    expect(guest_sheet.get_by_text("自定义", exact=True)).to_be_visible()
    expect(rows[2]).to_contain_text("自定义")
    host.get_by_test_id("room-settings-four_pair_as_singles-on").click()
    expect(guest_sheet.get_by_text("经典", exact=True)).to_be_visible()

    for page in (host, guest):
        page.get_by_test_id("room-settings-close").click()
    for row in rows:
        expect(row).to_contain_text("经典")


def check_mobile_hand(landlord):
    page = landlord.page
    page.set_viewport_size({"width": 320, "height": 568})
    rows = [page.get_by_test_id(f"doudizhu-hand-row-{row}").locator(HAND_CARDS) for row in (1, 2)]
    for row in rows:
        expect(row).to_have_count(10)
        visible_in_viewport(row)
        assert all(card.bounding_box()["width"] >= 24 for card in row.all()), "紧凑手牌的触控宽度不足 24px"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "320px 牌桌出现横向溢出"
    assert rows[0].first.bounding_box()["y"] < rows[1].first.bounding_box()["y"], "窄屏手牌没有分行"

    before = len(landlord.outgoing)
    rows[0].last.focus()
    page.keyboard.press("Tab")
    expect(rows[1].first).to_be_focused()
    page.keyboard.press("Space")
    expect(rows[1].first).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Enter")
    expect(rows[1].first).to_have_attribute("aria-pressed", "false")
    for card in (rows[0].last, rows[1].first):
        card.tap()
        expect(card).to_have_attribute("aria-pressed", "true")
    table_cards = page.locator(HAND_CARDS)
    cross_row = set(selected_cards(table_cards, "doudizhu-card-"))
    assert len(cross_row) == 2, "跨行点选丢失选中牌"
    assert not any(msg.get("type") == "doudizhu_action" for msg in landlord.outgoing[before:])

    expand = page.get_by_test_id("doudizhu-expand")
    expect(expand).to_be_in_viewport(ratio=0.99)
    expand.tap()
    picker = page.get_by_test_id("doudizhu-picker")
    expect(picker).to_be_visible()
    large_cards = picker.locator(LARGE_CARDS)
    expect(large_cards).to_have_count(20)
    assert set(selected_cards(large_cards, "doudizhu-large-card-")) == cross_row
    for card in large_cards.all():
        size = card.bounding_box()
        assert size and size["width"] >= 48 and size["height"] >= 48, "放大选牌的触控目标不足 48×48"

    extra = large_cards.nth(19)
    assert extra.get_attribute("aria-pressed") == "false"
    extra.tap()
    extra_id = extra.get_attribute("data-testid").removeprefix("doudizhu-large-card-")
    cross_row.add(extra_id)
    expect(extra).to_have_attribute("aria-pressed", "true")
    for action in ("pass", "hint", "play"):
        button = picker.get_by_test_id(f"doudizhu-large-{action}")
        expect(button).to_be_in_viewport(ratio=0.99)
        assert button.bounding_box()["height"] >= 48
    page.go_back(wait_until="commit")
    expect(picker).to_be_hidden()
    assert set(selected_cards(table_cards, "doudizhu-card-")) == cross_row
    assert not any(msg.get("type") == "doudizhu_action" for msg in landlord.outgoing[before:])

    for width, height in ((667, 375), (1440, 900)):
        page.set_viewport_size({"width": width, "height": height})
        expect(rows[0]).to_have_count(20)
        expect(rows[1]).to_have_count(0)
        visible_in_viewport(rows[0])
        assert set(selected_cards(table_cards, "doudizhu-card-")) == cross_row


def main():
    if not os.environ.get("GAMEHUB_TEST_API"):
        raise SystemExit("请用 test/run_e2e_isolated.py e2e_doudizhu_smoke 启动隔离后端")

    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("GAMEHUB_TEST_CHROMIUM"))
        players = [Player(browser, index, errors) for index in range(3)]
        by_id = {player.uid: player for player in players}
        host = players[0].page
        host.get_by_text("创建房间", exact=True).click()
        host.get_by_text("房间号", exact=True).wait_for()
        match = re.search(r"房间号\s*([A-Z0-9]{6})", host.locator("body").inner_text())
        assert match, "未找到六位房间号"
        code = match[1]
        for player in players[1:]:
            page = player.page
            page.get_by_text("加入房间", exact=True).click()
            page.get_by_placeholder("输入6位房间号").fill(code)
            page.get_by_text("加入", exact=True).click()
            page.get_by_text("房间号", exact=True).wait_for()
        check_room_settings(players)
        print("PASS 1: 独立三人房间，房主切换严格带牌、自定义和经典，成员实时同步", flush=True)

        for player in players[1:]:
            player.page.get_by_text("准 备", exact=True).click()
        host.get_by_text("开始游戏", exact=True).click()
        opening = [player.wait_message("game_start")["game"] for player in players]
        match_id = opening[0]["match_id"]
        assert {game["match_id"] for game in opening} == {match_id}
        assert len({card for game in opening for card in game["hand"]}) == 51
        for player, game in zip(players, opening):
            expect(player.page.get_by_test_id("doudizhu-table")).to_be_visible()
            expect(player.page.locator(HAND_CARDS)).to_have_count(17)
            assert game["phase"] == "bidding" and game["bottom"] == [] and "hands" not in game
            assert game["rules"] == {"all_pass": "redeal", "airplane_pair_as_singles": True,
                                     "four_pair_as_singles": True}

        game = opening[0]
        for bid in (1, 2, 3):
            actor = by_id[game["current_id"]]
            for player in players:
                button = player.page.get_by_test_id(f"doudizhu-bid-{bid}")
                if player is actor:
                    expect(button).to_be_enabled()
                else:
                    expect(button).to_be_disabled()
            after = [len(player.incoming) for player in players]
            actor.page.get_by_test_id(f"doudizhu-bid-{bid}").click()
            revision = game["revision"]
            states = [player.wait_message("doudizhu_state", lambda msg: msg["game"]["revision"] > revision,
                                          after=offset)["game"] for player, offset in zip(players, after)]
            game = states[0]

        landlord = by_id[game["landlord_id"]]
        assert landlord is actor and len(game["bottom"]) == 3
        for player, state in zip(players, states):
            assert state["bottom"] == game["bottom"]
            expect(player.page.locator(HAND_CARDS)).to_have_count(20 if player is landlord else 17)
        check_mobile_hand(landlord)
        print("PASS 2: 三人递增叫分、私有手牌、公开底牌、320×568 跨行点选与放大网格、横竖屏重排", flush=True)

        page = landlord.page
        sent = len(landlord.outgoing)
        received = len(landlord.incoming)
        page.get_by_test_id("doudizhu-hint").click()
        hint = landlord.wait_message("doudizhu_hint", after=received)
        assert hint["cards"], "地主领出应能得到合法提示"
        own_cards = page.locator(HAND_CARDS)
        expect(page.locator(f'{HAND_CARDS}[aria-pressed="true"]')).to_have_count(len(hint["cards"]))
        assert set(selected_cards(own_cards, "doudizhu-card-")) == set(hint["cards"])
        expect(own_cards).to_have_count(20)
        assert [msg["action"] for msg in landlord.outgoing[sent:] if msg["type"] == "doudizhu_action"] == ["hint"]

        # A single card leaves enough of every possible deal for following and reconnecting.
        lead, *others = hint["cards"]
        for card in others:
            page.get_by_test_id(f"doudizhu-card-{card}").click()
        stable = game["revision"]
        page.get_by_test_id("doudizhu-play").click()
        played = [player.wait_message("doudizhu_state", lambda msg: msg["game"]["revision"] > stable)["game"]
                  for player in players]
        for player, state in zip(players, played):
            assert state["last_play"]["player_id"] == landlord.uid
            assert state["last_play"]["cards"] == [lead]
            expect(player.page.get_by_test_id("doudizhu-public-play")).to_contain_text("出牌 · 单张 · 1 张")
        expect(own_cards).to_have_count(19)
        saved = next(state for player, state in zip(players, played) if player is landlord)
        received = len(landlord.incoming)
        page.reload(timeout=90000)
        synced = landlord.wait_message("sync", lambda msg: (msg.get("game") or {}).get("match_id") == match_id,
                                       after=received)["game"]
        for key in ("revision", "current_id", "hand", "last_play"):
            assert synced[key] == saved[key], f"刷新后 {key} 丢失"
        expect(own_cards).to_have_count(len(saved["hand"]))

        game = synced
        for _ in range(2):
            revision = game["revision"]
            by_id[game["current_id"]].page.get_by_test_id("doudizhu-pass").click()
            game = landlord.wait_message("doudizhu_state", lambda msg: msg["game"]["revision"] > revision)["game"]
        assert game["current_id"] == landlord.uid and game["last_play"] is None
        expect(page.get_by_test_id("doudizhu-public-play")).to_contain_text("轮到你领出")

        page.get_by_text("认输", exact=True).click()
        page.get_by_text("确认认输", exact=True).click()
        for player in players:
            summary = player.wait_message("game_over", lambda msg: msg["summary"]["match_id"] == match_id)["summary"]
            assert summary["winner"] == "farmers" and summary["reason"] == "surrender"
            button = player.page.get_by_test_id("doudizhu-back-to-room")
            expect(button).to_be_visible()
            expect(player.page.get_by_text("农民胜利", exact=True)).to_be_visible()
            button.click()
            player.page.get_by_text("房间号", exact=True).wait_for()
            expect(player.page.get_by_text(code, exact=True)).to_be_visible()
            expect(player.page.get_by_test_id("doudizhu-table")).to_have_count(0)
        for player in players[1:]:
            expect(player.page.get_by_text("准 备", exact=True)).to_be_visible()
        assert not errors, errors
        print("PASS 3: 提示只选牌、真实出牌同步、刷新恢复、认输结算并回到原房间", flush=True)

        solo = Player(browser, 3, errors)
        page = solo.page
        page.get_by_text("创建房间", exact=True).click()
        page.get_by_text("房间号", exact=True).wait_for()
        for count, difficulty in enumerate(("简单", "困难"), 1):
            offset = len(solo.incoming)
            page.get_by_text(difficulty, exact=True).click()
            page.get_by_text("添加人机", exact=True).click()
            solo.wait_message("lobby", lambda msg: sum(p.get("is_bot", False) for p in msg["room"]["players"]) == count,
                              after=offset)
            expect(page.get_by_text(f"{difficulty}难度", exact=True)).to_be_visible()
        page.get_by_text("开始游戏", exact=True).click()
        state = solo.wait_message("game_start")["game"]
        assert len([uid for uid in state["seat_uids"] if uid < 0]) == 2
        if state["current_id"] != solo.uid:
            state = solo.wait_message("doudizhu_state", lambda msg: msg["game"]["current_id"] == solo.uid)["game"]
        if state["phase"] == "bidding":
            revision = state["revision"]
            page.get_by_test_id("doudizhu-bid-3").click()
            state = solo.wait_message("doudizhu_state", lambda msg: msg["game"]["revision"] > revision)["game"]
        revision = state["revision"]
        offset = len(solo.incoming)
        page.get_by_test_id("doudizhu-hint").click()
        selection = solo.wait_message("doudizhu_hint", after=offset)["cards"]
        page.get_by_test_id("doudizhu-play" if selection else "doudizhu-pass").click()
        next_state = solo.wait_message("doudizhu_state", lambda msg: msg["game"]["revision"] > revision + 1)["game"]
        assert "hands" not in next_state
        page.get_by_text("认输", exact=True).click()
        page.get_by_text("确认认输", exact=True).click()
        expect(page.get_by_test_id("doudizhu-back-to-room")).to_be_visible()
        assert not errors, errors
        print("PASS 4: 加两名不同难度的人机，单人开局、自动叫分与出牌、正常结算", flush=True)
        browser.close()


if __name__ == "__main__":
    main()
