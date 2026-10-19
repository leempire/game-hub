"""中国象棋引擎单测。运行：python test/test_xiangqi.py（或 pytest）

摆残局时**必须同时给双方主帅**（`legal_moves` 找不到自己的王就返回空），
且两个王不能同列无遮挡——那是「将帅照面」，红方一开始就处于非法局面，
所有走法都会被过滤掉，测试会以看不懂的方式失败。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.game.xiangqi import (
    ROWS,
    COLS,
    RED,
    BLACK,
    DRAW_PLIES,
    create_board,
    board_from_rows,
    rows_of,
    color_of,
    type_of,
    other,
    in_palace,
    on_own_side,
    find_king,
    legal_moves,
    all_legal_moves,
    is_in_check,
    apply_move,
    undo_move,
    game_status,
    create_game,
    make_move,
    surrender,
    legal_payload,
    game_to_sync,
    snapshot,
)


def _board(pieces: dict) -> list:
    """10×9 空盘摆子，键为 (行, 列)。"""
    board = [[None] * COLS for _ in range(ROWS)]
    for (r, c), piece in pieces.items():
        board[r][c] = piece
    return board


def _sq(board, r, c) -> set:
    return set(legal_moves(board, r, c))


# ── 棋盘与开局 ────────────────────────────────────────

def test_initial_board_layout():
    """开局：红方（大写）在下半、黑方（小写）在上半，各 16 子。"""
    board = create_board()
    assert len(board) == ROWS and all(len(row) == COLS for row in board)
    assert rows_of(board)[0] == "rnbakabnr"
    assert rows_of(board)[9] == "RNBAKABNR"
    assert rows_of(board)[3] == "p.p.p.p.p"
    assert rows_of(board)[6] == "P.P.P.P.P"
    flat = [ch for row in board for ch in row if ch]
    assert len(flat) == 32
    assert sum(1 for ch in flat if ch.isupper()) == 16
    print("  ✓ test_initial_board_layout")


def test_opening_has_44_legal_moves():
    """象棋开局的合法着法数是已知的 44（红黑都一样）——最强的整体自检。"""
    board = create_board()
    assert len(all_legal_moves(board, RED)) == 44
    assert len(all_legal_moves(board, BLACK)) == 44
    print("  ✓ test_opening_has_44_legal_moves")


def test_board_from_rows_validates_length():
    try:
        board_from_rows(["........."] * 9)
        assert False, "应当抛 ValueError"
    except ValueError:
        pass
    print("  ✓ test_board_from_rows_validates_length")


def test_color_and_type_helpers():
    assert color_of("K") == RED and color_of("k") == BLACK
    assert type_of("K") == "K" and type_of("n") == "N"
    assert other(RED) == BLACK and other(BLACK) == RED
    assert in_palace(9, 4, RED) and not in_palace(9, 2, RED) and not in_palace(6, 4, RED)
    assert in_palace(0, 4, BLACK) and not in_palace(3, 4, BLACK)
    assert on_own_side(5, RED) and not on_own_side(4, RED)
    assert on_own_side(4, BLACK) and not on_own_side(5, BLACK)
    print("  ✓ test_color_and_type_helpers")


# ── 各棋子走法 ────────────────────────────────────────

def test_rook_slides_until_blocked():
    """车：沿直线滑行，被挡住即止；可以吃子但不能越过。"""
    board = _board({
        (9, 0): "R", (9, 6): "K", (0, 5): "k",
        (7, 0): "P", (9, 2): "p", (9, 3): "p",
    })
    moves = _sq(board, 9, 0)
    assert (8, 0) in moves                       # 空位可走
    assert (7, 0) not in moves                   # 己方兵挡住
    assert (6, 0) not in moves                   # 更远够不着
    assert (9, 1) in moves
    assert (9, 2) in moves                       # 可以吃黑卒
    assert (9, 3) not in moves                   # 被另一个黑卒挡住，跳不过去
    print("  ✓ test_rook_slides_until_blocked")


def test_horse_moves_and_leg_blocking():
    """马走日；蹩马腿后对应方向失效。"""
    board = _board({(1, 1): "N", (9, 3): "K", (0, 5): "k"})
    assert _sq(board, 1, 1) == {(0, 3), (2, 3), (3, 0), (3, 2)}
    board[2][1] = "p"                            # 蹩住向下的两条
    assert _sq(board, 1, 1) == {(0, 3), (2, 3)}
    print("  ✓ test_horse_moves_and_leg_blocking")


def test_elephant_eye_blocking_and_no_river_crossing():
    """相/象走田，塞象眼失效，且不能过河。"""
    board = _board({(9, 2): "B", (9, 4): "K", (0, 5): "k"})
    assert _sq(board, 9, 2) == {(7, 0), (7, 4)}
    board[8][1] = "p"                            # 塞住 (7,0) 的象眼
    assert _sq(board, 9, 2) == {(7, 4)}
    # 红相在 (5,2)：向前的两个田字落点都过河了，只能往回走
    board2 = _board({(5, 2): "B", (9, 4): "K", (0, 5): "k"})
    assert _sq(board2, 5, 2) == {(7, 0), (7, 4)}
    print("  ✓ test_elephant_eye_blocking_and_no_river_crossing")


def test_advisor_stays_in_palace():
    """仕/士走斜线一格，不出九宫。"""
    board = _board({(8, 4): "A", (9, 4): "K", (0, 5): "k"})
    assert _sq(board, 8, 4) == {(7, 3), (7, 5), (9, 3), (9, 5)}
    print("  ✓ test_advisor_stays_in_palace")


def test_king_stays_in_palace():
    """帅/将走直线一格，不出九宫。"""
    board = _board({(9, 3): "K", (0, 5): "k"})
    assert _sq(board, 9, 3) == {(8, 3), (9, 4)}   # (9,2) 出宫，不可走
    board2 = _board({(8, 3): "K", (0, 5): "k"})
    assert _sq(board2, 8, 3) == {(7, 3), (9, 3), (8, 4)}   # (8,2) 出宫
    print("  ✓ test_king_stays_in_palace")


def test_cannon_needs_exactly_one_screen():
    """炮：不吃子时同车；吃子必须隔且只隔一个炮架。"""
    board = _board({(5, 0): "C", (4, 0): "P", (2, 0): "r", (9, 4): "K", (0, 5): "k"})
    moves = _sq(board, 5, 0)
    assert (2, 0) in moves                       # 隔一个炮架吃掉黑车
    assert (3, 0) not in moves                   # 炮架挡着，翻不过去
    assert (1, 0) not in moves and (0, 0) not in moves   # 黑车又挡住
    assert (6, 0) in moves and (9, 0) in moves  # 下方无遮挡，同车
    assert (5, 8) in moves                       # 横向无遮挡
    # 没有炮架时不能吃子（(2,0) 是第一个碰到的子，只能算遮挡）
    board2 = _board({(5, 0): "C", (2, 0): "r", (9, 4): "K", (0, 5): "k"})
    assert (2, 0) not in _sq(board2, 5, 0)
    assert (4, 0) in _sq(board2, 5, 0)
    print("  ✓ test_cannon_needs_exactly_one_screen")


def test_pawn_forward_only_and_sideways_after_river():
    """兵/卒只能向前；过河后才能横走，永不后退。"""
    board = _board({(6, 4): "P", (9, 3): "K", (0, 5): "k"})
    assert _sq(board, 6, 4) == {(5, 4)}                       # 未过河：只能向前
    board2 = _board({(4, 4): "P", (9, 3): "K", (0, 5): "k"})
    assert _sq(board2, 4, 4) == {(3, 4), (4, 3), (4, 5)}      # 已过河：可横走
    board3 = _board({(3, 4): "p", (9, 5): "K", (0, 3): "k"})
    assert _sq(board3, 3, 4) == {(4, 4)}
    board4 = _board({(5, 4): "p", (9, 5): "K", (0, 3): "k"})
    assert _sq(board4, 5, 4) == {(6, 4), (5, 3), (5, 5)}      # 黑卒过河
    print("  ✓ test_pawn_forward_only_and_sideways_after_river")


def test_cannot_capture_own_piece():
    board = _board({(9, 4): "K", (8, 4): "A", (0, 5): "k"})
    assert (8, 4) not in _sq(board, 9, 4)
    print("  ✓ test_cannot_capture_own_piece")


# ── 将军 / 将死 / 困毙 ────────────────────────────────

def test_flying_general_makes_move_illegal():
    """将帅照面非法：挡在两王之间的子一旦横着挪开，红方就处于非法局面。

    用**过河兵**当挡子——没过河的兵只能直着走，挪一格仍在同一列上，照面与否
    根本没变（第一版测试就栽在这儿）。
    """
    board = board_from_rows([
        "....k....",
        ".........",
        ".........",
        ".........",
        "....P....",
        ".........",
        ".........",
        ".........",
        ".........",
        "....K....",
    ])
    assert is_in_check(board, RED) is False       # 兵挡着，两王没照面
    # 向前仍挡着 → 合法；横着挪开就照面 → 两条都非法
    assert _sq(board, 4, 4) == {(3, 4)}
    assert (8, 4) in _sq(board, 9, 4)             # 帅沿同列走仍被自己的兵挡着
    print("  ✓ test_flying_general_makes_move_illegal")


def test_check_detection_by_rook_and_cannon():
    board = _board({(0, 4): "k", (5, 4): "R", (9, 3): "K"})
    assert is_in_check(board, BLACK) is True
    board2 = _board({(0, 4): "k", (5, 4): "C", (3, 4): "P", (9, 3): "K"})
    assert is_in_check(board2, BLACK) is True     # 炮隔一个兵将军
    board3 = _board({(0, 4): "k", (5, 4): "C", (9, 3): "K"})
    assert is_in_check(board3, BLACK) is False    # 没有炮架
    print("  ✓ test_check_detection_by_rook_and_cannon")


def test_checkmate():
    board = board_from_rows([
        "R...k....",
        ".R.......",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "...K.....",
    ])
    assert is_in_check(board, BLACK) is True
    assert all_legal_moves(board, BLACK) == []
    assert game_status(board, BLACK) == "checkmate"
    print("  ✓ test_checkmate")


def test_stalemate_is_a_loss_in_xiangqi():
    """困毙（无子可动但没被将军）在象棋里判负，不是和棋。"""
    board = board_from_rows([
        "....k....",
        ".........",
        "..N...N..",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "...K.....",
    ])
    assert is_in_check(board, BLACK) is False
    assert all_legal_moves(board, BLACK) == []
    assert game_status(board, BLACK) == "stalemate"
    print("  ✓ test_stalemate_is_a_loss_in_xiangqi")


def test_king_cannot_move_into_check():
    """帅不能走进对方车的射程。"""
    board = _board({(9, 4): "K", (8, 0): "r", (0, 5): "k"})
    assert is_in_check(board, RED) is False
    assert (9, 3) in _sq(board, 9, 4)
    assert (8, 4) not in _sq(board, 9, 4)         # 第 8 行被黑车控制
    print("  ✓ test_king_cannot_move_into_check")


# ── apply / undo ──────────────────────────────────────

def test_apply_and_undo_move_restore_board():
    board = create_board()
    before = rows_of(board)
    captured = apply_move(board, (7, 1), (3, 1))
    assert captured is None
    assert board[3][1] == "C" and board[7][1] is None
    undo_move(board, (7, 1), (3, 1), captured)
    assert rows_of(board) == before
    print("  ✓ test_apply_and_undo_move_restore_board")


def test_find_king():
    board = create_board()
    assert find_king(board, RED) == (9, 4)
    assert find_king(board, BLACK) == (0, 4)
    assert find_king(_board({}), RED) is None
    print("  ✓ test_find_king")


# ── 对局状态机 ────────────────────────────────────────

def test_create_game_shape():
    gs = create_game()
    assert gs["next"] == RED                      # 红先
    assert gs["winner"] is None and gs["finished"] is False
    assert gs["moves"] == [] and gs["player_colors"] == {}
    assert gs["no_capture"] == 0 and gs["check"] is False
    print("  ✓ test_create_game_shape")


def test_make_move_records_and_switches_turn():
    gs = create_game()
    result = make_move(gs, (7, 1), (4, 1))        # 炮进
    assert result["ok"] is True
    assert gs["next"] == BLACK
    assert gs["moves"][0]["from"] == [7, 1] and gs["moves"][0]["to"] == [4, 1]
    assert result["move"]["legal"], "走子广播必须带上新的合法着法列表"
    assert result["move"]["next"] == BLACK
    # 轮到黑方，红方再走一步要被拒
    bad = make_move(gs, (9, 0), (8, 0))
    assert bad["ok"] is False and bad["error"] == "还没轮到你走"
    print("  ✓ test_make_move_records_and_switches_turn")


def test_make_move_rejects_bad_input():
    gs = create_game()
    assert make_move(gs, (-1, 0), (0, 0))["error"] == "落点超出棋盘"
    assert make_move(gs, (5, 5), (4, 5))["error"] == "起点没有棋子"
    assert make_move(gs, (0, 0), (1, 0))["error"] == "还没轮到你走"
    assert make_move(gs, (9, 0), (9, 1))["error"] == "这步棋不合规则"
    print("  ✓ test_make_move_rejects_bad_input")


def test_make_move_rejected_after_game_over():
    gs = create_game()
    surrender(gs, RED)
    assert gs["winner"] == BLACK and gs["finished"] is True
    assert make_move(gs, (9, 0), (8, 0))["error"] == "对局已结束"
    print("  ✓ test_make_move_rejected_after_game_over")


def test_surrender_sets_winner():
    gs = create_game()
    assert surrender(gs, BLACK)["game_over"] == {"winner": RED, "reason": "surrender"}
    assert surrender(gs, RED)["ok"] is False      # 已经结束
    print("  ✓ test_surrender_sets_winner")


def test_checkmate_ends_game():
    """红方一车沉底成杀，make_move 要带回 game_over。"""
    gs = create_game()
    gs["board"] = board_from_rows([
        "....k....",
        ".........",
        "R........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        ".........",
        "...K.....",
    ])
    gs["board"][1][1] = "R"                       # 封住第 1 行，黑将无处可逃
    result = make_move(gs, (2, 0), (0, 0))
    assert result["ok"] is True
    assert result["game_over"] == {"winner": RED, "reason": "checkmate"}
    assert gs["finished"] is True
    print("  ✓ test_checkmate_ends_game")


def test_no_capture_draw_after_120_plies():
    """60 回合无吃子判和（这条规则同时保证棋局必然终止）。"""
    gs = create_game()
    gs["no_capture"] = DRAW_PLIES - 1
    result = make_move(gs, (9, 0), (8, 0))        # 一步不吃子的走法
    assert result["ok"] is True
    assert gs["winner"] == "draw" and gs["reason"] == "no_capture"
    assert result["game_over"] == {"winner": "draw", "reason": "no_capture"}
    print("  ✓ test_no_capture_draw_after_120_plies")


def test_capture_resets_no_capture_counter():
    gs = create_game()
    gs["no_capture"] = 50
    result = make_move(gs, (7, 1), (0, 1))        # 炮打马，吃子
    assert result["ok"] is True
    assert gs["no_capture"] == 0
    print("  ✓ test_capture_resets_no_capture_counter")


# ── 下发结构 ──────────────────────────────────────────

def test_legal_payload_shape():
    gs = create_game()
    gs["player_colors"] = {1: RED, 2: BLACK}
    payload = legal_payload(gs)
    assert len(payload) == 44
    assert all(len(item) == 4 for item in payload)
    assert [7, 1, 0, 1] in payload                # 炮可以吃 (0,1) 的马
    print("  ✓ test_legal_payload_shape")


def test_game_to_sync_structure():
    gs = create_game()
    gs["player_colors"] = {1: RED, 2: BLACK}
    sync = game_to_sync(gs, 1)
    assert sync["rows"] == ROWS and sync["cols"] == COLS
    assert sync["board"] == gs["board"]
    assert sync["next"] == RED
    assert sync["your_color"] == RED
    assert sync["winner"] is None and sync["check"] is False
    assert len(sync["legal"]) == 44
    assert game_to_sync(gs, 2)["your_color"] == BLACK
    assert game_to_sync(gs, 99)["your_color"] == ""
    print("  ✓ test_game_to_sync_structure")


def test_snapshot_structure():
    gs = create_game()
    gs["winner"] = RED
    gs["reason"] = "checkmate"
    snap = snapshot(gs)
    assert snap["game"] == "xiangqi"
    assert snap["board"] == gs["board"]
    assert snap["winner"] == RED and snap["reason"] == "checkmate"
    print("  ✓ test_snapshot_structure")


if __name__ == "__main__":
    print("Running xiangqi unit tests...")
    test_initial_board_layout()
    test_opening_has_44_legal_moves()
    test_board_from_rows_validates_length()
    test_color_and_type_helpers()
    test_rook_slides_until_blocked()
    test_horse_moves_and_leg_blocking()
    test_elephant_eye_blocking_and_no_river_crossing()
    test_advisor_stays_in_palace()
    test_king_stays_in_palace()
    test_cannon_needs_exactly_one_screen()
    test_pawn_forward_only_and_sideways_after_river()
    test_cannot_capture_own_piece()
    test_flying_general_makes_move_illegal()
    test_check_detection_by_rook_and_cannon()
    test_checkmate()
    test_stalemate_is_a_loss_in_xiangqi()
    test_king_cannot_move_into_check()
    test_apply_and_undo_move_restore_board()
    test_find_king()
    test_create_game_shape()
    test_make_move_records_and_switches_turn()
    test_make_move_rejects_bad_input()
    test_make_move_rejected_after_game_over()
    test_surrender_sets_winner()
    test_checkmate_ends_game()
    test_no_capture_draw_after_120_plies()
    test_capture_resets_no_capture_counter()
    test_legal_payload_shape()
    test_game_to_sync_structure()
    test_snapshot_structure()
    print("\nAll xiangqi tests passed! ✓")
