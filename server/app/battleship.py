"""Battleship (海战棋) pure game logic — no I/O, easy to unit-test.

**棋盘尺寸与舰队配置是房间级设置**（v13，本文件唯一的「一局一参数」）：
标准 10×10 / 闪电战 6×6 / 大战场 12×12，各自配一套舰队（见 `SIZE_PRESETS`）。
船横竖摆、不许重叠出界；轮流开炮，一艘船的全部格子被打中即沉，
先把对手的船全部击沉者胜。没有和棋。

**尺寸只有一个来源**：`gs["size"]`。本模块里凡是「棋盘有多大」的判断
（边界、随机布阵、扫雷范围、逐格遍历）**一律从 `len(board)` 或显式传进来的
`size` 拿**，不要再去读模块级常量 —— 那个常量现在只是**默认值**。
理由与深空休眠 v9「几何只有一份实现」同一条：两份尺寸必然漂移。

v1.10 增补（见 docs/游戏设计/海战棋/增强调研.md）：
- **命中续射**：命中/击沉后保留回合再打一发；先手方的第一发不触发（补偿后手）。
- **特殊武器**：雷达 / 齐射 / 空袭 各每局 1 次，布阵阶段挂靠到三条**不同**的舰船上，
  该舰被击沉则武器失效（Sea Battle 2 的机制）。挂靠关系**对对手公开**——
  「找出并击沉武器舰」是它的反制手段，藏起来机制就不成立（只暴露舰名不暴露位置）。
  使用武器即本回合全部行动，**不触发续射**。
"""

import random

DEFAULT_SIZE = 10
BOARD_SIZE = DEFAULT_SIZE          # 旧名保留：只是缺省尺寸，**不是唯一尺寸**
EMPTY = 0
MISS = -1
HIT = -2

# 标准 10×10 的舰队（id 从 1 起，`size` 是占几格）
SHIP_DEFS = [
    {"id": 1, "name": "carrier", "size": 5},
    {"id": 2, "name": "battleship", "size": 4},
    {"id": 3, "name": "cruiser", "size": 3},
    {"id": 4, "name": "submarine", "size": 3},
    {"id": 5, "name": "destroyer", "size": 2},
]

SHIP_SIZES = [d["size"] for d in SHIP_DEFS]

SHIP_NAMES = {d["id"]: d["name"] for d in SHIP_DEFS}

# ── 三种尺寸（v13，房间级设置）────────────────────────────────
#
# ⚠️ **6×6 与 12×12 的舰队配置是本作自己定的**，没有来源：调研（`增强调研.md`
# 六·第三梯队第 5 条）只写了「棋盘尺寸 / 舰队配置可变（6×6 / 10×10 / 12×12）」，
# 而变体清单类来源（Rare Pike / 雾线舰位）列出的是尺寸档位本身，逐档舰队没给。
# 取值的两条约束：①**占格比例与标准档接近**（10×10 = 17/100 = 17%），
# 太小则开局全靠猜、太大则一局拖不完；②**最长的那艘 ≤ 尺寸 − 2**，
# 否则随机布阵的接受率会崩塌（12×12 上加一艘 6 格船就会明显变慢）。
SIZE_PRESETS: dict[int, dict] = {
    6: {
        "name": "闪电战",
        "blurb": "6×6 · 4 艘小船 · 一局两三分钟",
        "ships": [
            {"id": 1, "name": "cruiser", "size": 3},
            {"id": 2, "name": "submarine", "size": 2},
            {"id": 3, "name": "destroyer", "size": 2},
            {"id": 4, "name": "patrol", "size": 2},
        ],
    },
    10: {
        "name": "标准",
        "blurb": "10×10 · 经典 5 艘（5/4/3/3/2）",
        "ships": SHIP_DEFS,
    },
    12: {
        "name": "大战场",
        "blurb": "12×12 · 6 艘 · 船的活路更多",
        "ships": [
            {"id": 1, "name": "carrier", "size": 5},
            {"id": 2, "name": "battleship", "size": 4},
            {"id": 3, "name": "cruiser", "size": 3},
            {"id": 4, "name": "submarine", "size": 3},
            {"id": 5, "name": "destroyer", "size": 2},
            {"id": 6, "name": "patrol", "size": 2},
        ],
    },
}
SIZES = tuple(SIZE_PRESETS)


