"""Unit tests for the Aeroplane Chess (飞行棋) engine."""

import pytest
from app.game.ludo import (
    create_game,
    roll_dice,
    move_piece,
    surrender,
    compute_ranking,
    game_to_sync,
    get_global_position,
    is_own_color,
    is_arrow,
    arrow_target,
    resolve_landing,
    track_color,
    ARROW_FIRST,
    ARROW_STEP,
    JUMP_STEP,
    QUADRANT,
    COLORS,
    RING,
    RING_END,
    FINISH_PROGRESS,
    PLAYER_OFFSETS,
    PIECES_PER_PLAYER,
    TAKEOFF_ROLL,
    MAX_CONSECUTIVE_SIXES,
)


def _ready(gs, seat, pieces, dice, movable=None):
    """把状态摆成「轮到 seat、已掷出 dice、等待选子」。"""
    gs["current_seat"] = seat
    gs["phase"] = "move"
    gs["dice_value"] = dice
    gs["players"][seat]["pieces"] = list(pieces)
    gs["movable_pieces"] = list(movable if movable is not None else range(PIECES_PER_PLAYER))
    return gs


def _players_info(game):
    """Build players_info from game state for ranking."""
    return [
        {"seat": s, "username": f"p{s}", "nickname": f"Player{s}"}
        for s in range(game["num_players"])
    ]


class TestCreateGame:
    def test_default_4_players(self):
        g = create_game(4)
        assert g["num_players"] == 4
        assert len(g["players"]) == 4
        for seat in range(4):
            assert g["players"][seat]["pieces"] == [-1, -1, -1, -1]
            assert g["players"][seat]["color"] in (
                "red", "yellow", "blue", "green"
            )

    def test_2_players(self):
        g = create_game(2)
        assert g["num_players"] == 2
        assert len(g["players"]) == 2

    def test_invalid_players(self):
        with pytest.raises(ValueError):
            create_game(1)
        with pytest.raises(ValueError):
            create_game(5)

    def test_initial_phase(self):
        g = create_game()
        assert g["phase"] == "roll"
        assert g["current_seat"] == 0
        assert g["dice_value"] is None


class TestRollDice:
    def test_roll_returns_dice_and_movable(self):
        g = create_game()
        result = roll_dice(g, dice_value=3)
        assert result["ok"]
        assert result["dice"] == 3
        # No pieces in base can move with 3 (need 6)
        assert result["movable"] == []
        assert result["extra_turn"] is False

    def test_roll_6_takeoff(self):
        g = create_game()
        result = roll_dice(g, dice_value=6)
        assert result["ok"]
        assert result["dice"] == 6
        assert len(result["movable"]) == 4  # all 4 can take off
        assert result["extra_turn"] is True
        assert g["phase"] == "move"

    def test_roll_6_extra_turn_no_movable(self):
        """Rolling 6 when all pieces finished → extra turn but empty movable."""
        g = create_game()
        # Put all pieces at finish
        for i in range(4):
            g["players"][0]["pieces"][i] = FINISH_PROGRESS
        g["players"][0]["finished_count"] = 4
        g["players"][0]["finished"] = True
        g["players"][0]["finished_at"] = "2024-01-01T00:00:00"
        result = roll_dice(g, dice_value=6)
        # Should skip to next player
        assert result["ok"]
        assert g["current_seat"] != 0

    def test_consecutive_sixes_penalty(self):
        g = create_game()
        # Roll 6 three times
        for _ in range(2):
            roll_dice(g, dice_value=6)
            # Move a piece to clear phase
            move_piece(g, g["movable_pieces"][0])
        # Third roll of 6
        result = roll_dice(g, dice_value=6)
        assert result["penalty"] is True
        assert result["movable"] == []
        # All pieces back to base
        assert g["players"][0]["pieces"] == [-1, -1, -1, -1]
        # Turn advanced
        assert g["current_seat"] != 0

    def test_wrong_phase(self):
        g = create_game()
        roll_dice(g, dice_value=6)
        # Phase is now "move", can't roll again
        result = roll_dice(g, dice_value=3)
        assert not result["ok"]

    def test_finished_game(self):
        g = create_game()
        g["finished"] = True
        result = roll_dice(g, dice_value=3)
        assert not result["ok"]


