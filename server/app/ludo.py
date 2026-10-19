"""Aeroplane Chess (飞行棋) pure game logic — no I/O, easy to unit-test.

规则裁决 (v3，对齐 tmp/飞行棋.png 的十字环棋盘):
- 2-4 人，每人 4 架飞机，**十字形外圈赛道 52 格** + 每玩家 6 格终点航道
- 掷 6 起飞（从基地进入起飞点），掷 6 额外回合
- 连续 3 次掷 6：所有飞机回基地（乐极生悲惩罚）
- **同色跳格**：赛道 52 格按 蓝→绿→红→黄 循环着色（每色 13 格）。落点若与
  自己同色，向前跳到下一个同色格（+4 格）
- **虚线飞行**：每人的虚线格在相对进度 3（自己颜色），落上去沿虚线飞 12 格到
  另一个同色格（相对进度 15）；飞不过自己航道入口时不飞（见 ARROW_STEP）
- 跳、飞各最多触发一次。顺序由落点决定：踩虚线格 → 先飞后跳；跳到虚线格上 →
  飞完就停（标准规则原文：「若飞机是由上一个颜色相同的格子跳至颜色相同而有
  虚线连接的一格内，则…通过虚线到前方颜色相同的的一格后，飞机就不再移动」）
- 撞机：落点（含跳/飞之后的落点）有敌机 → 敌机回基地。落点是自己颜色的格子
  且已有敌机时直接撞落、不跳不飞（否则等于眼睁睁放过对手）
- 终点精确到达：超出步数则折返（超过多少退多少）
- 排名：先全部到达终点的排名靠前；剩余按已到达飞机数排序

棋盘模型（几何见 app/src/ludoBoard.ts，两边常量必须一致）：
- 15×15 逻辑格，**外圈（第 0/14 行/列）宽 2 倍**；赛道是「围着十字边缘一圈」的
  52 格环形，四个凹角是斜切（共角不共边，见 ludoBoard.ts 的 TRACK 注释）
- 每玩家起点偏移 13 格（52/4=13）。因为 13 ≡ 1 (mod 4)，四条起跑线正好落在
  配色循环的四个不同颜色上——所以每个座位的起点格天然就是自己的颜色，
  且「相对进度是 4 的倍数」⇔「踩在自己颜色上」对任何座位都成立
- 飞机状态 progress: -1=基地, 0..48=赛道(相对), 49..54=终点航道, 55=到达
- **赛道只走 49 格**（0..48）：自己的航道入口就在相对进度 48，飞机绕到自己
  航道口就拐进去，剩下的 3 格（52-49）是「起跑线之前」的那几格，自己永远不走
"""

import random
from datetime import datetime, timezone

# 外圈赛道格数（配色循环、全局坐标取模都按它）。飞机自己只走 0..RING_END。
RING = 52
# 相邻两条航道入口之间的格距（= 四条起跑线的间距），52/4。
QUADRANT = RING // 4  # 13
# 自己在赛道上能走的最后一格 = 自己航道入口（相对进度）。
RING_END = 48
HOME_STRETCH = 6
# 航道 49..54 共 6 格，走完即到达（55 停在中圈自己的三角里，不占航道格）。
FINISH_PROGRESS = RING_END + HOME_STRETCH + 1  # 55
PIECES_PER_PLAYER = 4
TAKEOFF_ROLL = 6
MAX_CONSECUTIVE_SIXES = 3
PLAYER_OFFSETS = [0, 13, 26, 39]
# 座位 → 颜色。顺序对齐标准棋盘方位：左上蓝 / 右上绿 / 右下红 / 左下黄。
# 客户端 ludoBoard.ts 的 TRACK 表按同一方位排列，改一边必须同步另一边。
COLORS = ["blue", "green", "red", "yellow"]

