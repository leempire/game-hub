"""Unit tests for Battleship engine. Run:
    cd server && python -m pytest test/test_battleship.py -q
"""

import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game.battleship import (
    SIZE_PRESETS,
    SIZES,
    default_weapon_assignment,
    fleet_of,
    validate_weapon_assignment,
    create_board,
    random_placement,
    validate_placement,
    apply_placement,
    fire,
    get_ship_cells,
    count_sunk_ships,
    all_ships_sunk,
    game_to_sync,
    snapshot,
    BOARD_SIZE,
    EMPTY,
    MISS,
    HIT,
    SHIP_DEFS,
)


def test_create_board():
    """Board is 10x10 all zeros."""
    board = create_board()
    assert len(board) == BOARD_SIZE
    assert all(len(row) == BOARD_SIZE for row in board)
    assert all(cell == EMPTY for row in board for cell in row)
    print("  OK test_create_board")


def test_random_placement_valid():
    """Random placement produces valid, non-overlapping ships."""
    for seed in range(20):
        import random as _r
        rng = _r.Random(seed)
        ships = random_placement(rng)
        assert len(ships) == 5
        assert validate_placement(ships)
        # Verify ships are on the board
        board = create_board()
        apply_placement(board, ships)
        ship_cells_total = sum(d["size"] for d in SHIP_DEFS)
        occupied = sum(1 for row in board for cell in row if cell != EMPTY)
        assert occupied == ship_cells_total
    print("  OK test_random_placement_valid")


def test_validate_placement_overlap():
    """Overlapping ships are rejected."""
    ships = [
        {"id": 1, "x": 0, "y": 0, "horizontal": True},
        {"id": 2, "x": 0, "y": 0, "horizontal": False},  # overlaps at (0,0)
        {"id": 3, "x": 5, "y": 5, "horizontal": True},
        {"id": 4, "x": 5, "y": 7, "horizontal": True},
        {"id": 5, "x": 8, "y": 8, "horizontal": True},
    ]
    assert not validate_placement(ships)
    print("  OK test_validate_placement_overlap")


def test_validate_placement_out_of_bounds():
    """Ships going out of bounds are rejected."""
    ships = [
        {"id": 1, "x": 7, "y": 0, "horizontal": True},  # 7+5=12 > 10
        {"id": 2, "x": 0, "y": 1, "horizontal": True},
        {"id": 3, "x": 0, "y": 3, "horizontal": True},
        {"id": 4, "x": 0, "y": 5, "horizontal": True},
        {"id": 5, "x": 0, "y": 7, "horizontal": True},
    ]
    assert not validate_placement(ships)
    print("  OK test_validate_placement_out_of_bounds")


def test_validate_placement_wrong_count():
    """Wrong number of ships is rejected."""
    assert not validate_placement([])
    assert not validate_placement([
        {"id": 1, "x": 0, "y": 0, "horizontal": True},
    ])
    print("  OK test_validate_placement_wrong_count")


def test_apply_placement():
    """Ships are correctly placed on the board."""
    ships = [
        {"id": 1, "x": 0, "y": 0, "horizontal": True},   # (0,0)-(4,0)
        {"id": 2, "x": 0, "y": 2, "horizontal": False},  # (0,2)-(0,5)
        {"id": 3, "x": 5, "y": 5, "horizontal": True},   # (5,5)-(7,5)
        {"id": 4, "x": 7, "y": 7, "horizontal": False},  # (7,7)-(7,9)
        {"id": 5, "x": 2, "y": 8, "horizontal": True},   # (2,8)-(3,8)
    ]
    board = create_board()
    apply_placement(board, ships)
    # Carrier (id=1) at row 0, cols 0-4
    for x in range(5):
        assert board[0][x] == 1
    # Battleship (id=2) at col 0, rows 2-5
    for y in range(2, 6):
        assert board[y][0] == 2
    print("  OK test_apply_placement")


def test_fire_miss():
    """Firing at empty water returns miss."""
    board = create_board()
    ships = [{"id": 1, "x": 0, "y": 0, "horizontal": True}]
    # Only place one ship for simplicity — pad the rest
    board[0][0] = 1
    board[0][1] = 1
    board[0][2] = 1
    board[0][3] = 1
    board[0][4] = 1
    attack = create_board()
    result = fire(board, attack, 5, 5)
    assert result["result"] == "miss"
    assert result["ship_id"] is None
    assert attack[5][5] == MISS
    print("  OK test_fire_miss")


