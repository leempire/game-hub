"""**旧版军棋人机的冻结快照**（v13 之前的 `app/luzhanqi_ai.py`，逐字复制）。

为什么留一份在版本库里：`增强调研.md` §6 要回答「新 AI 到底强了多少」，而
「新旧对打」是唯一能把这件事量出来的办法 —— 与深空休眠 `balance_stasis.py`
的「同策略对照」是同一条做法。**不要改这份文件**（它的价值就在于它是旧版），
要在它上面做实验就再复制一份。
"""

from __future__ import annotations

import random
import time

from app.luzhanqi import (
    BLACK, COLS, DILEI, GONGBING, HQ_CELLS, JUNQI, JUNZHANG, LIANZHANG, LVZHANG,
    PAIZHANG, PIECE_COUNTS, RANKS, RANK_VALUE, RED, ROWS, SHIZHANG, SILING,
    TUANZHANG, YINGZHANG, ZHADAN, back_two_rows, other, piece_moves, resolve_combat,
)

_TIME_BUDGET = 0.8
#: 只对静态分最高的前 K 个着法做威胁检查（全量检查是 K × 一次着法生成，太慢）
_TOP_K = 14

#: 子力价值。工兵高于排长/连长：它既能挖雷又是唯一能在铁路上拐弯的兵。
VALUE = {
    SILING: 100, JUNZHANG: 82, SHIZHANG: 60, LVZHANG: 45, TUANZHANG: 34,
    YINGZHANG: 24, LIANZHANG: 16, PAIZHANG: 11, GONGBING: 15,
    ZHADAN: 34, DILEI: 10, JUNQI: 1000,
}


# ------------------------------------------------------------------ 信念集


def _narrow_by_relation(options: set, mine: str, rel: int) -> set:
    """按一次交战的结果收窄「对手那枚」的可能身份。

    ``rel``：+1 = 对手那枚比我的强，-1 = 比我的弱，0 = 同归于尽（同级 / 有炸弹）。
    """
    if mine not in RANK_VALUE:
        return options
    mv = RANK_VALUE[mine]
    if rel > 0:
        keep = {p for p in RANKS if RANK_VALUE[p] > mv} | {ZHADAN}
        if mine != GONGBING:            # 工兵会吃掉地雷，其它子撞上去是自己死
            keep |= {DILEI}
    elif rel < 0:
        keep = {p for p in RANKS if RANK_VALUE[p] < mv}
        if mine == GONGBING:
            keep |= {DILEI}
    else:
        keep = {p for p in RANKS if RANK_VALUE[p] == mv} | {ZHADAN}
        if mine == ZHADAN:
            keep |= {DILEI}
    return options & keep if options & keep else options


def _beliefs(board, color: str, view: dict) -> dict:
    """对手每个未知格子的可能身份集合。"""
    enemy = other(color)
    cands: dict[tuple[int, int], set] = {}
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if not cell or cell["c"] != enemy or cell["p"] is not None:
                continue
            options = set(PIECE_COUNTS)
            if (r, c) not in back_two_rows(enemy):
                options.discard(DILEI)      # 地雷不能动，只会在最后两排
            if (r, c) not in HQ_CELLS:
                options.discard(JUNQI)      # 军旗只在大本营，也不动
            cands[(r, c)] = options
    # 用交战结果收窄。**只对「打完之后对手那枚还留在原地」的格子生效** ——
    # 那个格子现在仍然是未知敌子，是同一枚的概率很高；阵亡的棋子所在格已经空了
    # 或被我方占了，本来就不在 cands 里。
    for battle in view.get("battles", []):
        at = tuple(battle.get("at", ()))
        if at not in cands:
            continue
        i_attacked = battle.get("attacker") == color
        mine = battle.get("attacker_piece") if i_attacked else battle.get("defender_piece")
        if mine is None:
            continue
        outcome = battle.get("outcome")
        if i_attacked:
            rel = {"defender_dies": -1, "attacker_dies": 1, "both_die": 0}.get(outcome)
        else:
            rel = {"attacker_dies": 1, "defender_dies": -1, "both_die": 0}.get(outcome)
        if rel is None:
            continue
        cands[at] = _narrow_by_relation(cands[at], mine, rel)
    return cands


# ------------------------------------------------------------------ 局面工具