# 赛道配色循环：每 4 格重复一次，52 格正好 13 轮（每色 13 格）。
# 必须与 COLORS 同序：起跑线偏移 13 ≡ 1 (mod 4)，这样第 s 个座位的起点格
# （全局格 13s）才正好是自己颜色。
TRACK_COLORS = COLORS
# 同色跳跃步长：同色格每 4 格一个。
JUMP_STEP = 4
# 虚线捷径：赛道上每 13 格一处虚线（绝对索引 3 / 16 / 29 / 42），**一色一条**
# ——黄 / 蓝 / 绿 / 红，各归一个座位。踩上去沿虚线飞 ARROW_STEP = 12 格到另一个
# 同色格。四条虚线画出来是旋转对称的一组箭头（见 ludoBoard.ts 的 ARROWS）。
ARROW_STEP = 12
ARROW_FIRST = 3
# 迭子：同色 ≥2 架飞机停在同一格。迭子不可被敌方飞越（走子路径经过即非法），
# 敌机正好落在迭子上则**同归于尽**（双方都回基地）。
STACK_MIN = 2


def create_game(num_players: int = 4) -> dict:
    """Create a new aeroplane chess game state.

    Args:
        num_players: 2-4 players
    """
    if num_players < 2 or num_players > 4:
        raise ValueError("飞行棋需要 2-4 名玩家")
    players = {}
    for seat in range(num_players):
        players[seat] = {
            "pieces": [-1] * PIECES_PER_PLAYER,  # -1 = in base
            "color": COLORS[seat],
            "finished_count": 0,
            "finished": False,
            "finished_at": None,
            "rank": None,
        }
    return {
        "num_players": num_players,
        "players": players,
        "current_seat": 0,
        "dice_value": None,
        "consecutive_sixes": 0,
        "movable_pieces": [],  # list of piece indices that can move
        "phase": "roll",  # "roll" or "move"
        "finished": False,
        "reason": None,
        "ranking": [],
        "turn_count": 0,
        "last_roll_seat": None,
    }


def roll_dice(game: dict, dice_value: int | None = None) -> dict:
    """Roll the dice for the current player.

    Args:
        game: game state
        dice_value: optional injected dice value (for testing), 1-6

    Returns: {ok, dice, movable, extra_turn, penalty, game_over}
    """
    if game["finished"]:
        return {"ok": False, "error": "游戏已结束"}
    if game["phase"] != "roll":
        return {"ok": False, "error": "请先移动飞机"}

    seat = game["current_seat"]
    player = game["players"][seat]
    if player["finished"]:
        _advance_turn(game)
        return roll_dice(game, dice_value)

    dice = dice_value if dice_value is not None else random.randint(1, 6)
    game["dice_value"] = dice
    game["last_roll_seat"] = seat

    # Track consecutive sixes
    if dice == 6:
        game["consecutive_sixes"] += 1
    else:
        game["consecutive_sixes"] = 0

    # Penalty: 3 consecutive sixes → all pieces back to base
    if game["consecutive_sixes"] >= MAX_CONSECUTIVE_SIXES:
        game["consecutive_sixes"] = 0
        _reset_all_pieces(player)
        game["phase"] = "roll"
        game["movable_pieces"] = []
        _advance_turn(game)
        return {
            "ok": True,
            "dice": dice,
            "movable": [],
            "extra_turn": False,
            "penalty": True,
            "game_over": _check_game_over(game),
        }

    # Find movable pieces
    movable = _get_movable_pieces(player, dice, game, seat)
    game["movable_pieces"] = movable

    if not movable:
        # No valid moves, pass turn
        game["phase"] = "roll"
        extra = dice == 6
        if not extra:
            _advance_turn(game)
        return {
            "ok": True,
            "dice": dice,
            "movable": [],
            "extra_turn": extra,
            "penalty": False,
            "game_over": None,
        }

    game["phase"] = "move"
    return {
        "ok": True,
        "dice": dice,
        "movable": movable,
        "extra_turn": dice == 6,
        "penalty": False,
        "game_over": None,
    }


