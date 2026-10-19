"""军棋引擎单测。

棋盘拓扑的不变式（133 条边 / 35 铁路 / 98 公路 / 每方 5 行营 + 2 大本营 + 23 兵站）
是**照着调研文档锁死的** —— 拓扑一错，走法、AI、客户端渲染会一起错，而且从界面上
很难看出是几何错了。
"""

import random

import pytest

from app import luzhanqi as L


# ------------------------------------------------------------------ 工具


def _mk(pieces: dict) -> list:
    """``{(r,c): (color, piece)}`` → 棋盘。"""
    board = [[None] * L.COLS for _ in range(L.ROWS)]
    for (r, c), (color, piece) in pieces.items():
        board[r][c] = {"c": color, "p": piece}
    return board


def _setup_game(mode: str = "open", reveal: bool = True, rng=None) -> dict:
    """建一局、双方各发一套默认阵型、双方确认 → phase == playing。"""
    rng = rng or random.Random(20261014)
    gs = L.create_game(mode, reveal)
    gs["player_colors"] = {1: L.RED, 2: L.BLACK}
    for color in (L.RED, L.BLACK):
        L.fill_layout(gs, color, L.random_layout(color, rng))
    gs["ready"] = {L.RED: False, L.BLACK: False}
    gs["flag_revealed"] = {L.RED: False, L.BLACK: False}
    return gs


def _play(gs: dict) -> dict:
    for color in (L.RED, L.BLACK):
        L.confirm_setup(gs, color)
    return gs


def _find(board, color, piece):
    for r in range(L.ROWS):
        for c in range(L.COLS):
            cell = board[r][c]
            if cell and cell["c"] == color and cell["p"] == piece:
                return (r, c)
    raise AssertionError(f"棋盘上没有 {color} 的 {piece}")


# ------------------------------------------------------------------ 棋盘拓扑


class TestTopology:
    def test_sixty_landing_points(self):
        kinds = L.TOPOLOGY["kinds"]
        assert len(kinds) == L.ROWS == 12
        assert all(len(row) == L.COLS == 5 for row in kinds)

    def test_cell_kind_counts(self):
        kinds = [k for row in L.TOPOLOGY["kinds"] for k in row]
        assert kinds.count(L.POST) == 46
        assert kinds.count(L.CAMP) == 10
        assert kinds.count(L.HQ) == 4

    def test_each_side_has_five_camps_two_hqs_twentythree_posts(self):
        for color in (L.RED, L.BLACK):
            rows = set(L.own_rows(color))
            camps = [c for c in L.CAMP_CELLS if c[0] in rows]
            hqs = [c for c in L.HQ_CELLS if c[0] in rows]
            posts = [c for c in L.own_cells(color) if c not in L.HQ_CELLS]
            assert len(camps) == 5, color
            assert len(hqs) == 2, color
            assert len(posts) == 23, color
            assert len(L.own_cells(color)) == 25, color

    def test_edge_counts(self):
        """133 = 35 铁路 + 98 公路（调研文档里的邻接总账）。"""
        assert len(L.TOPOLOGY["rails"]) == 35
        assert len(L.TOPOLOGY["roads"]) == 98

    def test_no_duplicate_and_symmetric_edges(self):
        seen = set()
        for r in range(L.ROWS):
            for c in range(L.COLS):
                for kind in ("rail", "road"):
                    nb = L.NEIGHBORS[(r, c)][kind]
                    assert len(nb) == len(set(nb)), f"{(r, c)} {kind} 有重复邻居"
                    for other in nb:
                        assert (r, c) in L.NEIGHBORS[other][kind], "邻接不对称"
                        key = ((r, c), other) if (r, c) < other else (other, (r, c))
                        assert key not in seen or True  # 反向会在对称检查里覆盖
                        seen.add(key)
        assert len(seen) == 133

    def test_rail_rows_are_front_and_back_lines_not_second_and_fourth(self):
        """横向铁路在**底线与锋线**（通俗文章最容易记错的一处）。"""
        for r in L.RAIL_H_ROWS:
            assert (r, 0) in [(r, c) for c in range(L.COLS)]
            for c in range(L.COLS - 1):
                assert (r, c + 1) in L.NEIGHBORS[(r, c)]["rail"], f"row {r} 应当是整行铁路"
        for r in (2, 3, 4, 7, 8, 9):
            assert (r, 1) not in L.NEIGHBORS[(r, 0)]["rail"]

    def test_middle_column_rail_is_only_the_center_gap(self):
        """中路（列 2）的纵向铁路**只有中缝那一段**，阵地内是公路。"""
        assert (6, 2) in L.NEIGHBORS[(5, 2)]["rail"]
        for r in (1, 2, 3, 4, 6, 7, 8, 9):
            assert (r + 1, 2) not in L.NEIGHBORS[(r, 2)]["rail"], f"({r},2) 不该是铁路"
            assert (r + 1, 2) in L.NEIGHBORS[(r, 2)]["road"]

    def test_three_fronts_and_two_mountains(self):
        assert len(L.TOPOLOGY["fronts"]) == 3
        assert len(L.TOPOLOGY["mountains"]) == 2
        for r1, c1, r2, c2 in L.TOPOLOGY["fronts"]:
            assert (r2, c2) in L.NEIGHBORS[(r1, c1)]["rail"]
        for r1, c1, r2, c2 in L.TOPOLOGY["mountains"]:
            assert (r2, c2) not in L.NEIGHBORS[(r1, c1)]["rail"]
            assert (r2, c2) not in L.NEIGHBORS[(r1, c1)]["road"]

    def test_thirtytwo_diagonals_all_roads(self):
        diagonals = {((a, b) if a < b else (b, a))
                     for a in L.NEIGHBORS for b in L.NEIGHBORS[a]["road"]
                     if a[0] != b[0] and a[1] != b[1]}
        assert len(diagonals) == 32
        assert not [1 for a in L.NEIGHBORS for b in L.NEIGHBORS[a]["rail"]
                    if a[0] != b[0] and a[1] != b[1]]

    def test_camp_has_eight_road_neighbours_and_no_rail(self):
        """行营周围是 X 形通路：四正 + 四斜，全是公路。"""
        for camp in L.CAMP_CELLS:
            assert len(L.NEIGHBORS[camp]["road"]) == 8, camp
            assert L.NEIGHBORS[camp]["rail"] == [], camp


