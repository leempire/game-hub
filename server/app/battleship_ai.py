"""Battleship AI — three difficulty tiers (v1.10).

| 档位 | 搜索策略 | 命中后 | 参考平均发数 |
|---|---|---|---|
| easy 简单 | 棋盘格 parity 随机 | 随机打相邻格 | ~50 |
| normal 普通 | parity 随机 + 最小未沉舰过滤 | 线段延伸 | ~45 |
| hard 困难 | **概率热力图**（统计每格被多少种剩余舰位覆盖） | 候选按热力排序 | ~42 |

参考档位划分见 docs/游戏设计/海战棋/增强调研.md（sinkships 的五档阶梯）。

Single-step decision must complete in <100ms — 热力图是 O(舰数 × 100格 × 舰长)，
盘面只有 10×10，实测远低于预算。
"""

import random
from app.battleship import (
    EMPTY, MISS, HIT, SALVO_SHOTS, board_size_of, fleet_of,
    get_ship_cells,
)

# Adjacent offsets: right, left, down, up
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

DIFFICULTIES = ("easy", "normal", "hard")
DIFFICULTY_LABELS = {"easy": "简单", "normal": "普通", "hard": "困难"}
DEFAULT_DIFFICULTY = "normal"


def normalize_difficulty(value) -> str:
    """Coerce an arbitrary client value to a known difficulty tier."""
    return value if value in DIFFICULTIES else DEFAULT_DIFFICULTY


def best_move(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    opp_ships: list[dict],
    difficulty: str = DEFAULT_DIFFICULTY,
) -> tuple[int, int]:
    """Choose the best cell to fire at.

    attack_board: our view of opponent's board (0=unknown, MISS=-1, HIT=-2)
    opp_board: opponent's placement board (for checking sunk status)
    opp_ships: opponent's ship definitions (list of {id, x, y, horizontal})
    difficulty: "easy" | "normal" | "hard"

    Returns (x, y) — guaranteed to be an unfired cell.
    """
    level = normalize_difficulty(difficulty)
    unsunk_hits = _find_unsunk_hits(attack_board, opp_board)
    if unsunk_hits:
        if level == "easy":
            return _target_easy(attack_board, unsunk_hits)
        if level == "hard":
            return _target_weighted(attack_board, opp_board, unsunk_hits)
        return _target_mode(attack_board, opp_board, unsunk_hits)
    if level == "hard":
        return _heatmap_hunt(attack_board, opp_board)
    if level == "easy":
        return _hunt_easy(attack_board)
    return _hunt_mode(attack_board, opp_board, opp_ships)


def _find_unsunk_hits(
    attack_board: list[list[int]], opp_board: list[list[int]],
) -> list[tuple[int, int]]:
    """Find HIT cells that belong to not-yet-sunk ships."""
    sunk_ids = _sunk_ids(attack_board, opp_board)
    unsunk: list[tuple[int, int]] = []
    for y in range(board_size_of(attack_board)):
        for x in range(board_size_of(attack_board)):
            if attack_board[y][x] == HIT:
                ship_id = opp_board[y][x]
                if ship_id not in sunk_ids:
                    unsunk.append((x, y))
    return unsunk


def _target_mode(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    unsunk_hits: list[tuple[int, int]],
) -> tuple[int, int]:
    """Pick an adjacent cell of an unsunk hit to fire at."""
    candidates = _target_candidates(attack_board, opp_board, unsunk_hits)
    if not candidates:
        # Fallback: should not happen if unsunk_hits exist
        return _hunt_mode(attack_board, opp_board, [])
    return random.choice(candidates)


