"""军棋（陆战棋）引擎 —— 纯函数 + 自包含状态，无 IO。

棋盘 12 行 × 5 列 = 60 个停靠点，双方各占 6 行；两半之间夹着一排 3 个「前线」
（可穿过，铁路）+ 2 个「山界」（**没有边**）。完整地形见 `TOPOLOGY` —— 它是**唯一
来源**，随 `view_for` 整张下发给客户端，客户端一行拓扑规则都不写
（同深空休眠 v9：几何只有一份实现，别造两份再指望它们不漂移）。

坐标系：``row 0`` = 上方（黑方）端线，``row 11`` = 下方（红方）端线。
每方 6 行从自己前线往后退依次叫：锋线、二线、三线、四线、底线、端线。
红方占 rows 6..11，黑方占 rows 0..5（与象棋一致：红在下，客户端给黑方翻转视角）。

**信息不完全**是这个游戏的核心：暗棋模式下对手棋子的身份**压根不下发**（不是渲染时
藏），由 `view_for` 逐人裁剪 —— 与 UNO「手牌私有」同一条纪律。人机也走同一个视图，
所以它拿到的信息与真人完全一样，不许作弊。

状态是纯 dict（棋子是 ``{"c": 颜色, "p": 棋子码}``），可直接 JSON 落盘，
`persistence.py` 一行都不用改。
"""

from __future__ import annotations

import random

ROWS = 12
COLS = 5

RED = "red"
BLACK = "black"

# ------------------------------------------------------------------ 棋子

SILING = "siling"
JUNZHANG = "junzhang"
SHIZHANG = "shizhang"
LVZHANG = "lvzhang"
TUANZHANG = "tuanzhang"
YINGZHANG = "yingzhang"
LIANZHANG = "lianzhang"
PAIZHANG = "paizhang"
GONGBING = "gongbing"
DILEI = "dilei"
ZHADAN = "zhadan"
JUNQI = "junqi"

#: 军衔从大到小。工兵最小，但它是唯一能挖地雷的、也是唯一能在铁路上拐弯的。
RANKS = (SILING, JUNZHANG, SHIZHANG, LVZHANG, TUANZHANG,
         YINGZHANG, LIANZHANG, PAIZHANG, GONGBING)

#: 军衔值（司令 9 … 工兵 1）。炸弹 / 地雷 / 军旗不在这个序列里。
RANK_VALUE = {p: len(RANKS) - i for i, p in enumerate(RANKS)}

#: 每方 25 枚
PIECE_COUNTS = {
    SILING: 1, JUNZHANG: 1, SHIZHANG: 2, LVZHANG: 2, TUANZHANG: 2,
    YINGZHANG: 2, LIANZHANG: 3, PAIZHANG: 3, GONGBING: 3,
    DILEI: 3, ZHADAN: 2, JUNQI: 1,
}

#: 军旗与地雷不能移动（大本营里的棋子也不能，见 `piece_moves`）
IMMOBILE = frozenset({DILEI, JUNQI})

#: 连续多少半回合没有吃子就判和。来源写「连续 70 步」（口径不明），
#: 本项目取与象棋 `DRAW_PLIES` 相同的 120 半回合。
DRAW_PLIES = 120

MODES = ("open", "hidden")

# ------------------------------------------------------------------ 棋盘地形

POST = "post"
CAMP = "camp"
HQ = "hq"

#: 行营：二线的列 1/3（左前营/右前营）、三线的列 2（中营）、四线的列 1/3（左底营/右底营）
CAMP_CELLS = frozenset({
    (2, 1), (2, 3), (3, 2), (4, 1), (4, 3),
    (7, 1), (7, 3), (8, 2), (9, 1), (9, 3),
})

#: 大本营：端线的列 1 / 列 3（「左二」「右二」）
HQ_CELLS = frozenset({(0, 1), (0, 3), (11, 1), (11, 3)})

#: 横向铁路：底线与锋线（**不是二线/四线** —— 通俗文章这里记错得非常普遍）
RAIL_H_ROWS = frozenset({1, 5, 6, 10})
#: 纵向铁路：列 0 / 列 4，跨 row 1..10（含过界的 (5,c)-(6,c) 段）
RAIL_V_COLS = frozenset({0, 4})