def preset(size: int | None) -> dict:
    """按尺寸取预设（未知尺寸退回标准档 —— 老战绩里可能存着后来删掉的尺寸）。"""
    return SIZE_PRESETS.get(size or DEFAULT_SIZE, SIZE_PRESETS[DEFAULT_SIZE])


def fleet_of(size: int | None) -> list[dict]:
    """这一局的舰队（`[{id, name, size}, ...]`）。"""
    return preset(size)["ships"]


def ship_names(size: int | None) -> dict[int, str]:
    return {d["id"]: d["name"] for d in fleet_of(size)}

# ── 特殊武器 ──────────────────────────────────────────────────

WEAPON_RADAR = "radar"
WEAPON_SALVO = "salvo"
WEAPON_STRIKE = "strike"

SALVO_SHOTS = 3      # 齐射一次打几个格子
STRIKE_RADIUS = 1    # 空袭范围半径（1 → 3×3）

WEAPON_DEFS = {
    WEAPON_RADAR: {
        "name": "雷达",
        "desc": f"探测选定格周围 {(2 * STRIKE_RADIUS + 1)}×{(2 * STRIKE_RADIUS + 1)} "
                f"海域内是否有敌舰（只报有无，不报位置）",
    },
    WEAPON_SALVO: {
        "name": "齐射",
        "desc": f"一次性对 {SALVO_SHOTS} 个格子开炮，逐格结算",
    },
    WEAPON_STRIKE: {
        "name": "空袭",
        "desc": f"对选定格周围 {(2 * STRIKE_RADIUS + 1)}×{(2 * STRIKE_RADIUS + 1)} "
                f"海域范围打击，每格独立判定",
    },
}

def default_weapon_assignment(size: int | None = None) -> dict:
    """未显式挂靠时的默认分配（旧客户端 / 缺字段时用，保证向后兼容）。

    **放在最小的那三艘上**（原型攻略与 Sea Battle 2 都是这个思路：小船更难被
    找到，武器更可能活到后期）。**按尺寸推**而不是写死 —— 6×6 的舰队只有 4 艘、
    船号 1~4，写死的 `5` 在那张图上根本不存在（v13 加尺寸档时踩到的第一颗雷）。
    排序取 `(大小, 船号倒序)`：标准档因此仍然得到 5/4/3，与 v1.10 的默认完全一致。
    """
    fleet = sorted(fleet_of(size), key=lambda d: (d["size"], -d["id"]))
    picks = [d["id"] for d in fleet[:3]]
    return {WEAPON_RADAR: picks[0], WEAPON_SALVO: picks[1], WEAPON_STRIKE: picks[2]}


# 标准档的默认分配（向后兼容的名字，10×10 上等于 `default_weapon_assignment(10)`）
DEFAULT_WEAPON_ASSIGNMENT = default_weapon_assignment(DEFAULT_SIZE)


def create_board(size: int = DEFAULT_SIZE) -> list[list[int]]:
    """Create an empty `size`×`size` board."""
    return [[EMPTY] * size for _ in range(size)]


def board_size_of(board: list[list[int]]) -> int:
    """棋盘自己的尺寸 —— **一切「有多大」都从这里问**，不再读模块级常量。"""
    return len(board)


def _in_bounds(x: int, y: int, size: int = DEFAULT_SIZE) -> bool:
    return 0 <= x < size and 0 <= y < size


def _ship_cells(x: int, y: int, size: int, horizontal: bool) -> list[tuple[int, int]]:
    """Compute cell coordinates for a ship placement."""
    cells = []
    for i in range(size):
        cx = x + i if horizontal else x
        cy = y if horizontal else y + i
        cells.append((cx, cy))
    return cells


def validate_ship(
    x: int, y: int, size: int, horizontal: bool, board: int = DEFAULT_SIZE,
) -> bool:
    """Check if a single ship placement is within bounds (`board` = 棋盘边长)."""
    cells = _ship_cells(x, y, size, horizontal)
    return all(_in_bounds(cx, cy, board) for cx, cy in cells)