def _target_candidates(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    unsunk_hits: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Candidate follow-up cells around unsunk hits (line ends first, then ring)."""
    # Group unsunk hits by ship_id for smarter targeting
    groups: dict[int, list[tuple[int, int]]] = {}
    for x, y in unsunk_hits:
        sid = opp_board[y][x]
        groups.setdefault(sid, []).append((x, y))

    candidates: list[tuple[int, int]] = []
    for _sid, cells in groups.items():
        # If multiple hits in a line, extend along that line first
        direction = _detect_line_direction(cells)
        if direction:
            dx, dy = direction
            # Try extending both ends of the line
            sorted_cells = sorted(cells, key=lambda c: (c[0] * dx + c[1] * dy))
            for end_cell in [sorted_cells[0], sorted_cells[-1]]:
                ex, ey = end_cell
                for sign in [1, -1]:
                    nx, ny = ex + dx * sign, ey + dy * sign
                    if _valid_target(attack_board, nx, ny):
                        candidates.append((nx, ny))
        # Also try all adjacent cells of all unsunk hits
        for x, y in cells:
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if _valid_target(attack_board, nx, ny):
                    if (nx, ny) not in candidates:
                        candidates.append((nx, ny))

    if not candidates:
        # Fallback: should not happen if unsunk_hits exist
        return _hunt_mode(attack_board, opp_board, [])
    return candidates


def _detect_line_direction(
    cells: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """If cells form a horizontal or vertical line, return direction vector."""
    if len(cells) < 2:
        return None
    xs = {c[0] for c in cells}
    ys = {c[1] for c in cells}
    if len(ys) == 1:
        return (1, 0)  # horizontal
    if len(xs) == 1:
        return (0, 1)  # vertical
    return None


def _valid_target(
    attack_board: list[list[int]], x: int, y: int,
) -> bool:
    """Check if (x, y) is in bounds and not yet fired at."""
    n = board_size_of(attack_board)
    if x < 0 or x >= n or y < 0 or y >= n:
        return False
    return attack_board[y][x] == EMPTY


def _hunt_mode(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    opp_ships: list[dict],
) -> tuple[int, int]:
    """Random shot with checkerboard parity optimization."""
    # Determine smallest unsunk ship size
    sunk_ids = _sunk_ids(attack_board, opp_board)
    # 舰队从**棋盘自己**推（v13）：`opp_board` 的边长就是这一局的尺寸，
    # 于是 AI 一行都不用改签名就知道 6×6 / 10×10 / 12×12 各有哪些船。
    min_unsunk = min(
        (d["size"] for d in fleet_of(board_size_of(opp_board))
         if d["id"] not in sunk_ids),
        default=2,
    )
    # Collect all valid targets with parity (x+y even)
    parity_targets: list[tuple[int, int]] = []
    all_targets: list[tuple[int, int]] = []
    for y in range(board_size_of(attack_board)):
        for x in range(board_size_of(attack_board)):
            if attack_board[y][x] == EMPTY:
                all_targets.append((x, y))
                if (x + y) % 2 == 0:
                    parity_targets.append((x, y))
    # Use parity-filtered list if it has candidates
    pool = parity_targets if parity_targets else all_targets
    if not pool:
        raise RuntimeError("无可用目标")
    return random.choice(pool)


# ── 困难档：概率热力图 ────────────────────────────────────────


def _sunk_ids(
    attack_board: list[list[int]], opp_board: list[list[int]],
) -> set[int]:
    """Ship ids that are fully hit."""
    sunk: set[int] = set()
    for sdef in fleet_of(board_size_of(opp_board)):
        cells = get_ship_cells(opp_board, sdef["id"])
        if cells and all(attack_board[cy][cx] == HIT for cx, cy in cells):
            sunk.add(sdef["id"])
    return sunk


def _remaining_sizes(
    attack_board: list[list[int]], opp_board: list[list[int]],
) -> list[int]:
    """Sizes of the ships still afloat."""
    sunk = _sunk_ids(attack_board, opp_board)
    return [d["size"] for d in fleet_of(board_size_of(opp_board))
            if d["id"] not in sunk]


def _all_placements(
    attack_board: list[list[int]], size: int,
) -> list[list[tuple[int, int]]]:
    """Every placement of a `size`-long ship that fits entirely on unfired cells.

    开过炮的格子（MISS，或已沉舰的 HIT）不可能再藏着剩余舰——
    所以「剩余舰的合法摆法」只由未开火格构成。
    """
    n = board_size_of(attack_board)
    placements: list[list[tuple[int, int]]] = []
    for y in range(n):
        for x in range(n):
            for horizontal in (True, False):
                cells: list[tuple[int, int]] = []
                for i in range(size):
                    cx = x + i if horizontal else x
                    cy = y if horizontal else y + i
                    if cx >= n or cy >= n:
                        cells = []
                        break
                    if attack_board[cy][cx] != EMPTY:
                        cells = []
                        break
                    cells.append((cx, cy))
                if cells:
                    placements.append(cells)
    return placements


def _heat_scores(
    attack_board: list[list[int]], opp_board: list[list[int]],
) -> list[list[float]]:
    """Probability heat map: score(cell) = Σ over remaining ships of the
    share of that ship's still-possible placements which cover the cell.

    每艘舰的摆法总数归一化后再相加，这样长舰（摆法少）与短舰（摆法多）
    的权重可比较；空格子分数高说明它更可能落在某条舰上。
    """
    n = board_size_of(attack_board)
    scores = [[0.0] * n for _ in range(n)]
    for size in _remaining_sizes(attack_board, opp_board):
        placements = _all_placements(attack_board, size)
        if not placements:
            continue
        weight = 1.0 / len(placements)
        for cells in placements:
            for cx, cy in cells:
                scores[cy][cx] += weight
    return scores


def _heatmap_hunt(
    attack_board: list[list[int]], opp_board: list[list[int]],
) -> tuple[int, int]:
    """Hard-tier hunt: fire at the highest-scoring unfired cell."""
    scores = _heat_scores(attack_board, opp_board)
    best: list[tuple[int, int]] = []
    best_score = -1.0
    for y in range(board_size_of(attack_board)):
        for x in range(board_size_of(attack_board)):
            if attack_board[y][x] != EMPTY:
                continue
            if scores[y][x] > best_score:
                best_score = scores[y][x]
                best = [(x, y)]
            elif scores[y][x] == best_score:
                best.append((x, y))
    if not best:
        return _hunt_mode(attack_board, opp_board, [])
    return random.choice(best)


def _any_unfired(attack_board: list[list[int]]) -> tuple[int, int]:
    """Fallback target: any unfired cell."""
    n = board_size_of(attack_board)
    pool = [
        (x, y)
        for y in range(n)
        for x in range(n)
        if attack_board[y][x] == EMPTY
    ]
    if not pool:
        raise RuntimeError("无可用目标")
    return random.choice(pool)


# ── 简单档：纯随机搜索 + 命中后只打相邻格，不做线段延伸 ────────


def _hunt_easy(attack_board: list[list[int]]) -> tuple[int, int]:
    """Easy-tier hunt: uniform random among ALL unfired cells.

    刻意不用 parity 过滤，也不用「最小未沉舰」过滤——那两样本身就是优化，
    简单档应该是个「会追着打但不会推理」的对手（调研里的 Ensign 档）。
    """
    return _any_unfired(attack_board)


def _target_easy(
    attack_board: list[list[int]], unsunk_hits: list[tuple[int, int]],
) -> tuple[int, int]:
    """Easy-tier target: random adjacent cell, no line-extension reasoning."""
    candidates: list[tuple[int, int]] = []
    for x, y in unsunk_hits:
        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if _valid_target(attack_board, nx, ny) and (nx, ny) not in candidates:
                candidates.append((nx, ny))
    if not candidates:
        return _any_unfired(attack_board)
    return random.choice(candidates)


# ── 困难档：命中后候选按热力排序 ──────────────────────────────


def _target_weighted(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    unsunk_hits: list[tuple[int, int]],
) -> tuple[int, int]:
    """Hard-tier target: same candidates as normal, but ranked by heat map."""
    candidates = _target_candidates(attack_board, opp_board, unsunk_hits)
    if not candidates:
        return _heatmap_hunt(attack_board, opp_board)
    scores = _heat_scores(attack_board, opp_board)
    return max(candidates, key=lambda c: scores[c[1]][c[0]])


# ── 武器使用策略（人机）────────────────────────────────────────


def weapon_plan(
    attack_board: list[list[int]],
    opp_board: list[list[int]],
    available: dict,
    shots_fired: int = 0,
) -> tuple[str, dict] | None:
    """Decide whether a bot should spend a special weapon this turn.

    available: {weapon_key: bool} — 该武器此刻是否可用（未用过 + 携带舰还在）。
    返回 (weapon, kwargs) 或 None（None 表示照常开炮）。
    策略刻意保守：只在「已经锁定了目标」或「开局信息最值钱」时出手。
    """
    unsunk_hits = _find_unsunk_hits(attack_board, opp_board)
    # 已咬住一条未沉舰：空袭以命中点为心，多半能一次打成重伤
    if unsunk_hits and available.get("strike"):
        x, y = unsunk_hits[0]
        return ("strike", {"x": x, "y": y})
    # 齐射：沿同一批候选点一次覆盖多个方向
    if unsunk_hits and available.get("salvo"):
        targets = _target_candidates(attack_board, opp_board, unsunk_hits)
        if len(targets) >= SALVO_SHOTS:
            return ("salvo", {"cells": [list(t) for t in targets[:SALVO_SHOTS]]})
    # 雷达：开局盘面最空时收益最大，扫概率最高的一片海
    if not unsunk_hits and available.get("radar") and shots_fired < 8:
        x, y = _heatmap_hunt(attack_board, opp_board)
        return ("radar", {"x": x, "y": y})
    return None