def test_fire_hit():
    """Firing at a ship cell returns hit."""
    board = create_board()
    board[3][3] = 1  # carrier cell
    board[3][4] = 1
    board[3][5] = 1
    board[3][6] = 1
    board[3][7] = 1
    attack = create_board()
    result = fire(board, attack, 3, 3)
    assert result["result"] == "hit"
    assert result["ship_id"] == 1
    assert result["sunk_cells"] is None
    assert attack[3][3] == HIT
    print("  OK test_fire_hit")


def test_fire_sunk():
    """Firing the last cell of a ship returns sunk with all cells."""
    board = create_board()
    # Destroyer (id=5, size=2) at (0,0)-(1,0)
    board[0][0] = 5
    board[0][1] = 5
    attack = create_board()
    # First hit
    r1 = fire(board, attack, 0, 0)
    assert r1["result"] == "hit"
    # Second hit → sunk
    r2 = fire(board, attack, 1, 0)
    assert r2["result"] == "sunk"
    assert r2["ship_id"] == 5
    assert r2["sunk_cells"] is not None
    assert len(r2["sunk_cells"]) == 2
    print("  OK test_fire_sunk")


def test_fire_duplicate_raises():
    """Firing at the same cell twice raises ValueError."""
    board = create_board()
    attack = create_board()
    fire(board, attack, 3, 3)
    try:
        fire(board, attack, 3, 3)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  OK test_fire_duplicate_raises")


def test_fire_out_of_bounds_raises():
    """Firing out of bounds raises ValueError."""
    board = create_board()
    attack = create_board()
    try:
        fire(board, attack, -1, 0)
        assert False
    except ValueError:
        pass
    try:
        fire(board, attack, 10, 5)
        assert False
    except ValueError:
        pass
    print("  OK test_fire_out_of_bounds_raises")


def test_get_ship_cells():
    """get_ship_cells returns all cells for a given ship id."""
    board = create_board()
    board[2][3] = 3
    board[2][4] = 3
    board[2][5] = 3
    cells = get_ship_cells(board, 3)
    assert len(cells) == 3
    assert [3, 2] in cells
    assert [4, 2] in cells
    assert [5, 2] in cells
    print("  OK test_get_ship_cells")


def test_count_sunk_ships():
    """Count sunk ships correctly."""
    board = create_board()
    board[0][0] = 5
    board[0][1] = 5
    board[5][5] = 1
    board[5][6] = 1
    board[5][7] = 1
    board[5][8] = 1
    board[5][9] = 1
    attack = create_board()
    # Sink destroyer (id=5)
    attack[0][0] = HIT
    attack[0][1] = HIT
    assert count_sunk_ships(board, attack) == 1
    # Sink carrier (id=1)
    for x in range(5, 10):
        attack[5][x] = HIT
    assert count_sunk_ships(board, attack) == 2
    print("  OK test_count_sunk_ships")


def test_all_ships_sunk():
    """all_ships_sunk returns True only when every ship is hit."""
    import random as _r
    rng = _r.Random(42)
    ships = random_placement(rng)
    board = create_board()
    apply_placement(board, ships)
    attack = create_board()
    assert not all_ships_sunk(board, attack)
    # Hit every occupied cell
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] != EMPTY:
                attack[y][x] = HIT
    assert all_ships_sunk(board, attack)
    print("  OK test_all_ships_sunk")


def test_game_to_sync_structure():
    """Verify sync message structure."""
    gs = {
        "phase": "battle",
        "boards": {1: create_board(), 2: create_board()},
        "attack_boards": {1: create_board(), 2: create_board()},
        "placements": {1: [], 2: []},
        "shots": {1: [], 2: []},
        "player_colors": {1: "blue", 2: "red"},
        "next": 1,
        "winner": None,
        "reason": None,
        "placement_ready": {1: True, 2: True},
    }
    sync = game_to_sync(gs, 1)
    assert sync["board_size"] == BOARD_SIZE
    assert sync["phase"] == "battle"
    assert sync["your_color"] == "blue"
    assert sync["winner"] is None
    sync2 = game_to_sync(gs, 2)
    assert sync2["your_color"] == "red"
    print("  OK test_game_to_sync_structure")


