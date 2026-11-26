"""精神衰弱双浏览器回归。用 run_e2e_isolated.py 启动临时后端和数据库。"""

import json
import os
from pathlib import Path
import re
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("GAMEHUB_WEB_URL", "http://localhost:50540").rstrip("/")
OUT = Path(__file__).resolve().parent.parent / "tmp" / "checks"
CARD = '[data-testid^="concentration-card-"]'
SUITS = {"S": "黑桃", "H": "红桃", "C": "梅花", "D": "方块"}


def sign_in(page):
    page.goto(BASE, timeout=90000)
    page.get_by_text("注册", exact=True).first.click()
    page.get_by_placeholder("3-20位字母数字下划线").fill("memory_" + uuid4().hex[:9])
    page.get_by_placeholder("6-32位").fill("test123456")
    page.get_by_placeholder("再次输入密码").fill("test123456")
    with page.expect_response(lambda response: response.request.method == "POST"
                              and urlsplit(response.url).path == "/api/auth/register") as registration:
        page.get_by_text("注 册", exact=True).click()
    assert registration.value.ok
    return registration.value.json()["user"]["id"]


class Player:
    def __init__(self, browser, errors, *, touch=False):
        context = browser.new_context(viewport={"width": 320, "height": 568},
                                      has_touch=touch, reduced_motion="reduce")
        self.page = page = context.new_page()
        self.touch = touch
        self.messages = []
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("websocket", self.observe)
        self.uid = sign_in(page)

    def observe(self, socket):
        if urlsplit(socket.url).path.startswith("/ws/room/"):
            socket.on("framereceived", lambda payload: self.messages.append(json.loads(payload)))

    def game(self, kind, predicate=lambda game: True, *, offset=0):
        deadline = monotonic() + 15
        while monotonic() < deadline:
            for message in self.messages[offset:]:
                game = message.get("game")
                if message["type"] == kind and game and predicate(game):
                    return game
            self.page.wait_for_timeout(40)
        raise AssertionError(f"玩家 {self.uid} 未收到 {kind}，最近消息为 "
                             f"{[msg['type'] for msg in self.messages[-6:]]}")

    def card(self, index):
        return self.page.get_by_test_id(f"concentration-card-{index}")

    @property
    def status(self):
        return self.page.get_by_test_id("concentration-status")

    def flip(self, index):
        card = self.card(index)
        if self.touch:
            card.tap()
        else:
            card.click()

    def open_game(self):
        self.page.get_by_role("button", name="多人桌游", exact=True).click()
        self.page.get_by_test_id("game-card-concentration").click()

    def join(self, code):
        self.open_game()
        self.page.get_by_text("加入房间", exact=True).click()
        self.page.get_by_placeholder("输入6位房间号").fill(code)
        self.page.get_by_text("加入", exact=True).click()
        expect(self.page.get_by_text("准 备", exact=True)).to_be_visible()


def start_round(host, peers, count, previous_match=None):
    for peer in peers:
        peer.page.get_by_text("准 备", exact=True).click()
    players = (host, *peers)
    offsets = [len(player.messages) for player in players]
    host.page.get_by_text("开始游戏", exact=True).click()
    starts = [player.game("game_start", lambda state: state["match_id"] != previous_match, offset=offset)
              for player, offset in zip(players, offsets)]
    state = starts[0]
    assert all(start == state for start in starts)
    assert state["remaining_pairs"] == count // 2
    assert {score["id"] for score in state["scores"]} == {player.uid for player in players}
    assert state["slots"] == [{"state": "hidden"}] * count
    for player in players:
        expect(player.page.locator(CARD)).to_have_count(count)
        assert_concealed(player, range(count), "暗牌")
    return state


def assert_concealed(player, indexes, label):
    for index in indexes:
        card = player.card(index)
        row, col = divmod(index, 4)
        expect(card).to_have_attribute("aria-label", f"{row + 1} 行 {col + 1} 列，{label}")
        assert not card.get_by_text(re.compile(r"^(?:[A2-9]|10|[JQK]|[♠♥♣♦])$")).count(), index
        assert not card.locator("svg").count(), index


def board_positions(player):
    board = player.page.get_by_test_id("concentration-board")
    return board.locator(CARD).evaluate_all("""(cards) => {
        const origin = cards[0].getBoundingClientRect();
        return cards.map(card => {
            const { x, y, width, height } = card.getBoundingClientRect();
            return [x - origin.x, y - origin.y, width, height];
        });
    }""")


def assert_phone_layout(player):
    expect(player.status).to_be_in_viewport(ratio=0.99)
    expect(player.card(23)).to_be_in_viewport(ratio=0.99)
    positions = board_positions(player)
    assert_grid_order(positions)
    assert all(width >= 48 and height >= 48 for _, _, width, height in positions), positions
    assert player.page.locator("html").evaluate("el => el.scrollWidth === el.clientWidth")
    return positions


