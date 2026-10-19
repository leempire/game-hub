"""军棋人机。

**入口只接受 `luzhanqi.view_for(gs, color)` 产出的视图** —— 也就是说人机拿到的信息
与真人**一模一样**，暗棋下它同样看不见对手的棋子身份（`p` 是 `None`）。这不是靠自觉：
AI 根本拿不到别的数据，`gs` 压根没传进来。

两种模式走两条路（v13 重写，调研见 `docs/游戏设计/军棋/增强调研.md` §6）：

- **明棋：alpha-beta 搜索**（迭代加深 + 时间预算）。明棋是**完全信息**博弈，
  之前只做「静态评估 + 一层威胁检查」，等于主动放弃了明棋最大的优势 ——
  能算两步的对手永远赢只会看一步的。现在走 negamax：吃子优先排序、
  无棋可走按判负处理（军棋的真实规则：**无棋可走判负**，不是和棋）。
- **暗棋：信念集 + 期望值**（不做搜索 —— 生成对手的着法就等于偷看它的身份：
  「哪些子能动」本身就暴露了「它不是地雷也不是军旗」）。
  这一版把信念集补成了两件事：
  ① **信念跟着棋子走**：`last_move` 里对手刚搬过的那枚**不可能是地雷/军旗**
     （那两个不能动），所以「刚动过的子可以放心撞」；
  ② **威胁按信念加权**：一个未知格子的威胁不再是「它可能是司令」这种一刀切，
     而是「它的候选身份里有几个能吃掉我，占几成」——
     否则一枚其实是地雷的邻居能把子力吓得一局不敢动（「有点呆」的主因）。

同步搜索必须由调用方丢进 `asyncio.to_thread`（同象棋 / 四子棋）。
"""

from __future__ import annotations

import random
import time

from app.luzhanqi import (
    CAMP_CELLS, COLS, DILEI, GONGBING, HQ_CELLS, IMMOBILE, JUNQI,
    JUNZHANG, LIANZHANG, LVZHANG, NEIGHBORS, PAIZHANG, PIECE_COUNTS, RANKS,
    RANK_VALUE, RED, ROWS, SHIZHANG, SILING, TUANZHANG, YINGZHANG, ZHADAN,
    _can_land, _engineer_rail_moves, _rail_slide_moves, all_moves, back_two_rows,
    other, piece_moves, resolve_combat,
)

_TIME_BUDGET = 0.8
#: 只对静态分最高的前 K 个着法做威胁检查（全量检查是 K × 一次着法生成，太慢）
_TOP_K = 14
#: 明棋搜索的迭代加深上限。**它不是硬性深度** —— 每一层开始前都看时间预算，
#: 超了就用上一层的结果（所以弱机器上它自然地只搜 1~2 层）。
_SEARCH_DEPTH = 4
#: 将死/判负的分数。够大但不会在求和时溢出成 inf。
_MATE = 100000.0

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


def _dead_enemy_ranks(view: dict, color: str) -> dict:
    """从战报里数出**对手已经阵亡的棋子**（只在身份公开时数得到）。

    暗棋 + `reveal=False` 时战报里根本没有 `defender_piece`，这份表会是空的 ——
    那是对的（经典暗棋里玩家确实只知道「谁出局」）。
    """
    dead: dict[str, int] = {}
    for battle in view.get("battles", []):
        for key, side in (("attacker_piece", "attacker"), ("defender_piece", "defender")):
            # 只有「对手那一枚」的阵亡才算；我自己的损失不影响对手的候选池
            if battle.get(side) == other(color) and battle.get(key):
                dead[battle[key]] = dead.get(battle[key], 0) + 1
    return dead


def _beliefs(board, color: str, view: dict) -> dict:
    """对手每个未知格子的**可能身份集合**（信念集）。

    三层收窄，缺一不可：

    1. **位置约束**：地雷只可能在最后两排、军旗只可能在大本营（两个都不动）。
    2. **交战结果**：`battles` 里那一次碰子的相对大小。
    3. **它刚动过**（v13）：`last_move` 是对手走的 ⇒ 它搬到的那个格子**绝不可能是
       地雷/军旗** —— 这条以前没有，于是「刚出门的那枚」在信念里仍然可能是地雷，
       AI 会吓得不敢用小兵去试探它（而试探一个走过路的子恰恰是安全的）。

    另外把**已经确认全部阵亡的兵种**从所有格子的候选里剔掉（对手只剩 25 枚，
    死光了的兵种不可能再出现在任何格子上）。
    """
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

    # ③ 刚走过路的棋子：两个「不动」的兵种都要从它的候选里去掉
    last = view.get("last_move") or {}
    if last.get("by") == enemy:
        to = tuple(last.get("to") or ())
        if to in cands:
            cands[to] = cands[to] - {DILEI, JUNQI}

    # ① / ② 用交战结果收窄。**只对「打完之后对手那枚还留在原地」的格子生效** ——
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

    # 兵种级收窄：把「已经全灭的兵种」从每个格子里剔掉
    dead = _dead_enemy_ranks(view, color)
    gone = {rank for rank, n in dead.items() if n >= PIECE_COUNTS.get(rank, 0)}
    if gone:
        for key, options in cands.items():
            left = options - gone
            if left:                     # 剔空了说明战报不完整，那就别剔（宁松勿错）
                cands[key] = left
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


