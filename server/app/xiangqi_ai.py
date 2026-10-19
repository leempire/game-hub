"""中国象棋人机 AI —— negamax + alpha-beta + 迭代加深，纯函数、无 IO。

入口 ``best_move(board, color)`` 返回 ``((fr,fc),(tr,tc))``；无着可走时返回 ``None``。

两条实现上的取舍：

- **硬性时间预算**（``_TIME_BUDGET``）。象棋分支因子约 40，Python 里深度 4 全搜
  要几十秒——不设预算的话调用方会被卡死（调用方在 asyncio 里，见
  ``ws._bot_xiangqi_move``，那边还额外丢进线程池，别阻塞事件循环）。迭代加深
  保证「预算烧完时手上总有一个完整的浅层结果」。
- **同分随机**。等价的着法里随机挑一个，否则人机会每局下出完全相同的一盘棋。
"""

import random
import time

from app.xiangqi import (
    ROWS,
    all_legal_moves,
    apply_move,
    other,
    type_of,
    color_of,
    undo_move,
)

#: 子力价值（象棋常用量级；兵过河后另有位置加成，见 `_pos_bonus`）。
_VALUES = {"K": 10000, "R": 900, "C": 450, "N": 400, "B": 200, "A": 200, "P": 100}

_INF = 10 ** 6
_MATE = 10 ** 5
#: 迭代加深的最大层数（实际通常到不了——先撞时间预算）。
_MAX_DEPTH = 5
#: 单步搜索时间上限（秒）。人机的「思考延迟」另由 room_manager 控制（0.8~1.2s）。
_TIME_BUDGET = 0.9


class _Timeout(Exception):
    """搜索超时。用异常从递归深处直接跳出来，比逐层查返回值干净。"""


def _pos_bonus(kind: str, r: int, c: int, color: str) -> int:
    """位置分。行号一律归一到「红方视角」（9 = 自家底线）再算，黑方镜像。"""
    rr = r if color == "red" else ROWS - 1 - r
    if kind == "P":
        bonus = (6 - rr) * 12                      # 越靠前越好
        if rr <= 4:                                # 已过河：能横走，价值跳一档
            bonus += 70
        if rr <= 2 and 2 <= c <= 6:                # 逼近九宫
            bonus += 40
        return bonus
    if kind in ("N", "C"):
        return 12 if 2 <= c <= 6 else 0            # 中路更活
    if kind == "R":
        return 20 if rr <= 4 else 0                # 车过河
    return 0


def _evaluate(board, color: str) -> int:
    """静态评估（color 视角）。"""
    score = 0
    for r in range(ROWS):
        for c in range(len(board[0])):
            piece = board[r][c]
            if piece is None:
                continue
            owner = color_of(piece)
            value = _VALUES[type_of(piece)] + _pos_bonus(type_of(piece), r, c, owner)
            score += value if owner == color else -value
    return score


def _capture_score(board, move) -> int:
    """吃子价值（MVV-LVA）：吃大子优先，同等优先用小子吃。"""
    (fr, fc), (tr, tc) = move
    victim = board[tr][tc]
    if victim is None:
        return 0
    return _VALUES[type_of(victim)] * 10 - _VALUES[type_of(board[fr][fc])]


def _order_moves(board, moves):
    """先按吃子价值排序；**同分内部保持随机**——这样等价的着法不会被固定选同一个。

    先 shuffle 再稳定排序，等价于「按吃子价值分组、组内随机」。
    """
    shuffled = list(moves)
    random.shuffle(shuffled)
    return sorted(shuffled, key=lambda mv: _capture_score(board, mv), reverse=True)


def _negamax(board, color: str, depth: int, alpha: int, beta: int, deadline: float):
    """返回 (score, best_move)；超时抛 `_Timeout`。"""
    if time.time() > deadline:
        raise _Timeout
    if depth <= 0:
        return _evaluate(board, color), None
    moves = all_legal_moves(board, color)
    if not moves:
        # 无着可走：被将死或困毙，**象棋里两者都判负**。
        # 减去 depth 让「更早将死对手」的分更高。
        return -_MATE + (10 - depth), None
    best_move = None
    for frm, to in _order_moves(board, moves):
        captured = apply_move(board, frm, to)
        try:
            score, _ = _negamax(board, other(color), depth - 1, -beta, -alpha, deadline)
            score = -score
        finally:
            undo_move(board, frm, to, captured)
        if score > alpha:
            alpha = score
            best_move = (frm, to)
        if alpha >= beta:
            break
    return alpha, best_move


def _root_search(board, color: str, moves, depth: int, deadline: float):
    """根节点：返回本层全部最优着法（同分并列），供随机挑选。

    窗口下界取 ``alpha - 1`` 而不是 ``alpha``——这样「与当前最好同分」的着法
    仍会被完整搜索（不会被剪成上界），并列才认得出来；比它差的照旧被剪掉。
    """
    alpha = -_INF
    results = []
    for frm, to in moves:
        captured = apply_move(board, frm, to)
        try:
            score, _ = _negamax(board, other(color), depth - 1, -_INF, -(alpha - 1), deadline)
            score = -score
        finally:
            undo_move(board, frm, to, captured)
        results.append((score, (frm, to)))
        if score > alpha:
            alpha = score
    best = max(s for s, _ in results)
    return [(s, mv) for s, mv in results if s == best]


def best_move(board, color: str, time_budget: float = _TIME_BUDGET):
    """给 color 方算一步棋。返回 ``((fr,fc),(tr,tc))`` 或 None（无着可走）。

    **不改动传入的 board**（走子后一律撤销）——调用方可能正拿着房间里的盘面。
    """
    moves = all_legal_moves(board, color)
    if not moves:
        return None
    if len(moves) == 1:
        return moves[0]
    deadline = time.time() + time_budget
    ordered = _order_moves(board, moves)
    best = ordered[0]
    for depth in range(1, _MAX_DEPTH + 1):
        try:
            tier = _root_search(board, color, ordered, depth, deadline)
        except _Timeout:
            break
        if tier:
            best = random.choice([mv for _, mv in tier])
            if tier[0][0] >= _MATE - 100:      # 已经看到杀棋，不必再深
                break
        if time.time() > deadline:
            break
    return best