#: 山界：列 1 / 列 3 的过界边**没有边**，棋子过不去
MOUNTAIN_EDGES = frozenset({((5, 1), (6, 1)), ((5, 3), (6, 3))})

#: 斜向公路（32 条，全在上方玩家半场，下方镜像）。端线之间没有斜线；
#: 每条都是「行列各差 1」的对角步，绕行营形成 X 形通路。
_DIAG_TOP = (
    (1, 0, 2, 1), (2, 1, 3, 2), (3, 2, 4, 3), (4, 3, 5, 4),
    (1, 4, 2, 3), (2, 3, 3, 2), (3, 2, 4, 1), (4, 1, 5, 0),
    (1, 2, 2, 1), (2, 1, 3, 0), (3, 0, 4, 1), (4, 1, 5, 2),
    (4, 3, 5, 2), (3, 4, 4, 3), (2, 3, 3, 4), (1, 2, 2, 3),
)
def _normalize_diag(r1, c1, r2, c2):
    return ((r1, c1), (r2, c2)) if r1 < r2 else ((r2, c2), (r1, c1))


DIAGONAL_EDGES = frozenset(
    _normalize_diag(r1, c1, r2, c2)
    for (r1, c1, r2, c2) in (
        _DIAG_TOP + tuple((11 - r1, c1, 11 - r2, c2) for (r1, c1, r2, c2) in _DIAG_TOP)
    )
)


def in_board(r: int, c: int) -> bool:
    return 0 <= r < ROWS and 0 <= c < COLS


def other(color: str) -> str:
    return BLACK if color == RED else RED


def _edge_kind(a: tuple[int, int], b: tuple[int, int]) -> str | None:
    """两点之间的连线类型：``"rail"``（铁路）/ ``"road"``（公路）/ ``None``（不相连）。"""
    (r1, c1), (r2, c2) = a, b
    if r1 == r2:                                   # 横向
        return "rail" if r1 in RAIL_H_ROWS else "road"
    if c1 == c2:                                   # 纵向
        lo, hi = (r1, r2) if r1 < r2 else (r2, r1)
        if (lo, hi) == (5, 6) and c1 in (1, 3):
            return None                            # 山界
        if c1 in RAIL_V_COLS and lo >= 1 and hi <= 10:
            return "rail"
        if c1 == 2 and (lo, hi) == (5, 6):
            return "rail"                          # 中路只有中缝这一段是铁路
        return "road"
    if abs(r1 - r2) == 1 and abs(c1 - c2) == 1:    # 斜向（只有 32 条，全是公路）
        return "road" if (a, b) in DIAGONAL_EDGES else None
    return None


def _iter_edges():
    """遍历全部 133 条边，每条**只出一次**。

    斜边会被左右两个端点各生成一次（`(r,c)→(r+1,c+1)` 与 `(r,c+2)→(r+1,c+1)`
    是同一条），所以这里按规范化后的端点对去重 —— 不去重会让斜向落点在
    合法着法里出现两次。
    """
    seen = set()
    for r in range(ROWS):
        for c in range(COLS):
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                r2, c2 = r + dr, c + dc
                if not in_board(r2, c2):
                    continue
                a, b = (r, c), (r2, c2)
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                kind = _edge_kind(a, b)
                if kind:
                    yield a, b, kind


def _build_adjacency() -> dict:
    adj = {(r, c): {"rail": [], "road": []} for r in range(ROWS) for c in range(COLS)}
    for a, b, kind in _iter_edges():
        adj[a][kind].append(b)
        adj[b][kind].append(a)
    return adj


NEIGHBORS = _build_adjacency()


def _build_topology() -> dict:
    kinds = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            if (r, c) in HQ_CELLS:
                row.append(HQ)
            elif (r, c) in CAMP_CELLS:
                row.append(CAMP)
            else:
                row.append(POST)
        kinds.append(row)
    rails, roads = [], []
    for a, b, kind in _iter_edges():
        (rails if kind == "rail" else roads).append([a[0], a[1], b[0], b[1]])
    return {
        "rows": ROWS,
        "cols": COLS,
        "kinds": kinds,
        "rails": rails,
        "roads": roads,
        # 前线（可穿过的三条过界铁路）与山界（两条无边）—— 客户端要画它们
        "fronts": [[5, 0, 6, 0], [5, 2, 6, 2], [5, 4, 6, 4]],
        "mountains": [[5, 1, 6, 1], [5, 3, 6, 3]],
    }