def test_snapshot_structure():
    """Snapshot contains all game data for history."""
    gs = {
        "phase": "finished",
        "boards": {1: create_board(), 2: create_board()},
        "attack_boards": {1: create_board(), 2: create_board()},
        "placements": {
            1: [{"id": 1, "x": 0, "y": 0, "horizontal": True}],
            2: [],
        },
        "shots": {1: [{"x": 5, "y": 5, "result": "miss"}], 2: []},
        "player_colors": {1: "blue", 2: "red"},
        "next": 2,
        "winner": "blue",
        "reason": "all_sunk",
    }
    snap = snapshot(gs)
    assert snap["game"] == "battleship"
    assert snap["winner"] == "blue"
    assert "1" in snap["placements"]
    assert "1" in snap["shots"]
    assert snap["shots"]["1"][0]["result"] == "miss"
    print("  OK test_snapshot_structure")


if __name__ == "__main__":
    test_create_board()
    test_random_placement_valid()
    test_validate_placement_overlap()
    test_validate_placement_out_of_bounds()
    test_validate_placement_wrong_count()
    test_apply_placement()
    test_fire_miss()
    test_fire_hit()
    test_fire_sunk()
    test_fire_duplicate_raises()
    test_fire_out_of_bounds_raises()
    test_get_ship_cells()
    test_count_sunk_ships()
    test_all_ships_sunk()
    test_game_to_sync_structure()
    test_snapshot_structure()
    print("\nAll battleship engine tests passed!")


# ── v1.10 特殊武器（纯引擎）─────────────────────────────────────


def test_validate_weapon_assignment():
    from app.game.battleship import validate_weapon_assignment
    assert validate_weapon_assignment({"radar": 5, "salvo": 4, "strike": 3})
    # 三件武器不能挂同一艘船（否则「全堆在最难找的船上」成为支配策略）
    assert not validate_weapon_assignment({"radar": 5, "salvo": 5, "strike": 3})
    assert not validate_weapon_assignment({"radar": 5, "salvo": 4})
    assert not validate_weapon_assignment({"radar": 5, "salvo": 4, "strike": 9})
    assert not validate_weapon_assignment(None)
    print("  OK test_validate_weapon_assignment")


def test_normalize_weapon_assignment_falls_back():
    from app.game.battleship import (
        DEFAULT_WEAPON_ASSIGNMENT, normalize_weapon_assignment,
    )
    assert normalize_weapon_assignment(None) == DEFAULT_WEAPON_ASSIGNMENT
    assert normalize_weapon_assignment({"radar": 1}) == DEFAULT_WEAPON_ASSIGNMENT
    chosen = {"radar": 1, "salvo": 2, "strike": 3}
    assert normalize_weapon_assignment(chosen) == chosen
    print("  OK test_normalize_weapon_assignment_falls_back")


def test_ship_afloat_tracks_sunk():
    from app.game.battleship import ship_afloat
    board = create_board()
    apply_placement(board, [{"id": 5, "x": 0, "y": 0, "horizontal": True}])
    attack = create_board()
    assert ship_afloat(board, attack, 5)
    fire(board, attack, 0, 0)
    assert ship_afloat(board, attack, 5)
    fire(board, attack, 1, 0)
    assert not ship_afloat(board, attack, 5)
    print("  OK test_ship_afloat_tracks_sunk")


def test_area_cells_clamps_at_edges():
    from app.game.battleship import area_cells
    assert len(area_cells(5, 5)) == 9          # 盘中央是完整 3×3
    assert len(area_cells(0, 0)) == 4          # 角上被裁掉
    assert len(area_cells(0, 5)) == 6          # 边上裁成 2×3
    assert all(0 <= cx < BOARD_SIZE and 0 <= cy < BOARD_SIZE
               for cx, cy in area_cells(9, 9))
    print("  OK test_area_cells_clamps_at_edges")


def test_radar_scan_reports_presence_only():
    from app.game.battleship import radar_scan
    board = create_board()
    apply_placement(board, [{"id": 1, "x": 0, "y": 0, "horizontal": True}])
    assert radar_scan(board, 1, 1)             # 邻域内有舰
    assert radar_scan(board, 0, 0)             # 角落：3×3 被裁掉，仍能探到
    assert radar_scan(board, 4, 0)             # 以舰尾为心
    assert not radar_scan(board, 7, 7)         # 空海域
    # 舰占 (0..4, 0)：中心 (6,1) 的 3×3 覆盖 x=5..7,y=0..2，刚好完全错开
    assert not radar_scan(board, 6, 1)
    print("  OK test_radar_scan_reports_presence_only")


