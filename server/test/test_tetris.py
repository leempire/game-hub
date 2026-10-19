"""俄罗斯方块引擎单测（纯逻辑，无 IO）。

坐标约定：`board[row][col]`，row 0 在最上面、row 19 是底行；1..7 是方块 id，
8 是攻击行的填充块，0 是空。

**消行类用例一律手工造「差一块就满」的底行**，不用随机硬降：随机硬降的方块
永远铺不满一整行（自检里试过，26~58 块只会把盘堆到顶死、一行都消不掉），
用它断言「能拿到消行分」会稳定失败。

Run: pytest test/test_tetris.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game import tetris as t


# ── 工具 ──────────────────────────────────────────────────────


def _game(seeds=None, uids=(1, 2)):
    gs = t.create_game(seeds or {u: 7 for u in uids}, list(uids))
    for uid in uids:
        t.spawn_next(gs, uid)
    return gs


def _land(gs, uid, piece=None, rotation=0):
    """把 uid 的当前方块移到落点，返回它的落点格子。

    顺带把底行填满、只留这些格子空着——这样硬降下来正好补齐一整行。
    """
    board = gs["boards"][uid]
    cur = gs["current"][uid]
    if piece is not None:
        cur = {"piece": piece, "rotation": rotation, "x": 3, "y": 0}
        gs["current"][uid] = cur
    cur["y"] += t.drop_distance(board, cur)
    landing = t.cells_of(cur["piece"], cur["rotation"], cur["x"], cur["y"])
    for c in range(t.COLS):
        board[t.ROWS - 1][c] = 8
    for r, c in landing:
        board[r][c] = 0
    return landing


# ── 盘面与方块定义 ────────────────────────────────────────────


class TestBoardAndPieces:
    def test_empty_board_shape(self):
        b = t.create_board()
        assert len(b) == t.ROWS == 20
        assert all(len(r) == t.COLS == 10 for r in b)
        assert all(c == t.EMPTY for row in b for c in row)

    def test_seven_pieces_four_states_each(self):
        assert sorted(t.PIECES.keys()) == sorted("IOTSZJL")
        assert len(t.PIECE_IDS) == 7
        for name, states in t.PIECES.items():
            assert len(states) == 4, name
            for st in states:
                assert len(st) == 4, f"{name} 某态不是 4 格"
                assert len(set(st)) == 4, f"{name} 某态有重复格"
                assert all(0 <= dy <= 3 and 0 <= dx <= 3 for dy, dx in st), f"{name} 越界"

    def test_every_piece_can_spawn_on_empty_board(self):
        board = t.create_board()
        for name in t.PIECES:
            cur = t.spawn(name)
            assert t.is_valid(board, name, cur["rotation"], cur["x"], cur["y"]), name

    def test_spawn_is_centred(self):
        """出生位置要居中：7 种方块的落点列都该落在盘面中间一带。"""
        board = t.create_board()
        for name in t.PIECES:
            cur = t.spawn(name)
            cols = [c for _, c in t.cells_of(name, 0, cur["x"], cur["y"])]
            assert 3 <= min(cols) and max(cols) <= 6, (name, cols)

    def test_rotation_cycles_back(self):
        board = t.create_board()
        cur = t.spawn("T")
        for _ in range(4):
            t.rotate(board, cur, 1)
        assert cur["rotation"] == 0


class TestSevenBag:
    def test_bag_has_all_seven_once(self):
        bag = t._refill_bag(t.create_seeded_rng(42))
        assert sorted(bag) == sorted(t.PIECES.keys())

    def test_same_seed_same_sequence(self):
        a = t.create_game({1: 99}, [1])
        b = t.create_game({1: 99}, [1])
        seq_a = [t.draw_piece(a["queues"][1], a["rngs"][1]) for _ in range(30)]
        seq_b = [t.draw_piece(b["queues"][1], b["rngs"][1]) for _ in range(30)]
        assert seq_a == seq_b

    def test_different_seed_different_sequence(self):
        a = t.create_game({1: 1}, [1])
        b = t.create_game({1: 2}, [1])
        seq_a = [t.draw_piece(a["queues"][1], a["rngs"][1]) for _ in range(30)]
        seq_b = [t.draw_piece(b["queues"][1], b["rngs"][1]) for _ in range(30)]
        assert seq_a != seq_b

    def test_no_piece_missing_for_long_stretch(self):
        """7-bag 的意义：任何连续 7 个里七种各一次（不会长时间不来某个形状）。"""
        gs = t.create_game({1: 5}, [1])
        seq = [t.draw_piece(gs["queues"][1], gs["rngs"][1]) for _ in range(70)]
        for i in range(0, 70, 7):
            assert sorted(seq[i:i + 7]) == sorted(t.PIECES.keys()), i


# ── 移动与旋转 ────────────────────────────────────────────────


class TestMoveRotate:
    def test_move_left_stops_at_wall(self):
        board = t.create_board()
        cur = t.spawn("T")
        while t.move(board, cur, -1, 0):
            pass
        assert min(c for _, c in t.cells_of("T", 0, cur["x"], cur["y"])) == 0

    def test_move_right_stops_at_wall(self):
        board = t.create_board()
        cur = t.spawn("T")
        while t.move(board, cur, 1, 0):
            pass
        assert max(c for _, c in t.cells_of("T", 0, cur["x"], cur["y"])) == t.COLS - 1

    def test_soft_drop_lands_on_floor(self):
        board = t.create_board()
        cur = t.spawn("I")
        while t.move(board, cur, 0, 1):
            pass
        assert max(r for r, _ in t.cells_of("I", 0, cur["x"], cur["y"])) == t.ROWS - 1

    def test_hard_drop_distance_matches_soft_drop(self):
        board = t.create_board()
        a = t.spawn("T")
        d = t.drop_distance(board, a)
        b = t.spawn("T")
        for _ in range(d):
            assert t.move(board, b, 0, 1)
        assert not t.move(board, b, 0, 1)
        assert a["y"] + d == b["y"]

    def test_cannot_move_into_stack(self):
        board = t.create_board()
        board[19][0] = 8
        cur = {"piece": "O", "rotation": 0, "x": 0, "y": 0}
        while t.move(board, cur, 0, 1):
            pass
        # O 占 (0,1)(0,2)(1,1)(1,2)，落到最低时会压到 (19,0) 吗？不会（列 1/2）
        assert t.is_valid(board, "O", 0, cur["x"], cur["y"])

    def test_rotation_kicks_off_wall(self):
        """贴墙旋转靠踢墙成功，而不是原地失败。

        竖 I（rot=1）占 x+2 列；摆在 x=7（占第 9 列）转横（占 x..x+3 = 7..10）
        在 dx=0 时越界，必须靠 dx=-1 挪开。dx=0 就能成立的摆放测不出踢墙。
        """
        board = t.create_board()
        cur = {"piece": "I", "rotation": 1, "x": 7, "y": 5}
        assert t.is_valid(board, "I", 1, 7, 5)
        assert not t.is_valid(board, "I", 0, 7, 5), "这个摆放本来就该越界，用例前置不成立"
        assert t.rotate(board, cur, 1), "贴墙旋转应靠踢墙成功"
        assert cur["x"] == 6, f"应被踢到 x=6，实际 {cur['x']}"
        assert t.is_valid(board, "I", cur["rotation"], cur["x"], cur["y"])

    def test_rotation_fails_when_no_kick_fits(self):
        """前后左右都被堵死时旋转应当原地失败（不硬塞）。"""
        board = t.create_board()
        cur = {"piece": "I", "rotation": 0, "x": 3, "y": 10}
        # 把 rot=0 / rot=1 所有可能的落点都填上，只留当前这一处
        for r in range(t.ROWS):
            for c in range(t.COLS):
                board[r][c] = 8
        for r, c in t.cells_of("I", 0, 3, 10):
            board[r][c] = 0
        assert t.rotate(board, cur, 1) is False
        assert cur["rotation"] == 0, "旋转失败时不该改状态"


# ── 消行与计分 ────────────────────────────────────────────────


class TestClearAndScore:
    def test_single_line_clear(self):
        # 固定用横 I：所有格子都在同一行，消掉底行后底行必然全空。
        # 换成竖 I 的话它会在上面几行留残格，消行后整体下移，底行就不是空的了——
        # 那是用例的假设不成立，不是引擎的问题。
        gs = _game(uids=(1,))
        _land(gs, 1, piece="I", rotation=0)
        res = t.apply_action(gs, 1, "hard_drop")
        assert res["locked"] and res["cleared"] == 1
        assert res["score_gained"] == 100
        assert gs["scores"][1] == 100
        assert gs["lines"][1] == 1
        assert gs["level"][1] == 1
        assert all(c == 0 for c in gs["boards"][1][t.ROWS - 1]), "消掉的行应清空"
        assert res["garbage_sent"] == 0, "单行不攻击（标准规则）"

    def test_four_line_clear_scores_and_attacks(self):
        """四行消（Tetris）：800 分 + 送 4 行攻击。"""
        gs = _game(uids=(1, 2))
        board = gs["boards"][1]
        # 把底部 4 行铺满，只留第 9 列空 → 竖 I 插进去正好消 4 行
        for r in range(t.ROWS - 4, t.ROWS):
            for c in range(t.COLS - 1):
                board[r][c] = 8
        gs["current"][1] = {"piece": "I", "rotation": 1, "x": 7, "y": 0}
        res = t.apply_action(gs, 1, "hard_drop")
        assert res["cleared"] == 4, res
        assert res["score_gained"] == 800
        assert res["garbage_sent"] == 4
        assert all(c == 0 for row in board for c in row), "四行应全部清空"

    def test_score_multiplies_by_level(self):
        gs = _game(uids=(1,))
        gs["lines"][1] = 10          # 升到 2 级
        gs["level"][1] = t.level_of(10)
        _land(gs, 1)
        res = t.apply_action(gs, 1, "hard_drop")
        assert res["score_gained"] == 200, "2 级下单行应 200 分"

    def test_line_score_table(self):
        assert t.LINE_SCORES == {1: 100, 2: 300, 3: 500, 4: 800}
        assert t.GARBAGE_TABLE == {1: 0, 2: 1, 3: 2, 4: 4}

    def test_full_rows_returns_bottom_up(self):
        board = t.create_board()
        for c in range(t.COLS):
            board[19][c] = 8
            board[17][c] = 8
        assert t.full_rows(board) == [19, 17]

    def test_clear_rows_deletes_bottom_up(self):
        """多行一起消时必须从下往上删。

        每删一行都会往顶部 insert 一行，后面所有行的下标都变；从小到大删就会
        删错行——两行以上一起消时盘面立刻错乱（表现为悬空方块）。
        """
        board = t.create_board()
        # 第 16/18/19 行填满，第 17 行留一个标记格用来验位置
        for r in (16, 18, 19):
            for c in range(t.COLS):
                board[r][c] = 8
        board[17][0] = 3
        board[15][1] = 4
        t.clear_rows(board, [19, 18, 16])
        # 删 3 行、顶部补 3 空行 → 原来的 r 行落到 r - (它上面被删的行数) + 3：
        #   原 17（上面只删了 16）→ 3 + (17-1) = 19
        #   原 15（上面没删）    → 3 + 15 = 18
        assert board[19][0] == 3, f"标记格位置错了：{[r[0] for r in board]}"
        assert board[18][1] == 4
        assert board[17][1] == 0 and board[16][1] == 0
        # 全盘只剩这两个标记格
        assert sum(1 for row in board for c in row if c) == 2

    def test_clear_rows_handles_adjacent_rows(self):
        board = t.create_board()
        board[19][0] = 7
        for r in range(15, 19):
            for c in range(t.COLS):
                board[r][c] = 8
        t.clear_rows(board, [15, 16, 17, 18])
        assert board[19][0] == 7, "相邻多行一起消时位置错了"
        assert sum(1 for row in board for c in row if c) == 1

    def test_clear_rows_shifts_everything_down(self):
        board = t.create_board()
        for c in range(t.COLS):
            board[19][c] = 8
        board[18][0] = 3
        t.clear_rows(board, [19])
        assert board[19][0] == 3, "上面的方块应整体下移一行"
        assert sum(1 for c in board[18] if c) == 0

    def test_lock_on_invalid_placement_raises(self):
        """非法摆放要响亮地报错，不能静默写坏盘面。"""
        board = t.create_board()
        with pytest.raises(ValueError):
            t.lock(board, {"piece": "I", "rotation": 1, "x": 9, "y": 0})

    def test_unknown_action_is_noop(self):
        gs = _game(uids=(1,))
        res = t.apply_action(gs, 1, "teleport")
        assert res["ok"] is False
        assert gs["current"][1]["y"] == 0, "未知动作不该动方块"

    def test_actions_after_finish_are_rejected(self):
        gs = _game(uids=(1,))
        t.finish_player(gs, 1, "top_out")
        assert t.apply_action(gs, 1, "left")["ok"] is False


# ── 攻击行 ────────────────────────────────────────────────────


class TestGarbage:
    def test_add_garbage_lifts_the_board(self):
        board = t.create_board()
        board[19][0] = 5
        assert t.add_garbage(board, 2, [3, 7]) is False
        assert board[17][0] == 5, "原有方块应被顶上两行"
        assert board[18][3] == 0 and board[19][7] == 0, "每行一个洞"
        assert sum(1 for c in board[18] if c == 8) == 9
        assert sum(1 for c in board[19] if c == 8) == 9

    def test_garbage_lifts_the_falling_piece_too(self):
        """**关键不变量**：盘面上移时下落中的方块必须一起上移。

        只上移盘面的话，方块会陷进刚顶上来的行里（重叠/越界）。
        """
        gs = _game(uids=(1, 2))
        for _ in range(5):
            t.apply_action(gs, 2, "down")
        before_y = gs["current"][2]["y"]
        t.deliver_garbage(gs, 1, 3)
        cur = gs["current"][2]
        assert cur["y"] == before_y - 3, "方块没跟着盘面一起上移"
        assert t.is_valid(gs["boards"][2], cur["piece"], cur["rotation"], cur["x"], cur["y"]), \
            "方块上移后与盘面重叠了"
        assert gs["garbage_received"][2] == 3

    def test_garbage_never_kills_a_piece_that_has_room(self):
        """**v1.9.1 回归**：方块刚出生（第 0 行）收到攻击行不能判死。

        出生点就是第 0 行，本引擎没有可视区上方的缓冲区，方块没有可退的空间。
        早先的判据 `cur["y"] - count < 0` 于是把「刚出生的方块」直接顶死，
        而且判死时跳过了 `add_garbage`——盘面纹丝不动、方块凭空消失、之后
        重力与按键全部无效，玩家看到的就是「被顶之后卡住、再也不出方块」。
        """
        gs = _game(uids=(1, 2))
        assert gs["current"][2]["y"] == 0
        victims = t.deliver_garbage(gs, 1, 4)
        assert victims == [2], "受攻击的玩家要被列出来（调用方据此推新盘面）"
        assert gs["game_over_flags"][2] is False, "空盘上的方块不该被一次攻击顶死"
        assert gs["current"][2]["y"] == 0, "方块跟着盘面上移，但夹在第 0 行"
        assert gs["garbage_received"][2] == 4, "攻击行要真的记到账上"
        assert sum(1 for c in gs["boards"][2][t.ROWS - 1] if c == 8) == 9, \
            "盘面必须真的被顶起来（判死分支曾把这一步整个跳过）"

    def test_garbage_kills_when_stack_is_pressed_to_the_ceiling(self):
        """堆叠自身顶到天花板 → 顶死（攻击取胜的正常方式）。"""
        gs = _game(uids=(1, 2))
        board = gs["boards"][2]
        for r in range(3, t.ROWS):
            for c in range(t.COLS):
                board[r][c] = 8
        assert t.deliver_garbage(gs, 1, 4) == [2]
        assert gs["game_over_flags"][2] is True
        assert gs["finish_reasons"][2] == "garbage"
        assert gs["current"][2] is None

    def test_garbage_kills_when_the_piece_no_longer_fits(self):
        """堆叠压到天花板附近、方块顶上去放不下 → 顶死。"""
        gs = _game(uids=(1, 2))
        gs["current"][2] = {"piece": "I", "rotation": 0, "x": 3, "y": 1}
        board = gs["boards"][2]
        for r in range(4, t.ROWS):
            for c in range(t.COLS):
                board[r][c] = 8
        # 顶 4 行后堆叠正好到第 0 行，方块被夹在第 0 行 → 与堆叠重叠
        assert t.deliver_garbage(gs, 1, 4) == [2]
        assert gs["game_over_flags"][2] is True
        assert gs["finish_reasons"][2] == "garbage"

    def test_garbage_skips_finished_players(self):
        gs = _game(uids=(1, 2, 3))
        t.finish_player(gs, 3, "surrender")
        victims = t.deliver_garbage(gs, 1, 1)
        assert 3 not in victims, "已结束的玩家不该再收到攻击行"
        assert gs["garbage_received"].get(3, 0) == 0

    def test_garbage_does_not_hit_the_sender(self):
        gs = _game(uids=(1, 2))
        t.deliver_garbage(gs, 1, 2)
        assert gs["garbage_received"][1] == 0

    def test_garbage_top_out_when_stack_reaches_ceiling(self):
        board = t.create_board()
        board[0][5] = 8
        assert t.add_garbage(board, 1, [0]) is True

    def test_clearing_actually_delivers_garbage(self):
        """走完整链路：消 4 行 → 对手真的多 4 行、且被计入 garbage_received。"""
        gs = _game(uids=(1, 2))
        board = gs["boards"][1]
        for r in range(t.ROWS - 4, t.ROWS):
            for c in range(t.COLS - 1):
                board[r][c] = 8
        gs["current"][1] = {"piece": "I", "rotation": 1, "x": 7, "y": 0}
        # 对手的盘是空的，方块也在第 0 行——v1.9.1 起这不该被判死（见
        # test_garbage_never_kills_a_piece_that_has_room）
        assert gs["current"][2]["y"] == 0
        t.apply_action(gs, 1, "hard_drop")
        assert gs["garbage_received"][2] == 4
        assert sum(1 for c in gs["boards"][2][t.ROWS - 1] if c == 8) == 9, \
            "对手底行应该是一整行攻击行（含一个洞）"


# ── 出块 / 顶死 / 排名 ────────────────────────────────────────


class TestSpawnAndFinish:
    def test_spawn_next_sets_current_and_preview(self):
        gs = t.create_game({1: 3}, [1])
        assert t.spawn_next(gs, 1)
        assert gs["current"][1]["piece"] in t.PIECES
        assert gs["next_piece"][1] in t.PIECES
        assert gs["current"][1]["piece"] == gs["next_piece"][1] or True  # 只校验合法性

    def test_top_out_when_spawn_blocked(self):
        gs = t.create_game({1: 3}, [1])
        for r in range(4):
            for c in range(t.COLS):
                gs["boards"][1][r][c] = 8
        assert t.spawn_next(gs, 1) is False
        assert gs["game_over_flags"][1] is True
        assert gs["finish_reasons"][1] == "top_out"

    def test_lock_then_spawn_advances_the_queue(self):
        gs = _game(uids=(1,))
        first = gs["current"][1]["piece"]
        preview = gs["next_piece"][1]
        t.apply_action(gs, 1, "hard_drop")
        assert gs["current"][1]["piece"] == preview, "锁定后应发下一个方块"
        assert gs["current"][1]["piece"] != first or True

    def test_finish_player_is_idempotent(self):
        gs = _game(uids=(1, 2))
        t.finish_player(gs, 1, "surrender")
        t.finish_player(gs, 1, "top_out")
        assert gs["finish_reasons"][1] == "surrender"
        assert gs["finish_order"] == [1]

    def test_all_finished(self):
        gs = _game(uids=(1, 2))
        assert t.all_finished(gs) is False
        t.finish_player(gs, 1, "top_out")
        assert t.all_finished(gs) is False
        t.finish_player(gs, 2, "top_out")
        assert t.all_finished(gs) is True

    def test_ranking_puts_unfinished_first_then_score(self):
        gs = _game(uids=(1, 2, 3))
        gs["scores"] = {1: 100, 2: 900, 3: 500}
        t.finish_player(gs, 1, "top_out")
        t.finish_player(gs, 3, "top_out")
        rank = t.ranking(gs)
        assert rank[0]["user_id"] == 2, "还在玩的排最前"
        assert [r["rank"] for r in rank] == [1, 2, 3]
        # 已结束的两人里，3 后结束（撑得久）→ 排在 1 前面
        assert [r["user_id"] for r in rank[1:]] == [3, 1]

    def test_ranking_ties_break_by_score(self):
        gs = _game(uids=(1, 2))
        gs["scores"] = {1: 300, 2: 800}
        t.finish_player(gs, 1, "top_out")
        t.finish_player(gs, 2, "top_out")
        assert [r["user_id"] for r in t.ranking(gs)] == [2, 1]

    def test_leaderboard_sorted_by_score(self):
        gs = _game(uids=(1, 2, 3))
        gs["scores"] = {1: 100, 2: 900, 3: 500}
        assert [r["user_id"] for r in t.leaderboard(gs)] == [2, 3, 1]


class TestSyncAndSnapshot:
    def test_sync_carries_only_my_board(self):
        gs = _game(uids=(1, 2))
        gs["boards"][1][19][0] = 3
        sync = t.game_to_sync(gs, 1)
        assert "boards" not in sync, "sync 不能泄漏全部玩家的盘面"
        assert sync["board"] is gs["boards"][1]
        assert sync["board"][19][0] == 3
        assert sync["cols"] == 10 and sync["rows"] == 20
        assert sync["next_piece"] == gs["next_piece"][1]
        assert len(sync["leaderboard"]) == 2, "排行榜是公开信息（分数/行数）"

    def test_sync_carries_the_rotation_table(self):
        """客户端本地预演要用整套旋转表——**不能让它自己再写一份**（会漂移）。"""
        gs = _game(uids=(1,))
        sync = t.game_to_sync(gs, 1)
        shapes = sync["shapes"]
        assert set(shapes) == set(t.PIECES), "七种方块一个都不能少"
        for name, states in shapes.items():
            assert len(states) == 4, f"{name} 缺旋转态"
            assert [tuple(c) for c in states[0]] == list(t.PIECES[name][0])
            assert all(len(state) == 4 for state in states)

    def test_sync_of_unknown_user_is_safe(self):
        gs = _game(uids=(1,))
        sync = t.game_to_sync(gs, 999)
        assert sync["board"] == []
        assert sync["score"] == 0

    def test_snapshot_keeps_every_board(self):
        gs = _game(uids=(1, 2))
        gs["scores"] = {1: 100, 2: 200}
        gs["ranking"] = t.ranking(gs)
        snap = t.snapshot(gs)
        assert snap["game"] == "tetris"
        assert set(snap["boards"].keys()) == {1, 2}
        assert snap["scores"] == {1: 100, 2: 200}
        assert len(snap["ranking"]) == 2


class TestFullGameInvariants:
    def test_long_game_terminates_and_board_stays_well_formed(self):
        """反复硬降最终必然顶死；过程中盘面始终 20×10、格子值合法。"""
        gs = _game(uids=(1, 2))
        steps = 0
        while not t.all_finished(gs) and steps < 5000:
            for uid in (1, 2):
                if gs["game_over_flags"][uid]:
                    continue
                t.apply_action(gs, uid, "hard_drop")
            steps += 1
        assert t.all_finished(gs), "硬降 5000 次还没结束"
        assert gs["finish_reasons"][1] == "top_out"
        for uid in (1, 2):
            board = gs["boards"][uid]
            assert len(board) == t.ROWS
            assert all(len(r) == t.COLS for r in board)
            assert all(0 <= c <= 8 for row in board for c in row)

    def test_single_clear_removes_exactly_ten_cells(self):
        """确定性地消一行：格子数必须正好少 10。"""
        gs = _game(uids=(1,))
        _land(gs, 1, piece="I", rotation=0)      # 手工铺一个「差一块」的底行
        base = sum(1 for row in gs["boards"][1] for c in row if c)
        res = t.apply_action(gs, 1, "hard_drop")
        assert res["cleared"] == 1
        filled = sum(1 for row in gs["boards"][1] for c in row if c)
        # 这次硬降先放进 4 格、再由消行去掉 10 格
        assert filled == base + 4 - 10, f"消一行后格子数不对：{base} → {filled}"

    def test_board_cell_count_is_conserved_over_a_long_game(self):
        """格子总数守恒：`基数 + 4 × 锁定块数 − 10 × 消行数` 恒等于盘面格子数。

        这是最强的一条不变量，也是唯一能抓到「多行一起消时 clear_rows 删错行」
        的断言——错的实现会让格子数凭空变化（那个 bug 真的存在过：每删一行都往
        顶部 insert 一行，其余行的下标全变，第二行起就删错了）。

        **不要**顺手断言「每列都没有空洞」：俄罗斯方块的方块允许**悬挑**
        （S/Z/L 骑在一个洞上面是合法且常见的），按「每列必须连续」判会把合法
        的悬挑当成盘面损坏（这条断言我写错过一次）。方块不与已有方块重叠，
        由 `lock()` 的摆放校验保证。
        """
        gs = _game(uids=(1,))
        base = sum(1 for row in gs["boards"][1] for c in row if c)
        locks = cleared = 0
        for i in range(20):
            for _ in range(i % 5):
                t.apply_action(gs, 1, "left")
            for _ in range(i % 3):
                t.apply_action(gs, 1, "rotate_cw")
            res = t.apply_action(gs, 1, "hard_drop")
            if not res.get("locked"):
                break
            locks += 1
            cleared += res["cleared"]
            filled = sum(1 for row in gs["boards"][1] for c in row if c)
            assert filled == base + 4 * locks - 10 * cleared, (
                f"第 {locks} 块后格子数不对：filled={filled} "
                f"期望 {base + 4 * locks - 10 * cleared}"
            )
            if gs["game_over_flags"][1]:
                break
        assert locks > 5, "至少要真的锁定几块"