def _potential_moves(board, r: int, c: int) -> list:
    """**未知敌子可能走到哪** —— 引擎的 `piece_moves` 只认已知身份（返回 []），
    暗棋里对手的子都是 `p=None`，直接调它等于「威胁表恒为空」。

    所以这里按「它最可能是哪种会动的子」取并集：公路一步 + 铁路直行 + 工兵的铁路
    拐弯。**会动的子是地雷与军旗之外的全部**，而那两个在信念里本来就被位置约束
    排掉了（最后两排之外没有地雷、大本营之外没有军旗）—— 这里是**上界估计**，
    威胁表宁可偏保守（偏保守 = 不敢乱冲，而不是送子）。
    """
    dests = [nxt for nxt in NEIGHBORS[(r, c)]["road"] if _can_land(board, other, *nxt)]
    if (r, c) in CAMP_CELLS:
        return dests                        # 行营里只能一步（与引擎同一条）
    dests.extend(_engineer_rail_moves(board, other, r, c))
    dests.extend(_rail_slide_moves(board, other, r, c))
    return dests


def _combat_value(attacker: str, defender: str) -> float:
    """一次交战对**攻击方**的价值（未知防守方可能是军旗 —— 那是直接赢）。

    引擎的 `resolve_combat` 不处理军旗（碰到军旗是「判胜」这条独立规则），
    所以这里必须先拦一道：不拦的话 `RANK_VALUE["junqi"]` 直接 KeyError
    （跑对局台时踩到的真崩溃）。
    """
    if defender == JUNQI:
        return VALUE[JUNQI]
    outcome = resolve_combat(attacker, defender)
    gain = VALUE.get(defender, 0)
    if outcome == "defender_dies":
        return gain
    if outcome == "both_die":
        return gain - VALUE.get(attacker, 0)
    return -VALUE.get(attacker, 0)


def _threat_map(board, color: str, beliefs: dict | None) -> dict:
    """我这边的每一格「会被吃掉多少价值」，**按信念加权**（v13）。

    明棋（`beliefs is None`）：对手的子都是已知的，取最大价值 —— 与原来一致。

    暗棋：`beliefs` 给的是每个未知格子**可能是什么**，所以这里算的是
    **期望损失**：`Σ p(b) × (能吃掉我时我丢的价值)`。原来那一版把「未知」一律
    当成最大的威胁（只要对手有子够得着就算危险），于是**一枚其实是地雷的邻居
    能让它一局不敢动** —— 这正是「人机有点呆」最直观的那一面。
    """
    enemy = other(color)
    hits: dict[tuple[int, int], float] = {}
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if not cell or cell["c"] != enemy:
                continue
            options = None if beliefs is None else beliefs.get((r, c))
            if beliefs is not None and not options:
                continue                      # 已知身份的子走下面那一支…（见下）
            moves = None
            if cell["p"] is not None:
                # 明棋 / 暗棋里已公开的（军旗亮出、或 reveal 模式）
                moves = piece_moves(board, r, c)
                for dst in moves:
                    target = board[dst[0]][dst[1]]
                    if target and target["c"] == color and target["p"] is not None:
                        hits[dst] = max(hits.get(dst, 0), VALUE.get(target["p"], 0))
                continue
            if options is None:
                continue
            # **会动的才谈得上威胁**：地雷与军旗不主动攻击，从攻击方候选里去掉
            attackers = options - IMMOBILE
            if not attackers:
                continue
            for dst in _potential_moves(board, r, c):
                target = board[dst[0]][dst[1]]
                if not target or target["c"] != color or target["p"] is None:
                    continue
                mine = target["p"]
                p = 1.0 / len(attackers)
                # ⚠️ **我自己的军旗也可能在这一格上**：`resolve_combat` 不认军旗
                # （碰到军旗是「判胜」那条独立规则），直接调它会 KeyError。
                # 而它恰恰是**最贵的那一格** —— 被吃到就是输。
                if mine == JUNQI:
                    hits[dst] = max(hits.get(dst, 0.0), p * sum(
                        1.0 for _a in attackers) * VALUE[JUNQI])
                    continue
                risk = 0.0
                for attacker in attackers:
                    if resolve_combat(attacker, mine) in ("defender_dies", "both_die"):
                        risk += p * VALUE.get(mine, 0)
                if risk:
                    hits[dst] = max(hits.get(dst, 0.0), risk)
    return hits


