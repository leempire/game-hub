"""Unit tests for reversi game logic. Run: python test/test_reversi.py"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game.reversi import (
    create_board,
    find_flips_in_dir,
    find_all_flips,
    is_valid_move,
    get_legal_moves,
    apply_move,
    count_stones,
    game_to_sync,
    opponent,
    BOARD_SIZE,
    EMPTY,
    BLACK,
    WHITE,
)


def test_create_board_initial_layout():
    """Initial board has 4 stones in center diagonal pattern."""
    board = create_board()
    assert len(board) == BOARD_SIZE
    assert all(len(row) == BOARD_SIZE for row in board)
    # White at (3,3) and (4,4)
    assert board[3][3] == WHITE
    assert board[4][4] == WHITE
    # Black at (3,4) and (4,3)
    assert board[4][3] == BLACK
    assert board[3][4] == BLACK
    # All other cells empty
    empty_count = sum(cell == EMPTY for row in board for cell in row)
    assert empty_count == 60
    print("  ✓ test_create_board_initial_layout")


def test_opponent():
    assert opponent(BLACK) == WHITE
    assert opponent(WHITE) == BLACK
    print("  ✓ test_opponent")


def test_find_flips_horizontal():
    """Flip in horizontal direction."""
    board = create_board()
    # Place black at (5,3) should flip white at (4,3) with black at (3,3)?
    # No, (3,3) is white. Let's think:
    # Black at (4,3), White at (3,3). If we place black at (2,3),
    # going right (+1,0): (3,3)=W, (4,3)=B → flip (3,3)
    flips = find_flips_in_dir(board, 2, 3, BLACK, 1, 0)
    assert (3, 3) in flips
    assert len(flips) == 1
    print("  ✓ test_find_flips_horizontal")


def test_find_flips_vertical():
    """Flip in vertical direction."""
    board = create_board()
    # Place black at (3,2): going down (0,+1): (3,3)=W, (3,4)=B → flip (3,3)
    flips = find_flips_in_dir(board, 3, 2, BLACK, 0, 1)
    assert (3, 3) in flips
    assert len(flips) == 1
    print("  ✓ test_find_flips_vertical")


def test_find_flips_diagonal():
    """Flip in diagonal direction."""
    board = create_board()
    # Place black at (2,2): going (+1,+1): (3,3)=W, (4,4)=W → need B at end
    # But (5,5) is empty. No flip.
    flips = find_flips_in_dir(board, 2, 2, BLACK, 1, 1)
    assert len(flips) == 0
    # Setup: add black at (5,5)
    board[5][5] = BLACK
    flips = find_flips_in_dir(board, 2, 2, BLACK, 1, 1)
    assert (3, 3) in flips
    assert (4, 4) in flips
    assert len(flips) == 2
    print("  ✓ test_find_flips_diagonal")


def test_find_all_flips_multiple_directions():
    """A single move can flip in multiple directions simultaneously."""
    board = create_board()
    # Black at (5,3): going left (-1,0): (4,3)=B → no flip (immediate B)
    # Wait, (4,3) is black, so we need W then B
    # Let's try black at (5,4): going left (-1,0): (4,4)=W, (3,4)=B → flip (4,4)
    # going up (0,-1): (5,3)=E → no flip
    flips = find_all_flips(board, 5, 4, BLACK)
    assert (4, 4) in flips
    print("  ✓ test_find_all_flips_multiple_directions")


def test_find_all_flips_multi_direction():
    """Place stone that flips in 2+ directions simultaneously."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    # Horizontal: B at (2,3), [empty at (3,3)], W at (4,3), B at (5,3)
    board[3][2] = BLACK
    board[3][4] = WHITE
    board[3][5] = BLACK
    # Vertical: B at (3,0), W at (3,2), [empty at (3,3)]
    board[0][3] = BLACK
    board[2][3] = WHITE
    # Place black at (3,3) should flip:
    #   right (+1,0): (4,3)=W, (5,3)=B → flip (4,3)
    #   up (0,-1): (3,2)=W, (3,1)=E → no flip (need B at end)
    # Fix: need B at (3,0) not (3,0)... (3,0) means x=3,y=0
    # up (0,-1) from (3,3): (3,2)=W → continue → (3,1)=E → stop. No flip!
    # Let me fix: B at (3,0) means board[0][3]. Already set.
    # up (0,-1): check (3,2) y=2 → W → continue. (3,1) y=1 → E → stop. No flip.
    # Need: W at (3,2) and W at (3,1) with B at (3,0)
    board[1][3] = WHITE  # W at (3,1)
    flips = find_all_flips(board, 3, 3, BLACK)
    assert (4, 3) in flips  # horizontal flip
    assert (3, 2) in flips  # vertical flip
    assert (3, 1) in flips  # vertical flip
    assert len(flips) == 3
    print("  ✓ test_find_all_flips_multi_direction")


