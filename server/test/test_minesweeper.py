"""Unit tests for minesweeper competitive game logic.

Run: cd server && python -m pytest test/test_minesweeper.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game.minesweeper import (
    create_game,
    reveal,
    flag,
    chord,
    generate_mines,
    get_revealed_list,
    get_flagged_list,
    game_to_sync,
    compute_ranking,
    DIFFICULTIES,
    LIVES,
)


def test_create_game_defaults():
    g = create_game("beginner", num_players=2)
    assert g["width"] == 9
    assert g["height"] == 9
    assert g["mines"] == 10
    assert g["mine_positions"] is None
    assert g["finished"] is False
    assert len(g["players"]) == 2
    assert g["players"][0]["alive"] is True
    assert g["players"][1]["alive"] is True
    print("  ✓ test_create_game_defaults")


def test_create_game_difficulties():
    for diff, (w, h, m) in DIFFICULTIES.items():
        g = create_game(diff, num_players=1)
        assert g["width"] == w
        assert g["height"] == h
        assert g["mines"] == m
    print("  ✓ test_create_game_difficulties")


def test_generate_mines_excludes_safe_zone():
    """First click and its 8 neighbors must be mine-free."""
    mines = generate_mines(9, 9, 10, 4, 4)
    safe = {(4, 4)}
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            safe.add((4 + dx, 4 + dy))
    for mx, my in mines:
        assert (mx, my) not in safe, f"Mine at ({mx},{my}) is in safe zone"
    assert len(mines) == 10
    print("  ✓ test_generate_mines_excludes_safe_zone")


def test_generate_mines_corner():
    """Corner click: fewer neighbors, still generates correct count."""
    mines = generate_mines(9, 9, 10, 0, 0)
    safe = {(0, 0), (0, 1), (1, 0), (1, 1)}
    for mx, my in mines:
        assert (mx, my) not in safe
    assert len(mines) == 10
    print("  ✓ test_generate_mines_corner")


def test_first_reveal_safe():
    """First reveal never hits a mine."""
    for _ in range(20):
        g = create_game("beginner", num_players=2)
        result = reveal(g, 4, 4, seat=0)
        assert result["ok"]
        assert not result["hit_mine"], "First reveal should never hit mine"
        assert g["mine_positions"] is not None
        assert (4, 4) not in g["mine_positions"]
    print("  ✓ test_first_reveal_safe")


def test_flood_fill():
    """Reveal on empty area floods correctly."""
    g = create_game("beginner", num_players=1)
    result = reveal(g, 8, 8, seat=0)
    assert result["ok"]
    assert len(result["cells"]) > 0
    for cell in result["cells"]:
        assert len(cell) == 3
        x, y, v = cell
        assert 0 <= x < 9 and 0 <= y < 9
        assert v >= 0  # No mine hit on first reveal
    print("  ✓ test_flood_fill")


def test_reveal_already_revealed():
    g = create_game("beginner", num_players=1)
    reveal(g, 0, 0, seat=0)
    result = reveal(g, 0, 0, seat=0)
    assert not result["ok"]
    assert "已翻开" in result["error"]
    print("  ✓ test_reveal_already_revealed")


def test_reveal_out_of_bounds():
    g = create_game("beginner", num_players=1)
    result = reveal(g, -1, 0, seat=0)
    assert not result["ok"]
    result = reveal(g, 0, 9, seat=0)
    assert not result["ok"]
    print("  ✓ test_reveal_out_of_bounds")


def test_flag_toggle():
    g = create_game("beginner", num_players=2)
    result = flag(g, 3, 3, seat=0)
    assert result["ok"]
    assert result["flagged"] is True
    assert result["seat"] == 0
    result = flag(g, 3, 3, seat=0)
    assert result["ok"]
    assert result["flagged"] is False
    print("  ✓ test_flag_toggle")


def test_flag_on_revealed():
    g = create_game("beginner", num_players=1)
    reveal(g, 0, 0, seat=0)
    result = flag(g, 0, 0, seat=0)
    assert not result["ok"]
    assert "已翻开" in result["error"]
    print("  ✓ test_flag_on_revealed")


def test_reveal_on_flagged():
    g = create_game("beginner", num_players=1)
    flag(g, 3, 3, seat=0)
    result = reveal(g, 3, 3, seat=0)
    assert not result["ok"]
    assert "插旗" in result["error"]
    print("  ✓ test_reveal_on_flagged")


def test_hit_mine_eliminated():
    """Hitting a mine eliminates the player."""
    g = create_game("beginner", num_players=2)
    reveal(g, 4, 4, seat=0)  # Initialize mines
    mine_pos = next(iter(g["mine_positions"]))
    g["flagged"].discard(mine_pos)
    g["flag_owners"].pop(mine_pos, None)
    result = reveal(g, mine_pos[0], mine_pos[1], seat=0)
    assert result["ok"]
    assert result["hit_mine"]
    assert result["eliminated"] is True
    assert g["players"][0]["alive"] is False
    assert g["players"][0]["eliminated_at"] is not None
    print("  ✓ test_hit_mine_eliminated")


def _hit_mine(g, seat):
    """让 seat 踩一颗还没翻开的雷，返回 reveal 的结果。"""
    for pos in g["mine_positions"]:
        if pos in g["revealed"] or pos in g["flagged"]:
            continue
        g["flagged"].discard(pos)
        g["flag_owners"].pop(pos, None)
        return reveal(g, pos[0], pos[1], seat)
    raise AssertionError("没有可踩的雷了")


def _safe_single_cell(g):
    """找一个「翻开来必定安全、而且只翻开这一格」的格子（紧邻雷，不会连锁展开）。

    **不要随手挑一个空格翻开**：它可能正好是雷（扣掉一条命），也可能连锁翻开整片
    直到整盘清空（清空会触发 `_reveal_all_mines`）——两种都会让后面的 `_hit_mine`
    一颗雷都找不到（`test_intermediate_survives_first_mine` 曾因此 flaky）。
    紧邻雷的格子雷数 ≥1，翻开必不连锁、也不可能是雷。
    """
    for mx, my in g["mine_positions"]:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                x, y = mx + dx, my + dy
                if not (0 <= x < g["width"] and 0 <= y < g["height"]):
                    continue
                if (x, y) in g["mine_positions"] or (x, y) in g["revealed"]:
                    continue
                return (x, y)
    return None


class TestLives:
    """生命值：初级 1 条、中级 2 条、高级 3 条；踩雷扣一条，扣完才淘汰。"""

    def test_lives_per_difficulty(self):
        assert LIVES == {"beginner": 1, "intermediate": 2, "advanced": 3}
        for diff, lives in LIVES.items():
            g = create_game(diff, num_players=2)
            assert g["lives"] == lives
            for seat in range(2):
                assert g["players"][seat]["lives_left"] == lives
                assert g["players"][seat]["alive"] is True

    def test_beginner_one_life_eliminates_on_first_mine(self):
        g = create_game("beginner", num_players=1)
        reveal(g, 4, 4, seat=0)
        r = _hit_mine(g, 0)
        assert r["hit_mine"] and r["lost_life"]
        assert r["lives_left"] == 0
        assert r["eliminated"] is True
        assert g["players"][0]["alive"] is False

    def test_intermediate_survives_first_mine(self):
        """中级 2 条命：第一次踩雷只扣命，本人还能继续操作。"""
        g = create_game("intermediate", num_players=1)
        reveal(g, 8, 8, seat=0)
        r1 = _hit_mine(g, 0)
        assert r1["lost_life"] is True
        assert r1["eliminated"] is False
        assert r1["lives_left"] == 1
        assert g["players"][0]["alive"] is True
        assert g["players"][0]["eliminated_at"] is None
        # 还能继续翻格（挑一个必定安全、且只翻开一格的格子，别把命或整盘翻掉）
        safe = _safe_single_cell(g)
        assert safe is not None, "雷旁边应该总能找到安全格"
        assert reveal(g, safe[0], safe[1], seat=0)["ok"]
        assert g["players"][0]["lives_left"] == 1, "翻安全格不该扣命"
        # 第二次踩雷才淘汰
        r2 = _hit_mine(g, 0)
        assert r2["lost_life"] is True
        assert r2["eliminated"] is True
        assert r2["lives_left"] == 0
        assert g["players"][0]["alive"] is False
        assert g["players"][0]["eliminated_at"] is not None

    def test_advanced_three_lives(self):
        g = create_game("advanced", num_players=1)
        reveal(g, 15, 8, seat=0)
        for expected_left in (2, 1, 0):
            r = _hit_mine(g, 0)
            assert r["lost_life"] is True
            assert r["lives_left"] == expected_left
            assert r["eliminated"] is (expected_left == 0)
        assert g["players"][0]["alive"] is False

    def test_safe_reveal_does_not_touch_lives(self):
        g = create_game("intermediate", num_players=1)
        reveal(g, 8, 8, seat=0)
        before = g["players"][0]["lives_left"]
        # 找一个确定安全的格子（首翻格周围的连通区）
        safe = next((x, y) for y in range(g["height"]) for x in range(g["width"])
                    if g["revealed"].get((x, y), -1) >= 0)
        r = reveal(g, safe[0], safe[1], seat=0)
        if r["ok"]:
            assert r["lost_life"] is False
            assert r["lives_left"] == before
        assert g["players"][0]["lives_left"] == before

    def test_chord_losing_a_life_does_not_eliminate_at_intermediate(self):
        """chord 踩雷同样走 _reveal_cell，中级下只扣命、不淘汰。

        复用 `_chord_fixture`（错旗让 chord 去开一颗没插旗的雷），把命数改成 2。
        """
        g = _chord_fixture()
        g["lives"] = 2
        g["players"][0]["lives_left"] = 2
        assert flag(g, 2, 2, seat=0)["ok"]     # 错旗：把安全格插成雷
        r = chord(g, 1, 1, seat=0)
        assert r["ok"]
        assert r["hit_mine"] is True
        assert r["lost_life"] is True
        assert r["lives_left"] == 1
        assert r["eliminated"] is False
        assert g["players"][0]["alive"] is True
        assert g["players"][0]["eliminated_at"] is None

    def test_sync_carries_lives(self):
        g = create_game("advanced", num_players=2)
        sync = game_to_sync(g)
        assert sync["lives"] == 3
        assert sync["players"]["0"]["lives_left"] == 3
        assert sync["players"]["1"]["lives_left"] == 3
        reveal(g, 15, 8, seat=0)
        _hit_mine(g, 0)
        sync = game_to_sync(g)
        assert sync["players"]["0"]["lives_left"] == 2
        assert sync["players"]["1"]["lives_left"] == 3

    def test_game_over_only_after_all_lives_gone(self):
        """两人中级：各踩两次才终局；只踩一次不终局。"""
        g = create_game("intermediate", num_players=2)
        reveal(g, 8, 8, seat=0)
        r = _hit_mine(g, 0)
        assert r["game_over"] is None
        r = _hit_mine(g, 0)
        assert r["eliminated"] is True
        assert r["game_over"] is None, "另一个玩家还活着，不该终局"
        r = _hit_mine(g, 1)
        assert r["eliminated"] is False
        assert r["game_over"] is None
        r = _hit_mine(g, 1)
        assert r["eliminated"] is True
        assert r["game_over"] is not None
        assert r["game_over"]["reason"] == "eliminated"


def test_eliminated_player_cannot_act():
    """Eliminated player's actions are rejected."""
    g = create_game("beginner", num_players=2)
    reveal(g, 4, 4, seat=0)
    mine_pos = next(iter(g["mine_positions"]))
    g["flagged"].discard(mine_pos)
    g["flag_owners"].pop(mine_pos, None)
    reveal(g, mine_pos[0], mine_pos[1], seat=0)  # Eliminate seat 0
    # Seat 0 tries to reveal
    result = reveal(g, 0, 0, seat=0)
    assert not result["ok"]
    assert "淘汰" in result["error"]
    # Seat 0 tries to flag
    result = flag(g, 1, 1, seat=0)
    assert not result["ok"]
    assert "淘汰" in result["error"]
    print("  ✓ test_eliminated_player_cannot_act")


