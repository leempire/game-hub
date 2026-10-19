"""Connect Four AI — negamax + alpha-beta + iterative deepening.

Interface: best_move(board, player, time_budget=0.5) -> int | None
Board: list[list[int]] (6×7), board[row][col], EMPTY=0 / BLACK=1 / WHITE=2.
返回**列号**（落点行由重力决定）。

调用方注意：搜索是同步的，0.5s 直接 `await` 会卡住整个进程的事件循环——
`ws.py` 里要跟象棋 AI 一样丢进 `asyncio.to_thread` 并传盘面副本。
"""

import random
import time

from app.connect4 import (
    COLS, ROWS, EMPTY, BLACK, WHITE,
    landing_row, check_win, legal_columns, would_win_at,
)

_TIME_BUDGET = 0.5
_WIN_SCORE = 1_000_000

# 中列优先：既是标准走子顺序（中列参与的四连最多），也让同分选择稳定可预期。
_ORDER = [3, 2, 4, 1, 5, 0, 6]

# 四格窗口评分：窗口里我方 1/2/3/4 颗子且对方未占该窗口时的价值。
_SCORES = {1: 1, 2: 60, 3: 900, 4: 100000}
# 对方的窗口权重略高：同等的三连，堵比铺更急。
_OPP_SCORES = {1: 1, 2: 80, 3: 1200, 4: 100000}


class _Timeout(Exception):
    """迭代加深超时：丢弃这一层，沿用上一层的结果。"""


def _build_windows() -> list[list[tuple[int, int]]]:
    """预计算全部 69 个四格窗口（横 24 / 竖 21 / 斜 24）。"""
    windows = []
    for r in range(ROWS):
        for c in range(COLS - 3):
            windows.append([(r, c + i) for i in range(4)])
    for r in range(ROWS - 3):
        for c in range(COLS):
            windows.append([(r + i, c) for i in range(4)])
    for r in range(ROWS - 3):
        for c in range(COLS - 3):
            windows.append([(r + i, c + i) for i in range(4)])
            windows.append([(r + 3 - i, c + i) for i in range(4)])
    return windows


_WINDOWS = _build_windows()


def _opp(color: int) -> int:
    return WHITE if color == BLACK else BLACK


def _heights_of(board: list[list[int]]) -> list[int]:
    """每列已填格数（棋子都在列底，所以列高就够描述盘面了）。"""
    return [sum(1 for r in range(ROWS) if board[r][c] != EMPTY) for c in range(COLS)]


def _pick(candidates: list[int]) -> int:
    """中列优先地挑一个。"""
    for c in _ORDER:
        if c in candidates:
            return c
    return candidates[0]


def evaluate(board: list[list[int]], me: int) -> int:
    """静态评估，以 `me` 的视角。对方占住的窗口直接作废。"""
    opp = _opp(me)
    score = 0
    for window in _WINDOWS:
        mine = theirs = 0
        for r, c in window:
            v = board[r][c]
            if v == me:
                mine += 1
            elif v == opp:
                theirs += 1
        if mine and theirs:
            continue
        if mine:
            score += _SCORES[mine]
        elif theirs:
            score -= _OPP_SCORES[theirs]
    return score


def _search(board, heights, color, depth, alpha, beta, deadline, me) -> int:
    """negamax，返回**当前走子方** `color` 视角的分数。"""
    if time.monotonic() > deadline:
        raise _Timeout
    if depth == 0:
        return evaluate(board, me) if color == me else -evaluate(board, me)

    best = -_WIN_SCORE * 2
    for c in _ORDER:
        if heights[c] >= ROWS:
            continue
        row = ROWS - 1 - heights[c]
        board[row][c] = color
        heights[c] += 1
        if check_win(board, row, c):
            # 能立刻连成四子：越早赢越好，用 depth 当"剩余深度"给分
            score = _WIN_SCORE + depth
        else:
            score = -_search(board, heights, _opp(color), depth - 1,
                             -beta, -alpha, deadline, me)
        heights[c] -= 1
        board[row][c] = EMPTY
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _root_scores(board, heights, player, depth, deadline) -> dict[int, int]:
    """根节点逐列算分（alpha 在根共享，非最优列可能是上界，取 max 仍然正确）。"""
    opp = _opp(player)
    scores: dict[int, int] = {}
    alpha = -_WIN_SCORE * 2
    for c in _ORDER:
        if heights[c] >= ROWS:
            continue
        row = ROWS - 1 - heights[c]
        board[row][c] = player
        heights[c] += 1
        if check_win(board, row, c):
            score = _WIN_SCORE + depth
        else:
            score = -_search(board, heights, opp, depth - 1,
                             -_WIN_SCORE * 2, -alpha, deadline, player)
        heights[c] -= 1
        board[row][c] = EMPTY
        scores[c] = score
        if score > alpha:
            alpha = score
    return scores


def best_move(board: list[list[int]], player: int, time_budget: float = _TIME_BUDGET) -> int | None:
    """返回落子列号；盘面已满返回 None。"""
    cols = legal_columns(board)
    if not cols:
        return None

    # 一步能赢就直接赢（不去搜，时间不足时也绝不会看漏）
    for c in cols:
        if would_win_at(board, c, player):
            return c
    # 对方一步能赢就先堵；中列优先（同分时选更靠中的那条）
    blocks = [c for c in cols if would_win_at(board, c, _opp(player))]
    if blocks:
        return _pick(blocks)

    working = [row[:] for row in board]
    heights = _heights_of(board)
    deadline = time.monotonic() + time_budget
    best = _pick(cols)

    # 迭代加深：每层搜完才采纳，超时的那层整层丢弃（拿到的分是残缺的）
    for depth in range(2, 14):
        try:
            scores = _root_scores(working, heights, player, depth, deadline)
        except _Timeout:
            break
        top = max(scores.values())
        best = _pick([c for c in cols if scores.get(c) == top])
        if top >= _WIN_SCORE:
            break  # 已经算出必胜，不必再深
    return best


def random_move(board: list[list[int]], rng: random.Random | None = None) -> int | None:
    """随机走子（单测用：验证 AI 对随机玩家不输）。"""
    cols = legal_columns(board)
    if not cols:
        return None
    return (rng or random).choice(cols)