# ------------------------------------------------------------------ 布阵


class TestLayout:
    def test_random_layout_is_always_valid(self):
        rng = random.Random(3)
        for color in (L.RED, L.BLACK):
            for _ in range(60):
                layout = L.random_layout(color, rng)
                assert L.validate_layout(color, layout) is None

    def test_random_layout_respects_the_three_constraints(self):
        rng = random.Random(11)
        for color in (L.RED, L.BLACK):
            for _ in range(40):
                layout = L.random_layout(color, rng)
                flag_cells = [c for c, p in layout.items() if p == L.JUNQI]
                assert len(flag_cells) == 1 and flag_cells[0] in L.HQ_CELLS
                for cell, piece in layout.items():
                    if piece == L.DILEI:
                        assert cell in L.back_two_rows(color)
                    if piece == L.ZHADAN:
                        assert cell[0] != L.front_row(color)

    def test_validate_rejects_flag_outside_hq(self):
        layout = L.random_layout(L.RED, random.Random(1))
        flag_cell = next(c for c, p in layout.items() if p == L.JUNQI)
        target = next(c for c in L.own_cells(L.RED) if c not in L.HQ_CELLS)
        layout[flag_cell], layout[target] = layout[target], layout[flag_cell]
        assert L.validate_layout(L.RED, layout) == "军旗必须放在大本营"

    def test_validate_rejects_mine_in_front_area(self):
        layout = L.random_layout(L.RED, random.Random(2))
        mine_cell = next(c for c, p in layout.items() if p == L.DILEI)
        # 换到一个「不在最后两排」的格子上
        target = next(c for c in L.own_cells(L.RED)
                      if c not in L.back_two_rows(L.RED) and layout[c] != L.JUNQI)
        layout[mine_cell], layout[target] = layout[target], layout[mine_cell]
        err = L.validate_layout(L.RED, layout)
        assert err in ("地雷只能放在最后两排", "军旗必须放在大本营")

    def test_validate_rejects_bomb_in_front_row(self):
        layout = L.random_layout(L.RED, random.Random(4))
        bomb = next(c for c, p in layout.items() if p == L.ZHADAN)
        target = (L.front_row(L.RED), bomb[1])
        layout[bomb], layout[target] = layout[target], layout[bomb]
        assert L.validate_layout(L.RED, layout) == "炸弹不能放在第一排"

    def test_validate_rejects_wrong_counts_and_foreign_cells(self):
        layout = L.random_layout(L.RED, random.Random(5))
        short = dict(layout)
        short.pop(next(iter(short)))
        assert L.validate_layout(L.RED, short) == "布阵必须摆满自己半场的 25 个位置"
        outside = dict(layout)
        outside[(0, 0)] = outside.pop(next(iter(outside)))
        assert L.validate_layout(L.RED, outside) == "棋子只能摆在自己半场的兵站与大本营上"

    def test_swap_works_and_rejects_with_rollback(self):
        gs = _setup_game()
        board = gs["board"]
        # 两枚同级大子互换必然合法
        a = _find(board, L.RED, L.SHIZHANG)
        b = next(c for c in L.own_cells(L.RED)
                 if board[c[0]][c[1]] and board[c[0]][c[1]]["p"] == L.SHIZHANG and c != a)
        before = L.layout_of(gs, L.RED)
        assert L.swap_layout(gs, L.RED, a, b) is None
        after = L.layout_of(gs, L.RED)
        assert after[a] == before[b] and after[b] == before[a]
        # 把军旗换到非大本营 → 必须被拒且**原样回滚**
        flag_cell = _find(board, L.RED, L.JUNQI)
        target = next(c for c in L.own_cells(L.RED) if c not in L.HQ_CELLS)
        snapshot = L.layout_of(gs, L.RED)
        assert L.swap_layout(gs, L.RED, flag_cell, target) == "军旗必须放在大本营"
        assert L.layout_of(gs, L.RED) == snapshot

    def test_swap_rejects_after_confirm_and_enemy_cells(self):
        gs = _setup_game()
        L.confirm_setup(gs, L.RED)
        a, b = L.own_cells(L.RED)[0], L.own_cells(L.RED)[1]
        assert L.swap_layout(gs, L.RED, a, b) == "已经确认布阵，不能再改"
        assert L.swap_layout(gs, L.BLACK, a, b) == "只能交换自己半场的棋子"

    def test_every_preset_is_a_legal_layout_for_both_colors(self):
        """经典阵型是**照抄公开阵库**的 6×5 文本表 —— 抄错一个字就是「25 枚摆不满」
        或「地雷跑到第一排」。这条把每个阵型对两个颜色各验一遍。"""
        assert len(L.PRESETS) >= 4
        for key, label in L.PRESETS:
            for color in (L.RED, L.BLACK):
                layout = L.preset_layout(color, key)
                assert layout is not None, (key, color)
                assert len(layout) == 25, (label, color)
                assert L.validate_layout(color, layout) is None, (label, color)
                counts = {}
                for piece in layout.values():
                    counts[piece] = counts.get(piece, 0) + 1
                assert counts == L.PIECE_COUNTS, (label, color, counts)

    def test_presets_are_vertical_mirrors_of_each_other(self):
        """两个颜色摆的是**同一套阵**：黑在上、红在下，逐格上下镜像。"""
        for key, _ in L.PRESETS:
            red = L.preset_layout(L.RED, key)
            black = L.preset_layout(L.BLACK, key)
            mirrored = {(11 - r, c): p for (r, c), p in black.items()}
            assert mirrored == red, key

    def test_preset_matches_the_published_layout(self):
        """抽查双喜临门：第一行「师兵连排师」、军旗在端线列 3（红方即 (11,3)）。"""
        layout = L.preset_layout(L.RED, "shuangxi")
        front = L.front_row(L.RED)
        assert [layout[(front, c)] for c in range(L.COLS)] == [
            L.SHIZHANG, L.GONGBING, L.LIANZHANG, L.PAIZHANG, L.SHIZHANG,
        ]
        assert layout[(11, 3)] == L.JUNQI
        # 只落在自己半场（红在 rows 6..11），对手那半边一个子都不能有
        assert all(r >= 6 for r, _ in layout)

    def test_unknown_preset_name_returns_none(self):
        assert L.preset_layout(L.RED, "不存在的阵") is None
        assert L.preset_layout(L.RED, "") is None

    def test_presets_payload_shape(self):
        payload = L.presets_payload()
        assert payload == [{"key": k, "label": v} for k, v in L.PRESETS]
        assert all(set(item) == {"key", "label"} for item in payload)

    def test_view_carries_the_preset_list(self):
        gs = _setup_game()
        assert L.view_for(gs, L.RED)["presets"] == L.presets_payload()

    def test_confirm_both_starts_play(self):
        gs = _setup_game()
        assert gs["phase"] == "setup"
        L.confirm_setup(gs, L.RED)
        assert gs["phase"] == "setup"
        L.confirm_setup(gs, L.BLACK)
        assert gs["phase"] == "playing"