def validate_placement(ships: list[dict], size: int | None = None) -> bool:
    """Validate a full placement: all ships in bounds, no overlaps, correct count.

    Each ship dict: {"id": int, "x": int, "y": int, "horizontal": bool}
    """
    fleet = fleet_of(size)
    if len(ships) != len(fleet):
        return False
    occupied: set[tuple[int, int]] = set()
    ids_seen: set[int] = set()
    for ship in ships:
        sid = ship["id"]
        if sid in ids_seen:
            return False
        ids_seen.add(sid)
        sdef = next((d for d in fleet if d["id"] == sid), None)
        if not sdef:
            return False
        cells = _ship_cells(ship["x"], ship["y"], sdef["size"], ship["horizontal"])
        if not all(_in_bounds(cx, cy, size or DEFAULT_SIZE) for cx, cy in cells):
            return False
        if any(c in occupied for c in cells):
            return False
        occupied.update(cells)
    return True


def apply_placement(board: list[list[int]], ships: list[dict],
                    size: int | None = None) -> None:
    """Place ships on the board (mutates in place). Cells get ship id (1-based)."""
    fleet = fleet_of(size or board_size_of(board))
    for ship in ships:
        sdef = next(d for d in fleet if d["id"] == ship["id"])
        cells = _ship_cells(ship["x"], ship["y"], sdef["size"], ship["horizontal"])
        for cx, cy in cells:
            board[cy][cx] = ship["id"]


def random_placement(rng: random.Random | None = None,
                     size: int | None = None) -> list[dict]:
    """Generate a random valid placement for **这一局的整个舰队**.

    Returns list of {"id", "x", "y", "horizontal"} dicts.
    """
    _rng = rng or random
    n = size or DEFAULT_SIZE
    fleet = fleet_of(n)
    for _attempt in range(1000):
        ships: list[dict] = []
        board = create_board(n)
        ok = True
        for sdef in fleet:
            placed = False
            for _try in range(200):
                horizontal = _rng.choice([True, False])
                if horizontal:
                    x = _rng.randint(0, n - sdef["size"])
                    y = _rng.randint(0, n - 1)
                else:
                    x = _rng.randint(0, n - 1)
                    y = _rng.randint(0, n - sdef["size"])
                cells = _ship_cells(x, y, sdef["size"], horizontal)
                if any(board[cy][cx] != EMPTY for cx, cy in cells):
                    continue
                for cx, cy in cells:
                    board[cy][cx] = sdef["id"]
                ships.append({
                    "id": sdef["id"], "x": x, "y": y,
                    "horizontal": horizontal,
                })
                placed = True
                break
            if not placed:
                ok = False
                break
        if ok and len(ships) == len(fleet):
            return ships
    raise RuntimeError("无法生成随机布阵")


def fire(
    board: list[list[int]],
    attack_board: list[list[int]],
    x: int, y: int,
) -> dict:
    """Fire at (x, y). Mutates attack_board. Returns result dict.

    board: placement board (0=water, 1-5=ship id)
    attack_board: tracks shots (-1=miss, -2=hit, 0=unfired)

    Returns: {"result": "miss"|"hit"|"sunk", "ship_id": int|None,
              "sunk_cells": [[x,y],...]|None}
    """
    if not _in_bounds(x, y, board_size_of(board)):
        raise ValueError(f"坐标 ({x}, {y}) 超出范围")
    if attack_board[y][x] != EMPTY:
        raise ValueError(f"({x}, {y}) 已经开过炮")
    cell = board[y][x]
    if cell == EMPTY:
        attack_board[y][x] = MISS
        return {"result": "miss", "ship_id": None, "sunk_cells": None}
    # Hit
    attack_board[y][x] = HIT
    ship_id = cell
    # Check if all cells of this ship are now hit
    sunk_cells = _check_sunk(board, attack_board, ship_id)
    if sunk_cells:
        return {"result": "sunk", "ship_id": ship_id, "sunk_cells": sunk_cells}
    return {"result": "hit", "ship_id": ship_id, "sunk_cells": None}


def _check_sunk(
    board: list[list[int]],
    attack_board: list[list[int]],
    ship_id: int,
) -> list[list[int]] | None:
    """Return all cells of ship_id if fully hit, else None."""
    cells: list[list[int]] = []
    all_hit = True
    for y in range(board_size_of(board)):
        for x in range(board_size_of(board)):
            if board[y][x] == ship_id:
                cells.append([x, y])
                if attack_board[y][x] != HIT:
                    all_hit = False
    if all_hit and cells:
        return cells
    return None