TOPOLOGY = _build_topology()

# ------------------------------------------------------------------ 半场与布阵


def own_rows(color: str) -> range:
    """该方占据的行（红在下 rows 6..11，黑在上 rows 0..5）。"""
    return range(6, ROWS) if color == RED else range(0, 6)


def front_row(color: str) -> int:
    """该方的第一排（锋线）—— 炸弹不能放在这里。"""
    return 6 if color == RED else 5


def back_two_rows(color: str) -> frozenset:
    """该方的最后两排（底线 + 端线）—— 地雷只能放在这里。"""
    rows = (10, 11) if color == RED else (0, 1)
    return frozenset((r, c) for r in rows for c in range(COLS))


def own_cells(color: str) -> list[tuple[int, int]]:
    """该方可布阵的格子 = 23 个兵站 + 2 个大本营（**行营不能放子**），恰好 25 个。"""
    return [(r, c) for r in own_rows(color) for c in range(COLS)
            if (r, c) not in CAMP_CELLS]


def hq_cells(color: str) -> list[tuple[int, int]]:
    return [(11, 1), (11, 3)] if color == RED else [(0, 1), (0, 3)]


def _placement_error(color: str, cell: tuple[int, int], piece: str) -> str | None:
    if piece == JUNQI and cell not in HQ_CELLS:
        return "军旗必须放在大本营"
    if piece == DILEI and cell not in back_two_rows(color):
        return "地雷只能放在最后两排"
    if piece == ZHADAN and cell[0] == front_row(color):
        return "炸弹不能放在第一排"
    return None


def random_layout(color: str, rng: random.Random | None = None) -> dict:
    """随机生成一套**合法**阵型（默认阵型与「随机」按钮都用它）。

    顺序：军旗 → 随机一个大本营；地雷 → 最后两排里挑 3 格；炸弹 → 非第一排挑 2 格；
    其余 19 枚随机铺满剩下 19 格。每一步都只从合法格里挑，所以结果必然合法。
    """
    rng = rng or random
    cells = own_cells(color)
    layout: dict[tuple[int, int], str] = {}
    flag_at = rng.choice(hq_cells(color))
    layout[flag_at] = JUNQI
    used = {flag_at}
    mine_slots = [s for s in back_two_rows(color) if s not in used]
    for s in rng.sample(mine_slots, PIECE_COUNTS[DILEI]):
        layout[s] = DILEI
        used.add(s)
    rest = [s for s in cells if s not in used]
    non_front = [s for s in rest if s[0] != front_row(color)]
    for s in rng.sample(non_front, PIECE_COUNTS[ZHADAN]):
        layout[s] = ZHADAN
        used.add(s)
    others = [p for p, n in PIECE_COUNTS.items()
              if p not in (JUNQI, DILEI, ZHADAN) for _ in range(n)]
    rng.shuffle(others)
    free = [s for s in cells if s not in used]
    for s, p in zip(free, others):
        layout[s] = p
    return layout


# ------------------------------------------------------------------ 经典阵型

#: 每个阵型是 6 行 × 5 列，**第一行是锋线**（照自己这半边从前往后写），
#: `·` 是行营（不能放子）。写法照抄公开阵库，逐格与本作的地形对得上：
#: 行营恰好落在二~四线的 5 个 `CAMP_CELLS` 上、军旗落在大本营、地雷在后两排、
#: 炸弹不在第一排 —— `test_luzhanqi.py::TestPresets` 对两个颜色都跑一遍 `validate_layout`。
_PRESET_ROWS: dict[str, tuple[str, ...]] = {
    "shuangxi": ("师兵连排师", "旅·连·炸", "炸营·团旅", "军·兵·连", "司兵营雷排", "团排雷旗雷"),
    "wuye": ("连旅司兵团", "师·炸·军", "团排·连兵", "排·营·营", "雷雷连师炸", "雷旗旅排兵"),
    "feihua": ("连司军兵师", "师·连·旅", "团弹·弹团", "营·排·营", "旅兵兵排雷", "雷旗雷排连"),
    "piaoxiang": ("师兵连旅师", "团·连·炸", "团营·炸营", "司·兵·连", "军兵旅雷排", "排排雷旗雷"),
    "sanjie": ("团排军兵司", "连·兵·团", "师弹·兵连", "排·连·旅", "雷营师弹旅", "雷旗雷排营"),
}