def test_all_eliminated_game_over():
    """All players eliminated = game over."""
    g = create_game("beginner", num_players=2)
    reveal(g, 4, 4, seat=0)  # Initialize
    mines = list(g["mine_positions"])
    # Remove flags from mine positions
    for mx, my in mines[:2]:
        g["flagged"].discard((mx, my))
        g["flag_owners"].pop((mx, my), None)
    # Eliminate both players
    r1 = reveal(g, mines[0][0], mines[0][1], seat=0)
    assert r1["eliminated"]
    assert r1["game_over"] is None  # Not all eliminated yet
    r2 = reveal(g, mines[1][0], mines[1][1], seat=1)
    assert r2["eliminated"]
    assert r2["game_over"] is not None
    assert r2["game_over"]["reason"] == "eliminated"
    assert g["finished"] is True
    print("  ✓ test_all_eliminated_game_over")


def test_clear_win():
    """Reveal all safe cells to win."""
    g = create_game("beginner", num_players=1)
    reveal(g, 0, 0, seat=0)  # Initialize
    mines = g["mine_positions"]
    w, h = g["width"], g["height"]

    for y in range(h):
        for x in range(w):
            if (x, y) not in mines and (x, y) not in g["revealed"]:
                result = reveal(g, x, y, seat=0)
                if result["ok"] and result["game_over"]:
                    assert result["game_over"]["reason"] == "cleared"
                    print("  ✓ test_clear_win")
                    return

    assert g["finished"] is True
    assert g["reason"] == "cleared"
    print("  ✓ test_clear_win")