def get_ship_cells(board: list[list[int]], ship_id: int) -> list[list[int]]:
    """Return all cells occupied by a ship on the placement board."""
    cells: list[list[int]] = []
    for y in range(board_size_of(board)):
        for x in range(board_size_of(board)):
            if board[y][x] == ship_id:
                cells.append([x, y])
    return cells


def ship_afloat(
    board: list[list[int]], attack_board: list[list[int]], ship_id: int,
) -> bool:
    """A ship is afloat unless every one of its cells has been hit."""
    return _check_sunk(board, attack_board, ship_id) is None


def validate_weapon_assignment(weapons: dict, size: int | None = None) -> bool:
    """Weapons must map each of the 3 weapon keys to a distinct, valid ship id.

    三条武器不能挂靠同一艘船——否则「把武器全堆在最难找的船上」会成为支配策略。
    """
    if not isinstance(weapons, dict):
        return False
    if set(weapons.keys()) != set(WEAPON_DEFS.keys()):
        return False
    ship_ids = list(weapons.values())
    if len(set(ship_ids)) != len(ship_ids):
        return False
    valid_ids = {d["id"] for d in fleet_of(size)}
    return all(sid in valid_ids for sid in ship_ids)


def normalize_weapon_assignment(weapons: dict | None,
                                size: int | None = None) -> dict:
    """Return a valid assignment, falling back to the default when unusable.

    旧客户端不发 weapons 字段（或发得不合法）时用默认分配兜底，保证向后兼容。
    """
    if validate_weapon_assignment(weapons, size):
        return dict(weapons)
    return dict(DEFAULT_WEAPON_ASSIGNMENT)


def weapons_view(
    weapons: dict | None,
    used: list | None,
    board: list[list[int]],
    attack_board: list[list[int]],
    size: int | None = None,
) -> dict:
    """Per-weapon status for one player — sent to BOTH sides (assignment is public).

    公开的是「哪条船带着哪件武器」（反制目标），不是船的位置。
    """
    used = used or []
    view: dict = {}
    for key in WEAPON_DEFS:
        ship_id = (weapons or {}).get(key)
        is_used = key in used
        view[key] = {
            "ship_id": ship_id,
            "ship_name": ship_names(size).get(ship_id),
            "used": is_used,
            "available": bool(
                ship_id
                and not is_used
                and ship_afloat(board, attack_board, ship_id)
            ),
        }
    return view


def area_cells(x: int, y: int, radius: int = STRIKE_RADIUS,
               size: int = DEFAULT_SIZE) -> list[tuple[int, int]]:
    """Cells of a (2*radius+1)² square centred on (x, y), clamped to the board."""
    return [
        (cx, cy)
        for cy in range(max(0, y - radius), min(size, y + radius + 1))
        for cx in range(max(0, x - radius), min(size, x + radius + 1))
    ]


def radar_scan(board: list[list[int]], x: int, y: int,
               radius: int = STRIKE_RADIUS) -> bool:
    """True if any ship occupies the area centred on (x, y)."""
    return any(board[cy][cx] != EMPTY
               for cx, cy in area_cells(x, y, radius, board_size_of(board)))


def count_sunk_ships(
    board: list[list[int]], attack_board: list[list[int]],
) -> int:
    """Count how many ships have been fully sunk."""
    sunk = 0
    for sdef in fleet_of(board_size_of(board)):
        if _check_sunk(board, attack_board, sdef["id"]) is not None:
            sunk += 1
    return sunk


def all_ships_sunk(
    board: list[list[int]], attack_board: list[list[int]],
) -> bool:
    """Check if all ships on board have been sunk."""
    return count_sunk_ships(board, attack_board) == len(fleet_of(board_size_of(board)))