_PRESET_GLYPH = {
    "司": SILING, "军": JUNZHANG, "师": SHIZHANG, "旅": LVZHANG,
    "团": TUANZHANG, "营": YINGZHANG, "连": LIANZHANG, "排": PAIZHANG,
    # 「弹」与「炸」都是炸弹 —— 阵库里两种写法混用（飞花逐月/三节阵写「弹」，
    # 双喜临门/午夜风铃/飘香一剑写「炸」），照抄原文，别去统一它们。
    "兵": GONGBING, "雷": DILEI, "弹": ZHADAN, "炸": ZHADAN, "旗": JUNQI,
}

#: 阵型的展示名与顺序。**由服务端下发给客户端**，客户端只按它渲染芯片 ——
#: 同「房间设置」那条约定（客户端不抄第二份档位表）。
PRESETS: tuple[tuple[str, str], ...] = (
    ("shuangxi", "双喜临门"),
    ("wuye", "午夜风铃"),
    ("feihua", "飞花逐月"),
    ("piaoxiang", "飘香一剑"),
    ("sanjie", "三节阵"),
)


def preset_layout(color: str, name: str) -> dict | None:
    """取一套经典阵型；名字不认识返回 ``None``（调用方回一句中文错误）。

    阵型是**从自己这半边写的**（第一行＝锋线），所以两个颜色各自镜像一次：
    黑在上 ``row = 5 - i``，红在下 ``row = 6 + i``。列不镜像 —— 双方各按自己
    的视角读同一张图，两边摆出来的就是同一套阵。
    """
    rows = _PRESET_ROWS.get(name)
    if not rows:
        return None
    layout: dict[tuple[int, int], str] = {}
    for i, line in enumerate(rows):
        r = 5 - i if color == BLACK else 6 + i
        for j, glyph in enumerate(line):
            piece = _PRESET_GLYPH.get(glyph)
            if piece:
                layout[(r, j)] = piece
    return layout


def presets_payload() -> list[dict]:
    """下发给客户端的阵型表（键 + 中文名）。"""
    return [{"key": k, "label": v} for k, v in PRESETS]


def validate_layout(color: str, layout: dict) -> str | None:
    """校验一套阵型，合法返回 ``None``，否则返回中文错误。"""
    cells = set(own_cells(color))
    if len(layout) != len(cells):
        return "布阵必须摆满自己半场的 25 个位置"
    counts: dict[str, int] = {}
    for cell, piece in layout.items():
        if tuple(cell) not in cells:
            return "棋子只能摆在自己半场的兵站与大本营上"
        if piece not in PIECE_COUNTS:
            return f"未知棋子 {piece}"
        counts[piece] = counts.get(piece, 0) + 1
    for piece, n in PIECE_COUNTS.items():
        if counts.get(piece, 0) != n:
            return "棋子数量不对"
    for cell, piece in layout.items():
        err = _placement_error(color, tuple(cell), piece)
        if err:
            return err
    return None


def fill_layout(gs: dict, color: str, layout: dict) -> None:
    for cell, piece in layout.items():
        r, c = cell
        gs["board"][r][c] = {"c": color, "p": piece}


def layout_of(gs: dict, color: str) -> dict:
    out = {}
    for r in own_rows(color):
        for c in range(COLS):
            cell = gs["board"][r][c]
            if cell:
                out[(r, c)] = cell["p"]
    return out


# ------------------------------------------------------------------ 走法


def _in_camp(cell: tuple[int, int]) -> bool:
    return cell in CAMP_CELLS


def _can_land(board, color: str, r: int, c: int) -> bool:
    """能不能落在 (r,c)：空着可以；敌方棋子可以吃，**但行营里的不能吃**；己方不行。"""
    target = board[r][c]
    if target is None:
        return True
    if target["c"] == color:
        return False
    return not _in_camp((r, c))