def test_revealed_count_attribution():
    """Flood-fill cells are credited to the operator."""
    g = create_game("beginner", num_players=2)
    r = reveal(g, 4, 4, seat=0)
    count0 = g["players"][0]["revealed_count"]
    assert count0 == len([c for c in r["cells"] if c[2] != -1])
    assert g["players"][1]["revealed_count"] == 0
    print("  ✓ test_revealed_count_attribution")


def test_correct_and_wrong_flags():
    """Flag stats computed at end based on remaining flags and owners."""
    g = create_game("beginner", num_players=2)
    reveal(g, 4, 4, seat=0)  # Initialize
    mines = list(g["mine_positions"])
    w, h = g["width"], g["height"]

    # Seat 0 flags a mine correctly
    mine_cell = mines[0]
    flag(g, mine_cell[0], mine_cell[1], seat=0)

    # Seat 1 flags a non-mine cell (wrong flag)
    for y in range(h):
        for x in range(w):
            if (x, y) not in mines and (x, y) not in g["revealed"]:
                flag(g, x, y, seat=1)
                break
        else:
            continue
        break

    # Eliminate both players to end game
    for mx, my in mines[1:3]:
        g["flagged"].discard((mx, my))
        g["flag_owners"].pop((mx, my), None)
    reveal(g, mines[1][0], mines[1][1], seat=0)
    reveal(g, mines[2][0], mines[2][1], seat=1)

    # Compute ranking
    players_info = [
        {"seat": 0, "username": "alice", "nickname": "A"},
        {"seat": 1, "username": "bob", "nickname": "B"},
    ]
    ranking = compute_ranking(g, players_info)
    # Seat 0 has 1 correct flag
    r0 = next(e for e in ranking if e["seat"] == 0)
    assert r0["correct_flags"] == 1
    # Seat 1 has 1 wrong flag
    r1 = next(e for e in ranking if e["seat"] == 1)
    assert r1["wrong_flags"] == 1
    print("  ✓ test_correct_and_wrong_flags")