def move_piece(game: dict, piece_id: int) -> dict:
    """Move a piece by the current dice value.

    Args:
        game: game state
        piece_id: 0-3 index of piece to move

    Returns: {ok, piece_id, seat, old_progress, new_progress, dice, path,
              segments, captures, finished, extra_turn, game_over}

    `path` 是逐格动画用的 progress 序列（不含起点、含终点），把掷骰子的逐格
    前进、同色跳跃、虚线飞行串在一起——这三段的形状只有服务端知道，客户端
    不能自己推（旧版客户端按 `old+dice` 推路径，加了跳/飞之后会画错）。
    `segments` 描述每一段是走 / 跳 / 飞，供客户端区分动画与提示文案。
    """
    if game["finished"]:
        return {"ok": False, "error": "游戏已结束"}
    if game["phase"] != "move":
        return {"ok": False, "error": "请先掷骰子"}
    if piece_id not in game["movable_pieces"]:
        return {"ok": False, "error": "该飞机不能移动"}

    seat = game["current_seat"]
    player = game["players"][seat]
    dice = game["dice_value"]
    old_progress = player["pieces"][piece_id]
    captures = []
    piece_finished = False
    segments: list[dict] = []

    if old_progress == -1:
        # 起飞：基地 → 起飞点（相对进度 0，本座位的颜色）。
        # 起飞不触发跳格——起飞点本身就是同色格，触发的话掷个 6 就白送十几格。
        new_progress = 0
        path = [0]
        player["pieces"][piece_id] = new_progress
        captures = _check_capture(game, seat, new_progress, piece_id)
    else:
        path = _walk_path(old_progress, dice)
        new_progress = path[-1]
        if new_progress == FINISH_PROGRESS:
            # 精确到达终点
            player["pieces"][piece_id] = new_progress
            player["finished_count"] += 1
            piece_finished = True
            if player["finished_count"] >= PIECES_PER_PLAYER:
                player["finished"] = True
                player["finished_at"] = _now_iso()
        else:
            # 主轨道/航道落点再按跳子、飞棋规则结算（航道落点不受影响）
            new_progress, extra_path, segments = resolve_landing(game, seat, new_progress)
            path.extend(extra_path)
            # 迭子同归于尽：正好落在敌方的迭子上（同色 ≥2 架）时，双方全部回基地。
            # 先判再写位置——写完了再判会把刚落下的那架也算进「同色叠机」里。
            mutual = bool(enemy_stacks_at(game, seat, new_progress))
            captures = _check_capture(game, seat, new_progress, piece_id)
            if mutual:
                player["pieces"][piece_id] = -1
                new_progress = -1
                path.append(-1)
                segments.append({"kind": "bounce", "from": path[-2], "to": -1})
            else:
                player["pieces"][piece_id] = new_progress

    game["movable_pieces"] = []
    game["phase"] = "roll"

    # Extra turn for rolling 6
    extra_turn = dice == 6 and not game["finished"]
    if not extra_turn:
        _advance_turn(game)

    game_over = _check_game_over(game)

    return {
        "ok": True,
        "piece_id": piece_id,
        "seat": seat,
        "old_progress": old_progress,
        "new_progress": new_progress,
        "dice": dice,
        "path": path,
        "segments": segments,
        "captures": captures,
        "finished": piece_finished,
        "extra_turn": extra_turn,
        "game_over": game_over,
    }


def surrender(game: dict, seat: int) -> dict | None:
    """Handle player surrender: mark as finished with worst rank.

    Returns game_over dict or None if game continues.
    """
    if game["finished"]:
        return None
    player = game["players"].get(seat)
    if not player or player["finished"]:
        return None
    # Reset all pieces to base
    _reset_all_pieces(player)
    player["finished"] = True
    player["finished_at"] = _now_iso()
    player["surrendered"] = True
    return _check_game_over(game)