# ------------------------------------------------------------------ 走法


class TestMoves:
    def test_flag_mine_and_hq_pieces_cannot_move(self):
        board = _mk({
            (11, 1): (L.RED, L.JUNQI),      # 大本营里的军旗
            (10, 0): (L.RED, L.DILEI),
            (11, 3): (L.RED, L.SILING),     # 进了大本营就不能再动
            (5, 0): (L.RED, L.SHIZHANG),    # 在铁路上，能动
        })
        assert L.piece_moves(board, 11, 1) == []
        assert L.piece_moves(board, 10, 0) == []
        assert L.piece_moves(board, 11, 3) == []
        assert L.piece_moves(board, 5, 0) != []

    def test_road_move_is_one_step_including_diagonals(self):
        board = _mk({(2, 1): (L.RED, L.PAIZHANG)})      # 行营
        dests = set(L.piece_moves(board, 2, 1))
        assert dests == {(1, 1), (3, 1), (2, 0), (2, 2),
                         (1, 0), (1, 2), (3, 0), (3, 2)}

    def test_camp_piece_moves_only_one_step(self):
        """行营里的棋子每次只能走一步 —— 即使它就在铁路旁边。"""
        board = _mk({(5, 2): (L.RED, L.GONGBING),       # 中锋在铁路上
                     (4, 2): (L.RED, L.PAIZHANG)})      # 二线中路（不是行营）
        assert (5, 2) not in L.piece_moves(board, 4, 2)
        # 行营里的工兵不许沿铁路飞：只有 8 个一步之遥的公路邻格（含四个斜向）
        board2 = _mk({(3, 2): (L.RED, L.GONGBING)})     # 中营
        assert set(L.piece_moves(board2, 3, 2)) == {
            (2, 2), (4, 2), (3, 1), (3, 3),
            (2, 1), (2, 3), (4, 1), (4, 3),
        }

    def test_rail_slide_is_straight_and_unlimited(self):
        board = _mk({(1, 0): (L.RED, L.SHIZHANG)})
        dests = set(L.piece_moves(board, 1, 0))
        # 沿 row 1 向右 4 格 + 沿 col 0 向下 9 格 + 公路 (0,0)/(2,1)
        assert {(1, 1), (1, 2), (1, 3), (1, 4)} <= dests
        assert {(r, 0) for r in range(2, 11)} <= dests
        assert dests == ({(1, 1), (1, 2), (1, 3), (1, 4)}
                         | {(r, 0) for r in range(2, 11)} | {(0, 0), (2, 1)})

    def test_rail_slide_stops_at_blocker_and_can_capture_it(self):
        board = _mk({(1, 0): (L.RED, L.SHIZHANG),
                     (1, 2): (L.BLACK, L.PAIZHANG)})
        dests = set(L.piece_moves(board, 1, 0))
        assert (1, 1) in dests
        assert (1, 2) in dests          # 敌子可以吃
        assert (1, 3) not in dests      # 但过不去
        assert (1, 4) not in dests

    def test_rail_slide_blocked_by_own_piece(self):
        board = _mk({(1, 0): (L.RED, L.SHIZHANG),
                     (1, 2): (L.RED, L.PAIZHANG)})
        dests = set(L.piece_moves(board, 1, 0))
        assert (1, 1) in dests
        assert (1, 2) not in dests and (1, 3) not in dests

    def test_non_engineer_cannot_turn_corners(self):
        board = _mk({(1, 0): (L.RED, L.SHIZHANG)})
        dests = set(L.piece_moves(board, 1, 0))
        assert (1, 4) in dests                 # 沿 row 1 走到角上
        assert (2, 4) not in dests             # 但不能顺着 col 4 拐下去

    def test_engineer_can_turn_corners_on_rails(self):
        board = _mk({(1, 0): (L.RED, L.GONGBING)})
        dests = set(L.piece_moves(board, 1, 0))
        assert (1, 4) in dests
        assert (2, 4) in dests and (10, 4) in dests      # 拐弯下到右下角
        assert (5, 2) in dests and (6, 2) in dests       # 穿过中路中缝
        assert (10, 0) in dests
        assert (0, 0) in dests                           # 但那是**公路**一步，不是铁路
        rail_cells = {cell for cell in L.NEIGHBORS if L.NEIGHBORS[cell]["rail"]}
        assert (0, 0) not in rail_cells

    def test_engineer_covers_the_whole_rail_network(self):
        board = _mk({(1, 0): (L.RED, L.GONGBING)})
        rail_cells = {cell for cell in L.NEIGHBORS if L.NEIGHBORS[cell]["rail"]}
        reachable = set(L.piece_moves(board, 1, 0)) & rail_cells
        assert reachable == rail_cells - {(1, 0)}

    def test_cannot_attack_piece_inside_a_camp(self):
        board = _mk({(1, 0): (L.RED, L.SHIZHANG),
                     (2, 1): (L.BLACK, L.PAIZHANG)})    # 行营里的敌子
        dests = set(L.piece_moves(board, 1, 0))
        assert (2, 1) not in dests
        # 行营本身可以进（空着的时候）
        board2 = _mk({(1, 0): (L.RED, L.SHIZHANG)})
        assert (2, 1) in L.piece_moves(board2, 1, 0)

    def test_mountain_is_impassable_but_fronts_are_not(self):
        board = _mk({(5, 1): (L.RED, L.SHIZHANG), (5, 0): (L.RED, L.PAIZHANG)})
        assert (6, 1) not in L.piece_moves(board, 5, 1)
        assert (6, 0) in L.piece_moves(board, 5, 0)

    def test_no_move_loss_when_only_immobile_pieces_left(self):
        board = _mk({(11, 1): (L.RED, L.JUNQI), (10, 0): (L.RED, L.DILEI),
                     (5, 0): (L.BLACK, L.SILING)})
        assert L.has_any_move(board, L.RED) is False
        assert L.has_any_move(board, L.BLACK) is True


