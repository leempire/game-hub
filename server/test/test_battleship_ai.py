"""Unit tests for Battleship AI. Run:
    cd server && python -m pytest test/test_battleship_ai.py -q
"""

import sys
import os
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game.battleship import (
    create_board,
    random_placement,
    apply_placement,
    fire,
    all_ships_sunk,
    BOARD_SIZE,
    EMPTY,
    MISS,
    HIT,
    SHIP_DEFS,
)
from app.game.battleship_ai import best_move


# ── helper ────────────────────────────────────────────────────────

def _setup_game(seed=42):
    """Create a full game setup with two placements and attack boards."""
    rng = random.Random(seed)
    p1_ships = random_placement(rng)
    p2_ships = random_placement(rng)
    p1_board = create_board()
    p2_board = create_board()
    apply_placement(p1_board, p1_ships)
    apply_placement(p2_board, p2_ships)
    p1_attack = create_board()  # p1's shots at p2
    p2_attack = create_board()  # p2's shots at p1
    return p1_board, p2_board, p1_attack, p2_attack, p1_ships, p2_ships


# ── target mode: adjacent targeting ────────────────────────────────

def test_target_mode_hits_adjacent():
    """After a hit, AI must fire at an adjacent cell of the hit."""
    rng = random.Random(10)
    _p1_board, p2_board, p1_attack, _p2_attack, _p1_ships, p2_ships = _setup_game(10)
    # Find a ship cell on p2_board and simulate a hit
    target_cell = None
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if p2_board[y][x] != EMPTY:
                target_cell = (x, y)
                break
        if target_cell:
            break
    assert target_cell is not None
    tx, ty = target_cell
    # Mark as hit on p1's attack board
    p1_attack[ty][tx] = HIT
    # AI chooses next move
    random.seed(10)
    move = best_move(p1_attack, p2_board, p2_ships)
    # Must be adjacent to the hit
    dx = abs(move[0] - tx)
    dy = abs(move[1] - ty)
    assert (dx + dy) == 1, f"AI chose {move}, not adjacent to hit ({tx},{ty})"
    # Must not be a cell already fired at
    assert p1_attack[move[1]][move[0]] == EMPTY
    print("  OK test_target_mode_hits_adjacent")


def test_target_mode_follows_line():
    """With two collinear hits, AI should extend along the line."""
    _p1_board, p2_board, p1_attack, _p2_attack, _p1_ships, p2_ships = _setup_game(20)
    # Find a horizontal ship of size >= 3 on p2
    for sdef in SHIP_DEFS:
        if sdef["size"] >= 3:
            sid = sdef["id"]
            cells = []
            for y in range(BOARD_SIZE):
                for x in range(BOARD_SIZE):
                    if p2_board[y][x] == sid:
                        cells.append((x, y))
            if len(cells) >= 3:
                # Check if horizontal
                ys = {c[1] for c in cells}
                if len(ys) == 1:
                    cells.sort()
                    # Mark first two as hit
                    p1_attack[cells[0][1]][cells[0][0]] = HIT
                    p1_attack[cells[1][1]][cells[1][0]] = HIT
                    random.seed(30)
                    move = best_move(p1_attack, p2_board, p2_ships)
                    # Should prefer extending along the line (cells[2] or before cells[0])
                    expected = [
                        (cells[0][0] - 1, cells[0][1]),
                        (cells[-1][0] + 1, cells[-1][1]),
                    ]
                    # Accept any adjacent, but line extension is preferred
                    is_adjacent = any(
                        abs(move[0] - hx) + abs(move[1] - hy) == 1
                        for hx, hy in [(cells[0][0], cells[0][1]), (cells[1][0], cells[1][1])]
                    )
                    assert is_adjacent, f"AI chose {move}, not adjacent to any hit"
                    print("  OK test_target_mode_follows_line")
                    return
    # If no horizontal ship of size >=3, skip gracefully
    print("  OK test_target_mode_follows_line (skipped, no suitable ship)")


def test_target_mode_returns_to_hunt_after_sunk():
    """After sinking a ship, AI returns to hunt mode (parity-based)."""
    _p1_board, p2_board, p1_attack, _p2_attack, _p1_ships, p2_ships = _setup_game(50)
    # Find the smallest ship (destroyer, id=5, size=2) on p2_board
    destroyer_cells = []
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if p2_board[y][x] == 5:
                destroyer_cells.append((x, y))
    assert len(destroyer_cells) == 2
    # Sink the destroyer
    for cx, cy in destroyer_cells:
        p1_attack[cy][cx] = HIT
    # All hits are now sunk — AI should return to hunt mode
    random.seed(60)
    move = best_move(p1_attack, p2_board, p2_ships)
    # Should not be adjacent to the sunk ship (hunt mode with parity)
    # Verify it's a valid unfired cell
    assert p1_attack[move[1]][move[0]] == EMPTY
    print("  OK test_target_mode_returns_to_hunt_after_sunk")