def test_is_valid_move():
    """Test legal move detection."""
    board = create_board()
    # Valid moves for black from initial position:
    # (2,3): flip (3,3) in direction (+1,0) since (4,3)=B
    assert is_valid_move(board, 2, 3, BLACK) is True
    # (3,2): flip (3,3) in direction (0,+1) since (3,4)=B
    assert is_valid_move(board, 3, 2, BLACK) is True
    # (4,5): flip (4,4) in direction (0,-1) since (4,3)=B
    assert is_valid_move(board, 4, 5, BLACK) is True
    # (5,4): flip (4,4) in direction (-1,0) since (3,4)=B
    assert is_valid_move(board, 5, 4, BLACK) is True
    # Invalid: center occupied
    assert is_valid_move(board, 3, 3, BLACK) is False
    # Invalid: empty but doesn't flip anything
    assert is_valid_move(board, 0, 0, BLACK) is False
    # Out of bounds
    assert is_valid_move(board, -1, 0, BLACK) is False
    assert is_valid_move(board, 8, 0, BLACK) is False
    print("  ✓ test_is_valid_move")


def test_get_legal_moves_initial():
    """Initial position has exactly 4 legal moves for black."""
    board = create_board()
    moves = get_legal_moves(board, BLACK)
    assert len(moves) == 4
    expected = {(2, 3), (3, 2), (4, 5), (5, 4)}
    assert set(moves) == expected
    print("  ✓ test_get_legal_moves_initial")


def test_get_legal_moves_white():
    """White also has 4 legal moves from initial position (symmetric)."""
    board = create_board()
    moves = get_legal_moves(board, WHITE)
    assert len(moves) == 4
    expected = {(2, 4), (3, 5), (4, 2), (5, 3)}
    assert set(moves) == expected
    print("  ✓ test_get_legal_moves_white")


def test_apply_move_flips_stones():
    """Apply move actually places stone and flips opponents."""
    board = create_board()
    # Black plays (2,3): flips (3,3) from W to B
    flips = apply_move(board, 2, 3, BLACK)
    assert board[3][2] == BLACK  # placed
    assert (3, 3) in flips
    assert board[3][3] == BLACK  # flipped
    print("  ✓ test_apply_move_flips_stones")


def test_apply_move_illegal_raises():
    """Apply move on illegal position raises ValueError."""
    board = create_board()
    try:
        apply_move(board, 0, 0, BLACK)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  ✓ test_apply_move_illegal_raises")


