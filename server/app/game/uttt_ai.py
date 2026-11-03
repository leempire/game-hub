"""公开视图上的三档 AI；同步搜索须由房间层放入线程池。"""

import random
import time

from app.game.uttt import LINES, _result, apply_move, legal_moves


_BUDGET = {"normal": 0.15, "hard": 0.5}
_WIN = 1_000_000
_POSITION = (3, 2, 3, 2, 4, 2, 3, 2, 3)


class _Timeout(Exception):
    pass


def normalize_difficulty(value) -> str:
    return value if isinstance(value, str) and value in ("easy", "normal", "hard") else "normal"


def _play(gs: dict, move: tuple[int, int]) -> dict:
    board, cell = move
    child = dict(gs)
    child["boards"] = gs["boards"][:]
    child["boards"][board] = gs["boards"][board][:]
    child["local_results"] = gs["local_results"][:]
    child["moves"] = gs["moves"][:]
    apply_move(child, board, cell, gs["next"])
    return child


def _has_macro_win(gs: dict) -> bool:
    """只检查能补全大盘连线的小盘，共用引擎的连线/封盘判定。"""
    if gs["winner"] is not None:
        return False
    stone = 1 if gs["next"] == "black" else 2
    targets = set()
    results = gs["local_results"][:]
    for board, result in enumerate(results):
        if result == 0:
            results[board] = stone
            if _result(results) == stone:
                targets.add(board)
            results[board] = 0
    for board, cell in legal_moves(gs):
        if board in targets:
            cells = gs["boards"][board][:]
            cells[cell] = stone
            if _result(cells) == stone:
                return True
    return False


def _lines_score(cells: list[int], stone: int, weights: tuple[int, ...]) -> int:
    opponent = 3 - stone
    score = 0
    for a, b, c in LINES:
        values = (cells[a], cells[b], cells[c])
        if 3 in values:
            continue
        mine = values.count(stone)
        theirs = values.count(opponent)
        if not theirs:
            score += weights[mine]
        if not mine:
            score -= weights[theirs]
    return score


def _evaluate(gs: dict, color: str) -> int:
    stone = 1 if color == "black" else 2
    results = gs["local_results"]
    score = _lines_score(results, stone, (0, 100, 2000, 10000))
    for board, result in enumerate(results):
        if result in (1, 2):
            score += (800 + 20 * _POSITION[board]) * (1 if result == stone else -1)
        elif result == 0:
            score += _lines_score(gs["boards"][board], stone, (0, 2, 18, 200))
            center = gs["boards"][board][4]
            score += 3 if center == stone else -3 if center == 3 - stone else 0
    if gs["active_board"] is None:
        score += 30 if color == gs["next"] else -30
    return score


def _search(gs: dict, depth: int, alpha: int, beta: int, deadline: float, ply: int) -> int:
    if time.monotonic() >= deadline:
        raise _Timeout
    winner = gs["winner"]
    if winner is not None:
        if winner == "draw":
            return 0
        return (_WIN - ply) * (1 if winner == gs["next"] else -1)
    if depth == 0:
        return _evaluate(gs, gs["next"])
    moves = legal_moves(gs)
    if not moves:
        return 0
    moves.sort(key=lambda move: _POSITION[move[1]] + _POSITION[move[0]], reverse=True)
    best = -2 * _WIN
    for move in moves:
        score = -_search(_play(gs, move), depth - 1, -beta, -alpha, deadline, ply + 1)
        best = max(best, score)
        alpha = max(alpha, best)
        if alpha >= beta:
            break
    return best


def best_move(view: dict, difficulty: str = "normal", time_budget: float | None = None,
              rng: random.Random | None = None) -> tuple[int, int] | None:
    """简单随机；普通两层；困难迭代加深，仅采纳完整搜索层。"""
    moves = legal_moves(view)
    if not moves:
        return None
    level = normalize_difficulty(difficulty)
    rng = rng or random
    if level == "easy":
        return rng.choice(moves)
    budget = _BUDGET[level] if time_budget is None else max(0.0, time_budget)
    deadline = time.monotonic() + budget
    rng.shuffle(moves)
    children = [(move, _play(view, move)) for move in moves]
    for move, child in children:
        if child["winner"] == view["next"]:
            return move
    # 有界战术检查先排除对手下一手大盘胜；超时也保留一个已知安全动作。
    safe = [(move, child) for move, child in children if not _has_macro_win(child)]
    children = safe or children
    children.sort(key=lambda item: _evaluate(item[1], view["next"]), reverse=True)
    best = children[0][0]
    for depth in range(2, 3 if level == "normal" else 82):
        alpha = -2 * _WIN
        iteration_best = best
        children.sort(key=lambda item: item[0] != best)
        try:
            for move, child in children:
                score = -_search(child, depth - 1, -2 * _WIN, -alpha, deadline, 1)
                if score > alpha:
                    alpha = score
                    iteration_best = move
        except _Timeout:
            break
        best = iteration_best
        if abs(alpha) >= _WIN - 81:
            break
    return best