class TestMovePiece:
    def test_takeoff(self):
        g = create_game()
        roll_dice(g, dice_value=6)
        result = move_piece(g, 0)
        assert result["ok"]
        assert result["new_progress"] == 0
        assert result["seat"] == 0
        assert g["players"][0]["pieces"][0] == 0

    def test_normal_move(self):
        """普通落点（非自己颜色）就是 old + dice，没有跳/飞。"""
        g = _ready(create_game(), 0, [10, -1, -1, -1], dice=3)
        result = move_piece(g, 0)
        assert result["ok"]
        # 13 % 4 = 1 → 不是自己颜色，不跳
        assert result["new_progress"] == 13
        assert result["segments"] == []
        assert result["path"] == [11, 12, 13]

    def test_invalid_piece(self):
        g = create_game()
        roll_dice(g, dice_value=6)
        result = move_piece(g, 5)
        assert not result["ok"]

    def test_wrong_phase(self):
        g = create_game()
        result = move_piece(g, 0)
        assert not result["ok"]

    def test_capture(self):
        g = create_game(2)
        # Set up board directly: p0 piece at progress 12 (global 12),
        # p1 piece at progress 0 (global 13)
        g["players"][0]["pieces"][0] = 12
        g["players"][1]["pieces"][0] = 0
        g["current_seat"] = 0
        g["phase"] = "move"
        g["dice_value"] = 1
        g["movable_pieces"] = [0]
        # p0 moves from 12 to 13 (global 13), same as p1's global pos
        result = move_piece(g, 0)
        assert result["ok"]
        assert len(result["captures"]) == 1
        assert result["captures"][0]["seat"] == 1
        # p1 piece should be back at base
        assert g["players"][1]["pieces"][0] == -1

    def test_exact_finish(self):
        g = create_game()
        g["players"][0]["pieces"][0] = 49
        g["phase"] = "move"
        g["dice_value"] = 6
        g["movable_pieces"] = [0]
        result = move_piece(g, 0)
        assert result["ok"]
        assert result["finished"] is True
        assert g["players"][0]["pieces"][0] == FINISH_PROGRESS
        assert g["players"][0]["finished_count"] == 1

    def test_overshoot_bounce(self):
        g = create_game()
        g["players"][0]["pieces"][0] = 52
        g["phase"] = "move"
        g["dice_value"] = 5
        g["movable_pieces"] = [0]
        result = move_piece(g, 0)
        assert result["ok"]
        # 52 + 5 = 57, overshoot by 2, bounce to 55 - 2 = 53
        assert result["new_progress"] == 53
        assert g["players"][0]["pieces"][0] == 53

    def test_all_pieces_finish_wins(self):
        g = create_game(2)
        # Set player 0 with 3 pieces finished
        g["players"][0]["pieces"] = [55, 55, 55, 49]
        g["players"][0]["finished_count"] = 3
        g["phase"] = "move"
        g["dice_value"] = 6
        g["movable_pieces"] = [3]
        result = move_piece(g, 3)
        assert result["ok"]
        assert result["finished"] is True
        assert g["players"][0]["finished"] is True

    def test_extra_turn_on_6(self):
        g = create_game()
        roll_dice(g, dice_value=6)
        move_piece(g, 0)
        # Should still be player 0's turn
        assert g["current_seat"] == 0
        assert g["phase"] == "roll"


class TestTrackColors:
    """配色循环与起跑线偏移必须对齐——这是「同色跳格」成立的前提。"""

    def test_start_square_matches_seat_color(self):
        for seat in range(4):
            assert track_color(get_global_position(seat, 0)) == COLORS[seat]

    def test_own_color_iff_progress_multiple_of_four(self):
        """对任何座位：「相对进度是 4 的倍数」⇔「踩在自己颜色上」。"""
        for seat in range(4):
            for p in range(RING_END + 1):
                on_own = track_color(get_global_position(seat, p)) == COLORS[seat]
                assert on_own == is_own_color(p)

    def test_every_color_appears_thirteen_times(self):
        counts = {c: 0 for c in COLORS}
        for g in range(RING):
            counts[track_color(g)] += 1
        assert counts == {c: 13 for c in COLORS}