def test_weapons_view_hides_nothing_but_tracks_state():
    from app.game.battleship import weapons_view
    board = create_board()
    apply_placement(board, [{"id": 5, "x": 0, "y": 0, "horizontal": True}])
    attack = create_board()
    weapons = {"radar": 5, "salvo": 4, "strike": 3}
    view = weapons_view(weapons, [], board, attack)
    assert view["radar"]["ship_name"] == "destroyer"
    assert view["radar"]["available"] and not view["radar"]["used"]
    # 用过 → 不可用
    view = weapons_view(weapons, ["radar"], board, attack)
    assert view["radar"]["used"] and not view["radar"]["available"]
    # 携带舰被击沉 → 失效
    fire(board, attack, 0, 0)
    fire(board, attack, 1, 0)
    view = weapons_view(weapons, [], board, attack)
    assert not view["radar"]["available"]
    assert view["strike"]["available"]            # 别的武器不受影响
    print("  OK test_weapons_view_hides_nothing_but_tracks_state")


def test_game_to_sync_exposes_weapons_for_both_sides():
    gs = {
        "phase": "battle",
        "boards": {1: create_board(), 2: create_board()},
        "attack_boards": {1: create_board(), 2: create_board()},
        "placements": {1: [], 2: []},
        "shots": {1: [], 2: []},
        "player_colors": {1: "blue", 2: "red"},
        "placement_ready": {1: True, 2: True},
        "next": "blue",
        "winner": None,
        "reason": None,
        "weapons": {1: {"radar": 5, "salvo": 4, "strike": 3},
                    2: {"radar": 1, "salvo": 2, "strike": 3}},
        "weapons_used": {1: [], 2: []},
        "radar_results": {1: [{"x": 1, "y": 1, "has_ship": True}], 2: []},
    }
    sync = game_to_sync(gs, 1)
    # 自己的和对手的挂靠都下发（反制手段），但雷达结果只给自己
    assert sync["own_weapons"]["radar"]["ship_id"] == 5
    assert sync["opp_weapons"]["radar"]["ship_id"] == 1
    assert sync["own_radar"] == [{"x": 1, "y": 1, "has_ship": True}]
    assert "weapon_defs" in sync
    other = game_to_sync(gs, 2)
    assert other["own_radar"] == []              # 对手拿不到我的探测结果
    print("  OK test_game_to_sync_exposes_weapons_for_both_sides")


# ── 棋盘尺寸 / 舰队配置（v13）─────────────────────────────────
#
# 尺寸是**房间级设置**（房主在大厅选），引擎里凡是「有多大」都问 `gs["size"]`
# 或 `len(board)`。这一组用例把三档逐个钉住 —— 加新档位时照抄一条即可。


def _engine_sized(size: int) -> dict:
    """最小的一份 `gs`（只够 `game_to_sync` / 埋雷用）。"""
    n = size
    return {
        "size": n,
        "phase": "placing",
        "boards": {1: create_board(n), 2: create_board(n)},
        "attack_boards": {1: create_board(n), 2: create_board(n)},
        "placements": {1: [], 2: []},
        "shots": {1: [], 2: []},
        "player_colors": {1: "blue", 2: "red"},
        "placement_ready": {1: True, 2: True},
        "next": "blue", "winner": None, "reason": None,
        "weapons": {1: {}, 2: {}},
        "weapons_used": {1: [], 2: []},
        "radar_results": {1: [], 2: []},
    }


