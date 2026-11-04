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

def legal_actions(board: list[list[int]], player: int, popout: bool = False) -> list[dict]:
    """PopOut allows removing only one's own bottom checker."""
    actions = [{"action": "drop", "col": col} for col in legal_columns(board)]
    if popout:
        actions.extend({"action": "pop", "col": col} for col in range(COLS)
                       if board[ROWS - 1][col] == player)
    return actions


def pop(board: list[list[int]], col: int, player: int) -> None:
    """Remove a bottom checker and let its column fall one row."""
    if isinstance(col, bool) or not isinstance(col, int) or not 0 <= col < COLS:
        raise ValueError("列号不合法")
    if board[ROWS - 1][col] != player:
        raise ValueError("只能弹出自己最底下一枚棋子")
    for row in range(ROWS - 1, 0, -1):
        board[row][col] = board[row - 1][col]
    board[0][col] = EMPTY


def winning_players(board: list[list[int]]) -> set[int]:
    return {board[r][c] for r in range(ROWS) for c in range(COLS)
            if board[r][c] != EMPTY and check_win(board, r, c)}


def position_key(board: list[list[int]], next_player: int) -> str:
    return ''.join(str(cell) for row in board for cell in row) + ':' + str(next_player)


def apply_action(gs: dict, player: int, col: int, action: str = "drop") -> dict:
    """Authoritative normal/PopOut move, including both-player wins and repetition.

    The room owns player identity and must map it to the integer stone first.
    Same-move simultaneous lines award the mover the win (Hasbro PopOut).
    Three occurrences of the same board AND player to move draw the game.
    """
    if gs.get('winner'):
        return {'ok': False, 'error': '游戏已结束'}
    color = 'black' if player == BLACK else 'white'
    if player not in (BLACK, WHITE) or gs.get('next') != color:
        return {'ok': False, 'error': '还没轮到你'}
    if isinstance(col, bool) or not isinstance(col, int) or not 0 <= col < COLS:
        return {'ok': False, 'error': '列号不合法'}
    board = gs['board']
    if {'action': action, 'col': col} not in legal_actions(board, player, gs.get('popout', False)):
        return {'ok': False, 'error': '不能在这里落子或弹出'}
    initial = position_key(board, player)
    row = ROWS - 1 if action == 'pop' else landing_row(board, col)
    if action == 'pop':
        pop(board, col, player)
    else:
        drop(board, col, player)
    other = WHITE if player == BLACK else BLACK
    gs['next'] = 'white' if player == BLACK else 'black'
    move = {'x': col, 'y': row, 'color': color, 'next': gs['next'], 'action': action}
    gs['moves'].append(move.copy())
    winners = winning_players(board)
    winner = player if player in winners else other if other in winners else None
    if winner:
        gs['winner'] = 'black' if winner == BLACK else 'white'
        gs['reason'] = 'four'
    else:
        key = position_key(board, other)
        repetitions = gs.setdefault('repetitions', {initial: 1})
        repetitions[key] = repetitions.get(key, 0) + 1
        if repetitions[key] >= 3 or not legal_actions(board, other, gs.get('popout', False)):
            gs['winner'] = 'draw'
            gs['reason'] = 'repetition' if repetitions[key] >= 3 else 'draw'
    # A pop changes every occupied row in its column; clients use this authority.
    if action == 'pop':
        move['board'] = [line[:] for line in board]
    over = {'winner': gs['winner'], 'reason': gs['reason']} if gs.get('winner') else None
    return {'ok': True, 'move': move, 'game_over': over}