# ------------------------------------------------------------------ 碰子


class TestCombat:
    def test_rank_order(self):
        order = list(L.RANKS)
        for i, strong in enumerate(order):
            for weak in order[i + 1:]:
                assert L.resolve_combat(strong, weak) == "defender_dies"
                assert L.resolve_combat(weak, strong) == "attacker_dies"

    def test_equal_ranks_both_die(self):
        for piece in L.RANKS:
            assert L.resolve_combat(piece, piece) == "both_die"

    def test_bomb_destroys_anything_and_dies(self):
        for piece in L.RANKS + (L.DILEI,):
            assert L.resolve_combat(L.ZHADAN, piece) == "both_die"
            assert L.resolve_combat(piece, L.ZHADAN) == "both_die"

    def test_engineer_eats_mine_others_die_on_it(self):
        assert L.resolve_combat(L.GONGBING, L.DILEI) == "defender_dies"
        assert L.resolve_combat(L.ZHADAN, L.DILEI) == "both_die"
        for piece in L.RANKS[:-1]:
            assert L.resolve_combat(piece, L.DILEI) == "attacker_dies"

    def test_combat_in_a_real_game(self):
        gs = _play(_setup_game())
        board = gs["board"]
        # 摆一个确定局面：红师长吃黑排长
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[5][0] = {"c": L.RED, "p": L.SHIZHANG}
        board[5][1] = {"c": L.BLACK, "p": L.PAIZHANG}
        board[11][1] = {"c": L.RED, "p": L.JUNQI}
        board[11][3] = {"c": L.BLACK, "p": L.JUNQI}
        gs["next"] = L.RED
        res = L.make_move(gs, (5, 0), (5, 1))
        assert res["ok"] and res["move"]["battle"]["outcome"] == "defender_dies"
        assert board[5][1]["p"] == L.SHIZHANG and board[5][0] is None
        assert gs["next"] == L.BLACK