def _engineer_rail_moves(board, color: str, r: int, c: int) -> list:
    """工兵：在铁路网上任意行走（**可拐弯**），被棋子挡住就过不去。"""
    if not NEIGHBORS[(r, c)]["rail"]:
        return []
    seen = {(r, c)}
    out = []
    stack = [(r, c)]
    while stack:
        cur = stack.pop()
        for nxt in NEIGHBORS[cur]["rail"]:
            if nxt in seen:
                continue
            seen.add(nxt)
            if not _can_land(board, color, *nxt):
                continue
            out.append(nxt)
            if board[nxt[0]][nxt[1]] is None:
                stack.append(nxt)          # 只能穿过空格继续走
    return out


def _rail_slide_moves(board, color: str, r: int, c: int) -> list:
    """非工兵：沿铁路**直线**不限格数，遇到棋子就停（是敌子且不在行营就吃掉）。"""
    out = []
    for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        cur = (r, c)
        while True:
            nxt = (cur[0] + dr, cur[1] + dc)
            if nxt not in NEIGHBORS[cur]["rail"]:
                break
            if not _can_land(board, color, *nxt):
                break
            out.append(nxt)
            if board[nxt[0]][nxt[1]] is not None:
                break
            cur = nxt
    return out


def piece_moves(board, r: int, c: int) -> list:
    """(r,c) 上那枚棋子的全部合法落点。"""
    cell = board[r][c]
    if not cell or cell["p"] is None:
        return []
    piece, color = cell["p"], cell["c"]
    if piece in IMMOBILE or (r, c) in HQ_CELLS:
        return []
    dests = [nxt for nxt in NEIGHBORS[(r, c)]["road"] if _can_land(board, color, *nxt)]
    if (r, c) in CAMP_CELLS:
        return dests                       # 行营里的棋子每次只能走一步
    if piece == GONGBING:
        dests.extend(_engineer_rail_moves(board, color, r, c))
    else:
        dests.extend(_rail_slide_moves(board, color, r, c))
    return dests


def all_moves(board, color: str) -> list:
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if cell and cell["c"] == color:
                for dst in piece_moves(board, r, c):
                    out.append(((r, c), dst))
    return out


def has_any_move(board, color: str) -> bool:
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if cell and cell["c"] == color and piece_moves(board, r, c):
                return True
    return False


def alive_counts(board, color: str) -> dict:
    counts: dict[str, int] = {}
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if cell and cell["c"] == color:
                counts[cell["p"]] = counts.get(cell["p"], 0) + 1
    return counts


# ------------------------------------------------------------------ 碰子


def resolve_combat(attacker: str, defender: str) -> str:
    """碰子结果：``attacker_dies`` / ``defender_dies`` / ``both_die``。

    **军旗不在这里处理**（碰到军旗直接判胜，见 `make_move`）；地雷不会主动攻击。
    """
    if attacker == ZHADAN or defender == ZHADAN:
        return "both_die"
    if defender == DILEI:
        return "defender_dies" if attacker == GONGBING else "attacker_dies"
    a, d = RANK_VALUE[attacker], RANK_VALUE[defender]
    if a > d:
        return "defender_dies"
    if a < d:
        return "attacker_dies"
    return "both_die"


# ------------------------------------------------------------------ 对局状态


def create_game(mode: str = "open", reveal: bool = True) -> dict:
    """新开一局（形状与象棋 `create_game` 对齐，方便复用 room_manager 的通用路径）。"""
    return {
        "board": [[None] * COLS for _ in range(ROWS)],
        "moves": [],
        "next": RED,
        "winner": None,
        "reason": None,
        "player_colors": {},
        "finished": False,
        "last_move": None,
        "no_capture": 0,
        "phase": "setup",                       # setup → playing
        "mode": mode if mode in MODES else "open",
        "reveal": bool(reveal),                 # 暗棋下阵亡是否公开身份
        "ready": {},                            # {颜色: 是否已确认布阵}
        "flag_revealed": {},                    # {颜色: 司令阵亡后是否已亮军旗}
        "battles": [],                          # 战报（按收件人裁剪后才下发）
    }