def assert_grid_order(positions):
    xs = sorted({x for x, _, _, _ in positions})
    ys = sorted({y for _, y, _, _ in positions})
    assert len(xs) == 4 and len(ys) == 6
    assert [(x, y) for x, y, _, _ in positions] == [(x, y) for y in ys for x in xs]


def assert_desktop_layout(player):
    page = player.page
    page.set_viewport_size({"width": 1280, "height": 900})
    page.wait_for_function("""() => {
        const board = document.querySelector('[data-testid="concentration-board"]').getBoundingClientRect();
        return ['concentration-score-list', 'concentration-status'].every(id =>
            document.querySelector(`[data-testid="${id}"]`)?.getBoundingClientRect().left >= board.right);
    }""")
    assert_grid_order(board_positions(player))
    page.screenshot(path=OUT / "concentration-desktop.png")
    page.set_viewport_size({"width": 320, "height": 568})


def change_card_count(host, count):
    host.page.get_by_test_id("room-settings-row").click()
    option = host.page.get_by_test_id(f"room-settings-concentration_cards-{count}")
    option.click()
    expect(option).to_have_attribute("aria-label", re.compile("当前"))
    host.page.get_by_test_id("room-settings-close").click()


def next_card(state, remembered, first=None):
    candidates = [i for i, slot in enumerate(state["slots"]) if slot["state"] == "hidden"]
    if first is not None:
        pair = next((i for i in candidates if remembered.get(i) == remembered[first]), None)
        if pair is not None:
            return pair
    else:
        known = [i for i in candidates if i in remembered]
        for position, index in enumerate(known):
            if any(remembered[other] == remembered[index] for other in known[position + 1:]):
                return index
    return next(i for i in candidates if i not in remembered)


def play_standard(host, peer, start, positions):
    players = {player.uid: player for player in (host, peer)}
    match = start["match_id"]

    def both_after(previous, phases):
        states = [player.game("concentration_state", lambda game: game["match_id"] == match
                              and game["revision"] > previous["revision"] and game["phase"] in phases)
                  for player in players.values()]
        assert states[0] == states[1]
        for slot in states[0]["slots"]:
            if slot["state"] != "revealed":
                assert set(slot) == {"state"}, slot
        return states[0]

    turn = start["turn_id"]
    active = players[turn]
    other = next(player for uid, player in players.items() if uid != turn)
    expect(other.card(1)).to_be_disabled()
    active.card(0).focus()
    active.page.keyboard.press("ArrowRight")
    expect(active.card(1)).to_be_focused()
    active.page.keyboard.press("Enter")
    state = both_after(start, {"second"})
    face = state["slots"][1]
    rank = face["rank"]
    assert state["turn_id"] == turn
    assert [i for i, slot in enumerate(state["slots"]) if slot["state"] == "revealed"] == [1]
    for player in players.values():
        expect(player.card(1)).to_contain_text(rank)
        expect(player.card(1)).to_have_attribute("aria-label", re.compile(rf"{SUITS[face['suit']]}\s+{rank}$"))
        expect(player.status).to_contain_text(re.compile(r"第\s*2\s*张"))
        assert_concealed(player, [0, 2, 23], "暗牌")

    offset = len(other.messages)
    other.page.reload(timeout=90000)
    synced = other.game("sync", lambda game: game["match_id"] == match, offset=offset)
    assert synced == state
    expect(other.card(1)).to_contain_text(rank)
    assert_concealed(other, [0, 2, 23], "暗牌")
    print("PASS 2: 键盘移焦与翻牌、双端明牌同步、隐藏牌面和刷新恢复", flush=True)

    remembered = {1: rank}
    first = 1
    observed = set()
    for attempt in range(24):
        active = players[state["turn_id"]]
        if state["phase"] == "first":
            first = next_card(state, remembered)
            active.flip(first)
            state = both_after(state, {"second"})
            remembered[first] = state["slots"][first]["rank"]
        second = next_card(state, remembered, first)
        if attempt == 0:
            active.card(second).focus()
            active.page.keyboard.press("Space")
        else:
            active.flip(second)
        reveal = both_after(state, {"revealing"})
        assert [i for i, slot in enumerate(reveal["slots"]) if slot["state"] == "revealed"] == sorted((first, second))
        remembered[second] = reveal["slots"][second]["rank"]
        matched = remembered[first] == remembered[second]
        outcome = "match" if matched else "miss"
        assert reveal["outcome"] == outcome
        assert reveal["turn_id"] == active.uid
        if outcome not in observed:
            status = "配对成功" if matched else "未配对"
            for player in players.values():
                expect(player.status).to_contain_text(status)
            expect(active.card(next(i for i in range(24) if i not in (first, second)))).to_be_disabled()
        settled = both_after(reveal, {"first", "finished"})
        if matched:
            assert settled["remaining_pairs"] == state["remaining_pairs"] - 1
            assert settled["turn_id"] == (active.uid if settled["remaining_pairs"] else None)
            assert all(settled["slots"][i] == {"state": "collected"} for i in (first, second))
        else:
            assert settled["remaining_pairs"] == state["remaining_pairs"]
            assert settled["turn_id"] != active.uid
            assert all(settled["slots"][i] == {"state": "hidden"} for i in (first, second))
        if outcome not in observed and settled["phase"] != "finished":
            for player in players.values():
                assert_concealed(player, [first, second], "空位" if matched else "暗牌")
                expect(player.page.locator(CARD)).to_have_count(24)
            assert board_positions(host) == positions
            observed.add(outcome)
        state = settled
        if state["phase"] == "finished":
            break
    assert state["remaining_pairs"] == 0 and sum(row["pairs"] for row in state["scores"]) == 12
    assert observed == {"match", "miss"}
    print("PASS 3: 配对收牌留空、失配盖回换手、自然结算", flush=True)
    return state