class TestSameColorJump:
    """同色跳格：落在自己颜色的格子上向前跳 4 格，只跳一次。"""

    def test_landing_on_own_color_jumps_four(self):
        g = _ready(create_game(), 0, [5, -1, -1, -1], dice=3)
        result = move_piece(g, 0)
        assert result["new_progress"] == 12  # 8 是同色格 → 跳到 12
        assert result["path"] == [6, 7, 8, 12]
        assert result["segments"] == [{"kind": "jump", "from": 8, "to": 12}]

    def test_jump_is_one_shot(self):
        """跳一次就停，不连锁：落到 8 只跳到 12，不会继续跳到 16。"""
        g = _ready(create_game(), 0, [1, -1, -1, -1], dice=3)
        result = move_piece(g, 0)
        assert result["new_progress"] == 8
        assert result["segments"] == [{"kind": "jump", "from": 4, "to": 8}]

    def test_no_jump_when_not_own_color(self):
        g = _ready(create_game(), 0, [10, -1, -1, -1], dice=1)
        result = move_piece(g, 0)
        assert result["new_progress"] == 11
        assert result["segments"] == []

    def test_no_jump_into_home_stretch(self):
        """48 是同色格，但再跳 4 格就越过航道入口——不许跳进航道绕过精确到达。"""
        g = _ready(create_game(), 0, [44, -1, -1, -1], dice=4)
        result = move_piece(g, 0)
        assert result["new_progress"] == 48
        assert result["segments"] == []


class TestArrowFly:
    """虚线飞行：踩到虚线格先飞到对岸，再从对岸跳一次；跳到虚线格上则飞完就停。"""

    def test_only_own_color_arrow_is_usable(self):
        """四条虚线一色一条，**只有自己颜色的那条能飞**（标准规则原文）。

        赛道上的虚线在绝对索引 3/16/29/42（黄/蓝/绿/红，各归一个座位）；
        因为「自己颜色」⇔「相对进度是 4 的倍数」，而 3/16/29/42 里只有 16 是
        4 的倍数——所以对任何座位，可用的都恰好是相对进度 16 那一条。
        """
        usable = [p for p in range(RING_END + 1) if is_arrow(p)]
        assert usable == [ARROW_FIRST + QUADRANT] == [16]
        assert arrow_target(16) == 16 + ARROW_STEP == 28
        # 跨度必须是 4 的倍数，否则对岸不是同色格
        assert ARROW_STEP % JUMP_STEP == 0
        assert (16 + ARROW_STEP) % JUMP_STEP == 0

    def test_other_colors_arrows_never_fire(self):
        """另外三条虚线对任何座位都不是自己颜色 → 不生效（四个座位一致）。"""
        for p in (ARROW_FIRST, ARROW_FIRST + 2 * QUADRANT, ARROW_FIRST + 3 * QUADRANT):
            assert not is_arrow(p)
            assert arrow_target(p) == -1
            for seat in range(4):
                g = create_game(4)
                g["players"][seat]["pieces"][0] = p
                final, path, segments = resolve_landing(g, seat, p)
                assert (final, path, segments) == (p, [], [])

    def test_landing_on_other_colors_arrow_does_not_fly(self):
        """踩到别人颜色的虚线格：只是普通落点，不飞。"""
        g = _ready(create_game(), 0, [0, -1, -1, -1], dice=3)
        result = move_piece(g, 0)
        assert result["new_progress"] == 3
        assert result["path"] == [1, 2, 3]
        assert result["segments"] == []

    def test_walk_onto_arrow_flies_then_jumps(self):
        """走到自己颜色的虚线格：先飞，落到同色格后再跳一次（标准规则原文）。"""
        g = _ready(create_game(), 0, [12, -1, -1, -1], dice=4)
        result = move_piece(g, 0)
        assert result["new_progress"] == 32  # 16 → 飞 28 → 跳 32
        assert result["path"] == [13, 14, 15, 16, 28, 32]
        assert result["segments"] == [
            {"kind": "fly", "from": 16, "to": 28},
            {"kind": "jump", "from": 28, "to": 32},
        ]

    def test_jump_onto_arrow_flies_and_stops(self):
        """标准规则原文：由跳格落到虚线格上，飞完就不再移动。"""
        g = _ready(create_game(), 0, [9, -1, -1, -1], dice=3)
        result = move_piece(g, 0)
        assert result["new_progress"] == 28  # 12 → 跳 16 → 飞 28，落定
        assert result["path"] == [10, 11, 12, 16, 28]
        assert result["segments"] == [
            {"kind": "jump", "from": 12, "to": 16},
            {"kind": "fly", "from": 16, "to": 28},
        ]

    def test_takeoff_does_not_trigger_jump(self):
        """起飞只是「放到起飞点」：起飞点本身是同色格，但起飞不触发跳格。"""
        g = create_game()
        roll_dice(g, dice_value=6)
        result = move_piece(g, 0)
        assert result["new_progress"] == 0
        assert result["segments"] == []
        assert result["path"] == [0]

    def test_resolve_landing_is_read_only(self):
        """resolve_landing 只读：AI 拿它做推演，不能有副作用。"""
        g = _ready(create_game(), 0, [12, -1, -1, -1], dice=4)
        final, path, segments = resolve_landing(g, 0, 16)
        assert final == 32                       # 16 飞 28 再跳 32
        assert path == [28, 32]
        assert segments[0]["kind"] == "fly"
        assert g["players"][0]["pieces"][0] == 12


