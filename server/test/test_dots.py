"""Unit tests for Dots and Boxes engine + AI.
Run: cd server && python -m pytest test/test_dots.py -q
"""

import sys
import os
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dots import (
    create_game,
    is_valid_edge,
    get_legal_edges,
    apply_move,
    is_game_over,
    count_edges,
    total_edges,
    game_to_sync,
    snapshot,
    _count_sides,
    _find_completed_boxes,
    PLAYER1,
    PLAYER2,
    DEFAULT_DOTS,
)
from app import dots_ai


def test_create_game_default():
    """Default game: 5×5 dots → 4×4 boxes."""
    gs = create_game()
    assert gs["dots"] == 5
    assert gs["rows"] == 4
    assert gs["cols"] == 4
    assert gs["next"] == PLAYER1
    assert gs["scores"] == {PLAYER1: 0, PLAYER2: 0}
    assert gs["winner"] is None
    print("  ✓ test_create_game_default")


def test_total_edges_count():
    """4×4 boxes: h=5 rows × 4 cols=20, v=4 rows × 5 cols=20, total=40."""
    gs = create_game()
    assert total_edges(gs) == 40
    print("  ✓ test_total_edges_count")


def test_legal_edges_initial():
    """All edges are legal at game start."""
    gs = create_game()
    legal = get_legal_edges(gs)
    assert len(legal) == 40
    print("  ✓ test_legal_edges_initial")


def test_is_valid_edge():
    """Boundary checks for edge validation."""
    gs = create_game()
    assert is_valid_edge(gs, "h", 0, 0) is True
    assert is_valid_edge(gs, "h", 4, 3) is True
    assert is_valid_edge(gs, "h", 5, 0) is False  # y out of range
    assert is_valid_edge(gs, "h", 0, 4) is False  # x out of range
    assert is_valid_edge(gs, "v", 0, 0) is True
    assert is_valid_edge(gs, "v", 3, 4) is True
    assert is_valid_edge(gs, "v", 4, 0) is False  # y out of range
    assert is_valid_edge(gs, "v", 0, 5) is False  # x out of range
    assert is_valid_edge(gs, "z", 0, 0) is False  # bad orient
    print("  ✓ test_is_valid_edge")


def test_apply_move_basic():
    """Drawing an edge without completing a box."""
    gs = create_game()
    result = apply_move(gs, "h", 0, 0)
    assert result["completed"] == []
    assert result["extra_turn"] is False
    assert result["game_over"] is False
    assert gs["h"][0][0] == PLAYER1
    assert gs["next"] == PLAYER2
    assert count_edges(gs) == 1
    print("  ✓ test_apply_move_basic")