def main():
    if not os.environ.get("GAMEHUB_TEST_API"):
        raise SystemExit("Run with test/run_e2e_isolated.py e2e_concentration_smoke")
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("GAMEHUB_TEST_CHROMIUM"))
        errors = []
        host = Player(browser, errors)
        host.page.get_by_role("button", name="棋类对战", exact=True).click()
        expect(host.page.get_by_test_id("game-card-concentration")).to_have_count(0)
        peer = Player(browser, errors, touch=True)
        host.open_game()
        host.page.get_by_text("创建房间", exact=True).click()
        host.page.get_by_text("房间号", exact=True).wait_for()
        code = urlsplit(host.page.url).path.rsplit("/", 1)[-1]
        assert re.fullmatch(r"[A-Z0-9]{6}", code), host.page.url
        peer.join(code)
        host.page.get_by_test_id("room-settings-row").click()
        expect(host.page.get_by_test_id("room-settings-concentration_cards-24")).to_have_attribute(
            "aria-label", re.compile("当前"))
        host.page.get_by_test_id("room-settings-close").click()
        start = start_round(host, [peer], 24)
        assert_desktop_layout(host)
        positions = assert_phone_layout(host)
        host.page.screenshot(path=OUT / "concentration-phone.png")
        print("PASS 1: 多人桌游分类、默认 24 张、320×568 四列牌桌及 1280×900 右侧比分", flush=True)

        finished = play_standard(host, peer, start, positions)
        host.page.get_by_test_id("game-result-history").click()
        record = host.page.locator('[data-testid^="history-record-"]').filter(has_text="精神衰弱")
        record.click()
        history = host.page.get_by_test_id("concentration-history")
        expect(history).to_contain_text("24 张标准局")
        pairs = re.findall(r"(\d+)\s*对", history.inner_text())
        assert sorted(map(int, pairs)) == sorted(score["pairs"] for score in finished["scores"])
        host.page.go_back()
        peer.page.get_by_test_id("concentration-back-to-room").click()
        for player in (host, peer):
            expect(player.page.get_by_text("房间号", exact=True)).to_be_visible()
        change_card_count(host, 16)
        expect(peer.page.get_by_test_id("room-settings-row")).to_contain_text("16")
        quick = start_round(host, [peer], 16, finished["match_id"])
        print("PASS 4: 查看结算战绩、返回房间、切换 16 张并按新房规重新开局", flush=True)

        peer.page.get_by_test_id("concentration-leave").click()
        peer.page.get_by_test_id("concentration-confirm-leave").click()
        expect(peer.page.get_by_test_id("game-card-concentration")).to_be_visible()
        host.page.get_by_test_id("concentration-back-to-room").click()
        change_card_count(host, 24)
        newcomers = [Player(browser, errors, touch=True) for _ in range(2)]
        for member in (peer, *newcomers):
            member.join(code)
        start_round(host, [peer, *newcomers], 24, quick["match_id"])
        assert_phone_layout(host)
        scores = host.page.get_by_test_id("concentration-score-list").locator('[data-testid^="concentration-score-"]')
        expect(scores).to_have_count(4)
        boxes = [score.bounding_box() for score in scores.all()]
        assert boxes[0]["x"] == boxes[2]["x"] < boxes[1]["x"] == boxes[3]["x"], boxes
        assert boxes[0]["y"] == boxes[1]["y"] < boxes[2]["y"] == boxes[3]["y"], boxes
        expect(scores.last).to_be_in_viewport(ratio=0.99)
        host.page.screenshot(path=OUT / "concentration-phone-four.png")
        print("PASS 5: 对局确认离开、重新入房、四人标准局手机双列比分", flush=True)
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    main()