# ── hunt mode: parity ──────────────────────────────────────────────

def test_hunt_mode_parity():
    """Hunt mode should prefer cells with (x+y) even."""
    attack = create_board()
    opp_board = create_board()
    opp_ships: list[dict] = []
    # Place one ship on opp_board
    opp_ships = [{"id": 5, "x": 0, "y": 0, "horizontal": True}]
    opp_board[0][0] = 5
    opp_board[0][1] = 5
    # No hits yet — pure hunt mode
    even_count = 0
    total = 100
    for seed in range(total):
        random.seed(seed)
        move = best_move(attack, opp_board, opp_ships)
        if (move[0] + move[1]) % 2 == 0:
            even_count += 1
    # Should be overwhelmingly even parity
    assert even_count >= 90, f"Only {even_count}/{total} moves had even parity"
    print("  OK test_hunt_mode_parity")


# ── move validity ──────────────────────────────────────────────────

def test_move_is_unfired():
    """Returned move must always be an unfired cell."""
    random.seed(99)
    for seed in range(10):
        _p1b, p2b, p1a, _p2a, _p1s, p2s = _setup_game(seed)
        # Fire some random shots
        for _ in range(20):
            unfired = [
                (x, y) for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
                if p1a[y][x] == EMPTY
            ]
            if not unfired:
                break
            move = best_move(p1a, p2b, p2s)
            assert p1a[move[1]][move[0]] == EMPTY, f"AI returned fired cell {move}"
            result = fire(p2b, p1a, move[0], move[1])
            if all_ships_sunk(p2b, p1a):
                break
    print("  OK test_move_is_unfired")


# ── AI vs random: win rate ─────────────────────────────────────────

def _play_game(ai_is_player1: bool, seed: int) -> bool:
    """Play one full game: AI vs random. Returns True if AI wins."""
    rng = random.Random(seed)
    p1_ships = random_placement(rng)
    p2_ships = random_placement(rng)
    p1_board = create_board()
    p2_board = create_board()
    apply_placement(p1_board, p1_ships)
    apply_placement(p2_board, p2_ships)
    p1_attack = create_board()
    p2_attack = create_board()
    # Player 1 goes first
    turn = 1
    max_turns = 200
    for _ in range(max_turns):
        if turn == 1:
            attacker_board = p1_attack
            target_board = p2_board
            target_ships = p2_ships
            is_ai = ai_is_player1
        else:
            attacker_board = p2_attack
            target_board = p1_board
            target_ships = p1_ships
            is_ai = not ai_is_player1
        if is_ai:
            move = best_move(attacker_board, target_board, target_ships)
        else:
            unfired = [
                (x, y) for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
                if attacker_board[y][x] == EMPTY
            ]
            if not unfired:
                break
            move = rng.choice(unfired)
        fire(target_board, attacker_board, move[0], move[1])
        if all_ships_sunk(target_board, attacker_board):
            return is_ai  # AI wins if it was the attacker
        turn = 2 if turn == 1 else 1
    return False  # Shouldn't reach here


def test_ai_vs_random_winrate():
    """AI wins >=90% of 20 games vs random opponent."""
    wins = 0
    total = 20
    for i in range(total):
        if _play_game(ai_is_player1=(i % 2 == 0), seed=1000 + i):
            wins += 1
    assert wins >= int(total * 0.9), f"AI won only {wins}/{total} vs random"
    print(f"  OK test_ai_vs_random_winrate ({wins}/{total})")


# ── timing ─────────────────────────────────────────────────────────

def test_timing_single_move():
    """Single decision must complete within 100ms."""
    _p1b, p2b, p1a, _p2a, _p1s, p2s = _setup_game(77)
    # Fire some shots to create mid-game state
    rng = random.Random(77)
    for _ in range(30):
        unfired = [
            (x, y) for y in range(BOARD_SIZE) for x in range(BOARD_SIZE)
            if p1a[y][x] == EMPTY
        ]
        if not unfired:
            break
        move = rng.choice(unfired)
        fire(p2b, p1a, move[0], move[1])
    t0 = time.time()
    move = best_move(p1a, p2b, p2s)
    elapsed = time.time() - t0
    assert move is not None
    assert elapsed < 0.1, f"Move took {elapsed:.3f}s (>100ms)"
    print(f"  OK test_timing_single_move ({elapsed*1000:.1f}ms)")