def setup_ready(gs: dict) -> bool:
    return all(gs.get("ready", {}).get(c) for c in (RED, BLACK))


def confirm_setup(gs: dict, color: str) -> None:
    gs.setdefault("ready", {})[color] = True
    if setup_ready(gs):
        gs["phase"] = "playing"


def swap_layout(gs: dict, color: str, a: tuple, b: tuple) -> str | None:
    """交换自己半场两枚棋子。合法返回 ``None``，否则返回中文错误并**原样回滚**。

    这是布阵约束的**唯一判定点** —— 客户端不做任何校验，只把交换请求发上来。
    """
    if gs.get("phase") != "setup":
        return "现在不是布阵阶段"
    if gs.get("ready", {}).get(color):
        return "已经确认布阵，不能再改"
    a, b = tuple(a), tuple(b)
    for cell in (a, b):
        if not in_board(*cell) or cell[0] not in own_rows(color):
            return "只能交换自己半场的棋子"
        cur = gs["board"][cell[0]][cell[1]]
        if not cur or cur["c"] != color:
            return "只能交换自己的棋子"
    pa = gs["board"][a[0]][a[1]]["p"]
    pb = gs["board"][b[0]][b[1]]["p"]
    gs["board"][a[0]][a[1]]["p"] = pb
    gs["board"][b[0]][b[1]]["p"] = pa
    err = _placement_error(color, a, pb) or _placement_error(color, b, pa)
    if err:
        gs["board"][a[0]][a[1]]["p"] = pa
        gs["board"][b[0]][b[1]]["p"] = pb
        return err
    return None


def _finish(gs: dict, winner: str, reason: str) -> None:
    gs["winner"] = winner
    gs["reason"] = reason
    gs["finished"] = True


def make_move(gs: dict, frm, to) -> dict:
    """走一步棋。返回 ``{"ok", "move", "game_over"}``（形状同象棋）。"""
    if gs.get("finished") or gs.get("winner"):
        return {"ok": False, "error": "对局已结束"}
    if gs.get("phase") != "playing":
        return {"ok": False, "error": "还没开始对局"}
    fr, fc = int(frm[0]), int(frm[1])
    tr, tc = int(to[0]), int(to[1])
    if not (in_board(fr, fc) and in_board(tr, tc)):
        return {"ok": False, "error": "落点超出棋盘"}
    cell = gs["board"][fr][fc]
    if not cell:
        return {"ok": False, "error": "起点没有棋子"}
    if cell["c"] != gs["next"]:
        return {"ok": False, "error": "还没轮到你走"}
    if (tr, tc) not in piece_moves(gs["board"], fr, fc):
        return {"ok": False, "error": "这步棋不合规则"}

    mover, piece = cell["c"], cell["p"]
    revealed_before = dict(gs.get("flag_revealed", {}))
    battle = _apply_move(gs, fr, fc, tr, tc, mover, piece)
    # 先记账再判终局：`_result` 是从 `last_move` 拼消息的，漏了这步会发出上一步的位置。
    _record(gs, fr, fc, tr, tc, piece,
            battle["defender_piece"] if battle else None,
            battle["outcome"] if battle else None, mover)
    if battle and battle.get("flag_captured"):     # 吃掉军旗即胜
        _finish(gs, mover, "flag")
        return _result(gs, battle)
    gs["next"] = other(mover)
    _settle(gs, mover)
    newly = [{"color": c, "at": at}
             for c in (RED, BLACK)
             if gs.get("flag_revealed", {}).get(c) and not revealed_before.get(c)
             for at in [find_flag(gs["board"], c)] if at]
    return _result(gs, battle, newly)


