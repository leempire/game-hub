"""Minesweeper (扫雷) pure game logic — no I/O, easy to unit-test.

Competitive mode: players share one board and play simultaneously (no turns).
Each player has a per-difficulty life count — stepping on a mine costs one
life, and only at zero lives is that player eliminated (spectates to the end).
Game ends when all players are eliminated or all safe cells are revealed.
Per-player stats: lives_left, revealed_count and flag stats.
"""

import random
from datetime import datetime, timezone


# Preset difficulties: (width, height, mines)
DIFFICULTIES = {
    "beginner": (9, 9, 10),
    "intermediate": (16, 16, 40),
    "advanced": (30, 16, 99),
}

# 每个难度每人几条命。棋盘越大越容易误判，命数就越多；初级一命即出局，
# 和「小盘信息少、踩雷基本是看错」匹配。
LIVES = {
    "beginner": 1,
    "intermediate": 2,
    "advanced": 3,
}

# 难度的中文名。**放在服务端**（v1.15）：等待大厅里那排难度芯片由
# `room_settings` 下发，客户端不再自己抄一份档位表（原先 `app/src/minesweeper.ts`
# 里那张表就是这么漂移起来的 —— 它连棋盘尺寸都抄了一份，而尺寸其实由对局载荷给）。
DIFFICULTY_LABELS = {
    "beginner": "初级",
    "intermediate": "中级",
    "advanced": "高级",
}


def difficulty_blurb(difficulty: str) -> str:
    """一句难度说明，例如「16×16 / 40雷 · 每人 2 条命」。

    与棋盘几何**同一份数据推出来**，改 `DIFFICULTIES` / `LIVES` 就自动跟着变。
    分隔符用全角 `×` 与 `·`（旧版创建房间弹窗里手写的 `9x9` 用的是字母 x，
    和别处不统一 —— 那处手写已经删了）。
    """
    width, height, mines = DIFFICULTIES[difficulty]
    return f"{width}×{height} / {mines}雷 · 每人 {LIVES[difficulty]} 条命"


def create_game(
    difficulty: str = "intermediate", num_players: int = 1,
    seed: int | None = None, daily: bool = False,
) -> dict:
    """Create a new minesweeper game state. Mines not placed yet.

    Args:
        difficulty: one of "beginner", "intermediate", "advanced"
        num_players: number of players (1-4)
        seed: 固定雷盘种子（每日固定盘用；常规局为 None，走系统随机）
        daily: 是否「每日固定盘」——只用于客户端标注，不影响裁决
    """
    w, h, m = DIFFICULTIES[difficulty]
    lives = LIVES.get(difficulty, 1)
    players = {}
    for seat in range(num_players):
        players[seat] = {
            "alive": True,
            "lives_left": lives,
            "revealed_count": 0,
            "eliminated_at": None,
            # 计时：由 room_manager 在动作入口写入（引擎不读墙钟）。
            # `elapsed_ms` 在终局时结算，作为「同标雷数、同翻格数」的排名键。
            "first_action_ms": None,
            "last_action_ms": None,
            "elapsed_ms": None,
        }
    return {
        "width": w,
        "height": h,
        "mines": m,
        "lives": lives,
        "difficulty": difficulty,
        # 每日固定盘：种子按 UTC 日期生成，所有玩家同日同档拿到同一份雷盘
        # （第一下翻开的「safe zone」仍会随格子移动，见 generate_mines 注释）
        "seed": seed,
        "daily": bool(daily),
        "mine_positions": None,  # set of (x,y), generated on first reveal
        "revealed": {},          # (x,y) -> value (0-8, or -1 for mine)
        "flagged": set(),        # set of (x,y) —— 只有旗子参与 chord/通关判定
        "questioned": set(),     # set of (x,y) —— 问号只是笔记，不阻挡翻开
        "flag_owners": {},       # (x,y) -> seat (who placed the flag)
        "started": False,
        "finished": False,
        "reason": None,          # "cleared" or "eliminated" or None
        # 游戏时钟由外部写入（room_manager 用 time.time()），引擎只做存储与排序。
        "started_at_ms": None,
        "finished_at_ms": None,
        "players": players,
    }