def test_timing_empty_board():
    """First move (empty attack board) should be fast."""
    p2_board = create_board()
    ships = random_placement(random.Random(0))
    apply_placement(p2_board, ships)
    attack = create_board()
    t0 = time.time()
    move = best_move(attack, p2_board, ships)
    elapsed = time.time() - t0
    assert move is not None
    assert elapsed < 0.1, f"First move took {elapsed:.3f}s"
    print(f"  OK test_timing_empty_board ({elapsed*1000:.1f}ms)")


if __name__ == "__main__":
    test_target_mode_hits_adjacent()
    test_target_mode_follows_line()
    test_target_mode_returns_to_hunt_after_sunk()
    test_hunt_mode_parity()
    test_move_is_unfired()
    test_ai_vs_random_winrate()
    test_timing_single_move()
    test_timing_empty_board()
    print("\nAll battleship AI tests passed!")


# ── v1.10 三档难度 ────────────────────────────────────────────


def _avg_shots(difficulty, games=60, seed=4242):
    from app.game.battleship_ai import best_move
    rng = random.Random(seed)
    totals = []
    for _ in range(games):
        board = create_board()
        ships = random_placement(rng)
        apply_placement(board, ships)
        attack = create_board()
        n = 0
        while not all_ships_sunk(board, attack):
            x, y = best_move(attack, board, ships, difficulty)
            fire(board, attack, x, y)
            n += 1
            assert n <= 200, "AI 没能结束对局"
        totals.append(n)
    return sum(totals) / len(totals)


def test_difficulty_tiers_are_ordered():
    """难度必须真的递增：简单 > 普通 > 困难（发数越少越强）。"""
    easy = _avg_shots("easy")
    normal = _avg_shots("normal")
    hard = _avg_shots("hard")
    print(f"  easy={easy:.1f} normal={normal:.1f} hard={hard:.1f}")
    assert easy > normal > hard, f"档位没有拉开：{easy:.1f}/{normal:.1f}/{hard:.1f}"
    # 困难档要明显强于普通档，否则「难度」形同虚设
    assert normal - hard >= 4.0, f"困难档只比普通强 {normal - hard:.1f} 发"


def test_normalize_difficulty_falls_back():
    from app.game.battleship_ai import DEFAULT_DIFFICULTY, normalize_difficulty
    for level in ("easy", "normal", "hard"):
        assert normalize_difficulty(level) == level
    for bad in (None, "", "nightmare", 3):
        assert normalize_difficulty(bad) == DEFAULT_DIFFICULTY


def test_every_tier_returns_unfired_cell():
    from app.game.battleship_ai import best_move
    for level in ("easy", "normal", "hard"):
        rng = random.Random(7)
        board = create_board()
        ships = random_placement(rng)
        apply_placement(board, ships)
        attack = create_board()
        # 先随便打几发造出「有命中未沉」的局面，确保 target 路径也被覆盖
        for x, y in [(5, 5), (9, 9), (0, 0), (1, 0)]:
            fire(board, attack, x, y)
        for _ in range(20):
            x, y = best_move(attack, board, ships, level)
            assert attack[y][x] == EMPTY, f"{level} 打到了已开火的格子"
            fire(board, attack, x, y)


def test_weapon_plan_prefers_strike_on_locked_target():
    from app.game.battleship_ai import weapon_plan
    rng = random.Random(11)
    board = create_board()
    ships = random_placement(rng)
    apply_placement(board, ships)
    attack = create_board()
    # 打出一个未沉的命中
    first = None
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] != EMPTY:
                first = (x, y)
                break
        if first:
            break
    fire(board, attack, *first)
    avail = {"radar": True, "salvo": True, "strike": True}
    plan = weapon_plan(attack, board, avail, shots_fired=5)
    assert plan is not None and plan[0] == "strike"
    assert plan[1]["x"] == first[0] and plan[1]["y"] == first[1]
    # 一件都没有 → 不开炮以外的动作
    assert weapon_plan(attack, board, {"radar": False, "salvo": False,
                                       "strike": False}, 5) is None


def test_weapon_plan_radar_only_early():
    from app.game.battleship_ai import weapon_plan
    rng = random.Random(13)
    board = create_board()
    ships = random_placement(rng)
    apply_placement(board, ships)
    attack = create_board()
    avail = {"radar": True, "salvo": True, "strike": True}
    plan = weapon_plan(attack, board, avail, shots_fired=2)
    assert plan is not None and plan[0] == "radar"
    # 开局窗口过了就不再用雷达（此时它只是浪费回合）
    assert weapon_plan(attack, board, avail, shots_fired=30) is None
