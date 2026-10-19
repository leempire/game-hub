"""Aeroplane Chess (飞行棋) AI — capture-first greedy with danger avoidance.

Interface: best_move(gs) -> piece_id | None
gs: game state dict from ludo.create_game(); valid when phase == "move".

只做「选哪架飞机走」这一个决策——掷骰子是纯随机，没有选择空间。
对每个可动飞机算一个启发式分数，取最高分：

1. 撞机（+120/架 + 对手进度加成）：把对手踢回基地，收益最大
2. 精确到达终点（+90）：多一架到达，直接推进排名
3. 起飞（+45）：掷 6 时把基地飞机放出来，否则 6 白白浪费
4. 进入终点航道（+35）：进度 > RING_END 后不会被撞，安全区
5. 基础分（+0.6 × 新进度）：同等条件下推最靠前的那架
6. 落点风险（−18/个威胁）：落点在主轨道、且对手有飞机在其后 1-6 步
   （能精确踩到）时扣分，避免主动送人头

「新进度」一律走引擎的 resolve_landing：同色跳格与虚线飞行是强制触发的，
算成 old+dice 会低估收益、也算错落点危不危险。
"""

from app.game.ludo import (
    PIECES_PER_PLAYER,
    RING,
    RING_END,
    FINISH_PROGRESS,
    get_global_position,
    resolve_landing,
    enemy_stacks_at,
)

W_CAPTURE = 120.0
W_CAPTURE_PROGRESS = 0.5
W_EXACT_FINISH = 90.0
W_TAKEOFF = 45.0
W_HOME_STRETCH = 35.0
W_PROGRESS = 0.6
W_DANGER = 18.0


def best_move(gs: dict) -> int | None:
    """Pick the piece to move for the current seat. Returns piece_id or None."""
    movable = gs.get("movable_pieces") or []
    if not movable or gs.get("phase") != "move":
        return None
    seat = gs["current_seat"]
    dice = gs["dice_value"]
    if dice is None:
        return None
    scored = [
        (_score_piece(gs, seat, pid, dice), pid)
        for pid in movable
    ]
    # 同分时选下标小的，保证行为可复现（测试友好）
    best_score, best_pid = max(scored, key=lambda t: (t[0], -t[1]))
    return best_pid


def _score_piece(gs: dict, seat: int, pid: int, dice: int) -> float:
    player = gs["players"][seat]
    old = player["pieces"][pid]
    new = _destination(gs, seat, old, dice)

    score = 0.0

    if old == -1:
        score += W_TAKEOFF
    elif new == FINISH_PROGRESS:
        score += W_EXACT_FINISH
    if new > RING_END:
        score += W_HOME_STRETCH

    score += new * W_PROGRESS

    # 撞机只发生在赛道
    if 0 <= new <= RING_END:
        gpos = get_global_position(seat, new)
        captured = _pieces_at_global(gs, seat, gpos)
        if captured:
            score += W_CAPTURE * len(captured)
            score += W_CAPTURE_PROGRESS * sum(p for _, p in captured)
        score -= W_DANGER * _threat_count(gs, seat, gpos)

    return score


def _destination(gs: dict, seat: int, old: int, dice: int) -> int:
    """落点：基地(-1) 掷 6 → 起飞点；超出终点折返；之后还要按跳子/飞棋结算。

    跳/飞是强制触发的，评估时不能只看 `old + dice`——踩到同色格真实落点会
    再往前 4 格，踩到虚线格更是直接跨 12 格，忽略的话 AI 会算错收益与危险。
    """
    if old == -1:
        return 0
    landed = _walk_end(old, dice)
    if landed == FINISH_PROGRESS:
        return landed
    final, _path, _segments = resolve_landing(gs, seat, landed)
    if enemy_stacks_at(gs, seat, final):
        return -1   # 迭子同归于尽：自己也回基地，收益是负的
    return final


def _walk_end(old: int, dice: int) -> int:
    """掷骰子走完的落点（超出终点按规则折返）。"""
    new = old + dice
    if new > FINISH_PROGRESS:
        return FINISH_PROGRESS - (new - FINISH_PROGRESS)
    return new


def _pieces_at_global(gs: dict, seat: int, gpos: int) -> list[tuple[int, int]]:
    """返回 (seat, progress) 列表：落在全局格 gpos 上的对手飞机。"""
    hits = []
    for other_seat, other in gs["players"].items():
        if other_seat == seat or other.get("finished"):
            continue
        for opid in range(PIECES_PER_PLAYER):
            op = other["pieces"][opid]
            if 0 <= op <= RING_END and get_global_position(other_seat, op) == gpos:
                hits.append((other_seat, op))
    return hits


def _threat_count(gs: dict, seat: int, gpos: int) -> int:
    """数一数有多少对手飞机能在 1-6 步内精确踩到 gpos。

    额外要求 op + gap <= RING_END：对手若因此走进终点航道，则不会在赛道
    落点发生撞机（_check_capture 只对 new_progress <= RING_END 生效）。
    """
    threats = 0
    for other_seat, other in gs["players"].items():
        if other_seat == seat or other.get("finished"):
            continue
        for opid in range(PIECES_PER_PLAYER):
            op = other["pieces"][opid]
            if not (0 <= op <= RING_END):
                continue
            og = get_global_position(other_seat, op)
            gap = (gpos - og) % RING
            if 1 <= gap <= 6 and op + gap <= RING_END:
                threats += 1
    return threats