def compute_ranking(game: dict, players_info: list[dict]) -> list[dict]:
    """Compute final ranking.

    Args:
        game: game state
        players_info: list of {"seat": int, "username": str, "nickname": str|None}

    Returns ranking list sorted by rank.
    """
    entries = []
    for info in players_info:
        seat = info["seat"]
        player = game["players"].get(seat, {})
        entries.append({
            "seat": seat,
            "username": info["username"],
            "nickname": info.get("nickname"),
            "finished_count": player.get("finished_count", 0),
            "finished": player.get("finished", False),
            "finished_at": player.get("finished_at"),
            "surrendered": player.get("surrendered", False),
            "color": player.get("color", ""),
        })

    # Sort: non-surrendered finished first (by finished_at asc),
    # then unfinished (by finished_count desc),
    # then surrendered last
    completed = [e for e in entries if e["finished"] and not e.get("surrendered")]
    unfinished = [e for e in entries if not e["finished"]]
    surrendered = [e for e in entries if e.get("surrendered")]

    completed.sort(key=lambda e: e["finished_at"] or "")
    unfinished.sort(key=lambda e: -e["finished_count"])
    surrendered.sort(key=lambda e: -e["finished_count"])

    sorted_entries = completed + unfinished + surrendered

    result = []
    for i, e in enumerate(sorted_entries):
        rank = i + 1
        if i > 0:
            prev = sorted_entries[i - 1]
            if (e["finished"] == prev["finished"]
                    and e["finished_count"] == prev["finished_count"]
                    and e["finished_at"] == prev["finished_at"]):
                rank = result[-1]["rank"]
        result.append({
            "seat": e["seat"],
            "username": e["username"],
            "nickname": e["nickname"],
            "color": e["color"],
            "finished_count": e["finished_count"],
            "rank": rank,
        })

    return result


def game_to_sync(game: dict, players_info: list[dict] | None = None) -> dict:
    """Build sync message for reconnection."""
    players = {}
    for seat, p in game["players"].items():
        players[str(seat)] = {
            "pieces": list(p["pieces"]),
            "color": p["color"],
            "finished_count": p["finished_count"],
            "finished": p["finished"],
        }
    result = {
        "game": "ludo",
        "num_players": game["num_players"],
        "players": players,
        "current_seat": game["current_seat"],
        "dice_value": game["dice_value"],
        "movable_pieces": game["movable_pieces"],
        "phase": game["phase"],
        "finished": game["finished"],
        "reason": game["reason"],
        "consecutive_sixes": game["consecutive_sixes"],
    }
    if game["finished"] and players_info:
        result["ranking"] = compute_ranking(game, players_info)
    return result


def get_global_position(seat: int, progress: int) -> int:
    """Convert player-relative progress to global track position (0-51).

    Only valid on the ring (progress 0..RING_END).
    """
    if progress < 0 or progress > RING_END:
        return -1
    return (progress + PLAYER_OFFSETS[seat]) % RING


def track_color(global_index: int) -> str:
    """赛道第 global_index 格的颜色（0-51）。"""
    return TRACK_COLORS[global_index % 4]


def is_own_color(progress: int) -> bool:
    """赛道上该（相对）进度是否踩在自己颜色的格子上。

    因为起跑线偏移 13 ≡ 1 (mod 4)，配色循环每 4 格重复一次，「相对进度是 4 的
    倍数」⇔「踩在自己颜色上」对**任何座位**都成立——所以这里不需要 seat 参数。
    """
    return 0 <= progress <= RING_END and progress % JUMP_STEP == 0