def _capture_expectation(my_piece: str, options: set) -> tuple[float, float]:
    """暗棋：攻击一个未知格子的**期望收益**与「我活下来」的概率（按可能身份均分）。"""
    if not options:
        return 0.0, 0.0
    p = 1.0 / len(options)
    delta = 0.0
    survive = 0.0
    for defender in options:
        if defender == JUNQI:
            # 撞上军旗 = 直接赢（引擎那条独立规则），价值按 `VALUE[JUNQI]` 算
            delta += p * VALUE[JUNQI]
            survive += p
            continue
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


# ------------------------------------------------------------------ 明棋：搜索


def _copy_board(board):
    return [list(row) for row in board]


def _apply(board, frm, to):
    """在一个**副本**上走一步，返回 `(新盘, 战报或 None)`。

    与 `luzhanqi._apply_move` 同一套碰子规则，但那份是原地改 `gs` 的（还会写战报、
    记 `no_capture`），搜索里要的是纯函数 —— 所以这里自己写一遍。
    **军旗被吃 = 终局**，所以调用方看到 `flag` 就要按将死处理。
    """
    nb = _copy_board(board)
    src = nb[frm[0]][frm[1]]
    dst = nb[to[0]][to[1]]
    if dst is None:
        nb[to[0]][to[1]] = src
        nb[frm[0]][frm[1]] = None
        return nb, None
    if dst["p"] == JUNQI:
        nb[to[0]][to[1]] = src
        nb[frm[0]][frm[1]] = None
        return nb, "flag"
    outcome = resolve_combat(src["p"], dst["p"])
    if outcome == "attacker_dies":
        nb[frm[0]][frm[1]] = None
    elif outcome == "defender_dies":
        nb[to[0]][to[1]] = src
        nb[frm[0]][frm[1]] = None
    else:
        nb[to[0]][to[1]] = None
        nb[frm[0]][frm[1]] = None
    return nb, outcome


def _eval_open(board, color: str) -> float:
    """明棋的局面评估（从 `color` 视角）。**只看子力 + 推进**，不搞玄学。

    为什么这么朴素：军棋的胜负几乎完全由「谁的大子还活着」+「谁的工兵先摸到
    军旗」决定，而搜索本身会把这两件事的后果算进去（吃子、将死都在叶子里）。
    """
    enemy = other(color)
    score = 0.0
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if not cell:
                continue
            v = VALUE.get(cell["p"], 0)
            if cell["c"] == color:
                score += v
                score += _advance_score((r, c), (r, c), cell["p"], color) * 0.0
            else:
                score -= v
    # 推进：把「离对方大本营更近」折算成小分（0.6/格 与暗棋那一版同一个量级）
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if not cell or cell["c"] != color or cell["p"] in (DILEI, JUNQI):
                continue
            hqs = [(11, 1), (11, 3)] if color == RED else [(0, 1), (0, 3)]
            d = min(abs(r - h[0]) + abs(c - h[1]) for h in hqs)
            w = 2.0 if cell["p"] == GONGBING else 1.0
            score += (24 - d) * 0.25 * w
    return score


def _ordered_moves(board, side: str):
    """着法生成 + 排序：**吃子优先**（按被吃子力倒序），其次推进。

    排序是 alpha-beta 能不能剪枝的关键（先搜好着法，剪枝率天差地别）。
    """
    moves = list(all_moves(board, side))
    scored = []
    for frm, to in moves:
        src = board[frm[0]][frm[1]]
        dst = board[to[0]][to[1]]
        s = 0.0
        if dst is not None and src is not None:
            s = VALUE.get(dst["p"], 0) * 10 - VALUE.get(src["p"], 0)
        elif src is not None:
            s = _advance_score(frm, to, src["p"], side)
        scored.append((s, frm, to))
    scored.sort(key=lambda x: -x[0])
    return [(frm, to) for _, frm, to in scored]


def _negamax(board, side: str, root: str, depth: int, alpha: float, beta: float,
             deadline: float) -> float:
    """negamax + alpha-beta，返回**轮到 `side` 走时的局面分**（从 `side` 视角）。

    ⚠️ **无棋可走判负**（军棋的真实规则，不是象棋那种困毙判负的变体 —— 这里
    它就是「判负」）：所以轮到谁而没有着法，就是谁输，直接回 `-_MATE`。
    这条不写的话，搜索会以为「站着不动」是安全的选择。
    """
    if time.monotonic() > deadline:
        return _eval_open(board, side)
    moves = _ordered_moves(board, side)
    if not moves:
        return -_MATE + depth                      # 越早被杀越好（给搜索一点梯度）
    if depth <= 0:
        return _eval_open(board, side)
    best = -float("inf")
    for frm, to in moves:
        nb, res = _apply(board, frm, to)
        if res == "flag":
            return _MATE - depth                   # 吃掉军旗 = 立刻赢
        val = -_negamax(nb, other(side), root, depth - 1, -beta, -alpha, deadline)
        if val > best:
            best = val
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break                                  # 剪枝
    return best