def test_flag_cancelled_not_counted():
    """Cancelled flags don't count in final stats."""
    g = create_game("beginner", num_players=1)
    reveal(g, 4, 4, seat=0)
    mines = list(g["mine_positions"])

    # Flag a mine then cancel it
    flag(g, mines[0][0], mines[0][1], seat=0)
    flag(g, mines[0][0], mines[0][1], seat=0)  # Cancel

    players_info = [{"seat": 0, "username": "alice", "nickname": "A"}]
    from app.game.minesweeper import _compute_flag_stats
    stats = _compute_flag_stats(g)
    assert stats[0]["correct_flags"] == 0
    print("  ✓ test_flag_cancelled_not_counted")


def test_ranking_order():
    """Survivors first, then by correct_flags desc, revealed_count desc."""
    g = create_game("beginner", num_players=3)
    # (4,4) 首翻：引擎保证该格及八邻无雷，seat 0 存活且 revealed_count>0
    reveal(g, 4, 4, seat=0)
    mines = list(g["mine_positions"])

    # Seat 0: alive（首翻安全）；Seat 2: alive（未操作，revealed_count=0）
    assert g["players"][0]["alive"] is True

    # Seat 1: 挑一个尚未翻开的雷淘汰（首翻只展开安全格，所有雷均未翻开）
    target = next(m for m in mines if m not in g["revealed"])
    reveal(g, target[0], target[1], seat=1)
    assert g["players"][1]["alive"] is False

    # Compute ranking
    players_info = [
        {"seat": 0, "username": "alice", "nickname": "A"},
        {"seat": 1, "username": "bob", "nickname": "B"},
        {"seat": 2, "username": "charlie", "nickname": "C"},
    ]
    ranking = compute_ranking(g, players_info)
    # Survivors should come first
    for entry in ranking:
        if entry["alive"]:
            assert entry["rank"] <= 2
    # Eliminated player should be last
    eliminated = [e for e in ranking if not e["alive"]]
    assert len(eliminated) == 1
    assert eliminated[0]["rank"] == 3
    print("  ✓ test_ranking_order")