def is_arrow(progress: int) -> bool:
    """该（相对）进度是否是**可以起飞**的虚线格。

    赛道上有四条虚线（绝对索引 3 / 16 / 29 / 42，一色一条：黄 / 蓝 / 绿 / 红），
    但**只有落在自己颜色的那条上才能飞**——标准规则原文：「走到与自己颜色相同
    且有虚线连接的格子」。

    因为起跑线偏移 13 ≡ 1 (mod 4)，「踩在自己颜色上」⇔「相对进度是 4 的倍数」，
    而四条虚线的相对进度 3 / 16 / 29 / 42 里**只有 16 是 4 的倍数**——所以对
    任何座位，可用虚线都恰好是相对进度 16 那一条，飞 12 格到 28。
    四条绝对虚线各自属于一个座位（相对进度都是 16，绝对位置不同）：
    蓝方 (4,11) / 绿方 (11,10) / 红方 (10,3) / 黄方 (3,4)。
    """
    return (0 <= progress <= RING_END
            and progress % QUADRANT == ARROW_FIRST
            and progress % JUMP_STEP == 0)


def arrow_target(progress: int) -> int:
    """虚线终点（相对进度）；不是可起飞的虚线格时返回 -1。"""
    if not is_arrow(progress):
        return -1
    target = progress + ARROW_STEP
    return target if target <= RING_END else -1


def resolve_landing(game: dict, seat: int, landed: int) -> tuple[int, list[int], list[dict]]:
    """按「跳子 / 飞棋」规则把落点结算成最终位置。

    跳、飞各最多一次，一共不超过两段（标准规则原意）：
    - 落在自己颜色的普通格 → 向前跳到下一个同色格（+4）
    - 落在自己颜色的虚线格 → 先沿虚线飞到对岸，再从对岸跳一次
    - 跳到虚线格上 → 飞完就停，不再跳
    - 自己颜色的格子上已有敌机 → 直接撞落，不跳不飞

    Returns: (最终 progress, 追加经过的 progress 列表, 段落描述列表)
    """
    path: list[int] = []
    segments: list[dict] = []
    cur = landed

    if is_own_color(cur) and _enemies_at(game, seat, cur):
        return cur, path, segments

    jumped = False
    flew = False
    for _ in range(2):
        target = arrow_target(cur) if not flew else -1
        if target > 0:
            segments.append({"kind": "fly", "from": cur, "to": target})
            cur = target
            flew = True
        elif is_own_color(cur) and not jumped and cur + JUMP_STEP <= RING_END:
            # 不允许跳进终点航道：航道不属于 52 格的配色环，跳过去会绕过
            # 「终点必须精确到达」这条规则
            nxt = cur + JUMP_STEP
            segments.append({"kind": "jump", "from": cur, "to": nxt})
            cur = nxt
            jumped = True
        else:
            break
        path.append(cur)
    return cur, path, segments


# ── Internal helpers ────────────────────────────────────────────


def _reset_all_pieces(player: dict):
    """Reset all pieces to base."""
    player["pieces"] = [-1] * PIECES_PER_PLAYER
    player["finished_count"] = 0


def _walk_path(old: int, dice: int) -> list[int]:
    """掷骰子逐格走过的 progress 序列（不含起点，含超出终点后的折返）。

    例：old=55, dice=5 → [56, 57, 56, 55, 54]，终点 54 与引擎的
    「超出多少退多少」一致，但中间过程也画得出来（客户端逐格动画要用）。
    """
    out: list[int] = []
    for i in range(1, dice + 1):
        p = old + i
        if p > FINISH_PROGRESS:
            p = FINISH_PROGRESS - (p - FINISH_PROGRESS)
        out.append(p)
    return out