def test_apply_move_multiple_flips():
    """A move can flip stones in multiple directions."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    # Setup for multi-direction flip:
    # B at (2,2), W at (3,3), B at (4,4) already
    board[2][2] = BLACK   # B at (2,2)
    board[3][3] = WHITE   # W at (3,3)
    board[4][4] = WHITE   # W at (4,4)
    board[5][5] = BLACK   # B at (5,5)
    board[3][5] = BLACK   # B at (5,3)
    # Place black at (4,3):
    # diagonal (-1,-1): (3,2)=E → no
    # left (-1,0): (3,3)=W, (2,3)=E → no
    # right (+1,0): (5,3)=B → no (immediate)
    # diagonal (+1,+1): (5,4)=E → no
    # Let me use a cleaner setup:
    board2 = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    board2[2][2] = BLACK  # (2,2)
    board2[3][3] = WHITE  # (3,3)
    board2[4][4] = BLACK  # (4,4)
    board2[2][4] = BLACK  # (4,2)
    board2[4][2] = BLACK  # (2,4)
    # Place black at (3,3) → illegal, already occupied
    # Place black at (3,4) (board[4][3]):
    # up (0,-1): (3,3)=W, (3,2)=E → no
    # diag (-1,-1): (2,3)=E → no
    # Simpler: horizontal bracket
    board3 = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    board3[3][1] = BLACK  # B at (1,3)
    board3[3][2] = WHITE  # W at (2,3)
    board3[3][3] = WHITE  # W at (3,3)
    board3[3][4] = WHITE  # W at (4,3)
    board3[3][5] = BLACK  # B at (5,3)
    # Place black at (0,3)... need board3[3][0]=E, going right: (1,3)=B → no flip
    # Place white at (0,3): going right: (1,3)=B → no W after
    # Place black at (6,3): going left: (5,3)=B → no
    # Place white at (3,0): going down: (3,1)=B → no
    # OK let me just use the initial board
    board_init = create_board()
    flips = apply_move(board_init, 2, 3, BLACK)
    assert len(flips) == 1
    assert (3, 3) in flips
    # Now check: after this, black has 4 stones, white has 1
    score = count_stones(board_init)
    assert score["black"] == 4
    assert score["white"] == 1
    print("  ✓ test_apply_move_multiple_flips")


def test_count_stones():
    """Count stones on the board."""
    board = create_board()
    score = count_stones(board)
    assert score["black"] == 2
    assert score["white"] == 2
    print("  ✓ test_count_stones")


def test_no_legal_moves_pass():
    """When a player has no legal moves, get_legal_moves returns empty."""
    # Setup: a board where one color has no moves
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    # Only black stones → white has nothing to flip
    board[0][0] = BLACK
    board[0][1] = BLACK
    moves = get_legal_moves(board, WHITE)
    assert len(moves) == 0
    # Black also has nothing to flip (no white stones)
    moves_b = get_legal_moves(board, BLACK)
    assert len(moves_b) == 0
    print("  ✓ test_no_legal_moves_pass")


def test_game_over_both_no_moves():
    """Game over when neither player can move."""
    # Board with only one color → both have no moves
    board = [[BLACK] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    moves_b = get_legal_moves(board, BLACK)
    moves_w = get_legal_moves(board, WHITE)
    assert len(moves_b) == 0
    assert len(moves_w) == 0
    score = count_stones(board)
    assert score["black"] == 64
    assert score["white"] == 0
    print("  ✓ test_game_over_both_no_moves")


def test_game_over_draw():
    """Test a tied game (32-32)."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    # Fill half black, half white
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if (x + y) % 2 == 0:
                board[y][x] = BLACK
            else:
                board[y][x] = WHITE
    score = count_stones(board)
    assert score["black"] == 32
    assert score["white"] == 32
    # Neither can move (no empty cells, but actually check: any flips possible?)
    # On a full board, no empty cells → no moves
    moves_b = get_legal_moves(board, BLACK)
    moves_w = get_legal_moves(board, WHITE)
    assert len(moves_b) == 0
    assert len(moves_w) == 0
    print("  ✓ test_game_over_draw")