class TestJumpFlyCapture:
    def test_enemy_on_own_color_square_captured_without_jumping(self):
        """落在自己颜色格且有敌机 → 直接撞落，不跳不飞（否则等于放过对手）。"""
        g = create_game(2)
        # seat1 progress 47 → 全局 (47+13)%52 = 8
        g["players"][1]["pieces"][0] = 47
        _ready(g, 0, [6, -1, -1, -1], dice=2)
        result = move_piece(g, 0)
        assert result["new_progress"] == 8  # 没跳
        assert result["segments"] == []
        assert len(result["captures"]) == 1
        assert g["players"][1]["pieces"][0] == -1

    def test_capture_happens_at_jump_destination(self):
        """撞机发生在跳/飞之后的最终落点。"""
        g = create_game(2)
        # seat1 progress 15 → 全局 (15+13)%52 = 28，正是 seat0 从 24 跳到的格子
        g["players"][1]["pieces"][0] = 15
        _ready(g, 0, [20, -1, -1, -1], dice=4)
        result = move_piece(g, 0)
        assert result["new_progress"] == 28
        assert len(result["captures"]) == 1
        assert g["players"][1]["pieces"][0] == -1


class TestStacking:
    """迭子（同色 ≥2 架同格）：不可被敌方飞越；落上去同归于尽。

    几何：seat0 相对进度 p → 全局 p；seat1 相对进度 q → 全局 (q+13)%52。
    """

    def test_enemy_stack_blocks_path(self):
        """路径穿过敌方迭子 → 该走法非法（迭子不可飞越）。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        # seat1 两架叠在全局 8（= seat1 相对进度 47）
        assert get_global_position(1, 47) == 8
        g["players"][1]["pieces"][0] = 47
        g["players"][1]["pieces"][1] = 47
        result = roll_dice(g, dice_value=4)   # 6 → 10，途经 8
        assert result["movable"] == []

    def test_own_stack_does_not_block(self):
        """自己一方的迭子不挡自己。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        g["players"][0]["pieces"][1] = 8
        g["players"][0]["pieces"][2] = 8
        result = roll_dice(g, dice_value=4)
        assert 0 in result["movable"]

    def test_single_enemy_does_not_block(self):
        """单架敌机不构成迭子，可以飞越。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        g["players"][1]["pieces"][0] = 47   # 全局 8，只有一架
        result = roll_dice(g, dice_value=4)
        assert 0 in result["movable"]

    def test_landing_on_enemy_stack_is_mutual_destruction(self):
        """正好落在敌方迭子上：双方全部回基地。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        g["players"][1]["pieces"][0] = 47   # 全局 8
        g["players"][1]["pieces"][1] = 47
        result = roll_dice(g, dice_value=2)  # 6 → 8
        assert result["movable"] == [0]
        moved = move_piece(g, 0)
        assert moved["ok"]
        assert moved["new_progress"] == -1            # 自己也回基地
        assert len(moved["captures"]) == 2            # 对方两架一起回
        assert g["players"][0]["pieces"][0] == -1
        assert g["players"][1]["pieces"][:2] == [-1, -1]
        assert moved["segments"][-1]["kind"] == "bounce"

    def test_landing_on_single_enemy_is_normal_capture(self):
        """单架敌机是普通撞机：对方回基地，自己留下。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        g["players"][1]["pieces"][0] = 47   # 全局 8，只有一架
        result = roll_dice(g, dice_value=2)
        assert result["movable"] == [0]
        moved = move_piece(g, 0)
        assert moved["new_progress"] == 8
        assert len(moved["captures"]) == 1
        assert g["players"][0]["pieces"][0] == 8
        assert g["players"][1]["pieces"][0] == -1

    def test_final_cell_stack_does_not_block(self):
        """终点格上的迭子不算「飞越」——落上去是同归于尽，不是非法走法。"""
        g = create_game(2)
        g["players"][0]["pieces"][0] = 6
        g["players"][1]["pieces"][0] = 46   # 全局 7，途经格（单架，不该被挡）
        g["players"][1]["pieces"][1] = 47   # 全局 8 = 落点
        g["players"][1]["pieces"][2] = 47
        result = roll_dice(g, dice_value=2)
        assert 0 in result["movable"]


class TestGlobalPosition:
    def test_player_0_start(self):
        assert get_global_position(0, 0) == 0

    def test_player_1_start(self):
        assert get_global_position(1, 0) == 13

    def test_player_2_start(self):
        assert get_global_position(2, 0) == 26

    def test_player_3_start(self):
        assert get_global_position(3, 0) == 39

    def test_wraparound(self):
        # Player 0 at progress 48 = global 48（赛道最后一格）
        assert get_global_position(0, RING_END) == 48
        # Player 3 at progress 20 = global (20+39) % 52 = 7
        assert get_global_position(3, 20) == 7

    def test_invalid_progress(self):
        assert get_global_position(0, -1) == -1
        assert get_global_position(0, RING_END + 1) == -1


class TestSurrender:
    def test_surrender_resets_pieces(self):
        g = create_game(2)
        g["players"][0]["pieces"] = [10, 20, 30, FINISH_PROGRESS]
        g["players"][0]["finished_count"] = 1
        result = surrender(g, 0)
        assert g["players"][0]["pieces"] == [-1, -1, -1, -1]
        assert g["players"][0]["finished"] is True

    def test_surrender_all_ends_game(self):
        g = create_game(2)
        # Player 1 already finished
        g["players"][1]["pieces"] = [FINISH_PROGRESS] * 4
        g["players"][1]["finished_count"] = 4
        g["players"][1]["finished"] = True
        g["players"][1]["finished_at"] = "2024-01-01T00:00:00"
        result = surrender(g, 0)
        assert result is not None
        assert g["finished"] is True


class TestRanking:
    def test_ranking_order(self):
        g = create_game(3)
        g["players"][0]["finished"] = True
        g["players"][0]["finished_at"] = "2024-01-01T00:02:00"
        g["players"][0]["finished_count"] = 4
        g["players"][1]["finished"] = True
        g["players"][1]["finished_at"] = "2024-01-01T00:01:00"
        g["players"][1]["finished_count"] = 4
        g["players"][2]["finished"] = False
        g["players"][2]["finished_count"] = 2
        ranking = compute_ranking(g, _players_info(g))
        assert ranking[0]["seat"] == 1  # finished first
        assert ranking[1]["seat"] == 0  # finished second
        assert ranking[2]["seat"] == 2  # not finished
        assert ranking[0]["rank"] == 1
        assert ranking[1]["rank"] == 2
        assert ranking[2]["rank"] == 3

    def test_unfinished_sorted_by_count(self):
        g = create_game(3)
        g["players"][0]["finished_count"] = 1
        g["players"][1]["finished_count"] = 3
        g["players"][2]["finished_count"] = 2
        ranking = compute_ranking(g, _players_info(g))
        assert ranking[0]["seat"] == 1  # most pieces finished
        assert ranking[1]["seat"] == 2
        assert ranking[2]["seat"] == 0


class TestGameToSync:
    def test_sync_structure(self):
        g = create_game(3)
        sync = game_to_sync(g)
        assert sync["game"] == "ludo"
        assert sync["num_players"] == 3
        assert "0" in sync["players"]
        assert "1" in sync["players"]
        assert "2" in sync["players"]
        assert sync["current_seat"] == 0
        assert sync["phase"] == "roll"

    def test_sync_with_ranking(self):
        g = create_game(2)
        g["finished"] = True
        g["reason"] = "all_finished"
        info = _players_info(g)
        sync = game_to_sync(g, info)
        assert "ranking" in sync


class TestFullGameFlow:
    """Integration test: play a short game with injected dice."""
    def test_two_player_game(self):
        g = create_game(2)
        # Player 0: take off all 4 pieces and finish them
        moves = 0
        max_moves = 500
        while not g["finished"] and moves < max_moves:
            seat = g["current_seat"]
            if g["phase"] == "roll":
                # Alternate dice values to avoid 3 consecutive 6s
                dice = [6, 3, 4, 5, 2, 1][moves % 6]
                result = roll_dice(g, dice_value=dice)
                if not result["ok"]:
                    break
                if not result["movable"]:
                    moves += 1
                    continue
            elif g["phase"] == "move":
                piece = g["movable_pieces"][0]
                result = move_piece(g, piece)
                if not result["ok"]:
                    break
                moves += 1
        # Game should complete within 500 moves
        assert moves < max_moves or g["finished"]