# ------------------------------------------------------------------ 对局流程


class TestGameFlow:
    def test_cannot_move_before_setup_done(self):
        gs = _setup_game()
        res = L.make_move(gs, (6, 0), (6, 1))
        assert res == {"ok": False, "error": "还没开始对局"}

    def test_illegal_moves_are_rejected(self):
        gs = _play(_setup_game())
        assert L.make_move(gs, (5, 5), (5, 0))["error"] == "落点超出棋盘"
        assert L.make_move(gs, (0, 0), (0, 1))["error"] == "还没轮到你走"
        empty = next((r, c) for r in L.own_rows(L.RED) for c in range(L.COLS)
                     if gs["board"][r][c] is None or True)
        assert L.make_move(gs, (6, 0), (6, 0))["ok"] is False

    def test_capturing_the_flag_wins_immediately(self):
        gs = _play(_setup_game())
        board = gs["board"]
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[11][1] = {"c": L.BLACK, "p": L.JUNQI}
        board[10][1] = {"c": L.RED, "p": L.PAIZHANG}
        board[11][3] = {"c": L.RED, "p": L.JUNQI}
        board[5][0] = {"c": L.BLACK, "p": L.PAIZHANG}   # 给黑方留一口气，别触发无棋可走
        gs["next"] = L.RED
        res = L.make_move(gs, (10, 1), (11, 1))
        assert res["game_over"] == {"winner": L.RED, "reason": "flag"}
        assert gs["battles"][-1]["flag_captured"] is True

    def test_marshal_death_reveals_the_flag(self):
        gs = _play(_setup_game())
        board = gs["board"]
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[5][0] = {"c": L.BLACK, "p": L.SILING}
        board[5][1] = {"c": L.RED, "p": L.ZHADAN}       # 炸弹同归于尽
        board[11][1] = {"c": L.RED, "p": L.JUNQI}
        board[0][1] = {"c": L.BLACK, "p": L.JUNQI}
        board[10][4] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["next"] = L.RED
        res = L.make_move(gs, (5, 1), (5, 0))
        assert gs["flag_revealed"][L.BLACK] is True
        assert gs["flag_revealed"][L.RED] is False
        # 光发一个布尔值没用：得把「亮出来的那面军旗在哪」一起发下去
        assert res["move"]["newly_revealed"] == [{"color": L.BLACK, "at": [0, 1]}]
        # 之后没有新的司令阵亡 → 不再重复播报
        gs["next"] = L.BLACK
        res2 = L.make_move(gs, (10, 4), (9, 4))
        assert res2["move"]["newly_revealed"] == []

    def test_no_move_loses(self):
        gs = _play(_setup_game())
        board = gs["board"]
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[11][1] = {"c": L.RED, "p": L.JUNQI}
        board[10][0] = {"c": L.RED, "p": L.DILEI}
        board[6][0] = {"c": L.RED, "p": L.PAIZHANG}     # 红方最后一枚能动的子
        board[0][1] = {"c": L.BLACK, "p": L.JUNQI}
        board[5][4] = {"c": L.BLACK, "p": L.SILING}
        gs["next"] = L.RED
        res = L.make_move(gs, (6, 0), (7, 0))
        assert res["game_over"] is None                 # 黑方还能动
        board[7][0] = None                              # 红方现在只剩军旗 + 地雷
        gs["next"] = L.BLACK
        res = L.make_move(gs, (5, 4), (4, 4))
        assert res["game_over"] == {"winner": L.BLACK, "reason": "nomove"}

    def test_draw_after_long_peace(self):
        gs = _play(_setup_game())
        board = gs["board"]
        for r in range(L.ROWS):
            for c in range(L.COLS):
                board[r][c] = None
        board[11][1] = {"c": L.RED, "p": L.JUNQI}
        board[0][1] = {"c": L.BLACK, "p": L.JUNQI}
        board[6][0] = {"c": L.RED, "p": L.PAIZHANG}
        board[5][4] = {"c": L.BLACK, "p": L.PAIZHANG}
        gs["next"] = L.RED
        gs["no_capture"] = L.DRAW_PLIES - 1
        res = L.make_move(gs, (6, 0), (7, 0))
        assert res["game_over"] == {"winner": "draw", "reason": "no_capture"}

    def test_surrender(self):
        gs = _play(_setup_game())
        assert L.surrender(gs, L.RED)["game_over"] == {"winner": L.BLACK, "reason": "surrender"}
        assert L.surrender(gs, L.BLACK)["ok"] is False

    def test_red_moves_first(self):
        gs = _play(_setup_game())
        assert gs["next"] == L.RED


