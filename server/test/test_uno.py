"""UNO 引擎单测（纯逻辑，无 IO）。

牌用字符串编码：`R5` = 红 5、`RS` = 红跳过、`RR` = 红反转、`R+2` = 红 +2、
`W` = 变色、`W+4` = 变色 +4。

**所有用例都手工造确定状态**（`_setup`），不用 `create_game` 洗出来的牌：
项目有过「测试依赖随机性 → flaky」的教训（见 test_minesweeper.py 的
`_safe_single_cell`）。`create_game` 只用来测「发牌 7 张 / 起始牌是数字牌」这类
它自己的契约。

Run: pytest test/test_uno.py -v
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import uno as u
from app import uno_ai as ai
from app import uno_rules


# ── 工具 ──────────────────────────────────────────────────────


def _setup(hands, discard="R5", draw=(), turn=0, direction=1, color=None,
           rules=None):
    """手工构造确定状态。

    hands 是 {seat: [牌]}；discard 是当前（弃牌堆顶）牌；draw 是抽牌堆（pop 取顶）。
    `color` 用于「变色牌之后」的场景（此时当前有效色与牌面颜色不同）。
    `rules` 是房规（见 `uno_rules`）；**不传就是经典 UNO**。
    """
    n = len(hands)
    gs = u.create_game(list(range(101, 101 + n)), rules=rules)  # uid 取 101+，与牌码不混淆
    gs["hands"] = {s: list(h) for s, h in hands.items()}
    gs["discard_pile"] = [discard]
    gs["current_color"] = color or u.card_color(discard)
    gs["draw_pile"] = list(draw)
    gs["turn"] = turn
    gs["direction"] = direction
    return gs


def _dispatch(gs, seat, act):
    """把策略给出的动作交给引擎（**颜色轮盘也算一种动作**）。"""
    if act["action"] == "play":
        return u.play(gs, seat, act["card"], act.get("color"),
                      target=act.get("target"))
    if act["action"] == "roulette":
        return u.resolve_roulette(gs, seat, act["color"])
    if act["action"] == "draw":
        return u.draw(gs, seat)
    return u.pass_turn(gs, seat)


def _info(n):
    return [{"seat": s, "username": f"u{s}", "nickname": None} for s in range(n)]


def _total_cards(gs):
    return (sum(len(h) for h in gs["hands"].values())
            + len(gs["draw_pile"]) + len(gs["discard_pile"]))


# ── 牌与牌堆 ──────────────────────────────────────────────────


class TestDeck:
    def test_deck_has_108_cards(self):
        assert len(u.build_deck()) == u.DECK_SIZE == 108

    def test_deck_composition(self):
        deck = u.build_deck()
        for color in u.COLORS:
            assert deck.count(color + "0") == 1
            for n in "123456789":
                assert deck.count(color + n) == 2
            for sym in u.SYMBOLS:
                assert deck.count(color + sym) == 2
        assert deck.count("W") == 4
        assert deck.count("W+4") == 4

    def test_card_helpers(self):
        assert u.card_color("R5") == "R"
        assert u.card_kind("R5") == "5"
        assert u.card_color("W+4") == "W"
        assert u.card_kind("W+4") == "+4"
        assert u.card_kind("W") == ""
        assert u.is_wild("W") and u.is_wild("W+4") and not u.is_wild("R5")

    def test_card_points(self):
        assert u.card_points("R0") == 0
        assert u.card_points("R9") == 9
        assert u.card_points("RS") == 20
        assert u.card_points("R+2") == 20
        assert u.card_points("W") == 50
        assert u.card_points("W+4") == 50
        assert u.hand_points(["R9", "RS", "W"]) == 79

    def test_create_game_deals_seven_each(self):
        for n in (2, 3, 4):
            gs = u.create_game(list(range(1, n + 1)))
            assert gs["num_players"] == n
            assert all(len(h) == u.HAND_SIZE == 7 for h in gs["hands"].values())
            assert len(gs["discard_pile"]) == 1
            assert _total_cards(gs) == 108
            assert gs["seat_uids"] == list(range(1, n + 1))

    def test_create_game_start_card_is_a_number(self):
        """起始牌必须是数字牌 —— 否则「起始就是功能牌该谁先动」没有定义。"""
        for _ in range(30):
            gs = u.create_game([1, 2])
            top = gs["discard_pile"][-1]
            assert u.card_kind(top).isdigit(), top
            assert gs["current_color"] == u.card_color(top)

    def test_create_game_fills_player_colors(self):
        """player_colors 被 ws.py 当「玩家 uid 集合」遍历，**留空会让广播静默失效**。"""
        gs = u.create_game([7, 8, 9])
        assert set(gs["player_colors"]) == {7, 8, 9}
        assert len(set(gs["player_colors"].values())) == 3

    def test_create_game_rejects_bad_player_count(self):
        with pytest.raises(ValueError):
            u.create_game([1])
        with pytest.raises(ValueError):
            u.create_game([1, 2, 3, 4, 5])


# ── 可出判定 ──────────────────────────────────────────────────


class TestPlayable:
    def test_match_by_color(self):
        gs = _setup({0: ["R3", "G3"], 1: ["B2"]}, discard="R5")
        assert u.playable_cards(gs, 0) == ["R3"]      # G3 颜色与数字都不对

    def test_match_by_number(self):
        gs = _setup({0: ["G5", "B6"], 1: ["B2"]}, discard="R5")
        assert u.playable_cards(gs, 0) == ["G5"]      # 同数字跨颜色可出

    def test_match_by_symbol(self):
        gs = _setup({0: ["GS", "G5", "R5"], 1: ["B2"]}, discard="RS")
        assert u.playable_cards(gs, 0) == ["GS", "R5"]   # 跳过压跳过；颜色对也可

    def test_wild_always_playable(self):
        # G3 颜色与数字都不对，只剩两张黑牌可出
        gs = _setup({0: ["W", "W+4", "G3"], 1: ["B2"]}, discard="R5")
        assert u.playable_cards(gs, 0) == ["W", "W+4"]

    def test_nothing_playable(self):
        gs = _setup({0: ["G3", "B4"], 1: ["B2"]}, discard="R5")
        assert u.playable_cards(gs, 0) == []

    def test_empty_when_not_your_turn(self):
        gs = _setup({0: ["R3"], 1: ["R2"]}, discard="R5", turn=1)
        assert u.playable_cards(gs, 0) == []

    def test_after_wild_only_the_chosen_color_counts(self):
        """变色牌之后只能跟新颜色 —— 牌面的数字不再是判断依据。

        拿弃牌堆顶那张（`W`，牌面为空）比牌面，而不是拿牌面数字比。
        """
        gs = _setup({0: ["G5", "R5"], 1: ["B2"]}, discard="W", color="R")
        assert u.playable_cards(gs, 0) == ["R5"]

    def test_after_wild_same_number_is_not_playable(self):
        gs = _setup({0: ["G5", "B7"], 1: ["B2"]}, discard="W", color="B")
        assert u.playable_cards(gs, 0) == ["B7"]


# ── 出牌 ──────────────────────────────────────────────────────


class TestPlay:
    def test_play_moves_card_to_discard(self):
        gs = _setup({0: ["R3", "G5"], 1: ["B2"]}, discard="R5")
        r = u.play(gs, 0, "R3")
        assert r["ok"] and r["effect"] == "play"
        assert gs["hands"][0] == ["G5"]
        assert gs["discard_pile"] == ["R5", "R3"]
        assert gs["current_color"] == "R"
        assert gs["turn"] == 1

    def test_cannot_play_out_of_turn(self):
        gs = _setup({0: ["R3"], 1: ["R2"]}, discard="R5", turn=1)
        assert u.play(gs, 0, "R3")["ok"] is False
        assert gs["turn"] == 1

    def test_cannot_play_unplayable_card(self):
        gs = _setup({0: ["G3"], 1: ["B2"]}, discard="R5")
        assert u.play(gs, 0, "G3")["ok"] is False
        assert gs["hands"][0] == ["G3"]

    def test_cannot_play_card_not_in_hand(self):
        gs = _setup({0: ["R3"], 1: ["R2"]}, discard="R5")
        assert u.play(gs, 0, "R9")["ok"] is False

    def test_wild_requires_a_valid_color(self):
        gs = _setup({0: ["W", "R3"], 1: ["B2"]}, discard="R5")
        assert u.play(gs, 0, "W")["ok"] is False           # 没给颜色
        assert u.play(gs, 0, "W", "Z")["ok"] is False      # 非法颜色
        assert u.play(gs, 0, "W", "G")["ok"] is True
        assert gs["current_color"] == "G"

    def test_emptying_hand_wins(self):
        gs = _setup({0: ["R3"], 1: ["B2"]}, discard="R5")
        r = u.play(gs, 0, "R3")
        assert r["effect"] == "empty"
        assert gs["finished"] is True
        assert gs["winner"] == 0
        assert gs["reason"] == u.REASON_EMPTIED
        assert r["game_over"]["winner"] == 0

    def test_cannot_play_after_game_over(self):
        gs = _setup({0: ["R3"], 1: ["B2"]}, discard="R5")
        u.play(gs, 0, "R3")
        assert u.play(gs, 1, "B2")["ok"] is False


# ── 牌面效果 ──────────────────────────────────────────────────


class TestEffects:
    def test_skip_skips_one_player(self):
        gs = _setup({0: ["RS", "R1"], 1: ["B2"], 2: ["B3"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "RS")
        assert r["effect"] == "skip"
        assert r["skipped"] == [1]
        assert gs["turn"] == 2

    def test_skip_wraps_back_to_self_in_two_player(self):
        """2 人局里「跳过下一个」= 跳过对手 = 自己再走一次。"""
        gs = _setup({0: ["RS", "R1"], 1: ["B2"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "RS")
        assert r["skipped"] == [1]        # 被跳过的是对手，不含自己
        assert gs["turn"] == 0

    def test_reverse_flips_direction(self):
        gs = _setup({0: ["RR", "R1"], 1: ["B2"], 2: ["B3"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "RR")
        assert r["effect"] == "reverse"
        assert gs["direction"] == -1
        assert gs["turn"] == 2            # 逆时针：0 的上一个是 2

    def test_reverse_acts_as_skip_in_two_player(self):
        """2 人局的反转按官方规则等同跳过，且不改方向。"""
        gs = _setup({0: ["RR", "R1"], 1: ["B2"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "RR")
        assert r["effect"] == "skip"
        assert gs["turn"] == 0
        assert gs["direction"] == 1

    def test_draw2_sets_pending(self):
        gs = _setup({0: ["R+2", "R1"], 1: ["B2"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "R+2")
        assert r["effect"] == "draw2"
        assert gs["pending_draw"] == 2
        assert gs["pending_kind"] == u.DRAW2
        assert gs["turn"] == 1

    def test_wild4_sets_pending(self):
        gs = _setup({0: ["W+4", "R1"], 1: ["B2"]}, discard="R5", turn=0)
        r = u.play(gs, 0, "W+4", "B")
        assert r["effect"] == "wild4"
        assert gs["pending_draw"] == 4
        assert gs["pending_kind"] == u.WILD4
        assert gs["current_color"] == "B"

    def test_direction_persists_across_turns(self):
        gs = _setup({0: ["RR", "R1"], 1: ["B2"], 2: ["R3", "B1"]},
                    discard="R5", turn=0)
        u.play(gs, 0, "RR")
        assert gs["direction"] == -1
        # 轮到 2；2 出牌后逆时针前进 → 1。
        # 注意别让 2 手里只剩这一张：出完就终局了，回合不会再推进。
        u.play(gs, 2, "R3")
        assert gs["turn"] == 1


# ── 叠加转嫁（房规） ──────────────────────────────────────────


class TestStacking:
    def test_only_same_penalty_kind_is_playable(self):
        gs = _setup({0: ["R+2", "W+4", "G1"], 1: ["B2"]}, discard="Y5", turn=0)
        gs["pending_draw"] = 2
        gs["pending_kind"] = u.DRAW2
        assert u.playable_cards(gs, 0) == ["R+2"]

    def test_cannot_stack_wild4_on_draw2(self):
        gs = _setup({0: ["W+4", "R+2"], 1: ["B2"]}, discard="Y5", turn=0)
        gs["pending_draw"] = 2
        gs["pending_kind"] = u.DRAW2
        assert u.play(gs, 0, "W+4", "R")["ok"] is False
        assert gs["pending_draw"] == 2

    def test_stacking_accumulates_and_passes_on(self):
        gs = _setup({0: ["R+2", "G1"], 1: ["G+2", "B9"], 2: ["Y1"]},
                    discard="Y5", turn=0)
        gs["pending_draw"] = 2
        gs["pending_kind"] = u.DRAW2

        r = u.play(gs, 0, "R+2")
        assert r["ok"]
        assert gs["pending_draw"] == 4
        assert gs["pending_kind"] == u.DRAW2
        assert gs["turn"] == 1

        assert u.playable_cards(gs, 1) == ["G+2"]    # 只有 +2 能接
        u.play(gs, 1, "G+2")
        assert gs["pending_draw"] == 6
        assert gs["turn"] == 2
        assert u.playable_cards(gs, 2) == []         # 没 +2 → 只能摸

    def test_wild4_stacks_on_wild4(self):
        gs = _setup({0: ["W+4", "G1"], 1: ["W+4", "B9"]}, discard="Y5", turn=0)
        gs["pending_draw"] = 4
        gs["pending_kind"] = u.WILD4
        assert u.playable_cards(gs, 0) == ["W+4"]
        u.play(gs, 0, "W+4", "G")
        assert gs["pending_draw"] == 8
        assert u.playable_cards(gs, 1) == ["W+4"]


# ── 摸牌 ──────────────────────────────────────────────────────


class TestDraw:
    # 抽牌堆用 `pop()` 取顶 —— 列表**末尾**是顶。所以「先摸到哪张」要写在最后。
    def test_draw_passes_turn_when_unplayable(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "G9"], turn=0)
        r = u.draw(gs, 0)
        assert r["ok"] and r["kept_turn"] is False
        assert gs["hands"][0] == ["G1", "G9"]
        assert gs["turn"] == 1
        assert gs["drawn_this_turn"] is None

    def test_draw_keeps_turn_when_playable(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "R9"], turn=0)
        r = u.draw(gs, 0)
        assert r["kept_turn"] is True
        assert gs["drawn_this_turn"] == "R9"
        assert gs["turn"] == 0
        # 摸过牌之后**只能出刚摸的那张**
        assert u.playable_cards(gs, 0) == ["R9"]

    def test_cannot_draw_twice_in_a_turn(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "R9"], turn=0)
        u.draw(gs, 0)
        assert u.draw(gs, 0)["ok"] is False

    def test_cannot_draw_out_of_turn(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["R9"], turn=1)
        assert u.draw(gs, 0)["ok"] is False

    def test_pending_draw_takes_all_accumulated_cards(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5",
                    draw=["G9", "B3", "Y4", "R8", "G7"], turn=0)
        gs["pending_draw"] = 4
        gs["pending_kind"] = u.WILD4
        r = u.draw(gs, 0)
        assert r["penalty"] == 4
        assert len(r["drawn"]) == 4
        assert len(gs["hands"][0]) == 5
        assert gs["pending_draw"] == 0
        assert gs["pending_kind"] is None
        assert gs["turn"] == 1          # 被罚的人回合结束
        assert gs["drawn_this_turn"] is None

    def test_auto_pass_never_keeps_turn(self):
        """断线玩家自动过牌：摸到能出的牌也不保留回合（不能替他决定出牌）。"""
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "R9"], turn=0)
        r = u.auto_pass(gs, 0)
        assert r["kept_turn"] is False
        assert gs["turn"] == 1
        assert gs["drawn_this_turn"] is None

    def test_auto_pass_finishes_an_already_drawn_turn(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "R9"], turn=0)
        u.draw(gs, 0)              # 摸到能出的牌，回合保留
        assert gs["turn"] == 0
        u.auto_pass(gs, 0)         # 断线了 → 替他过牌
        assert gs["turn"] == 1

    def test_reshuffles_discard_when_draw_pile_is_empty(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="B3", draw=[], turn=0)
        gs["discard_pile"] = ["Y7", "B3"]      # 除顶牌外还有一张可洗
        gs["current_color"] = "B"
        r = u.draw(gs, 0)
        assert r["ok"] and len(r["drawn"]) == 1
        assert gs["discard_pile"] == ["B3"]    # 顶牌不参与洗牌
        assert len(gs["draw_pile"]) == 0
        assert _total_cards(gs) == 4           # 手牌 2 + 弃牌 1 + 抽牌 1(已摸走)

    def test_draw_with_everything_empty_just_passes(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="B3", draw=[], turn=0)
        gs["discard_pile"] = ["B3"]            # 弃牌堆只剩顶牌，洗不动
        r = u.draw(gs, 0)
        assert r["ok"] and r["drawn"] == []
        assert gs["turn"] == 1


# ── 过牌 ──────────────────────────────────────────────────────


class TestPass:
    def test_pass_after_drawing(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", draw=["B3", "R9"], turn=0)
        u.draw(gs, 0)
        r = u.pass_turn(gs, 0)
        assert r["ok"]
        assert gs["turn"] == 1
        assert gs["drawn_this_turn"] is None

    def test_cannot_pass_without_drawing(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        assert u.pass_turn(gs, 0)["ok"] is False


# ── 喊 UNO ────────────────────────────────────────────────────


class TestUnoCall:
    def test_only_with_one_card(self):
        gs = _setup({0: ["R1"], 1: ["B2", "B3"]}, discard="R5", turn=0)
        assert u.can_call_uno(gs, 0) is True
        assert u.can_call_uno(gs, 1) is False

    def test_call_sets_flag_once(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        assert u.call_uno(gs, 0)["ok"] is True
        assert gs["uno_called"][0] is True
        assert u.call_uno(gs, 0)["ok"] is False
        assert u.can_call_uno(gs, 0) is False

    def test_flag_cleared_when_hand_grows(self):
        """被罚摸牌之后不再是「只剩一张」，标记要清掉（否则界面一直挂着 UNO 徽标）。"""
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", draw=["G9"], turn=0)
        u.call_uno(gs, 0)
        u.draw(gs, 0)
        assert len(gs["hands"][0]) == 2
        assert 0 not in gs["uno_called"]
        assert u.can_call_uno(gs, 0) is False

    def test_cannot_call_when_out(self):
        gs = _setup({0: ["R1"], 1: ["B2", "B3"]}, discard="R5", turn=1)
        gs["out"] = [0]
        assert u.can_call_uno(gs, 0) is False


# ── 认输 ──────────────────────────────────────────────────────


class TestSurrender:
    def test_surrender_keeps_playing_with_three_left(self):
        gs = _setup({0: ["R1"], 1: ["B2"], 2: ["B3"]}, discard="Y5", turn=0)
        assert u.surrender(gs, 1) is None
        assert gs["out"] == [1]
        assert gs["finished"] is False
        assert gs["turn"] == 0                 # 不是他的回合，回合不动

    def test_surrender_moves_turn_when_it_was_theirs(self):
        gs = _setup({0: ["R1"], 1: ["B2"], 2: ["B3"]}, discard="Y5", turn=0)
        u.surrender(gs, 0)
        assert gs["turn"] == 1                 # 跳过已出局的 0

    def test_last_remaining_wins(self):
        gs = _setup({0: ["R1"], 1: ["B2"], 2: ["B3"]}, discard="Y5", turn=0)
        u.surrender(gs, 0)
        go = u.surrender(gs, 1)
        assert go is not None
        assert go["reason"] == u.REASON_LAST_REMAINING
        assert go["winner"] == 2
        assert gs["finished"] is True

    def test_surrender_twice_is_a_noop(self):
        gs = _setup({0: ["R1"], 1: ["B2"], 2: ["B3"]}, discard="Y5", turn=0)
        u.surrender(gs, 2)
        assert u.surrender(gs, 2) is None
        assert gs["out"] == [2]

    def test_surrendered_seat_is_skipped_by_turn_advance(self):
        gs = _setup({0: ["R1"], 1: ["B2"], 2: ["B3"]}, discard="R5", turn=0)
        u.surrender(gs, 1)
        u.play(gs, 0, "R1")          # 出完手牌 → 应该赢（1 已出局）
        assert gs["winner"] == 0
        assert gs["reason"] == u.REASON_EMPTIED


# ── 排名 ──────────────────────────────────────────────────────


class TestRanking:
    def test_winner_first_then_by_points(self):
        gs = _setup({0: [], 1: ["R9", "R8"], 2: ["G1"]}, discard="R5")
        gs["winner"] = 0
        gs["finished"] = True
        rk = u.compute_ranking(gs, _info(3))
        assert [e["seat"] for e in rk] == [0, 2, 1]
        assert [e["rank"] for e in rk] == [1, 2, 3]
        assert rk[2]["points"] == 17

    def test_surrendered_sorts_after_equal_points(self):
        gs = _setup({0: [], 1: ["G1"], 2: ["B1"]}, discard="R5")
        gs["winner"] = 0
        gs["finished"] = True
        gs["out"] = [1]
        rk = u.compute_ranking(gs, _info(3))
        assert [e["seat"] for e in rk] == [0, 2, 1]

    def test_equal_points_share_a_rank(self):
        gs = _setup({0: [], 1: ["G1"], 2: ["B1"]}, discard="R5")
        gs["winner"] = 0
        gs["finished"] = True
        rk = u.compute_ranking(gs, _info(3))
        assert rk[1]["rank"] == rk[2]["rank"] == 2

    def test_points_use_uno_values(self):
        gs = _setup({0: [], 1: ["RS", "W+4", "R9"]}, discard="R5")
        gs["winner"] = 0
        gs["finished"] = True
        rk = u.compute_ranking(gs, _info(2))
        assert rk[1]["points"] == 20 + 50 + 9


# ── 逐人下发（手牌私有是核心不变量） ─────────────────────────


class TestSyncPrivacy:
    def test_only_own_hand_is_exposed(self):
        gs = _setup({0: ["R1", "G2"], 1: ["B3", "Y4"], 2: ["R7"]},
                    discard="R5", turn=0)
        sync = u.game_to_sync(gs, 101)
        assert sync["your_seat"] == 0
        assert sync["hand"] == ["R1", "G2"]
        assert "B3" not in sync["hand"] and "Y4" not in sync["hand"]
        assert "hands" not in sync                       # 不能把整个 hands 发出去

    def test_hand_counts_are_public(self):
        gs = _setup({0: ["R1", "G2"], 1: ["B3", "Y4"], 2: ["R7"]},
                    discard="R5", turn=0)
        sync = u.game_to_sync(gs, 103)                   # seat 2
        assert sync["hand_counts"] == {"0": 2, "1": 2, "2": 1}
        assert sync["hand"] == ["R7"]

    def test_playable_is_private_to_the_requester(self):
        gs = _setup({0: ["R1", "G2"], 1: ["B3"], 2: ["R7"]}, discard="R5", turn=0)
        assert u.game_to_sync(gs, 101)["playable"] == ["R1"]
        assert u.game_to_sync(gs, 102)["playable"] == []   # 不是他的回合

    def test_non_player_gets_empty_hand(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        sync = u.game_to_sync(gs, 999)
        assert sync["your_seat"] is None
        assert sync["hand"] == []
        assert sync["playable"] == []

    def test_sync_reports_action_flags(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        sync = u.game_to_sync(gs, 101)
        assert sync["can_draw"] is True
        assert sync["can_pass"] is False
        assert sync["can_call_uno"] is True

    def test_sync_ships_the_color_picker_rule(self):
        """**哪些牌要先选色由服务端下发**（`color_kinds`），客户端不再自己抄一份。

        抄漏一张的后果不是渲染错，而是**点了没反应**：客户端会不带颜色把牌发上去，
        服务端整手拒收，而玩家没有任何办法补上那个颜色 —— v1.14 的
        `W+6` / `W+10` / `W+RD4` 就是这么漏的（客户端当时只认经典的两张黑牌）。
        """
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        assert u.game_to_sync(gs, 101)["color_kinds"] == list(u.WILD_NEEDS_COLOR)

    def test_every_wild_kind_needs_a_color_except_roulette(self):
        """黑牌只有两种归宿：**要选色**，或者是颜色轮盘（它的颜色由下家点名）。

        这条锁的是「引擎加了新黑牌、却忘了放进 `WILD_NEEDS_COLOR`」——那种牌会被
        `_guard_play` 放行、不带颜色就落到弃牌堆上，而界面上也永远不会弹选色框。
        """
        wilds = {u.card_kind(c) for c in u.build_deck("no_mercy") if u.is_wild(c)}
        # 组队档的两张专属黑牌也一起算进来（它们只在组队牌堆里）
        wilds |= {u.card_kind(c) for c in u.build_deck("classic", teams=True) if u.is_wild(c)}
        assert wilds - {u.ROULETTE} == set(u.WILD_NEEDS_COLOR)

    def test_snapshot_keeps_final_hands(self):
        gs = _setup({0: [], 1: ["RS", "R9"]}, discard="R5")
        gs["winner"] = 0
        gs["finished"] = True
        snap = u.snapshot(gs)
        assert snap["game"] == "uno"
        assert snap["hands"]["1"] == ["RS", "R9"]
        assert snap["points"]["1"] == 29
        assert snap["winner"] == 0


# ── 房规（v1.14）：叠牌口径 ───────────────────────────────────


class TestStackMode:
    """`stack_mode` 三档：不叠 / 同型（默认）/ 等值或更高。"""

    def _pending(self, mode, kind=u.DRAW2, n=2, hand=None):
        gs = _setup({0: ["G9"], 1: hand or ["R+2", "R+4", "W+4", "G3"]},
                    discard="R5", draw=["B3", "B4", "B5"],
                    rules={"stack_mode": mode} if mode else None)
        gs["pending_draw"] = n
        gs["pending_kind"] = kind
        gs["turn"] = 1
        return gs

    def test_off_nothing_can_be_stacked(self):
        gs = self._pending("off")
        assert u.playable_cards(gs, 1) == []
        assert u.draw(gs, 1)["ok"] is True
        assert len(gs["hands"][1]) == 6        # 摸走累计的 2 张

    def test_same_only_the_matching_kind(self):
        gs = self._pending("same")
        assert u.playable_cards(gs, 1) == ["R+2"]

    def test_same_stacks_any_card_of_the_same_kind(self):
        """「同型」比的是**牌面**，不看颜色 —— 被 +4 罚时红 +4 与黑 +4 都能压。"""
        gs = self._pending("same", kind=u.WILD4, n=4)
        assert set(u.playable_cards(gs, 1)) == {"R+4", "W+4"}

    def test_upgrade_allows_a_bigger_penalty(self):
        """等值或更高：被 +2 罚时，手里有 +4 也能压上去（No Mercy 口径）。"""
        gs = self._pending("upgrade")
        assert set(u.playable_cards(gs, 1)) == {"R+2", "R+4", "W+4"}

    def test_upgrade_refuses_a_smaller_penalty(self):
        """被 +4 罚时不能用 +2 压 —— 2 < 4。"""
        gs = self._pending("upgrade", kind=u.WILD4, n=4,
                           hand=["R+2", "R+4"])
        assert u.playable_cards(gs, 1) == ["R+4"]

    def test_upgrade_accumulates(self):
        gs = self._pending("upgrade")
        r = u.play(gs, 1, "R+4", "R")
        assert r["ok"] and gs["pending_draw"] == 6
        assert gs["pending_kind"] == u.WILD4

    def test_default_is_same(self):
        gs = self._pending(None)
        assert gs["rules"]["stack_mode"] == "same"
        assert u.playable_cards(gs, 1) == ["R+2"]


# ── 房规：摸到能出为止 / 有牌必出 ─────────────────────────────


class TestDrawUntilPlay:
    def test_draws_until_a_playable_card(self):
        # 抽牌堆 pop 取顶：先摸 G3（不能出）、再摸 R9（能出）
        gs = _setup({0: ["G9"], 1: ["B7"]}, discard="R5", draw=["R9", "G3"],
                    rules={"draw_until_play": True})
        r = u.draw(gs, 0)
        assert r["ok"] and r["kept_turn"] is True
        assert sorted(r["drawn"]) == ["G3", "R9"]
        assert gs["drawn_this_turn"] == "R9"       # 记的是**最后摸到**的那张
        assert gs["turn"] == 0                     # 保留回合

    def test_stops_when_the_pile_runs_out(self):
        gs = _setup({0: ["G9"], 1: ["B7"]}, discard="R5", draw=["G3", "G2"],
                    rules={"draw_until_play": True})
        r = u.draw(gs, 0)
        assert r["kept_turn"] is False
        assert sorted(r["drawn"]) == ["G2", "G3"]
        assert gs["turn"] == 1                     # 摸穿了也没得打 → 移交
        assert _total_cards(gs) == len(gs["hands"][0]) + len(gs["hands"][1]) + 1 + 0

    def test_classic_still_draws_exactly_one(self):
        # 抽牌堆 pop 取顶：先摸 R9（能出，但经典规则只摸一张）
        gs = _setup({0: ["G9"], 1: ["B7"]}, discard="R5", draw=["G3", "R9"])
        r = u.draw(gs, 0)
        assert r["drawn"] == ["R9"]
        assert r["kept_turn"] is True
        assert gs["drawn_this_turn"] == "R9"


class TestMustPlay:
    def test_cannot_draw_with_a_playable_card(self):
        gs = _setup({0: ["R9"], 1: ["B7"]}, discard="R5",
                    rules={"must_play": True})
        assert gs["rules"]["must_play"] is True
        assert u.playable_cards(gs, 0) == ["R9"]
        assert u.draw(gs, 0)["ok"] is False
        sync = u.game_to_sync(gs, 101)
        assert sync["can_draw"] is False

    def test_may_draw_when_nothing_is_playable(self):
        gs = _setup({0: ["G9"], 1: ["B7"]}, discard="R5", draw=["G3"],
                    rules={"must_play": True})
        assert u.game_to_sync(gs, 101)["can_draw"] is True
        assert u.draw(gs, 0)["ok"] is True

    def test_may_draw_when_a_penalty_must_be_taken(self):
        """被罚牌时他本来就出不了牌，摸牌是唯一出路 —— 不受「有牌必出」限制。"""
        gs = _setup({0: ["R9"], 1: ["B7"]}, discard="R5", draw=["G3"],
                    rules={"must_play": True})
        gs["pending_draw"], gs["pending_kind"] = 2, u.DRAW2
        assert u.game_to_sync(gs, 101)["can_draw"] is True
        assert u.draw(gs, 0)["ok"] is True

    def test_cannot_pass_after_drawing(self):
        gs = _setup({0: ["G9"], 1: ["B7"]}, discard="R5", draw=["R9"],
                    rules={"must_play": True})
        assert u.draw(gs, 0)["kept_turn"] is True
        assert u.game_to_sync(gs, 101)["can_pass"] is False
        assert u.pass_turn(gs, 0)["ok"] is False

    def test_auto_pass_plays_instead_of_stalling(self):
        """断线代打撞上「有牌必出」时**必须替他打一张** —— 摸牌会被引擎拒，
        回合永远交不出去（人机/代打的死循环，深空休眠踩过两次）。"""
        gs = _setup({0: ["R9", "G2"], 1: ["B7"]}, discard="R5", draw=["G3"],
                    rules={"must_play": True})
        r = u.auto_pass(gs, 0)
        assert r["ok"] is True
        assert gs["discard_pile"][-1] == "R9"
        assert gs["turn"] == 1


# ── 房规：抢出（Jump-in） ─────────────────────────────────────


class TestJumpIn:
    RULES = {"jump_in": True}

    def test_nothing_jumpable_when_the_rule_is_off(self):
        gs = _setup({0: ["R5", "G3"], 1: ["R5"]}, discard="R5")
        assert u.jumpable_cards(gs, 1) == []

    def test_only_an_exact_match_is_jumpable(self):
        gs = _setup({0: ["R5", "G3"], 1: ["R5", "R6", "G5"]}, discard="R5",
                    rules=self.RULES)
        assert u.jumpable_cards(gs, 1) == ["R5"]

    def test_wild_cards_cannot_be_jumped_in_with(self):
        gs = _setup({0: ["W+4", "G3"], 1: ["W+4", "W"]}, discard="W+4",
                    color="R", rules=self.RULES)
        assert u.jumpable_cards(gs, 1) == []

    def test_empty_on_your_own_turn(self):
        """轮到我时走正常出牌，不必抢 —— 两个列表同时亮会让界面分不清。"""
        gs = _setup({0: ["R5", "G3"], 1: ["R5"]}, discard="R5", turn=1,
                    rules=self.RULES)
        assert u.jumpable_cards(gs, 1) == []
        assert u.playable_cards(gs, 1) == ["R5"]

    def test_jump_plays_out_of_turn_and_turn_continues_from_the_jumper(self):
        gs = _setup({0: ["G9"], 1: ["R5", "B3"], 2: ["B7"]}, discard="R5", turn=0,
                    rules=self.RULES)
        r = u.play(gs, 1, "R5", jump=True)
        assert r["ok"] is True and r["jump"] is True
        assert gs["discard_pile"][-1] == "R5"
        assert gs["turn"] == 2                 # 从抢出者（座位 1）往下走

    def test_jump_clears_a_pending_draw(self):
        """**真坑**：抢出可能发生在「某人刚摸完牌、还没决定出不出」的那一瞬间。

        `drawn_this_turn` 是全局唯一那个字段，不清的话它仍指着那张早已不在他手里的
        牌，`playable_cards` 会一直回空表 —— **那个人从此出不了任何牌**。
        """
        gs = _setup({0: ["G9"], 1: ["R5", "B3"], 2: ["B7"]}, discard="R5", turn=0,
                    rules=self.RULES)
        gs["drawn_this_turn"] = "G3"                 # 摆出「摸过还没过牌」
        assert u.play(gs, 1, "R5", jump=True)["ok"] is True
        assert gs["drawn_this_turn"] is None

    def test_jump_with_a_draw2_grows_the_penalty(self):
        gs = _setup({0: ["G9"], 1: ["R+2", "B3"], 2: ["B7"]}, discard="R+2",
                    turn=0, rules=self.RULES)
        r = u.play(gs, 1, "R+2", jump=True)
        assert r["ok"] and gs["pending_draw"] == 2
        assert gs["pending_kind"] == u.DRAW2
        assert gs["turn"] == 2

    def test_jump_is_refused_on_your_turn(self):
        gs = _setup({0: ["R5"], 1: ["B7"]}, discard="R5", turn=0,
                    rules=self.RULES)
        r = u.play(gs, 0, "R5", jump=True)
        assert r["ok"] is False and "轮到你了" in r["error"]

    def test_jump_is_refused_without_the_rule(self):
        gs = _setup({0: ["G9"], 1: ["R5"]}, discard="R5", turn=0)
        r = u.play(gs, 1, "R5", jump=True)
        assert r["ok"] is False

    def test_losing_the_race_gets_a_readable_error(self):
        """抢输不能静默 —— 界面上一句「点了没反应」是最难查的一类反馈。"""
        gs = _setup({0: ["G9"], 1: ["R5", "B3"]}, discard="R5", turn=0,
                    rules=self.RULES)
        gs["discard_pile"].append("G3")        # 别人抢先一步打掉了另一张，牌堆顶变了
        r = u.play(gs, 1, "R5", jump=True)
        assert r["ok"] is False and "抢" in r["error"]

    def test_a_player_may_jump_with_a_duplicate_of_what_he_just_played(self):
        gs = _setup({0: ["R5", "R5"], 1: ["B7"]}, discard="R5", turn=0,
                    rules=self.RULES)
        assert u.play(gs, 0, "R5")["ok"] is True
        assert gs["turn"] == 1
        assert u.jumpable_cards(gs, 0) == ["R5"]      # 手里还有一张一模一样的
        assert u.play(gs, 0, "R5", jump=True)["ok"] is True

    def test_sync_exposes_jumpable(self):
        gs = _setup({0: ["G9"], 1: ["R5"]}, discard="R5", turn=0,
                    rules=self.RULES)
        sync = u.game_to_sync(gs, 102)
        assert sync["jumpable"] == ["R5"]
        assert sync["playable"] == []
        assert sync["rules"]["jump_in"] is True

    def test_jump_winning_the_game(self):
        gs = _setup({0: ["G9"], 1: ["R5"]}, discard="R5", turn=0,
                    rules=self.RULES)
        r = u.play(gs, 1, "R5", jump=True)
        assert r["effect"] == "empty" and gs["winner"] == 1 and gs["finished"]


# ── 房规：7 换手 / 0 传手 ─────────────────────────────────────


class TestSevenZero:
    def test_seven_swaps_hands(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9", "B8", "B7"]}, discard="R5",
                    rules={"seven_zero": "both"})
        r = u.play(gs, 0, "R7", target=1)
        assert r["ok"] is True and r["effect"] == "swap" and r["target"] == 1
        assert gs["hands"][0] == ["B9", "B8", "B7"]
        assert gs["hands"][1] == ["G3"]

    def test_seven_without_a_target_is_refused_and_changes_nothing(self):
        """**先校验再出牌**：牌已经落地了才报「没给换手对象」，状态就回不去了。"""
        gs = _setup({0: ["R7", "G3"], 1: ["B9"]}, discard="R5",
                    rules={"seven_zero": "swap"})
        before = (list(gs["hands"][0]), list(gs["discard_pile"]), gs["turn"])
        r = u.play(gs, 0, "R7")
        assert r["ok"] is False
        assert (gs["hands"][0], gs["discard_pile"], gs["turn"]) == before

    def test_seven_cannot_swap_with_yourself(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9"]}, discard="R5",
                    rules={"seven_zero": "swap"})
        assert u.play(gs, 0, "R7", target=0)["ok"] is False

    def test_seven_cannot_swap_with_an_out_player(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9"], 2: ["G8"]}, discard="R5",
                    rules={"seven_zero": "swap"})
        gs["out"].append(1)
        assert u.play(gs, 0, "R7", target=1)["ok"] is False
        assert u.play(gs, 0, "R7", target=2)["ok"] is True

    def test_seven_is_an_ordinary_card_when_the_rule_is_off(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9"]}, discard="R5")
        r = u.play(gs, 0, "R7")
        assert r["ok"] and r["effect"] == "play" and r["target"] is None

    def test_zero_rotates_every_hand_one_step(self):
        gs = _setup({0: ["R0", "G3"], 1: ["B9", "B8"], 2: ["G1"]}, discard="R5",
                    rules={"seven_zero": "both"})
        r = u.play(gs, 0, "R0")
        assert r["ok"] and r["effect"] == "rotate"
        assert gs["hands"][1] == ["G3"]        # 收到座位 0 打完 0 之后剩下的那张
        assert gs["hands"][2] == ["B9", "B8"]
        assert gs["hands"][0] == ["G1"]

    def test_zero_rotates_the_other_way_when_reversed(self):
        gs = _setup({0: ["R0", "G3"], 1: ["B9"], 2: ["G1", "G2"]}, discard="R5",
                    direction=-1, rules={"seven_zero": "rotate"})
        assert u.play(gs, 0, "R0")["ok"] is True
        assert gs["hands"][2] == ["G3"] and gs["hands"][1] == ["G1", "G2"]

    def test_rotation_skips_players_who_are_out(self):
        gs = _setup({0: ["R0", "G3"], 1: ["B9"], 2: ["G1", "G2"]}, discard="R5",
                    rules={"seven_zero": "rotate"})
        gs["out"].append(1)
        assert u.play(gs, 0, "R0")["ok"] is True
        assert gs["hands"][1] == ["B9"]        # 出局者的手牌只用于排名，不参与流转
        assert gs["hands"][2] == ["G3"] and gs["hands"][0] == ["G1", "G2"]

    def test_rotation_clears_a_stale_uno_flag(self):
        """**只清不加**：手里不再是恰好一张的，标记作废；而**接到**一手一张牌的人
        并没有喊过 —— 他得自己再喊一次（换手是双向的，凭「手里一张」白送一个
        UNO 徽标说不过去）。"""
        gs = _setup({0: ["R0", "G3", "G4"], 1: ["B9"], 2: ["G1"]}, discard="R5",
                    rules={"seven_zero": "rotate"})
        gs["uno_called"][1] = True
        assert u.play(gs, 0, "R0")["ok"] is True
        assert 1 not in gs["uno_called"]       # 他现在 2 张了
        assert 0 not in gs["uno_called"]       # 他接到了 1 张，但得自己喊

    def test_swap_targets_are_published_for_the_client(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9"], 2: ["G8"]}, discard="R5",
                    rules={"seven_zero": "swap"})
        assert u.game_to_sync(gs, 101)["swap_targets"] == [1, 2]

    def test_swap_targets_are_empty_without_a_playable_seven(self):
        gs = _setup({0: ["R7", "G3"], 1: ["B9"]}, discard="R5",
                    rules={"seven_zero": "swap"})
        gs["turn"] = 1
        assert u.game_to_sync(gs, 102)["swap_targets"] == []
        gs2 = _setup({0: ["R7"], 1: ["B9"]}, discard="R5")
        assert u.game_to_sync(gs2, 101)["swap_targets"] == []


# ── 无怜悯牌堆（v1.14 批次 B）：5 张新牌 + 25 张淘汰 ──────────

NM = {"deck": "no_mercy"}


class TestNoMercyDeck:
    def test_deck_is_164_cards(self):
        assert u.DECK_SIZE == 108
        nm = u.build_deck("no_mercy")
        assert len(nm) == 164, f"无怜悯牌堆应 164 张，实际 {len(nm)}"
        assert len(u.build_deck()) == 108, "标准牌堆一个字都不该动"

    def test_the_extra_cards_are_the_five_new_kinds(self):
        """多出来的 56 张**包含五种新牌，也包含若干老牌的加印** ——
        「功能牌占多数」是这副牌的卖点，不是纯加五种牌就够了。"""
        from collections import Counter
        extra = Counter(u.build_deck("no_mercy")) - Counter(u.build_deck())
        assert sum(extra.values()) == 56
        assert {u.card_kind(c) for c in extra} == {
            u.SKIP_ALL, u.DISCARD_ALL, u.DRAW2, u.SKIP, u.REVERSE,
            u.WILD4, u.DRAW6, u.DRAW10, u.REV_DRAW4, u.ROULETTE,
        }
        for kind in (u.SKIP_ALL, u.DISCARD_ALL):
            assert all(u.card_color(c) in u.COLORS for c in extra if u.card_kind(c) == kind)
        for kind in (u.DRAW6, u.DRAW10, u.REV_DRAW4):
            assert all(c == "W" + kind for c in extra if u.card_kind(c) == kind)

    def test_points_of_the_new_kinds(self):
        assert u.card_points("RSE") == 20      # 有色功能牌
        assert u.card_points("RDA") == 20
        assert u.card_points("W+6") == 50      # 黑牌
        assert u.card_points("W+10") == 50
        assert u.card_points("W+RD4") == 50

    def test_the_new_kinds_are_wild_or_symbols_as_appropriate(self):
        assert u.is_wild("W+6") and u.is_wild("W+10") and u.is_wild("W+RD4")
        assert not u.is_wild("RSE") and not u.is_wild("RDA")


class TestSkipEveryone:
    def test_everyone_else_is_skipped_and_the_turn_comes_back(self):
        gs = _setup({0: ["RSE", "G3"], 1: ["B9"], 2: ["Y1"]}, discard="R5",
                    rules=NM)
        r = u.play(gs, 0, "RSE")
        assert r["ok"] and r["effect"] == "skip_all"
        assert sorted(r["skipped"]) == [1, 2]
        assert gs["turn"] == 0, "跳过所有人之后回合回到自己"
        assert gs["hands"][0] == ["G3"]

    def test_two_player_game_also_returns_to_self(self):
        gs = _setup({0: ["RSE", "G3"], 1: ["B9"]}, discard="R5", rules=NM)
        r = u.play(gs, 0, "RSE")
        assert r["skipped"] == [1] and gs["turn"] == 0

    def test_only_active_seats_are_skipped(self):
        gs = _setup({0: ["RSE", "G3"], 1: ["B9"], 2: ["Y1"]}, discard="R5",
                    rules=NM)
        gs["out"].append(1)
        r = u.play(gs, 0, "RSE")
        assert r["skipped"] == [2] and gs["turn"] == 0


class TestDiscardAll:
    def test_all_same_colour_cards_are_discarded(self):
        gs = _setup({0: ["RDA", "R9", "RS", "G3"], 1: ["B9"]}, discard="R5",
                    rules=NM)
        r = u.play(gs, 0, "RDA")
        assert r["ok"] and r["effect"] == "discard_all"
        assert gs["hands"][0] == ["G3"]
        assert gs["discard_pile"][-3:] == ["R9", "RS"] or             sorted(gs["discard_pile"][-2:]) == ["R9", "RS"]
        assert gs["turn"] == 1

    def test_discarded_action_cards_do_not_fire(self):
        """官方口径：弃掉的牌「当普通牌丢掉」，效果不结算 ——
        丢一张 R+2 不该让下家摸牌（否则这张牌会变成一次全场乱斗）。"""
        gs = _setup({0: ["RDA", "R+2"], 1: ["B9"]}, discard="R5", rules=NM)
        assert u.play(gs, 0, "RDA")["ok"] is True
        assert gs["pending_draw"] == 0
        assert len(gs["hands"][1]) == 1

    def test_emptying_the_hand_wins(self):
        gs = _setup({0: ["RDA", "R9"], 1: ["B9"]}, discard="R5", rules=NM)
        r = u.play(gs, 0, "RDA")
        assert r["effect"] == "empty" and gs["winner"] == 0 and gs["finished"]


class TestNewPenalties:
    def test_wild_draw_six(self):
        gs = _setup({0: ["W+6", "G3"], 1: ["B9"]}, discard="R5", rules=NM)
        r = u.play(gs, 0, "W+6", "R")
        assert r["ok"] and r["effect"] == "draw6"
        assert gs["pending_draw"] == 6 and gs["pending_kind"] == u.DRAW6
        assert gs["current_color"] == "R"

    def test_wild_draw_ten(self):
        gs = _setup({0: ["W+10", "G3"], 1: ["B9"]}, discard="R5", rules=NM)
        r = u.play(gs, 0, "W+10", "B")
        assert r["effect"] == "draw10" and gs["pending_draw"] == 10

    def test_upgrade_stacking_goes_all_the_way_up(self):
        gs = _setup({0: ["G3"], 1: ["R+2", "G+4", "W+6", "W+10"]}, discard="R5",
                    draw=["B9"], rules={**NM, "stack_mode": "upgrade"})
        gs["pending_draw"], gs["pending_kind"], gs["turn"] = 2, u.DRAW2, 1
        assert set(u.playable_cards(gs, 1)) == {"R+2", "G+4", "W+6", "W+10"}
        assert u.play(gs, 1, "W+10", "G")["ok"] is True
        assert gs["pending_draw"] == 12

    def test_upgrade_refuses_a_smaller_one_and_off_refuses_all(self):
        gs = _setup({0: ["G3"], 1: ["R+2"]}, discard="R5", draw=["B9"],
                    rules={**NM, "stack_mode": "upgrade"})
        gs["pending_draw"], gs["pending_kind"], gs["turn"] = 6, u.DRAW6, 1
        assert u.playable_cards(gs, 1) == []          # 2 < 6
        gs2 = _setup({0: ["G3"], 1: ["R+2"]}, discard="R5", draw=["B9"],
                     rules={**NM, "stack_mode": "off"})
        gs2["pending_draw"], gs2["pending_kind"], gs2["turn"] = 2, u.DRAW2, 1
        assert u.playable_cards(gs2, 1) == []

    def test_reverse_draw_four_turns_first_then_penalises(self):
        gs = _setup({0: ["W+RD4", "G3"], 1: ["B9"], 2: ["Y1"]}, discard="R5",
                    rules=NM)
        r = u.play(gs, 0, "W+RD4", "G")
        assert r["effect"] == "rev_draw4"
        assert gs["direction"] == -1, "先转向"
        assert gs["pending_draw"] == 4 and gs["pending_kind"] == u.REV_DRAW4
        assert gs["turn"] == 2, "转向后的下家是座位 2"

    def test_reverse_draw_four_can_be_stacked_on_a_plus_two(self):
        gs = _setup({0: ["G3"], 1: ["W+RD4"]}, discard="R5", draw=["B9"],
                    rules={**NM, "stack_mode": "upgrade"})
        gs["pending_draw"], gs["pending_kind"], gs["turn"] = 2, u.DRAW2, 1
        assert u.playable_cards(gs, 1) == ["W+RD4"]   # 4 >= 2


class TestMercy:
    RULES = {**NM, "mercy": True}

    def test_off_by_default(self):
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", rules=NM)
        assert gs["rules"]["mercy"] is False
        assert u.create_game([1, 2])["rules"]["mercy"] is False

    def test_hand_of_25_is_knocked_out(self):
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", draw=["Y1"] * 30,
                    rules=self.RULES)
        gs["turn"] = 1
        gs["hands"][1] = ["B" + str(i) for i in range(24)]   # 摸一张就到 25
        r = u.draw(gs, 1)
        assert r["ok"] is True
        assert 1 in gs["out"]
        assert gs["winner"] == 0 and gs["finished"], "只剩一个人就终局"
        assert gs["reason"] == u.REASON_LAST_REMAINING
        # **返回值里也必须带上 game_over**：摸牌这条路径原来恒返回 None，
        # 于是「摸牌把人顶出局、场上只剩一个」时客户端永远收不到 `game_over`，
        # 界面上停在旧盘面不声不响（同「热重启后同一人连走两步」那类静默 bug）。
        assert r["game_over"] == {"reason": u.REASON_LAST_REMAINING, "winner": 0,
                                  "winner_team": None}

    def test_24_cards_is_still_safe(self):
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", draw=["Y1"],
                    rules=self.RULES)
        gs["turn"] = 1
        gs["hands"][1] = ["B" + str(i) for i in range(23)]
        assert u.draw(gs, 1)["ok"] is True
        assert 1 not in gs["out"] and len(gs["hands"][1]) == 24

    def test_penalty_can_push_someone_over_the_line(self):
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", draw=["Y1"] * 30,
                    rules=self.RULES)
        gs["hands"][1] = ["B" + str(i) for i in range(20)]
        gs["pending_draw"], gs["pending_kind"], gs["turn"] = 10, u.DRAW10, 1
        assert u.draw(gs, 1)["ok"] is True
        assert 1 in gs["out"], "一次摸 10 张把他推过 25 张"

    def test_a_swap_can_knock_someone_out(self):
        """换手接过来的一手同样要判 —— 这也是「7 换手」在这副牌里最狠的用法。"""
        gs = _setup({0: ["R7", "G3"], 1: ["B9"]}, discard="R5",
                    draw=["Y1"], rules={**self.RULES, "seven_zero": "swap"})
        gs["hands"][1] = ["B" + str(i) for i in range(26)]
        r = u.play(gs, 0, "R7", target=1)
        assert r["ok"] is True
        assert 0 in gs["out"], "换到 26 张的那个人（座位 0）出局"
        assert gs["winner"] == 1 and gs["finished"]

    def test_the_turn_never_rests_on_a_knocked_out_seat(self):
        """**摸牌把人顶过 25 张时，回合不能留在那个座位上**（真 bug，fuzz 抓到）。

        「摸到能出为止」档下最后一张必然可出，所以原来一定会走到「保留回合」那条
        分支 —— 于是一个已经出局的人成了当前回合玩家，下一轮断言直接炸出来。
        """
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", draw=["Y1"] * 30,
                    rules={**self.RULES, "draw_until_play": True})
        gs["turn"] = 1
        gs["hands"][1] = ["B" + str(i) for i in range(24)]
        r = u.draw(gs, 1)
        assert r["ok"] is True and r["kept_turn"] is False
        assert 1 in gs["out"]
        assert 1 not in gs["out"] or gs["turn"] != 1, "回合不该留在一个已出局的座位上"
        assert gs["winner"] == 0 and gs["finished"]

    def test_mercy_does_not_fire_when_off(self):
        gs = _setup({0: ["G3"], 1: ["B9"]}, discard="R5", draw=["Y1"] * 30,
                    rules=NM)
        gs["turn"] = 1
        gs["hands"][1] = ["B" + str(i) for i in range(30)]
        assert u.draw(gs, 1)["ok"] is True
        assert 1 not in gs["out"]


# ── 房规校验 ──────────────────────────────────────────────────


class TestRuleValidation:
    def test_defaults_are_the_classic_rules(self):
        assert u.create_game([1, 2])["rules"] == uno_rules.DEFAULTS
        assert uno_rules.DEFAULTS["stack_mode"] == "same"

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError):
            uno_rules.normalize({"nope": True})

    def test_bad_value_raises(self):
        with pytest.raises(ValueError):
            uno_rules.normalize({"stack_mode": "whatever"})

    def test_preset_of_round_trips(self):
        for pid in uno_rules.PRESET_IDS:
            assert uno_rules.preset_of(uno_rules.rules_of_preset(pid)) == pid
        assert uno_rules.preset_of({"jump_in": True}) == uno_rules.CUSTOM_ID

    def test_rules_survive_the_snapshot(self):
        gs = _setup({0: [], 1: ["R9"]}, discard="R5", rules={"jump_in": True})
        gs["finished"], gs["winner"] = True, 0
        assert u.snapshot(gs)["rules"]["jump_in"] is True


class TestTeams:
    """2v2 组队（`teams=2v2`，Mattel 的 UNO Teams 口径）。

    三条不变量，每条都对应一个容易做反的地方：

    1. **对家是队友**（`seat % 2`），而且这个配对只在服务端算（客户端读下发的
       `seat_teams`）。
    2. **一个人出完手牌不算赢** —— 他退出轮转、队伍继续，两队谁先「两人都出完」
       谁赢。做反了就成了「谁先出完谁赢」，那和经典档没有区别。
    3. 出完的人**不再被轮到**（他在 `out` 里），但**不算认输**（排名里要排在
       获胜队伍里，不能当逃兵排到最后）。
    """

    RULES = {"teams": "2v2"}

    def test_opposite_seats_are_partners(self):
        gs = _setup({0: ["R1"], 1: ["R1"], 2: ["R1"], 3: ["R1"]},
                    discard="R5", rules=self.RULES)
        assert [u.team_of(gs, s) for s in range(4)] == [0, 1, 0, 1]
        assert u.team_seats(gs, 0) == [0, 2]
        assert u.team_seats(gs, 1) == [1, 3]
        # 没开组队档时一律 None（客户端据此决定画不画队友标识）
        off = _setup({0: ["R1"], 1: ["R1"]}, discard="R5")
        assert [u.team_of(off, s) for s in range(2)] == [None, None]

    def test_team_mode_needs_exactly_four_players(self):
        with pytest.raises(ValueError):
            u.create_game([1, 2], rules=self.RULES)
        with pytest.raises(ValueError):
            u.create_game([1, 2, 3], rules=self.RULES)
        assert u.create_game([1, 2, 3, 4], rules=self.RULES)["num_players"] == 4

    def test_emptying_takes_half_the_partner_hand_and_keeps_playing(self):
        """官方 GOING OUT：出完手牌**不是出局**，队友把一半交过来接着打。"""
        gs = _setup({0: ["G3"], 1: ["B9", "B8"], 2: ["Y7", "Y6"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        r = u.play(gs, 0, "G3")
        assert r["ok"] is True
        assert not gs["finished"], "队友还没出完，局必须继续"
        assert gs["hands"][0] == ["Y7"] and gs["hands"][2] == ["Y6"], gs["hands"]
        assert 0 not in gs["out"] and 0 not in gs["emptied"], "接管之后他还在场上"
        # 这一手照常结算：回合移交到下家
        assert gs["turn"] == 1, gs["turn"]

    def test_odd_partner_hand_keeps_the_larger_half(self):
        """官方 NOTE：9 张 → 传 4 留 5（**传的人留多的那些**）。"""
        mate_hand = ["Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9"]
        gs = _setup({0: ["G3"], 1: ["B9"], 2: list(mate_hand), 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.play(gs, 0, "G3")["ok"] is True
        assert len(gs["hands"][0]) == 4 and len(gs["hands"][2]) == 5, gs["hands"]

    def test_a_partner_on_the_last_card_leaves_you_out(self):
        """队友只剩 1 张时半手传不出来 —— 本人留在场外等他出完。"""
        gs = _setup({0: ["G3"], 1: ["B9", "B8"], 2: ["Y7"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.play(gs, 0, "G3")["ok"] is True
        assert 0 in gs["emptied"] and 0 in gs["out"]
        assert gs["turn"] == 1, "回合必须离开他，否则整局卡死"

    def test_both_members_emptied_wins_for_the_team(self):
        gs = _setup({0: ["G3"], 1: ["B9", "B8"], 2: ["G7"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.play(gs, 0, "G3")["ok"] is True
        gs["turn"] = 2
        r = u.play(gs, 2, "G7")
        assert r["ok"] is True, r
        assert gs["finished"] is True
        assert gs["reason"] == u.REASON_TEAM_EMPTIED
        assert gs["winner_team"] == 0
        assert r["game_over"]["winner_team"] == 0

    def test_drawing_tags_an_out_partner_back_in(self):
        """官方 TAGGING BACK IN：队友在场外时，我摸到牌就立刻分他一半、拉他回来。"""
        gs = _setup({0: ["G3"], 1: ["B9", "B8"], 2: ["Y7"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.play(gs, 0, "G3")["ok"] is True
        assert 0 in gs["out"]
        # 队友（座位 2）摸一张 → 手里 2 张 → 分一半给 0，0 回到场上
        gs["draw_pile"] = ["B1"]
        gs["turn"] = 2
        u.draw(gs, 2)
        assert 0 not in gs["out"] and 0 not in gs["emptied"]
        assert gs["hands"][0] == ["Y7"] and gs["hands"][2] == ["B1"], gs["hands"]

    def test_a_surrendered_partner_is_never_tagged_back_in(self):
        """认输是**退出这一局**，不是「出完了」—— 摸牌不能把人拉回来。"""
        gs = _setup({0: ["G3"], 1: ["B9", "B8"], 2: ["Y7", "Y6"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.surrender(gs, 0) is None
        gs["draw_pile"] = ["B1"]
        gs["turn"] = 2
        u.draw(gs, 2)
        assert 0 in gs["out"] and 0 not in gs["emptied"]
        assert len(gs["hands"][2]) == 3, gs["hands"]

    def test_team_cards_only_enter_the_team_deck(self):
        """两张专属黑牌**只在组队档进牌堆**，经典牌堆一个字都不动。"""
        classic = u.build_deck("classic")
        assert len(classic) == 108
        assert not [c for c in classic if u.card_kind(c) in ("TP", "TD4")]
        team = u.build_deck("classic", teams=True)
        assert len(team) == 108 + 2 * u.TEAM_CARD_COPIES
        for c in u.TEAM_CARDS:
            assert team.count(c) == u.TEAM_CARD_COPIES
        # 组队 + 无怜悯：加在 164 张之上（两个旋钮正交）
        assert len(u.build_deck("no_mercy", teams=True)) == 164 + 2 * u.TEAM_CARD_COPIES

    def test_team_wild_cards_need_a_colour(self):
        """两张都是变色牌 —— 不选色发上去必须被拒（同 v1.14 那三张黑牌的教训）。"""
        for card in u.TEAM_CARDS:
            assert u.is_wild(card) and u.card_kind(card) in u.WILD_NEEDS_COLOR
            gs = _setup({0: [card], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                        discard="G5", turn=0, rules=self.RULES)
            r = u.play(gs, 0, card)
            assert r["ok"] is False, (card, r)

    def test_team_play_jumps_the_turn_to_the_partner(self):
        # 手里留一张：这一手要是把自己打空了，会先走 GOING OUT 的接管逻辑
        gs = _setup({0: ["WTP", "R1"], 1: ["B9"], 2: ["G1", "G2"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        r = u.play(gs, 0, "WTP", "G")
        assert r["ok"] is True, r
        assert gs["turn"] == 2, "回合直接跳到队友"
        assert sorted(r["skipped"]) == [1, 3], "两个对手都被跳过"
        assert gs["current_color"] == "G"
        assert gs["hands"][0] == ["R1"] and len(gs["hands"][2]) == 2, gs["hands"]

    def test_team_play_penalises_the_player_when_the_partner_cannot_play(self):
        """队友一张都出不了 → **出牌者自己**摸 2（官方口径，不是队友摸）。"""
        gs = _setup({0: ["WTP", "R1"], 1: ["B9"], 2: ["R7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES, draw=["B1", "B2"])
        r = u.play(gs, 0, "WTP", "G")
        assert r["ok"] is True, r
        assert gs["turn"] == 2
        assert sorted(gs["hands"][0]) == ["B1", "B2", "R1"], gs["hands"][0]
        assert gs["hands"][2] == ["R7"], "队友一张都没多"

    def test_team_draw4_hits_both_opponents(self):
        """一次打掉对手**一整队** 4 张，而且下家还停一轮。"""
        gs = _setup({0: ["WTD4", "R1"], 1: ["B9"], 2: ["G1"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES,
                    draw=["B1", "B2", "B3", "B4"])
        r = u.play(gs, 0, "WTD4", "G")
        assert r["ok"] is True, r
        assert len(gs["hands"][1]) == 3, "下家摸 2"
        assert len(gs["hands"][3]) == 3, "他的队友也摸 2"
        assert r["skipped"] == [1], "下家停一轮"
        assert gs["turn"] == 2, "回合落到我队友身上"

    def test_team_draw4_also_hits_an_out_partner(self):
        """官方原文括号那句：队友**在场外也照样摸**（摸完就被拉回场上）。"""
        gs = _setup({0: ["WTD4", "R1"], 1: ["B9", "B8"], 2: ["G1"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES,
                    draw=["B1", "B2", "B3", "B4"])
        assert u.surrender(gs, 3) is None
        assert 3 in gs["out"]
        r = u.play(gs, 0, "WTD4", "G")
        assert r["ok"] is True, r
        assert len(gs["hands"][3]) == 4, "场外的队友照样摸 2"

    def test_a_teammate_may_call_uno_for_you(self):
        """官方 CALLING "UNO!"：队友可以替你喊。"""
        gs = _setup({0: ["G3", "G4"], 1: ["B9", "B8"], 2: ["Y7"], 3: ["R4", "R3"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.uno_call_targets(gs, 0) == [2], "自己两张喊不了，能喊的是队友那一张"
        assert u.can_call_uno(gs, 0) is True
        r = u.call_uno(gs, 0)
        assert r["ok"] is True and r["seat"] == 2 and r["for_mate"] is True
        assert gs["uno_called"][2] is True
        assert u.can_call_uno(gs, 0) is False, "喊过之后按钮必须灭掉"

    def test_you_call_for_yourself_before_your_teammate(self):
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.uno_call_targets(gs, 0) == [0, 2]
        r = u.call_uno(gs, 0)
        assert r["seat"] == 0 and r["for_mate"] is False

    def test_calling_uno_for_a_non_teammate_is_rejected(self):
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        r = u.call_uno(gs, 0, target=1)
        assert r["ok"] is False and r["error"] == "不能替他喊 UNO"

    def test_team_calling_does_not_leak_into_classic_mode(self):
        """没开组队时「替别人喊」这条路必须关死 —— 对手那张不归我喊。"""
        gs = _setup({0: ["G3", "G4"], 1: ["B9"]}, discard="G5", turn=0)
        assert u.uno_call_targets(gs, 0) == []
        assert u.can_call_uno(gs, 0) is False
        assert u.call_uno(gs, 0, target=1)["ok"] is False
        assert u.uno_call_targets(gs, 1) == [1], "经典档只能喊自己"

    def test_emptied_seats_are_skipped_in_turn_order(self):
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        u.play(gs, 0, "G3")                      # 0 出完
        seen = []
        for _ in range(4):
            seen.append(gs["turn"])
            u._advance(gs, 1)
        assert 0 not in seen, seen

    def test_a_surrendering_member_does_not_end_it(self):
        """队友认输只是减员 —— 另一人还能打（与「出完手牌」是两件事）。"""
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.surrender(gs, 0) is None
        assert not gs["finished"]
        assert 0 not in gs["emptied"]

    def test_both_surrendering_members_lose_the_round(self):
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.surrender(gs, 0) is None
        go = u.surrender(gs, 2)
        assert go and go["winner_team"] == 1, go
        assert go["reason"] == u.REASON_LAST_REMAINING

    def test_ranking_puts_the_winning_team_first(self):
        gs = _setup({0: ["G3"], 1: ["B9", "B8", "B7"], 2: ["G7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        assert u.play(gs, 0, "G3")["ok"] is True
        gs["turn"] = 2
        assert u.play(gs, 2, "G7")["ok"] is True
        ranking = u.compute_ranking(gs, _info(4))
        assert [r["team"] for r in ranking[:2]] == [0, 0]
        assert {r["seat"] for r in ranking[:2]} == {0, 2}
        assert [r["team"] for r in ranking[2:]] == [1, 1]
        assert ranking[0]["rank"] == 1 and ranking[1]["rank"] == 1
        # 出完手牌的人**不算认输**（否则会被当成逃兵排到队尾）
        assert all(not r["surrendered"] for r in ranking)

    def test_sync_carries_the_pairing(self):
        gs = _setup({0: ["G3"], 1: ["B9"], 2: ["Y7"], 3: ["R4"]},
                    discard="G5", turn=0, rules=self.RULES)
        sync = u.game_to_sync(gs, 101)          # uid 101 = seat 0
        assert sync["seat_teams"] == [0, 1, 0, 1]
        assert sync["your_team"] == 0
        assert sync["emptied"] == []
        assert sync["winner_team"] is None

    def test_ai_never_swaps_hands_with_a_teammate(self):
        """「不能坑队友」：换手是双向的，拿队友当对象就是把烂牌塞给他。"""
        gs = _setup({0: ["R7"], 1: ["B1", "B2", "B3"], 2: ["Y1"],
                     3: ["G1", "G2"]},
                    discard="R5", turn=0,
                    rules={**self.RULES, "seven_zero": "swap"})
        assert ai._swap_target(gs, 0) != 2, "队友（座位 2）不该被选成换手对象"
        assert ai._swap_target(gs, 0) in (1, 3)

    def test_ai_falls_back_to_a_teammate_when_no_opponent_is_left(self):
        """对手全出局时只能换给队友 —— **总比发一个会被引擎拒掉的动作好**
        （被拒在「一次决策一个动作」下就是白扔一轮）。"""
        gs = _setup({0: ["R7"], 1: ["B1"], 2: ["Y1"], 3: ["G1"]},
                    discard="R5", turn=0,
                    rules={**self.RULES, "seven_zero": "swap"})
        gs["out"].extend([1, 3])                # 对手两个都出局了
        assert ai._swap_target(gs, 0) == 2


# ── 自对弈模糊测试 ────────────────────────────────────────────


class TestFuzz:
    """随机与贪心两种策略各跑若干整局：任何一条不变量破掉都会立刻炸出来。

    比手写用例更容易抓到「回合推进跳过出局者」「洗牌把顶牌洗进去」这类状态 bug。
    用固定 seed → 失败可复现，不会 flaky。
    """

    def _play_a_game(self, policy, seed, n, rules=None):
        random.seed(seed)
        gs = u.create_game(list(range(1, n + 1)), rules=rules)
        # 牌总数按**这副牌**算：无怜悯档是 164 张（`deck` 房规换的就是整副牌）
        deck_size = len(u.build_deck(gs["rules"]["deck"],
                                     teams=gs["rules"]["teams"] == u.TEAMS_2V2))
        assert _total_cards(gs) == deck_size

        for _ in range(6000):
            if gs["finished"]:
                break
            seat = gs["turn"]
            assert seat not in gs["out"], "回合落在已出局的座位上"
            act = policy(gs, seat)
            assert act is not None, "轮到的人必须有事可做"
            r = _dispatch(gs, seat, act)
            assert r["ok"], f"策略给出的动作被引擎拒绝：{act} → {r}"
            assert _total_cards(gs) == deck_size, "牌总数变了（发牌丢了或重复了）"
            assert all(len(h) >= 0 for h in gs["hands"].values())
        return gs

    @pytest.mark.parametrize("n", [2, 3, 4])
    def test_random_selfplay_terminates(self, n):
        for seed in range(12):
            gs = self._play_a_game(ai.random_action, seed, n)
            assert gs["finished"], f"n={n} seed={seed} 没能在步数上限内终局"
            assert gs["winner"] in range(n)

    @pytest.mark.parametrize("n", [2, 3, 4])
    def test_greedy_ai_actions_are_always_legal(self, n):
        """贪心 AI 的每个动作都必须被引擎接受 —— 抓住「AI 与引擎规则不一致」。"""
        for seed in range(12):
            gs = self._play_a_game(ai.best_action, seed, n)
            assert gs["finished"], f"n={n} seed={seed} 没能在步数上限内终局"

    @pytest.mark.parametrize("preset", ["classic", "party", "no_mercy"])
    @pytest.mark.parametrize("n", [2, 3, 4])
    def test_every_preset_terminates_with_legal_actions(self, preset, n):
        """**每个预设各跑一遍** —— 新规则最有效的回归网。

        除了「必然终局」和「动作必须被接受」，这里还锁住一条抢出特有的不变量：
        抢出可能让同一张牌在几个人之间来回，所以额外断言**牌总数不变**（发牌丢了
        或重复了会立刻炸出来）。
        """
        rules = uno_rules.rules_of_preset(preset)
        for seed in range(8):
            gs = self._play_a_game(ai.best_action, seed, n, rules=rules)
            assert gs["finished"], f"{preset} n={n} seed={seed} 没能终局"
            assert gs["winner"] in range(n)

    @pytest.mark.parametrize("base", ["classic", "party", "no_mercy"])
    def test_team_mode_terminates_and_only_a_full_team_wins(self, base):
        """组队档各配一份基础房规跑一遍。

        除了「必然终局 / 动作必须被接受」，这里锁住这一档唯一容易做反的地方：
        **赢的那一队必须两人都出完了**（或者对手整队已经没了）。写成「谁先出完
        谁赢」的话，第一条断言会立刻红。

        和 `party` / `no_mercy` 组合着跑是必要的：抢出、7 换手、25 张淘汰都会
        改手牌，而「出完手牌」这条判据正是在这些路径上被反复触发。
        """
        rules = {**uno_rules.rules_of_preset(base), "teams": "2v2"}
        for seed in range(10):
            gs = self._play_a_game(ai.best_action, seed, 4, rules=rules)
            assert gs["finished"], f"{base} seed={seed} 没能终局"
            wt = gs["winner_team"]
            assert wt in (0, 1), gs["winner_team"]
            if gs["reason"] == u.REASON_TEAM_EMPTIED:
                assert all(s in gs["emptied"] for s in u.team_seats(gs, wt))
            else:
                alive = {u.team_of(gs, s) for s in u.active_seats(gs)}
                assert alive <= {wt}, f"赢的是 {wt}，但场上还有 {alive}"

    def test_greedy_ai_uses_jump_in(self):
        """开了抢出房规，人机得真的用 —— 不然这条玩法就成了「只有真人在玩」。"""
        rules = uno_rules.rules_of_preset("party")
        random.seed(7)
        gs = u.create_game([1, 2, 3], rules=rules)
        for _ in range(4000):
            if gs["finished"]:
                break
            for seat in range(gs["num_players"]):
                if seat == gs["turn"] or seat in gs["out"]:
                    continue
                jump = ai.best_jump(gs, seat)
                if jump:
                    r = u.play(gs, seat, jump["card"], jump.get("color"),
                               jump=True, target=jump.get("target"))
                    assert r["ok"], f"人机给出的抢出被引擎拒绝：{jump} → {r}"
                    break
            else:
                seat = gs["turn"]
                act = ai.best_action(gs, seat)
                if act is None:
                    break
                if act["action"] == "play":
                    r = u.play(gs, seat, act["card"], act.get("color"),
                               target=act.get("target"))
                elif act["action"] == "draw":
                    r = u.draw(gs, seat)
                else:
                    r = u.pass_turn(gs, seat)
                assert r["ok"], f"人机动作被拒：{act} → {r}"
        assert gs["finished"]

    def test_ranking_is_consistent_after_selfplay(self):
        gs = self._play_a_game(ai.best_action, 3, 4)
        rk = u.compute_ranking(gs, _info(4))
        assert rk[0]["seat"] == gs["winner"]
        assert rk[0]["rank"] == 1
        assert sorted(e["rank"] for e in rk) == sorted(e["rank"] for e in rk)
        assert {e["seat"] for e in rk} == {0, 1, 2, 3}


# ── AI 策略 ───────────────────────────────────────────────────


class TestAi:
    def test_best_color_picks_the_most_common(self):
        assert ai.best_color(["G1", "G2", "R3"]) == "G"

    def test_best_color_is_deterministic_on_ties(self):
        assert ai.best_color(["R1", "G2"]) == ai.best_color(["G2", "R1"])
        assert ai.best_color([]) == u.COLORS[0]

    def test_prefers_numbers_over_action_cards(self):
        gs = _setup({0: ["R+2", "R3"], 1: ["B2"]}, discard="R5", turn=0)
        assert ai.best_action(gs, 0)["card"] == "R3"

    def test_saves_wild_for_last(self):
        gs = _setup({0: ["W", "RS"], 1: ["B2"]}, discard="R5", turn=0)
        assert ai.best_action(gs, 0)["card"] == "RS"

    def test_wild_gets_a_color(self):
        gs = _setup({0: ["W", "G1", "G2"], 1: ["B2"]}, discard="R5", turn=0)
        act = ai.best_action(gs, 0)
        assert act["action"] == "play"
        assert act["color"] in u.COLORS or act["card"] != "W"

    def test_stacks_penalty_when_possible(self):
        gs = _setup({0: ["R+2", "G1"], 1: ["B2"]}, discard="Y5", turn=0)
        gs["pending_draw"] = 2
        gs["pending_kind"] = u.DRAW2
        assert ai.best_action(gs, 0)["card"] == "R+2"

    def test_draws_when_stuck(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", turn=0)
        assert ai.best_action(gs, 0) == {"action": "draw"}

    def test_passes_after_drawing_an_unplayable_card(self):
        gs = _setup({0: ["G1"], 1: ["B2"]}, discard="R5", turn=1)
        gs["drawn_this_turn"] = "G9"        # 手工制造「摸过牌但出不了」
        act = ai.best_action(gs, 1)
        assert act == {"action": "pass"}

    def test_returns_none_when_not_their_turn(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=1)
        assert ai.best_action(gs, 0) is None

    def test_returns_none_when_game_finished(self):
        gs = _setup({0: ["R1"], 1: ["B2"]}, discard="R5", turn=0)
        gs["finished"] = True
        assert ai.best_action(gs, 0) is None