def _apply_move(gs: dict, fr, fc, tr, tc, mover: str, piece: str) -> dict | None:
    """落子并结算碰子，返回本条战报（走到空格则返回 None）。

    **原地改 `gs["board"]`**：按碰子结果决定谁留在棋盘上 ——
    攻击方阵亡就留在原地，同归于尽就两格都清空。
    """
    target = gs["board"][tr][tc]
    if target is None:
        gs["board"][tr][tc] = gs["board"][fr][fc]
        gs["board"][fr][fc] = None
        gs["no_capture"] += 1
        return None
    captured = target["p"]
    battle = {"at": [tr, tc], "attacker": mover, "attacker_piece": piece,
              "defender": target["c"], "defender_piece": captured}
    gs["no_capture"] = 0
    if captured == JUNQI:
        gs["board"][tr][tc] = gs["board"][fr][fc]
        gs["board"][fr][fc] = None
        battle["outcome"] = "defender_dies"
        battle["flag_captured"] = True
        gs["battles"].append(battle)
        return battle
    outcome = resolve_combat(piece, captured)
    battle["outcome"] = outcome
    if outcome == "attacker_dies":
        gs["board"][fr][fc] = None
    elif outcome == "defender_dies":
        gs["board"][tr][tc] = gs["board"][fr][fc]
        gs["board"][fr][fc] = None
    else:
        gs["board"][tr][tc] = None
        gs["board"][fr][fc] = None
    for dcolor, dpiece in (
        ((mover, piece) if outcome in ("attacker_dies", "both_die") else (None, None)),
        ((target["c"], captured) if outcome in ("defender_dies", "both_die") else (None, None)),
    ):
        if dpiece == SILING:                       # 司令阵亡 → 该方必须亮出军旗
            gs.setdefault("flag_revealed", {})[dcolor] = True
    gs["battles"].append(battle)
    return battle


def _settle(gs: dict, mover: str) -> None:
    """走完一步之后判胜负：无棋可走判负、长时间无吃子判和。"""
    if not has_any_move(gs["board"], gs["next"]):
        _finish(gs, mover, "nomove")
    elif gs["no_capture"] >= DRAW_PLIES:
        _finish(gs, "draw", "no_capture")


def _record(gs, fr, fc, tr, tc, piece, captured, outcome, mover) -> None:
    gs["last_move"] = {"from": [fr, fc], "to": [tr, tc], "by": mover}
    gs["moves"].append({"from": [fr, fc], "to": [tr, tc], "piece": piece,
                        "captured": captured, "outcome": outcome})


def find_flag(board, color: str) -> list | None:
    """该方军旗的坐标（`[r, c]`），找不到返回 None。"""
    for r in range(ROWS):
        for c in range(COLS):
            cell = board[r][c]
            if cell and cell["c"] == color and cell["p"] == JUNQI:
                return [r, c]
    return None


def _result(gs: dict, battle: dict | None, newly_revealed: list | None = None) -> dict:
    move = {"from": gs["last_move"]["from"], "to": gs["last_move"]["to"],
            "by": gs["last_move"]["by"], "battle": battle,
            "next": gs["next"], "flag_revealed": dict(gs.get("flag_revealed", {})),
            # 司令刚阵亡 → 把「亮出来的那面军旗在哪」也发下去。
            # 光发一个布尔值，对手知道「军旗亮了」却看不见它在哪 —— 这条规则就白设了。
            "newly_revealed": newly_revealed or []}
    game_over = None
    if gs["winner"]:
        game_over = {"winner": gs["winner"], "reason": gs["reason"]}
    return {"ok": True, "move": move, "game_over": game_over}


def surrender(gs: dict, color: str) -> dict:
    """认输（room_manager 的通用路径会直接写 winner/reason，这里供单测/直接调用）。"""
    if gs.get("finished") or gs.get("winner"):
        return {"ok": False, "error": "对局已结束"}
    _finish(gs, other(color), "surrender")
    return {"ok": True, "game_over": {"winner": gs["winner"], "reason": gs["reason"]}}


# ------------------------------------------------------------------ 视图（逐人裁剪）

# ⚠️ 服务端**不需要**棋子码 → 中文名的映射（错误文案里也不出现棋子名）。
# 界面上的名字由客户端 `app/src/luzhanqi.ts` 映射（同 `uno.ts` 的定位：展示层解析、无规则）。
# 这里刻意不留一份，免得两边各写一套名字。