class TestVariantRules:
    """v1.18.1 房规：舰船不可相邻 / 击沉不报舰种。"""

    @staticmethod
    def _spread() -> list[dict]:
        """每隔一行放一艘：正常规则合法，且没有两艘相邻。"""
        return [
            {"id": 1, "x": 0, "y": 0, "horizontal": True},
            {"id": 2, "x": 0, "y": 2, "horizontal": True},
            {"id": 3, "x": 0, "y": 4, "horizontal": True},
            {"id": 4, "x": 0, "y": 6, "horizontal": True},
            {"id": 5, "x": 0, "y": 8, "horizontal": True},
        ]

    def test_no_touch_rejects_adjacent_ships(self):
        tight = self._spread()
        tight[1] = {"id": 2, "x": 0, "y": 1, "horizontal": True}   # 贴住 1 号舰
        assert validate_placement(tight), "不开启房规时相邻是合法的"
        assert validate_placement(self._spread(), no_touch=True)
        assert not validate_placement(tight, no_touch=True), "开启后上下相邻应被拒"

    def test_no_touch_includes_diagonal(self):
        ships = self._spread()
        # 2 号舰从 (0,2) 横放挪到 (5,1)：斜角贴着 1 号舰的 (4,0)
        ships[1] = {"id": 2, "x": 5, "y": 1, "horizontal": True}
        assert not validate_placement(ships, no_touch=True), "斜角相邻也算相邻"

    def test_random_placement_respects_no_touch_on_every_size(self):
        for size in SIZES:
            for seed in range(5):
                ships = random_placement(random.Random(seed), size, no_touch=True)
                assert validate_placement(ships, size, no_touch=True), (size, seed)

    def test_sync_hides_sunk_ship_type(self):
        from app.game import battleship as bs
        ships = self._spread()
        board = create_board()
        apply_placement(board, ships)
        attack = create_board()
        # 击沉 5 号舰（2 格）
        for cx, cy in get_ship_cells(board, 5):
            attack[cy][cx] = HIT
        visible = bs._visible_opp_ships(board, attack, ships, hide_type=True)
        assert len(visible) == 1 and visible[0]["sunk"] is True
        assert "id" not in visible[0], "击沉不报舰种时协议里不能带舰种"
        assert len(visible[0]["cells"]) == 2, "位置仍然公开（要能标出沉船）"
        shown = bs._visible_opp_ships(board, attack, ships, hide_type=False)
        assert shown[0]["id"] == 5


class TestBoardSizes:
    def test_every_preset_fits_its_board(self):
        """每个档位的舰队都必须**放得进自己的棋盘**（最长的那艘 ≤ 边长）。"""
        for size, preset in SIZE_PRESETS.items():
            assert preset["ships"], size
            longest = max(d["size"] for d in preset["ships"])
            assert longest <= size - 1, (size, longest)
            # 船的 id 必须唯一且从 1 起 —— 棋盘格子上存的就是 id
            ids = [d["id"] for d in preset["ships"]]
            assert ids == list(range(1, len(ids) + 1)), (size, ids)

    def test_random_placement_is_valid_on_every_size(self):
        for size in SIZES:
            for seed in range(5):
                ships = random_placement(random.Random(seed), size)
                assert validate_placement(ships, size), (size, seed)
                assert len(ships) == len(fleet_of(size))

    def test_fire_bounds_follow_the_board(self):
        """开炮的越界判定按**这一局那张盘**：6×6 上 (6, 0) 就越界，
        12×12 上它还在盘内。"""
        small = create_board(6)
        attack = create_board(6)
        try:
            fire(small, attack, 6, 0)
            raise AssertionError("6×6 上 (6,0) 应当越界")
        except ValueError:
            pass
        big = create_board(12)
        assert fire(big, create_board(12), 6, 0)["result"] == "miss"

    def test_sunk_count_uses_this_sizes_fleet(self):
        """6×6 只有 4 艘船 —— 全部击沉就是 4，不是 5。"""
        for size in SIZES:
            n = size
            board = create_board(n)
            attack = create_board(n)
            ships = random_placement(random.Random(1), n)
            apply_placement(board, ships, n)
            assert count_sunk_ships(board, attack) == 0
            assert not all_ships_sunk(board, attack)
            # 把每一艘的每一格都判成命中
            for s in ships:
                for cx, cy in get_ship_cells(board, s["id"]):
                    attack[cy][cx] = HIT
            assert count_sunk_ships(board, attack) == len(fleet_of(n))
            assert all_ships_sunk(board, attack)

    def test_default_weapon_assignment_is_valid_for_every_size(self):
        """默认挂靠按**这一局的舰队**推：三件武器挂三条**不同的**小船。

        ⚠️ 写死 `{radar: 5, ...}` 在 6×6 上是**非法**的（那张图只有 1~4 号船）——
        这正是加尺寸档时踩到的第一颗雷，所以这条钉住「按尺寸推」。
        """
        for size in SIZES:
            assign = default_weapon_assignment(size)
            assert validate_weapon_assignment(assign, size), size
            assert len(set(assign.values())) == 3
            ids = {d["id"] for d in fleet_of(size)}
            assert set(assign.values()) <= ids

    def test_sync_carries_size_and_fleet(self):
        for size in SIZES:
            gs = _engine_sized(size)
            sync = game_to_sync(gs, 1)
            assert sync["board_size"] == size
            assert len(sync["own_board"]) == size
            assert [d["size"] for d in sync["fleet"]] == \
                [d["size"] for d in fleet_of(size)]
            assert all(d["name"] for d in sync["fleet"])