def _neighbors(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    """Return valid neighbor coordinates (8-directional)."""
    result = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                result.append((nx, ny))
    return result


def generate_mines(
    width: int, height: int, count: int, safe_x: int, safe_y: int,
    rng: random.Random | None = None,
) -> set[tuple[int, int]]:
    """Generate mine positions, excluding safe_x/safe_y and its 8 neighbors.

    `rng` 给定时用它抽样（每日固定盘：同一天 + 同一第一下翻开 → 同一份雷盘）。
    注意 safe zone 随第一下点击移动，所以「固定盘」保证的是**同种子**，不是
    跨玩家逐格完全相同——第一下点在不同位置时安全区不同。
    """
    safe_zone = {(safe_x, safe_y)}
    safe_zone.update(_neighbors(safe_x, safe_y, width, height))
    candidates = [
        (x, y)
        for x in range(width)
        for y in range(height)
        if (x, y) not in safe_zone
    ]
    count = min(count, len(candidates))
    return set((rng or random).sample(candidates, count))


def _count_adjacent_mines(
    x: int, y: int, mine_positions: set, w: int, h: int
) -> int:
    """Count mines adjacent to (x, y)."""
    return sum(1 for nx, ny in _neighbors(x, y, w, h)
               if (nx, ny) in mine_positions)


def _flood_fill(
    game: dict, x: int, y: int
) -> list[tuple[int, int, int]]:
    """Flood-fill reveal from (x, y). Returns list of (x, y, value)."""
    w, h = game["width"], game["height"]
    mines = game["mine_positions"]
    revealed = game["revealed"]
    flagged = game["flagged"]

    if (x, y) in revealed or (x, y) in flagged:
        return []

    # Stepped on a mine
    if (x, y) in mines:
        revealed[(x, y)] = -1
        return [(x, y, -1)]

    stack = [(x, y)]
    result = []
    while stack:
        cx, cy = stack.pop()
        if (cx, cy) in revealed:
            continue
        if (cx, cy) in flagged:
            continue
        if (cx, cy) in mines:
            continue
        val = _count_adjacent_mines(cx, cy, mines, w, h)
        revealed[(cx, cy)] = val
        result.append((cx, cy, val))
        if val == 0:
            for nx, ny in _neighbors(cx, cy, w, h):
                if (nx, ny) not in revealed and (nx, ny) not in flagged:
                    stack.append((nx, ny))
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_game_end(game: dict) -> bool:
    """Check if game should end. Returns True if game ended."""
    w, h = game["width"], game["height"]
    total_safe = w * h - game["mines"]
    revealed_count = sum(1 for v in game["revealed"].values() if v != -1)

    # All safe cells revealed
    if revealed_count >= total_safe:
        game["finished"] = True
        game["reason"] = "cleared"
        _reveal_all_mines(game)
        return True

    # All players eliminated
    all_eliminated = all(
        not p["alive"] for p in game["players"].values()
    )
    if all_eliminated:
        game["finished"] = True
        game["reason"] = "eliminated"
        _reveal_all_mines(game)
        return True

    return False


def _compute_flag_stats(game: dict) -> dict[int, dict]:
    """Compute correct_flags and wrong_flags for each player based on remaining flags.

    Returns {seat: {"correct_flags": int, "wrong_flags": int}}
    """
    stats = {seat: {"correct_flags": 0, "wrong_flags": 0}
             for seat in game["players"]}
    mines = game["mine_positions"] or set()
    for (x, y), seat in game["flag_owners"].items():
        if (x, y) not in game["flagged"]:
            continue  # Flag was cancelled
        if (x, y) in mines:
            stats[seat]["correct_flags"] += 1
        else:
            stats[seat]["wrong_flags"] += 1
    return stats


def compute_ranking(game: dict, players_info: list[dict]) -> list[dict]:
    """Compute final ranking.

    Args:
        game: game state dict
        players_info: list of {"seat": int, "username": str, "nickname": str|None}

    Returns list of ranking entries sorted by rank:
        [{"seat", "username", "nickname", "alive", "correct_flags",
          "wrong_flags", "revealed_count", "rank"}, ...]
    """
    flag_stats = _compute_flag_stats(game)

    entries = []
    for info in players_info:
        seat = info["seat"]
        player = game["players"].get(seat, {})
        entries.append({
            "seat": seat,
            "username": info["username"],
            "nickname": info.get("nickname"),
            "alive": player.get("alive", False),
            "correct_flags": flag_stats.get(seat, {}).get("correct_flags", 0),
            "wrong_flags": flag_stats.get(seat, {}).get("wrong_flags", 0),
            "revealed_count": player.get("revealed_count", 0),
            "eliminated_at": player.get("eliminated_at"),
            # 用时（毫秒）：从本局第一次翻开到该玩家最后一次翻开的跨度。
            # **它是排名键里的第三档** —— 游戏叫「竞速」，同标雷数、同翻格数时
            # 必须让更快的人赢，否则慢慢插旗也能拿第一。
            "elapsed_ms": player.get("elapsed_ms"),
        })

    # Sort: survivors first, then by correct_flags desc, revealed_count desc
    # Eliminated: by revealed_count desc, then eliminated_at desc (later = better)
    def sort_key(e):
        alive_sort = 0 if e["alive"] else 1
        if e["alive"]:
            return (alive_sort, -e["correct_flags"], -e["revealed_count"])
        else:
            # eliminated_at desc means later time = smaller sort key
            elim = e["eliminated_at"] or ""
            return (alive_sort, -e["revealed_count"], elim)

    # For eliminated players, we want eliminated_at desc (later is better).
    # Since we sort ascending by default, we negate the timestamp effect
    # by reversing the string comparison. Use a two-pass approach:
    # Survivors: correct flags desc, revealed desc, then elapsed asc (faster wins).
    # Eliminated: revealed desc, then eliminated_at desc (later = better).
    def elapsed_key(e) -> float:
        v = e.get("elapsed_ms")
        return float("inf") if v is None else float(v)

    survivors = [e for e in entries if e["alive"]]
    eliminated = [e for e in entries if not e["alive"]]

    survivors.sort(key=lambda e: (-e["correct_flags"], -e["revealed_count"], elapsed_key(e)))
    # Eliminated: revealed_count desc, then eliminated_at desc (later = better)
    eliminated.sort(key=lambda e: (-e["revealed_count"],
                                    -( _iso_to_ordinal(e["eliminated_at"]))))

    sorted_entries = survivors + eliminated

    # Assign ranks (ties share same rank)
    result = []
    for i, e in enumerate(sorted_entries):
        rank = i + 1
        # Check tie with previous
        if i > 0:
            prev = sorted_entries[i - 1]
            if _is_tied(e, prev):
                rank = result[-1]["rank"]
        entry = {
            "seat": e["seat"],
            "username": e["username"],
            "nickname": e["nickname"],
            "alive": e["alive"],
            "correct_flags": e["correct_flags"],
            "wrong_flags": e["wrong_flags"],
            "revealed_count": e["revealed_count"],
            "elapsed_ms": e.get("elapsed_ms"),
            "rank": rank,
        }
        result.append(entry)

    return result


def _iso_to_ordinal(iso_str: str | None) -> int:
    """Convert ISO timestamp to ordinal for sorting. None -> 0."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1000000)
    except (ValueError, TypeError):
        return 0


def _is_tied(a: dict, b: dict) -> bool:
    """Check if two entries are tied in ranking."""
    if a["alive"] != b["alive"]:
        return False
    if a["alive"]:
        return (a["correct_flags"] == b["correct_flags"]
                and a["revealed_count"] == b["revealed_count"]
                and a.get("elapsed_ms") == b.get("elapsed_ms"))
    else:
        return (a["revealed_count"] == b["revealed_count"]
                and a["eliminated_at"] == b["eliminated_at"])


def _reveal_cell(game: dict, x: int, y: int, seat: int) -> dict:
    """Reveal a single cell for `seat` — no guard checks (caller validates first).

    Shared by `reveal` (one cell) and `chord` (a batch of neighbor cells) so the
    mine/flood-fill/life-loss ruling lives in exactly one place.

    Returns {cells, hit_mine, lost_life, lives_left, eliminated}
    """
    hit_mine = (x, y) in game["mine_positions"]
    # 问号只是笔记：翻开时自动清掉（旗子会拦住翻开，问号不会）
    game.setdefault("questioned", set()).discard((x, y))
    cells = _flood_fill(game, x, y)

    # Credit revealed cells to the operator
    safe_cells_revealed = [c for c in cells if c[2] != -1]
    game["players"][seat]["revealed_count"] += len(safe_cells_revealed)

    player = game["players"][seat]
    lost_life = False
    eliminated = False
    if hit_mine:
        # 踩雷扣一条命；命扣完才淘汰。扣完之前本人可以继续操作
        # （扫雷是共享棋盘同时操作、没有回合概念，所以没有「移交回合」这回事）。
        player["lives_left"] = max(0, player.get("lives_left", 1) - 1)
        lost_life = True
        if player["lives_left"] <= 0:
            player["alive"] = False
            player["eliminated_at"] = _now_iso()
            eliminated = True

    return {
        "cells": cells,
        "hit_mine": hit_mine,
        "lost_life": lost_life,
        "lives_left": player["lives_left"],
        "eliminated": eliminated,
    }


def reveal(game: dict, x: int, y: int, seat: int) -> dict:
    """Reveal cell (x, y) for player at given seat.

    Returns result dict:
      ok: bool
      cells: list of [x, y, value] (value -1 = mine)
      hit_mine: bool
      eliminated: bool (this player was just eliminated)
      revealed_count: int (this player's total revealed count)
      game_over: None or {"reason": str, "ranking": [...]}
      error: str (only if ok=False)
    """
    if game["finished"]:
        return {"ok": False, "error": "游戏已结束"}
    if seat not in game["players"]:
        return {"ok": False, "error": "无效的玩家"}
    if not game["players"][seat]["alive"]:
        return {"ok": False, "error": "你已被淘汰"}
    w, h = game["width"], game["height"]
    if x < 0 or x >= w or y < 0 or y >= h:
        return {"ok": False, "error": "坐标超出范围"}
    if (x, y) in game["revealed"]:
        return {"ok": False, "error": "该格已翻开"}
    if (x, y) in game["flagged"]:
        return {"ok": False, "error": "该格已插旗"}

    # Lazy mine generation on first reveal
    if game["mine_positions"] is None:
        rng = random.Random(game["seed"]) if game.get("seed") is not None else None
        game["mine_positions"] = generate_mines(w, h, game["mines"], x, y, rng)
        game["started"] = True

    outcome = _reveal_cell(game, x, y, seat)

    # Check end conditions
    game_over = None
    if _check_game_end(game):
        # Ranking will be computed by caller (room_manager) with player info
        game_over = {"reason": game["reason"]}

    return {
        "ok": True,
        "cells": [[cx, cy, v] for cx, cy, v in outcome["cells"]],
        "hit_mine": outcome["hit_mine"],
        "lost_life": outcome["lost_life"],
        "lives_left": outcome["lives_left"],
        "eliminated": outcome["eliminated"],
        "revealed_count": game["players"][seat]["revealed_count"],
        "game_over": game_over,
    }


def chord(game: dict, x: int, y: int, seat: int) -> dict:
    """Chord: tap a revealed number whose surrounding flag count equals it, and
    every remaining (unrevealed, unflagged) neighbor opens at once.

    Each neighbor goes through the same `_reveal_cell` path as a normal reveal
    (flood fill, life loss, stats), so a wrong flag can eliminate the player.

    Returns the same shape as `reveal`. When the precondition does not hold
    (cell not revealed / not a number / flag count mismatch / nothing left to
    open) it returns ok=False — the caller stays silent, no error popup.
    """
    if game["finished"]:
        return {"ok": False, "error": "游戏已结束"}
    if seat not in game["players"]:
        return {"ok": False, "error": "无效的玩家"}
    if not game["players"][seat]["alive"]:
        return {"ok": False, "error": "你已被淘汰"}
    if game["mine_positions"] is None:
        return {"ok": False, "error": "尚未开始"}
    w, h = game["width"], game["height"]
    if x < 0 or x >= w or y < 0 or y >= h:
        return {"ok": False, "error": "坐标超出范围"}

    value = game["revealed"].get((x, y))
    if value is None or value <= 0:
        return {"ok": False, "error": "该格不是数字格"}

    neighbors = _neighbors(x, y, w, h)
    flag_count = sum(1 for n in neighbors if n in game["flagged"])
    if flag_count != value:
        return {"ok": False, "error": "旗数与数字不符"}

    targets = [n for n in neighbors
               if n not in game["revealed"] and n not in game["flagged"]]
    if not targets:
        return {"ok": False, "error": "没有可揭开的邻格"}

    cells: list[tuple[int, int, int]] = []
    hit_mine = False
    lost_life = False
    eliminated = False
    for tx, ty in targets:
        outcome = _reveal_cell(game, tx, ty, seat)
        cells.extend(outcome["cells"])
        hit_mine = hit_mine or outcome["hit_mine"]
        lost_life = lost_life or outcome["lost_life"]
        eliminated = eliminated or outcome["eliminated"]

    game_over = None
    if _check_game_end(game):
        game_over = {"reason": game["reason"]}

    return {
        "ok": True,
        "cells": [[cx, cy, v] for cx, cy, v in cells],
        "hit_mine": hit_mine,
        "lost_life": lost_life,
        "lives_left": game["players"][seat].get("lives_left", 1),
        "eliminated": eliminated,
        "revealed_count": game["players"][seat]["revealed_count"],
        "game_over": game_over,
    }


def flag(game: dict, x: int, y: int, seat: int) -> dict:
    """Cycle a cell through 空 → 旗 → 问号 → 空.

    问号是**纯笔记**：不参与 chord 计数、不阻挡翻开、不计入通关判定；只有旗子
    才是「这里确定有雷」的断言。三态轮换比「长按插旗 / 切模式点击拔旗」少一次
    操作，也是桌面扫雷的通行做法。

    Returns: {ok, x, y, flagged: bool, questioned: bool, state: str, seat, error}
    """
    if game["finished"]:
        return {"ok": False, "error": "游戏已结束"}
    if seat not in game["players"]:
        return {"ok": False, "error": "无效的玩家"}
    if not game["players"][seat]["alive"]:
        return {"ok": False, "error": "你已被淘汰"}
    w, h = game["width"], game["height"]
    if x < 0 or x >= w or y < 0 or y >= h:
        return {"ok": False, "error": "坐标超出范围"}
    if (x, y) in game["revealed"]:
        return {"ok": False, "error": "已翻开的格子不能插旗"}

    flagged = game["flagged"]
    questioned = game.setdefault("questioned", set())
    if (x, y) in flagged:
        # 旗 → 问号
        flagged.discard((x, y))
        game["flag_owners"].pop((x, y), None)
        questioned.add((x, y))
        state = "question"
    elif (x, y) in questioned:
        # 问号 → 空
        questioned.discard((x, y))
        state = "none"
    else:
        # 空 → 旗
        flagged.add((x, y))
        game["flag_owners"][(x, y)] = seat
        state = "flag"

    return {
        "ok": True,
        "x": x,
        "y": y,
        "flagged": state == "flag",
        "questioned": state == "question",
        "state": state,
        "seat": seat,
    }


def _reveal_all_mines(game: dict):
    """Reveal all mine positions (for end-game display)."""
    if game["mine_positions"] is None:
        return
    for mx, my in game["mine_positions"]:
        if (mx, my) not in game["revealed"]:
            game["revealed"][(mx, my)] = -1


def get_revealed_list(game: dict) -> list[list[int]]:
    """Get revealed cells as [[x, y, value], ...] for sync messages."""
    return [[x, y, v] for (x, y), v in game["revealed"].items()]


def get_flagged_list(game: dict) -> list[list[int]]:
    """Get flagged cells as [[x, y], ...] for sync messages."""
    return [[x, y] for x, y in game["flagged"]]


def get_questioned_list(game: dict) -> list[list[int]]:
    """问号格（纯笔记，不影响裁决）。老对局态没有这个键，按空集处理。"""
    return [[x, y] for x, y in game.get("questioned") or ()]


def game_to_sync(game: dict, players_info: list[dict] | None = None) -> dict:
    """Build the game portion of a sync message.

    Args:
        game: game state dict
        players_info: list of {"seat": int, "username": str, "nickname": str|None}
                      Required for ranking when game is finished.
    """
    result = {
        "game": "minesweeper",
        "width": game["width"],
        "height": game["height"],
        "mines": game["mines"],
        "lives": game.get("lives", 1),
        "difficulty": game.get("difficulty"),
        "daily": bool(game.get("daily")),
        "revealed": get_revealed_list(game),
        "flagged": get_flagged_list(game),
        "questioned": get_questioned_list(game),
        "started": game["started"],
        "finished": game["finished"],
        "reason": game["reason"],
        # 只下发相对量的**起点**没有必要：客户端不自己计时（以服务端结算为准），
        # 这里仅给结算/回放一个可显示的用时。
        "started_at_ms": game.get("started_at_ms"),
        "players": {
            str(seat): {
                "alive": p["alive"],
                "lives_left": p.get("lives_left", 1),
                "revealed_count": p["revealed_count"],
                "elapsed_ms": p.get("elapsed_ms"),
            }
            for seat, p in game["players"].items()
        },
    }
    if game["finished"] and players_info:
        result["ranking"] = compute_ranking(game, players_info)
    return result