def mask_battle(battle: dict, viewer_color: str, mode: str, reveal: bool) -> dict | None:
    """按收件人裁剪一条战报。

    明棋全公开；暗棋只有两种口径：``reveal=True`` 公开阵亡身份，
    ``reveal=False`` 只公布「谁出局」—— 后者是经典暗棋（裁判只喊「红下/黑下/同下」）。
    **自己那一枚永远知道是什么**（那是你自己的棋子）。
    """
    if not battle:
        return None
    if mode != "hidden" or reveal:
        return dict(battle)
    out = {k: v for k, v in battle.items() if k not in ("attacker_piece", "defender_piece")}
    if battle["attacker"] == viewer_color:
        out["attacker_piece"] = battle["attacker_piece"]
    if battle["defender"] == viewer_color:
        out["defender_piece"] = battle["defender_piece"]
    return out


def legal_payload(gs: dict, color: str) -> list:
    """**只给该颜色自己**的合法着法，扁平 ``[fr, fc, tr, tc]``。

    ⚠️ 不能像象棋那样「把轮走方的着法全发下去」—— 暗棋里「某个位置有子可动」
    本身就泄露了「它不是地雷也不是军旗」。所以只算收件人自己的。
    """
    if gs.get("phase") != "playing" or gs.get("finished") or gs.get("winner"):
        return []
    if gs["next"] != color:
        return []
    return [[fr, fc, tr, tc] for (fr, fc), (tr, tc) in all_moves(gs["board"], color)]


def view_for(gs: dict, viewer_color: str) -> dict:
    """给某个颜色的对局视图 —— **暗棋下对手棋子的身份压根不出现在这里**。

    布阵阶段**两种模式都扣着对手的半场**：明棋也是「各自摆好、摆完同时亮出」，
    边摆边被对手盯着看反而怪（也让布阵没法藏拙）。
    """
    mode = gs.get("mode", "open")
    reveal = bool(gs.get("reveal", True))
    revealed = gs.get("flag_revealed", {})
    hide_other = mode == "hidden" or gs.get("phase") == "setup"
    cells = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            cell = gs["board"][r][c]
            if cell is None:
                row.append(None)
            elif hide_other and cell["c"] != viewer_color:
                # 对手的棋子一律扣着；**唯一例外**是「司令阵亡后亮出的那面军旗」
                show_flag = cell["p"] == JUNQI and revealed.get(cell["c"])
                row.append({"c": cell["c"], "p": JUNQI if show_flag else None})
            else:
                row.append({"c": cell["c"], "p": cell["p"]})
        cells.append(row)
    battles = [b for b in (mask_battle(x, viewer_color, mode, reveal)
                           for x in gs.get("battles", [])) if b]
    return {
        "rows": ROWS,
        "cols": COLS,
        "cells": cells,
        "topology": TOPOLOGY,
        "phase": gs.get("phase", "setup"),
        "mode": mode,
        "reveal": reveal,
        "next": gs["next"],
        "your_color": viewer_color,
        "winner": gs["winner"],
        "reason": gs["reason"],
        "finished": bool(gs.get("finished")),
        "last_move": gs.get("last_move"),
        "flag_revealed": dict(revealed),
        "ready": dict(gs.get("ready", {})),
        "alive": alive_counts(gs["board"], viewer_color),
        "battles": battles[-8:],
        "legal": legal_payload(gs, viewer_color),
        # 布阵阶段可选用的经典阵型（名字与顺序都由服务端给）
        "presets": presets_payload(),
    }


def game_to_sync(gs: dict, user_id: int) -> dict:
    """sync / game_start 里的对局视图（照象棋 `game_to_sync` 的形状）。"""
    return view_for(gs, gs["player_colors"].get(user_id, ""))


def snapshot(gs: dict) -> dict:
    """终局快照（存进战绩，供历史页回放）。**这里给的是全量真相** ——
    对局已经结束，暗棋也没有藏的必要，全量反而更有复盘价值。

    连 `topology` 一起存：战绩页复用对局屏那一份棋盘画法，而棋盘的线要照地形画
    （铁路粗双线 / 公路细线 / 行营圆圈 / 山界折线），没有地形就画不出来。
    """
    return {
        "game": "luzhanqi",
        "rows": ROWS,
        "cols": COLS,
        "mode": gs.get("mode", "open"),
        "topology": TOPOLOGY,
        "board": gs.get("board", []),
        "moves": gs.get("moves", []),
        "winner": gs.get("winner"),
        "reason": gs.get("reason"),
    }