def _enemy_attack_cells(board, color: str) -> dict:
    """对手一步能打到的格子 → 打到时能吃掉我方的子力价值（取最大）。"""
    enemy = other(color)
    hits: dict[tuple[int, int], int] = {}
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if not cell or cell["c"] != enemy:
                continue
            for dst in piece_moves(board, r, c):
                target = board[dst[0]][dst[1]]
                if target and target["c"] == color and target["p"] is not None:
                    hits[dst] = max(hits.get(dst, 0), VALUE.get(target["p"], 0))
    return hits


def _capture_expectation(my_piece: str, options: set) -> tuple[float, float]:
    """暗棋：攻击一个未知格子的**期望收益**与「我活下来」的概率（按可能身份均分）。"""
    if not options:
        return 0.0, 0.0
    p = 1.0 / len(options)
    delta = 0.0
    survive = 0.0
    for defender in options:
        outcome = resolve_combat(my_piece, defender)
        gain = VALUE.get(defender, 0)
        if outcome == "defender_dies":
            delta += p * gain
            survive += p
        elif outcome == "both_die":
            delta += p * (gain - VALUE.get(my_piece, 0))
        else:
            delta -= p * VALUE.get(my_piece, 0)
    return delta, survive


def _advance_score(frm, to, piece: str, color: str) -> float:
    """朝对方大本营推进的奖励 —— 军棋的胜负点就在那两格上。"""
    hqs = [(11, 1), (11, 3)] if color == RED else [(0, 1), (0, 3)]
    before = min(abs(frm[0] - h[0]) + abs(frm[1] - h[1]) for h in hqs)
    after = min(abs(to[0] - h[0]) + abs(to[1] - h[1]) for h in hqs)
    weight = 2.0 if piece == GONGBING else 1.0
    return (before - after) * weight


# ------------------------------------------------------------------ 主入口


def _score_move(board, frm, to, piece, color, target, beliefs, enemy_hits, rng) -> float:
    """给一个候选着法打分：吃子期望收益 + 推进奖励 − 送死惩罚 + 一点随机。"""
    if beliefs is not None and target and target["p"] is None:
        delta, survive = _capture_expectation(piece, beliefs.get(to, set(PIECE_COUNTS)))
    elif target:
        delta, survive = _capture_expectation(piece, {target["p"]})
    else:
        delta, survive = 0.0, 1.0
    score = delta + _advance_score(frm, to, piece, color) * 0.6
    # 大子往对手的火力里送要扣分（暗棋下对手的子力是未知的，按「最坏情况」估）
    threat = enemy_hits.get(to, 0)
    if survive > 0 and threat > VALUE.get(piece, 0) * 0.8:
        score -= threat * 0.5
    # 工兵朝对方最后两排走 = 去挖雷，额外加分
    if piece == GONGBING and to in back_two_rows(other(color)):
        score += 6.0
    return score + rng.random() * 0.4            # 打散同分着法，别每局都走同一步


def best_move(view: dict, time_budget: float = _TIME_BUDGET):
    """给视图里那一方算一步棋，返回 ``((fr,fc),(tr,tc))`` 或 ``None``。"""
    legal = view.get("legal") or []
    color = view.get("your_color") or ""
    if not legal or not color:
        return None
    board = [list(row) for row in view.get("cells") or []]
    if len(board) != ROWS:
        return None
    deadline = time.monotonic() + time_budget
    rng = random.Random()
    beliefs = _beliefs(board, color, view) if view.get("mode", "open") == "hidden" else None
    enemy_hits = _enemy_attack_cells(board, color)

    scored = []
    for move in legal:
        frm = (int(move[0]), int(move[1]))
        to = (int(move[2]), int(move[3]))
        cell = board[frm[0]][frm[1]]
        if not cell or cell["p"] is None:
            continue
        target = board[to[0]][to[1]]
        if target and target["p"] == JUNQI:
            return frm, to                       # 能吃军旗就直接赢
        scored.append((
            _score_move(board, frm, to, cell["p"], color, target, beliefs, enemy_hits, rng),
            frm, to,
        ))
        if time.monotonic() > deadline:
            break

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best = scored[0][0]
    near = [s for s in scored[:max(1, min(_TOP_K, len(scored)))] if best - s[0] <= 2.0]
    _, frm, to = rng.choice(near)
    return frm, to