def enemy_stacks_at(game: dict, seat: int, progress: int) -> list[int]:
    """落点上停着迭子（同色 ≥2 架）的对手座位号。只读。

    迭子的两条规则都靠它：走子路径经过迭子格 → 该走法非法；落点正好是迭子 →
    同归于尽（见 move_piece）。
    """
    if progress < 0 or progress > RING_END:
        return []
    global_pos = get_global_position(seat, progress)
    out: list[int] = []
    for other_seat, other_player in game["players"].items():
        if other_seat == seat or other_player["finished"]:
            continue
        n = 0
        for pid in range(PIECES_PER_PLAYER):
            other_progress = other_player["pieces"][pid]
            if 0 <= other_progress <= RING_END:
                if get_global_position(other_seat, other_progress) == global_pos:
                    n += 1
        if n >= STACK_MIN:
            out.append(other_seat)
    return out


def _path_blocked(game: dict, seat: int, path: list[int]) -> bool:
    """走子路径是否被敌方迭子挡住（终点格不算——落上去是同归于尽，不是穿越）。"""
    for prog in path[:-1]:
        if enemy_stacks_at(game, seat, prog):
            return True
    return False


def _enemies_at(game: dict, seat: int, progress: int) -> bool:
    """落点上是否停着对手飞机（只读，不改动状态——改状态的留给 _check_capture）。"""
    if progress < 0 or progress > RING_END:
        return False
    global_pos = get_global_position(seat, progress)
    for other_seat, other_player in game["players"].items():
        if other_seat == seat or other_player["finished"]:
            continue
        for pid in range(PIECES_PER_PLAYER):
            other_progress = other_player["pieces"][pid]
            if 0 <= other_progress <= RING_END:
                if get_global_position(other_seat, other_progress) == global_pos:
                    return True
    return False


def _get_movable_pieces(player: dict, dice: int, game: dict | None = None, seat: int = 0) -> list[int]:
    """Return list of piece indices that can legally move.

    传了 game/seat 时会额外剔除「路径被敌方迭子挡住」的飞机（迭子不可飞越）。
    不传时只按点数判断——旧调用点与单测不受影响。
    """
    movable = []
    for i in range(PIECES_PER_PLAYER):
        progress = player["pieces"][i]
        if progress == -1:
            # Can take off only with a 6
            if dice != TAKEOFF_ROLL:
                continue
        elif progress == FINISH_PROGRESS:
            continue  # already finished
        if game is not None and progress != -1:
            if _path_blocked(game, seat, _walk_path(progress, dice)):
                continue
        movable.append(i)
    return movable


def _check_capture(
    game: dict, seat: int, progress: int, moved_piece: int
) -> list[dict]:
    """Check if moving to progress captures opponent pieces.

    Only captures on the ring (progress 0..RING_END).
    Returns list of {"seat": int, "piece_id": int} for captured pieces.
    """
    if progress < 0 or progress > RING_END:
        return []

    global_pos = get_global_position(seat, progress)
    captures = []

    for other_seat, other_player in game["players"].items():
        if other_seat == seat or other_player["finished"]:
            continue
        for pid in range(PIECES_PER_PLAYER):
            other_progress = other_player["pieces"][pid]
            if other_progress < 0 or other_progress > RING_END:
                continue
            other_global = get_global_position(other_seat, other_progress)
            if other_global == global_pos:
                other_player["pieces"][pid] = -1
                captures.append({"seat": other_seat, "piece_id": pid})

    return captures


def _advance_turn(game: dict):
    """Advance to the next non-finished player."""
    num = game["num_players"]
    for _ in range(num):
        game["current_seat"] = (game["current_seat"] + 1) % num
        if not game["players"][game["current_seat"]]["finished"]:
            game["turn_count"] += 1
            game["consecutive_sixes"] = 0
            return
    # All finished — shouldn't reach here if game_over check is correct


def _check_game_over(game: dict) -> dict | None:
    """Check if the game is over.

    Game ends when all players have finished (all 4 pieces home)
    or only one player remains unfinished.
    """
    unfinished = [
        s for s, p in game["players"].items() if not p["finished"]
    ]
    if len(unfinished) <= 1:
        game["finished"] = True
        game["reason"] = "all_finished" if not unfinished else "last_remaining"
        return {"reason": game["reason"]}
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