def test_ranking_tie():
    """Tied players share the same rank."""
    g = create_game("beginner", num_players=2)
    # Both alive, no reveals, no flags
    players_info = [
        {"seat": 0, "username": "alice", "nickname": "A"},
        {"seat": 1, "username": "bob", "nickname": "B"},
    ]
    ranking = compute_ranking(g, players_info)
    assert ranking[0]["rank"] == 1
    assert ranking[1]["rank"] == 1  # Tied
    print("  ✓ test_ranking_tie")


def test_ranking_elapsed_breaks_tie():
    """同标雷数、同翻格数时按用时排序（「竞速」必须真的看时间）。"""
    players_info = [
        {"seat": 0, "username": "alice", "nickname": "A"},
        {"seat": 1, "username": "bob", "nickname": "B"},
    ]
    g = create_game("beginner", num_players=2)
    g["players"][0].update(alive=True, revealed_count=10, elapsed_ms=5000)
    g["players"][1].update(alive=True, revealed_count=10, elapsed_ms=3000)
    ranking = compute_ranking(g, players_info)
    assert ranking[0]["seat"] == 1, ranking
    assert ranking[0]["rank"] == 1
    assert ranking[1]["seat"] == 0
    assert ranking[1]["rank"] == 2

    # 用时也相同才是真平局
    g["players"][0]["elapsed_ms"] = 3000
    ranking = compute_ranking(g, players_info)
    assert ranking[0]["rank"] == 1
    assert ranking[1]["rank"] == 1
    print("  ✓ test_ranking_elapsed_breaks_tie")


def test_no_actions_after_game_over():
    g = create_game("beginner", num_players=2)
    reveal(g, 4, 4, seat=0)
    mines = list(g["mine_positions"])
    for mx, my in mines[:2]:
        g["flagged"].discard((mx, my))
        g["flag_owners"].pop((mx, my), None)
    reveal(g, mines[0][0], mines[0][1], seat=0)
    reveal(g, mines[1][0], mines[1][1], seat=1)
    # Game should be over
    result = reveal(g, 0, 0, seat=0)
    assert not result["ok"]
    assert "结束" in result["error"]
    result = flag(g, 0, 0, seat=0)
    assert not result["ok"]
    assert "结束" in result["error"]
    print("  ✓ test_no_actions_after_game_over")


def test_sync_message_structure():
    g = create_game("intermediate", num_players=2)
    players_info = [
        {"seat": 0, "username": "alice", "nickname": "A"},
        {"seat": 1, "username": "bob", "nickname": "B"},
    ]
    sync = game_to_sync(g, players_info)
    assert sync["game"] == "minesweeper"
    assert sync["width"] == 16
    assert sync["height"] == 16
    assert sync["mines"] == 40
    assert sync["revealed"] == []
    assert sync["flagged"] == []
    assert sync["started"] is False
    assert sync["finished"] is False
    assert "players" in sync
    assert "0" in sync["players"]
    assert sync["players"]["0"]["alive"] is True
    print("  ✓ test_sync_message_structure")