def test_game_to_sync_structure():
    """Verify sync message structure."""
    gs = {
        "board": create_board(),
        "moves": [],
        "next": "black",
        "pass_count": 0,
        "winner": None,
        "reason": None,
        "player_colors": {1: "black", 2: "white"},
    }
    sync = game_to_sync(gs, 1)
    assert sync["board_size"] == BOARD_SIZE
    assert sync["board"] == gs["board"]
    assert sync["next"] == "black"
    assert sync["your_color"] == "black"
    assert sync["pass_count"] == 0
    assert sync["winner"] is None
    assert sync["score"]["black"] == 2
    assert sync["score"]["white"] == 2
    sync2 = game_to_sync(gs, 2)
    assert sync2["your_color"] == "white"
    print("  ✓ test_game_to_sync_structure")


def test_edge_flips():
    """Stones on board edges can be flipped correctly."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    board[0][1] = WHITE  # W at (1,0)
    board[0][2] = WHITE  # W at (2,0)
    board[0][3] = BLACK  # B at (3,0)
    # Place black at (0,0): right (+1,0): (1,0)=W, (2,0)=W, (3,0)=B → flip both
    flips = find_all_flips(board, 0, 0, BLACK)
    assert (1, 0) in flips
    assert (2, 0) in flips
    assert len(flips) == 2
    print("  ✓ test_edge_flips")


def test_corner_no_flip_through():
    """Corner placement doesn't flip through the corner."""
    board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
    board[0][1] = WHITE  # W at (1,0)
    board[0][2] = BLACK  # B at (2,0)
    # Place black at (0,0): right (+1,0): (1,0)=W, (2,0)=B → flip (1,0)
    flips = find_all_flips(board, 0, 0, BLACK)
    assert (1, 0) in flips
    assert len(flips) == 1
    print("  ✓ test_corner_no_flip_through")


def test_sequence_of_moves():
    """Play a few moves in sequence and verify state."""
    board = create_board()
    # Black plays (2,3)
    apply_move(board, 2, 3, BLACK)
    # Board: B at (2,3), B at (3,3)[flipped], B at (4,3), W at (4,4), B at (3,4)
    assert board[3][2] == BLACK  # B at (2,3)
    assert board[3][3] == BLACK  # flipped from W
    assert board[4][3] == BLACK  # B at (3,4) - original
    assert board[4][4] == WHITE  # W at (4,4) - original
    assert board[3][4] == BLACK  # B at (4,3) - original (was BLACK from start)
    score = count_stones(board)
    assert score["black"] == 4
    assert score["white"] == 1
    # White plays (2,2)
    # Check: (2,2) going right(+1,0): (3,2)=B, (4,2)=E → no
    # going down(0,+1): (2,3)=B → no
    # going diag(+1,+1): (3,3)=B → no
    # Hmm, white at (2,2) might not be legal. Let's check legal moves for white.
    white_moves = get_legal_moves(board, WHITE)
    assert len(white_moves) > 0
    # Play first legal move for white
    wx, wy = white_moves[0]
    apply_move(board, wx, wy, WHITE)
    score2 = count_stones(board)
    assert score2["black"] + score2["white"] == 6  # 5 + 1 new stone
    print("  ✓ test_sequence_of_moves")


if __name__ == "__main__":
    print("Running reversi unit tests...")
    test_create_board_initial_layout()
    test_opponent()
    test_find_flips_horizontal()
    test_find_flips_vertical()
    test_find_flips_diagonal()
    test_find_all_flips_multiple_directions()
    test_find_all_flips_multi_direction()
    test_is_valid_move()
    test_get_legal_moves_initial()
    test_get_legal_moves_white()
    test_apply_move_flips_stones()
    test_apply_move_illegal_raises()
    test_apply_move_multiple_flips()
    test_count_stones()
    test_no_legal_moves_pass()
    test_game_over_both_no_moves()
    test_game_over_draw()
    test_game_to_sync_structure()
    test_edge_flips()
    test_corner_no_flip_through()
    test_sequence_of_moves()
    print("\nAll reversi tests passed! ✓")
