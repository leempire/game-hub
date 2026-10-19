"""Connect Four (重力四子棋) pure game logic — no I/O, easy to unit-test.

7 列 × 6 行，双方轮流选一列落子，棋子受重力落到该列最下面的空位。
先连成四子（横 / 竖 / 两条斜线）者胜；棋盘填满仍无四连则和棋。

坐标统一用 `board[row][col]`（row 0 在**最上面**，row ROWS-1 是最下面一行），
列号 `col` 即走子时客户端唯一需要给的东西（落点行由重力决定）。
棋子编号沿用五子棋：1=黑（先手）、2=白。
"""

COLS = 7
ROWS = 6
EMPTY = 0
BLACK = 1
WHITE = 2

# 只需朝左上/上/右上/右四个方向数——反方向由 sign=-1 那一轮覆盖。
DIRECTIONS = [(0, 1), (1, 1), (1, 0), (1, -1)]

WIN_LEN = 4


def create_board() -> list[list[int]]:
    """Create an empty 6x7 board (board[row][col])."""
    return [[EMPTY] * COLS for _ in range(ROWS)]


def in_board(row: int, col: int) -> bool:
    return 0 <= row < ROWS and 0 <= col < COLS


def landing_row(board: list[list[int]], col: int) -> int:
    """该列落下后停在哪一行；列已满返回 -1。"""
    for row in range(ROWS - 1, -1, -1):
        if board[row][col] == EMPTY:
            return row
    return -1


def is_valid_move(board: list[list[int]], col: int) -> bool:
    """列号合法且该列未满。"""
    if col < 0 or col >= COLS:
        return False
    return board[0][col] == EMPTY


def legal_columns(board: list[list[int]]) -> list[int]:
    return [c for c in range(COLS) if board[0][c] == EMPTY]


def drop(board: list[list[int]], col: int, color: int) -> int:
    """落子（原地修改），返回落点行号；非法则抛 ValueError。"""
    if not is_valid_move(board, col):
        raise ValueError(f"非法落点：第 {col} 列")
    row = landing_row(board, col)
    board[row][col] = color
    return row


def check_win(board: list[list[int]], row: int, col: int) -> bool:
    """判断 (row, col) 这颗子是否凑成四连。"""
    color = board[row][col]
    if color == EMPTY:
        return False
    for dr, dc in DIRECTIONS:
        count = 1
        for sign in (1, -1):
            nr, nc = row + dr * sign, col + dc * sign
            while in_board(nr, nc) and board[nr][nc] == color:
                count += 1
                nr += dr * sign
                nc += dc * sign
        if count >= WIN_LEN:
            return True
    return False


def is_board_full(board: list[list[int]]) -> bool:
    """顶行没有空位即满盘（和棋条件）。"""
    return all(cell != EMPTY for cell in board[0])


def winning_columns(board: list[list[int]], color: int) -> list[int]:
    """落哪几列能立刻四连（AI 与「必胜/必堵」提示共用）。"""
    out = []
    for col in legal_columns(board):
        row = landing_row(board, col)
        board[row][col] = color
        won = check_win(board, row, col)
        board[row][col] = EMPTY
        if won:
            out.append(col)
    return out


def would_win_at(board: list[list[int]], col: int, color: int) -> bool:
    row = landing_row(board, col)
    if row < 0:
        return False
    board[row][col] = color
    won = check_win(board, row, col)
    board[row][col] = EMPTY
    return won