def test_flagged_list_for_sync():
    g = create_game("beginner", num_players=1)
    flag(g, 1, 2, seat=0)
    flag(g, 3, 4, seat=0)
    fl = get_flagged_list(g)
    assert len(fl) == 2
    assert [1, 2] in fl
    assert [3, 4] in fl
    print("  ✓ test_flagged_list_for_sync")


def test_invalid_seat():
    """Actions from invalid seat are rejected."""
    g = create_game("beginner", num_players=2)
    result = reveal(g, 0, 0, seat=5)
    assert not result["ok"]
    assert "无效" in result["error"]
    result = flag(g, 0, 0, seat=5)
    assert not result["ok"]
    assert "无效" in result["error"]
    print("  ✓ test_invalid_seat")


def _chord_fixture() -> dict:
    """Deterministic 5x5 board: mines at the four corners (0,0)/(0,3)/(3,0)/(3,3).

    (1,1) is revealed with value 1, and every other neighbor of (1,1) also has
    value ≥ 1 — so a chord opens exactly those neighbors without any 0-flood
    spreading over the board. No reliance on the random layout.
    """
    g = create_game("beginner", num_players=2)
    g["width"] = g["height"] = 5
    g["mines"] = 4
    g["mine_positions"] = {(0, 0), (0, 3), (3, 0), (3, 3)}
    g["started"] = True
    r = reveal(g, 1, 1, seat=0)
    assert r["ok"] and g["revealed"][(1, 1)] == 1
    assert r["cells"] == [[1, 1, 1]]  # no flood fill from a 1-cell
    return g


def test_chord_requires_revealed_number():
    """Chording an unrevealed or non-number cell is a silent no-op."""
    g = _chord_fixture()
    res = chord(g, 4, 4, seat=0)  # never revealed
    assert not res["ok"]
    # A revealed 0-cell has no flags to match → also a no-op
    g["revealed"][(4, 4)] = 0
    res = chord(g, 4, 4, seat=0)
    assert not res["ok"]
    print("  ✓ test_chord_requires_revealed_number")


def test_chord_flag_count_mismatch_noop():
    """Flag count below the number → chord does not fire, nothing opens."""
    g = _chord_fixture()
    before = set(g["revealed"])
    res = chord(g, 1, 1, seat=0)  # 0 flags vs number 1
    assert not res["ok"]
    assert set(g["revealed"]) == before
    assert g["players"][0]["revealed_count"] == 1
    print("  ✓ test_chord_flag_count_mismatch_noop")


def test_chord_opens_remaining_neighbors():
    """Correct flag count → every other unflagged neighbor opens."""
    g = _chord_fixture()
    assert flag(g, 0, 0, seat=0)["ok"]  # correct flag on the mine
    res = chord(g, 1, 1, seat=0)
    assert res["ok"]
    assert res["hit_mine"] is False
    assert res["eliminated"] is False
    opened = {(x, y) for x, y, _ in res["cells"]}
    assert (0, 1) in opened and (1, 0) in opened and (2, 2) in opened
    assert len(opened) == 7  # 8 neighbors minus the flagged mine
    # The flagged mine is skipped, so it never enters the revealed set
    assert (0, 0) not in g["revealed"]
    assert (0, 0) in g["flagged"]
    # Every opened cell is credited to the operator
    assert g["players"][0]["revealed_count"] == len(g["revealed"]) == 8
    assert not g["finished"]
    print("  ✓ test_chord_opens_remaining_neighbors")


def test_chord_hits_mine_eliminates():
    """A wrong flag makes the chord open a mine → operator is eliminated."""
    g = _chord_fixture()
    assert flag(g, 2, 2, seat=0)["ok"]  # wrong flag on a safe cell
    res = chord(g, 1, 1, seat=0)
    assert res["ok"]
    assert res["hit_mine"] is True
    assert res["eliminated"] is True
    assert g["players"][0]["alive"] is False
    assert g["players"][0]["eliminated_at"] is not None
    assert g["revealed"][(0, 0)] == -1  # the unflagged mine was opened
    print("  ✓ test_chord_hits_mine_eliminates")