def game_to_sync(gs: dict, user_id: int) -> dict:
    """Build the game portion of a sync/reconnect message for battleship."""
    color = gs["player_colors"].get(user_id, "")
    opp_color = "red" if color == "blue" else "blue"
    # Find own and opponent user_ids
    own_uid = user_id
    opp_uid = None
    for uid, c in gs["player_colors"].items():
        if c == opp_color:
            opp_uid = uid
            break
    n = gs.get("size") or DEFAULT_SIZE
    own_board = gs["boards"].get(own_uid, create_board(n))
    own_attack = gs["attack_boards"].get(opp_uid, create_board(n)) if opp_uid else create_board(n)
    # incoming_board: opponent's shots at us (MISS=-1, HIT=-2, 0=not fired)
    incoming = gs["attack_boards"].get(own_uid, create_board(n))
    own_ships = gs["placements"].get(own_uid, [])
    opp_ships = gs["placements"].get(opp_uid, []) if opp_uid else []
    # Hide opponent ship positions until sunk
    visible_opp_ships = _visible_opp_ships(
        gs["boards"].get(opp_uid, create_board(n)) if opp_uid else create_board(n),
        own_attack,
        opp_ships,
    )
    return {
        "board_size": gs.get("size") or DEFAULT_SIZE,
        # **这一局的舰队**（v13）：尺寸档位不同，舰队也不同 —— 客户端的布阵 UI
        # （船卡片、格数、武器挂靠）全靠它，所以组成只能有一个来源（服务端）。
        # 客户端只把 `name`（英文 id）翻成中文显示。
        "fleet": [{"id": d["id"], "name": d["name"], "size": d["size"]}
                  for d in fleet_of(n)],
        "phase": gs["phase"],
        "own_board": own_board,
        "own_attack_board": own_attack,
        "incoming_board": incoming,
        "own_ships": own_ships,
        "opp_ships": visible_opp_ships,
        "opp_ship_count": len(opp_ships),
        "own_sunk_count": count_sunk_ships(
            gs["boards"].get(own_uid, create_board(n)),
            gs["attack_boards"].get(own_uid, create_board(n)),
        ),
        "opp_sunk_count": count_sunk_ships(
            gs["boards"].get(opp_uid, create_board(n)) if opp_uid else create_board(n),
            own_attack,
        ),
        "next": gs["next"],
        "your_color": color,
        "winner": gs["winner"],
        "reason": gs["reason"],
        "placement_ready": gs.get("placement_ready", {}),
        "shots_fired": len(gs.get("shots", {}).get(own_uid, [])),
        # ── 特殊武器（v1.10）──
        "weapon_defs": WEAPON_DEFS,
        "own_weapons": weapons_view(
            gs.get("weapons", {}).get(own_uid),
            gs.get("weapons_used", {}).get(own_uid),
            gs["boards"].get(own_uid, create_board(n)),
            gs["attack_boards"].get(own_uid, create_board(n)),
            n,
        ),
        # 对手的挂靠关系同样下发——「找出并击沉武器舰」是对它的反制
        "opp_weapons": weapons_view(
            gs.get("weapons", {}).get(opp_uid) if opp_uid else None,
            gs.get("weapons_used", {}).get(opp_uid) if opp_uid else None,
            gs["boards"].get(opp_uid, create_board(n)) if opp_uid else create_board(n),
            own_attack,
            n,
        ),
        "own_radar": gs.get("radar_results", {}).get(own_uid, []),
    }


def _visible_opp_ships(
    opp_board: list[list[int]],
    attack_board: list[list[int]],
    opp_ships: list[dict],
) -> list[dict]:
    """Return opponent ships that have been sunk (revealed positions)."""
    visible = []
    for ship in opp_ships:
        cells = get_ship_cells(opp_board, ship["id"])
        all_hit = all(attack_board[cy][cx] == HIT for cx, cy in cells)
        if all_hit:
            visible.append({**ship, "cells": cells, "sunk": True})
    return visible


def snapshot(gs: dict) -> dict:
    """Serialize full game state for history record."""
    result: dict = {
        "game": "battleship",
        "phase": gs["phase"],
        "winner": gs["winner"],
        "reason": gs["reason"],
        "next": gs["next"],
        "player_colors": gs["player_colors"],
        "weapons": {str(uid): w for uid, w in gs.get("weapons", {}).items()},
        "placements": {},
        "shots": {},
    }
    for uid, ships in gs.get("placements", {}).items():
        board = gs["boards"].get(uid, create_board(gs.get("size") or DEFAULT_SIZE))
        result["placements"][str(uid)] = {
            "ships": ships,
            "board": board,
        }
    for uid, shots in gs.get("shots", {}).items():
        result["shots"][str(uid)] = shots
    return result