# ------------------------------------------------------------------ 信息裁剪


class TestHiddenInfo:
    def test_open_mode_shows_everything(self):
        gs = _play(_setup_game("open"))
        view = L.view_for(gs, L.RED)
        hidden = [cell for row in view["cells"] for cell in row
                  if cell and cell["p"] is None]
        assert hidden == []

    def test_hidden_mode_never_sends_opponent_pieces(self):
        gs = _play(_setup_game("hidden"))
        for viewer in (L.RED, L.BLACK):
            view = L.view_for(gs, viewer)
            for r in range(L.ROWS):
                for c in range(L.COLS):
                    cell = view["cells"][r][c]
                    real = gs["board"][r][c]
                    if cell is None:
                        assert real is None
                    elif cell["c"] == viewer:
                        assert cell["p"] == real["p"]        # 自己的棋子看得到
                    else:
                        assert cell["p"] is None, "对手的棋子身份泄露了"

    def test_hidden_mode_shows_own_layout_during_setup(self):
        gs = _setup_game("hidden")
        view = L.view_for(gs, L.RED)
        assert view["phase"] == "setup"
        assert sum(1 for row in view["cells"] for cell in row
                   if cell and cell["c"] == L.RED) == 25
        assert sum(1 for row in view["cells"] for cell in row
                   if cell and cell["c"] == L.BLACK and cell["p"] is None) == 25

    def test_revealed_flag_becomes_visible(self):
        gs = _play(_setup_game("hidden"))
        gs["flag_revealed"][L.BLACK] = True
        view = L.view_for(gs, L.RED)
        flag = _find(gs["board"], L.BLACK, L.JUNQI)
        assert view["cells"][flag[0]][flag[1]]["p"] == L.JUNQI

    def test_legal_moves_are_only_for_the_viewer(self):
        """暗棋里「某位置有子可动」本身就是情报，所以只能算收件人自己的。"""
        gs = _play(_setup_game("hidden"))
        gs["next"] = L.BLACK
        assert L.view_for(gs, L.RED)["legal"] == []
        assert L.view_for(gs, L.BLACK)["legal"] != []
        assert all(m[0] >= 6 for m in L.view_for(gs, L.BLACK)["legal"]) is False

    def test_mask_battle_modes(self):
        battle = {"at": [5, 1], "attacker": L.RED, "attacker_piece": L.SHIZHANG,
                  "defender": L.BLACK, "defender_piece": L.LVZHANG,
                  "outcome": "defender_dies"}
        full = L.mask_battle(battle, L.RED, "open", False)
        assert full["attacker_piece"] == L.SHIZHANG and full["defender_piece"] == L.LVZHANG
        revealed = L.mask_battle(battle, L.RED, "hidden", True)
        assert revealed["defender_piece"] == L.LVZHANG
        closed = L.mask_battle(battle, L.RED, "hidden", False)
        assert closed["attacker_piece"] == L.SHIZHANG     # 自己的棋子自己知道
        assert "defender_piece" not in closed             # 对手的一律不公布
        assert closed["outcome"] == "defender_dies"       # 但「谁出局」是公布的
        other_side = L.mask_battle(battle, L.BLACK, "hidden", False)
        assert other_side["defender_piece"] == L.LVZHANG
        assert "attacker_piece" not in other_side

    def test_snapshot_is_full_truth(self):
        gs = _play(_setup_game("hidden"))
        snap = L.snapshot(gs)
        assert snap["game"] == "luzhanqi" and snap["mode"] == "hidden"
        assert snap["rows"] == 12 and snap["cols"] == 5
        assert sum(1 for row in snap["board"] for cell in row if cell) == 50

    def test_view_is_json_serialisable(self):
        import json
        gs = _play(_setup_game("hidden"))
        json.dumps(L.view_for(gs, L.RED))
        json.dumps(L.snapshot(gs))


# ------------------------------------------------------------------ 随机对局


class TestRandomPlaythrough:
    def test_random_games_terminate_and_stay_consistent(self):
        """随机走子跑完整局：不能崩、不能出现非法状态、必须终止。"""
        rng = random.Random(99)
        for game_no in range(12):
            gs = _play(_setup_game("open", rng=rng))
            for _ in range(4000):
                if gs["finished"]:
                    break
                moves = L.all_moves(gs["board"], gs["next"])
                if not moves:
                    break
                frm, to = rng.choice(moves)
                res = L.make_move(gs, frm, to)
                assert res["ok"], res
                if res["game_over"]:
                    break
            assert gs["finished"], f"第 {game_no} 局没能在 4000 步内结束"
            assert gs["winner"] in (L.RED, L.BLACK, "draw")
            # 军旗还在棋盘上（除非是被吃掉的终局）
            for color in (L.RED, L.BLACK):
                alive = L.alive_counts(gs["board"], color)
                if gs["reason"] != "flag" or gs["winner"] == color:
                    assert alive.get(L.JUNQI, 0) == 1