def test_chord_eliminated_player_rejected():
    """An already-eliminated player cannot chord."""
    g = _chord_fixture()
    g["players"][0]["alive"] = False
    res = chord(g, 1, 1, seat=0)
    assert not res["ok"]
    assert "淘汰" in res["error"]
    print("  ✓ test_chord_eliminated_player_rejected")


def test_chord_no_actions_after_game_over():
    """Chording after the game ended is rejected."""
    g = _chord_fixture()
    g["finished"] = True
    res = chord(g, 1, 1, seat=0)
    assert not res["ok"]
    assert "结束" in res["error"]
    print("  ✓ test_chord_no_actions_after_game_over")


def test_flag_cycles_through_question_mark():
    """空 → 旗 → 问号 → 空；问号不参与 chord，也不阻挡翻开。"""
    g = create_game("beginner", num_players=1)
    first = flag(g, 3, 3, seat=0)
    assert first["state"] == "flag" and (3, 3) in g["flagged"]
    second = flag(g, 3, 3, seat=0)
    assert second["state"] == "question"
    assert second["flagged"] is False and second["questioned"] is True
    assert (3, 3) not in g["flagged"], "问号不算旗子（不参与通关/chord）"
    assert (3, 3) in g["questioned"]
    third = flag(g, 3, 3, seat=0)
    assert third["state"] == "none"
    assert (3, 3) not in g["questioned"]


def test_question_mark_does_not_block_reveal():
    g = create_game("beginner", num_players=1)
    flag(g, 4, 4, seat=0)
    flag(g, 4, 4, seat=0)          # → 问号
    res = reveal(g, 4, 4, seat=0)
    assert res["ok"], "问号只是笔记，应该可以直接翻开"
    assert (4, 4) not in g["questioned"], "翻开后问号自动清掉"
    sync = game_to_sync(g)
    assert sync["questioned"] == []


def test_daily_seed_makes_the_mine_layout_reproducible():
    """每日固定盘：同种子 + 同一第一下 → 同一份雷盘。"""
    def mines_for(seed, first=(4, 4)):
        g = create_game("beginner", 1, seed=seed, daily=True)
        reveal(g, first[0], first[1], seat=0)
        return g["mine_positions"]

    a = mines_for(20261021)
    b = mines_for(20261021)
    c = mines_for(20261022)
    assert a == b, "同一天同难度必须拿到同一份雷盘"
    assert a != c
    # 不同第一下点击 → 安全区不同，雷盘也随之不同（同种子，不是逐格固定）
    assert mines_for(20261021, first=(0, 0)) != a


def test_sync_carries_difficulty_and_daily_flag():
    g = create_game("advanced", 1, seed=123, daily=True)
    sync = game_to_sync(g)
    assert sync["difficulty"] == "advanced"
    assert sync["daily"] is True
    assert "questioned" in sync


if __name__ == "__main__":
    print("Running minesweeper unit tests...")
    test_create_game_defaults()
    test_create_game_difficulties()
    test_generate_mines_excludes_safe_zone()
    test_generate_mines_corner()
    test_first_reveal_safe()
    test_flood_fill()
    test_reveal_already_revealed()
    test_reveal_out_of_bounds()
    test_flag_toggle()
    test_flag_on_revealed()
    test_reveal_on_flagged()
    test_hit_mine_eliminated()
    test_eliminated_player_cannot_act()
    test_all_eliminated_game_over()
    test_clear_win()
    test_revealed_count_attribution()
    test_correct_and_wrong_flags()
    test_flag_cancelled_not_counted()
    test_ranking_order()
    test_ranking_tie()
    test_ranking_elapsed_breaks_tie()
    test_no_actions_after_game_over()
    test_sync_message_structure()
    test_flagged_list_for_sync()
    test_invalid_seat()
    test_chord_requires_revealed_number()
    test_chord_flag_count_mismatch_noop()
    test_chord_opens_remaining_neighbors()
    test_chord_hits_mine_eliminates()
    test_chord_eliminated_player_rejected()
    test_chord_no_actions_after_game_over()
    print("\nAll minesweeper tests passed! ✓")