def _search_best(board, color: str, legal: list, deadline: float, rng: random.Random):
    """明棋：迭代加深，每一层都可能被时间掐掉（那就用上一层的结论）。"""
    moves = [(int(m[0]), int(m[1]), int(m[2]), int(m[3])) for m in legal]
    # 能吃军旗的着法直接走（不必搜）
    for fr, fc, tr, tc in moves:
        target = board[tr][tc]
        if target and target["p"] == JUNQI:
            return (fr, fc), (tr, tc)
    best_move = None
    best_score = -float("inf")
    for depth in range(1, _SEARCH_DEPTH + 1):
        if time.monotonic() > deadline:
            break
        local_best, local_score = None, -float("inf")
        # 浅一层的最优着法先搜（排序），剪枝率更高
        ordered = sorted(
            moves,
            key=lambda m: 0 if best_move is None
            else (0 if (m[0], m[1], m[2], m[3]) ==
                  (best_move[0][0], best_move[0][1], best_move[1][0], best_move[1][1])
                  else 1),
        )
        for fr, fc, tr, tc in ordered:
            nb, res = _apply(board, (fr, fc), (tr, tc))
            if res == "flag":
                return (fr, fc), (tr, tc)
            val = -_negamax(nb, other(color), color, depth - 1,
                            -float("inf"), -local_score, deadline)
            if val > local_score:
                local_score, local_best = val, ((fr, fc), (tr, tc))
            if time.monotonic() > deadline:
                break
        if local_best is not None:
            best_move, best_score = local_best, local_score
    return best_move


# ------------------------------------------------------------------ 打分（暗棋那条路）


def _score_move(board, frm, to, piece, color, target, beliefs, enemy_hits, rng) -> float:
    """给一个候选着法打分：吃子期望收益 + 推进奖励 − 送死惩罚 + 一点随机。"""
    if beliefs is not None and target and target["p"] is None:
        delta, survive = _capture_expectation(piece, beliefs.get(to, set(PIECE_COUNTS)))
    elif target:
        delta, survive = _capture_expectation(piece, {target["p"]})
    else:
        delta, survive = 0.0, 1.0
    score = delta + _advance_score(frm, to, piece, color) * 0.6
    # 大子往对手的火力里送要扣分。**代价按信念加权**（v13）：明棋是确定值、
    # 暗棋是期望值（见 `_threat_map`）—— 后者不会再被「可能是司令」的虚影吓住。
    threat = enemy_hits.get(to, 0)
    if survive > 0 and threat > VALUE.get(piece, 0) * 0.8:
        score -= threat * 0.5
    # 工兵朝对方最后两排走 = 去挖雷，额外加分
    if piece == GONGBING and to in back_two_rows(other(color)):
        score += 6.0
    # **撞一个「刚走过路」的未知子**（不可能是雷/旗）＝安全的试探，给点奖励
    if beliefs is not None and target and target["p"] is None:
        options = beliefs.get(to, set())
        if options and not (options & {DILEI, JUNQI}):
            score += 2.0
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
    hidden = view.get("mode", "open") == "hidden"

    # 能吃军旗就直接赢（两种模式共用；搜索里也有这条，但先拦一下省时间）
    for m in legal:
        if view["cells"][int(m[2])][int(m[3])] is not None:
            cell = view["cells"][int(m[2])][int(m[3])]
            if cell.get("p") == JUNQI:
                return ((int(m[0]), int(m[1])), (int(m[2]), int(m[3])))

    if not hidden:
        # **明棋：搜索**（完全信息，不该只会看一步）
        move = _search_best(board, color, legal, deadline, rng)
        if move is not None:
            return move
        # 搜索被时间掐掉且一步都没算出来（几乎不可能）→ 退回静态打分
    beliefs = _beliefs(board, color, view) if hidden else None
    enemy_hits = _threat_map(board, color, beliefs)

    scored = []
    for move in legal:
        frm = (int(move[0]), int(move[1]))
        to = (int(move[2]), int(move[3]))
        cell = board[frm[0]][frm[1]]
        if not cell or cell["p"] is None:
            continue
        target = board[to[0]][to[1]]
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
    _, frm, to = near[rng.randrange(len(near))]
    return frm, to