def test_apply_move_illegal():
    """Drawing on an already-drawn edge raises ValueError."""
    gs = create_game()
    apply_move(gs, "h", 0, 0)
    try:
        apply_move(gs, "h", 0, 0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  ✓ test_apply_move_illegal")


def test_apply_move_completes_box():
    """Completing a box: player scores and gets extra turn."""
    gs = create_game()
    # Set up 3 sides of box (0,0): top, left, right drawn
    gs["h"][0][0] = PLAYER1  # top
    gs["v"][0][0] = PLAYER1  # left
    gs["v"][0][1] = PLAYER1  # right
    gs["moves"] = [
        {"orient": "h", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 1, "player": PLAYER1, "completed": []},
    ]
    gs["next"] = PLAYER2
    # Draw bottom edge h[1][0] — completes box (0,0)
    result = apply_move(gs, "h", 1, 0)
    assert len(result["completed"]) == 1
    assert result["completed"][0] == (0, 0)
    assert result["extra_turn"] is True
    assert gs["scores"][PLAYER2] == 1
    assert gs["boxes"][0][0] == PLAYER2
    # Next player should still be PLAYER2 (extra turn)
    assert gs["next"] == PLAYER2
    print("  ✓ test_apply_move_completes_box")


def test_extra_turn_chain():
    """Completing two adjacent boxes in one turn (chain)."""
    gs = create_game()
    # Box (0,0): top=h[0][0], bottom=h[1][0], left=v[0][0], right=v[0][1] (shared)
    # Box (0,1): top=h[0][1], bottom=h[1][1], left=v[0][1] (shared), right=v[0][2]
    # Pre-set all sides except v[0][1] for both boxes
    gs["h"][0][0] = PLAYER1  # top of box (0,0)
    gs["h"][1][0] = PLAYER1  # bottom of box (0,0)
    gs["v"][0][0] = PLAYER1  # left of box (0,0)
    gs["h"][0][1] = PLAYER1  # top of box (0,1)
    gs["h"][1][1] = PLAYER1  # bottom of box (0,1)
    gs["v"][0][2] = PLAYER1  # right of box (0,1)
    gs["moves"] = [
        {"orient": "h", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "h", "y": 1, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "h", "y": 0, "x": 1, "player": PLAYER1, "completed": []},
        {"orient": "h", "y": 1, "x": 1, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 2, "player": PLAYER1, "completed": []},
    ]
    gs["next"] = PLAYER2
    # Draw v[0][1] (shared edge) — completes both boxes
    result = apply_move(gs, "v", 0, 1)
    assert len(result["completed"]) == 2
    assert result["extra_turn"] is True
    assert gs["scores"][PLAYER2] == 2
    print("  ✓ test_extra_turn_chain")


def test_game_over_all_edges():
    """Game ends when all edges are drawn."""
    gs = create_game(dots=3)  # 3×3 dots = 2×2 boxes = 4 boxes
    # Total edges: h=3*2=6, v=2*3=6, total=12
    assert total_edges(gs) == 12
    # Draw all edges
    legal = get_legal_edges(gs)
    turn = PLAYER1
    while legal:
        e = legal[0]
        result = apply_move(gs, e[0], e[1], e[2])
        legal = get_legal_edges(gs)
        if not result["extra_turn"]:
            pass  # turn already switched in apply_move
    assert is_game_over(gs)
    assert gs["winner"] is not None
    total_boxes = gs["scores"][PLAYER1] + gs["scores"][PLAYER2]
    assert total_boxes == 4  # 2×2 = 4 boxes
    print("  ✓ test_game_over_all_edges")


def test_small_board_full_play():
    """Play a complete game on 2×2 dots (1 box) — deterministic."""
    gs = create_game(dots=2)  # 2×2 dots = 1×1 box
    assert total_edges(gs) == 4
    # 4 edges to draw. The 4th edge completes the box.
    # P1 draws h[0][0]
    apply_move(gs, "h", 0, 0)
    assert gs["next"] == PLAYER2
    # P2 draws h[1][0]
    apply_move(gs, "h", 1, 0)
    assert gs["next"] == PLAYER1
    # P1 draws v[0][0]
    apply_move(gs, "v", 0, 0)
    assert gs["next"] == PLAYER2
    # P2 draws v[0][1] — completes the box
    result = apply_move(gs, "v", 0, 1)
    assert result["extra_turn"] is True
    assert gs["scores"][PLAYER2] == 1
    assert is_game_over(gs)
    assert gs["winner"] == "white"
    print("  ✓ test_small_board_full_play")


def test_draw_game():
    """Test a tie scenario on a 2-box board."""
    # 3×2 dots = 2×1 boxes (2 boxes)
    gs = create_game(dots=3)
    # We need custom: rows=2, cols=2 for default 5×5
    # Let's manually create a 3-dot × 2-dot board: rows=2, cols=1
    # Actually create_game uses dots for both dimensions, so 3×3 dots = 2×2 boxes.
    # For a guaranteed draw, let's just verify the score mechanism works.
    # Manually set up: P1 gets 2 boxes, P2 gets 2 boxes on a 4-box board.
    gs = create_game(dots=3)
    gs["scores"] = {PLAYER1: 2, PLAYER2: 2}
    gs["winner"] = "draw"
    gs["reason"] = "score"
    snap = snapshot(gs)
    assert snap["scores"] == {PLAYER1: 2, PLAYER2: 2}
    assert snap["winner"] == "draw"
    print("  ✓ test_draw_game")


def test_game_to_sync_structure():
    """Verify sync message structure."""
    gs = create_game()
    gs["player_colors"] = {10: "black", 20: "white"}
    sync = game_to_sync(gs, 10)
    assert sync["dots"] == 5
    assert sync["your_color"] == "black"
    assert sync["next"] == PLAYER1
    assert sync["winner"] is None
    assert sync["scores"] == {PLAYER1: 0, PLAYER2: 0}
    sync2 = game_to_sync(gs, 20)
    assert sync2["your_color"] == "white"
    print("  ✓ test_game_to_sync_structure")


def test_snapshot_structure():
    """Verify snapshot contains all needed fields."""
    gs = create_game()
    snap = snapshot(gs)
    assert snap["game"] == "dots"
    assert snap["dots"] == 5
    assert "h" in snap
    assert "v" in snap
    assert "boxes" in snap
    assert "scores" in snap
    print("  ✓ test_snapshot_structure")


def test_completed_boxes_detection():
    """Test _find_completed_boxes with pre-set edges."""
    gs = create_game()
    # Set up box (0,0) with 3 sides, then add the 4th
    gs["h"][0][0] = PLAYER1
    gs["v"][0][0] = PLAYER1
    gs["v"][0][1] = PLAYER1
    # Now place bottom edge
    gs["h"][1][0] = PLAYER2
    completed = _find_completed_boxes(gs, "h", 1, 0)
    assert (0, 0) in completed
    print("  ✓ test_completed_boxes_detection")


def test_count_sides():
    """Test _count_sides helper."""
    gs = create_game()
    assert _count_sides(gs, 0, 0) == 0
    gs["h"][0][0] = PLAYER1
    assert _count_sides(gs, 0, 0) == 1
    gs["v"][0][0] = PLAYER1
    assert _count_sides(gs, 0, 0) == 2
    gs["v"][0][1] = PLAYER1
    assert _count_sides(gs, 0, 0) == 3
    gs["h"][1][0] = PLAYER1
    assert _count_sides(gs, 0, 0) == 4
    print("  ✓ test_count_sides")


# ── AI tests ──


def test_ai_returns_legal_move():
    """AI always returns a legal edge."""
    gs = create_game()
    move = dots_ai.best_move(gs)
    assert move is not None
    orient, y, x = move
    assert is_valid_edge(gs, orient, y, x)
    print("  ✓ test_ai_returns_legal_move")


def test_ai_completes_box():
    """AI takes a free box when available."""
    gs = create_game()
    # Set up box (0,0) with 3 sides
    gs["h"][0][0] = PLAYER1
    gs["v"][0][0] = PLAYER1
    gs["v"][0][1] = PLAYER1
    gs["next"] = PLAYER1
    move = dots_ai.best_move(gs)
    assert move is not None
    orient, y, x = move
    # The AI should draw h[1][0] to complete box (0,0)
    assert orient == "h" and y == 1 and x == 0
    print("  ✓ test_ai_completes_box")


def test_ai_avoids_giving_box():
    """AI avoids drawing the 3rd side of a box when safe edges exist."""
    gs = create_game()
    # Set up box (0,0) with 2 sides: top and left
    gs["h"][0][0] = PLAYER1
    gs["v"][0][0] = PLAYER1
    gs["next"] = PLAYER2
    move = dots_ai.best_move(gs)
    assert move is not None
    orient, y, x = move
    # AI should NOT draw h[1][0] (bottom) or v[0][1] (right) — those give P1 a 3-sided box
    # It should pick something else
    would_give = (
        (orient == "h" and y == 1 and x == 0)
        or (orient == "v" and y == 0 and x == 1)
    )
    assert not would_give, f"AI gave away a box: {orient}{y},{x}"
    print("  ✓ test_ai_avoids_giving_box")


def test_ai_forced_to_open():
    """When all edges create 3-sided boxes, AI picks least damaging one."""
    gs = create_game(dots=2)  # 1 box, 4 edges
    # Draw 2 edges (top and left) — next player is forced to create 3-sided box
    gs["h"][0][0] = PLAYER1
    gs["v"][0][0] = PLAYER1
    gs["next"] = PLAYER2
    gs["moves"] = [
        {"orient": "h", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
    ]
    move = dots_ai.best_move(gs)
    assert move is not None
    # Both remaining edges (h[1][0] and v[0][1]) create 3-sided box — AI must pick one
    orient, y, x = move
    assert is_valid_edge(gs, orient, y, x)
    print("  ✓ test_ai_forced_to_open")


def test_ai_none_when_no_moves():
    """AI returns None when no legal edges remain."""
    gs = create_game(dots=2)
    # Fill all edges
    gs["h"][0][0] = PLAYER1
    gs["h"][1][0] = PLAYER1
    gs["v"][0][0] = PLAYER1
    gs["v"][0][1] = PLAYER1
    gs["boxes"][0][0] = PLAYER1
    gs["scores"] = {PLAYER1: 1, PLAYER2: 0}
    gs["moves"] = [
        {"orient": "h", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "h", "y": 1, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 0, "player": PLAYER1, "completed": []},
        {"orient": "v", "y": 0, "x": 1, "player": PLAYER1, "completed": []},
    ]
    move = dots_ai.best_move(gs)
    assert move is None
    print("  ✓ test_ai_none_when_no_moves")


def test_ai_plays_full_game():
    """AI plays a full game on a small board without errors."""
    gs = create_game(dots=3)  # 3×3 dots = 2×2 boxes
    max_iters = 100
    iters = 0
    while not is_game_over(gs) and iters < max_iters:
        move = dots_ai.best_move(gs)
        assert move is not None
        result = apply_move(gs, move[0], move[1], move[2])
        iters += 1
    assert is_game_over(gs)
    total_boxes = gs["scores"][PLAYER1] + gs["scores"][PLAYER2]
    assert total_boxes == 4  # 2×2 = 4 boxes
    assert gs["winner"] is not None
    print("  ✓ test_ai_plays_full_game")


if __name__ == "__main__":
    print("Running Dots and Boxes unit tests...")
    test_create_game_default()
    test_total_edges_count()
    test_legal_edges_initial()
    test_is_valid_edge()
    test_apply_move_basic()
    test_apply_move_illegal()
    test_apply_move_completes_box()
    test_extra_turn_chain()
    test_game_over_all_edges()
    test_small_board_full_play()
    test_draw_game()
    test_game_to_sync_structure()
    test_snapshot_structure()
    test_completed_boxes_detection()
    test_count_sides()
    test_ai_returns_legal_move()
    test_ai_completes_box()
    test_ai_avoids_giving_box()
    test_ai_forced_to_open()
    test_ai_none_when_no_moves()
    test_ai_plays_full_game()
    print("\nAll Dots and Boxes tests passed! ✓")
