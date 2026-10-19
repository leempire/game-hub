"""中国象棋（Xiangqi）纯引擎 —— 无 IO，便于单测。

棋盘 10 行 × 9 列，行 0 在最上（黑方底线）、行 9 在最下（红方底线），**红先**。
棋子用单字符表示（与象棋 FEN 一致，红方大写、黑方小写）：

    K/k 帅将   A/a 仕士   B/b 相象   N/n 马   R/r 车   C/c 炮   P/p 兵卒

河界在行 4 与行 5 之间：红方（大写）只在行 5..9 活动，黑方只在行 0..4 活动。
相/象不过河、仕/士与帅/将不出九宫（红 7..9 行、黑 0..2 行、列 3..5）。

两条容易被忽略但**必须**实现的规则：
- **将帅照面**：两王同列且中间无子时，该局面非法（等同于被将军）。见 `is_in_check`。
- **困毙判负**：无子可动但没被将军（西方象棋判和），象棋里**判负**。见 `game_status`。
"""

ROWS = 10
COLS = 9

RED = "red"
BLACK = "black"

#: 开局局面（红方在下，大写）。
_START_ROWS = (
    "rnbakabnr",
    ".........",
    ".c.....c.",
    "p.p.p.p.p",
    ".........",
    ".........",
    "P.P.P.P.P",
    ".C.....C.",
    ".........",
    "RNBAKABNR",
)

_ORTHO = ((-1, 0), (1, 0), (0, -1), (0, 1))
_DIAG = ((-1, -1), (-1, 1), (1, -1), (1, 1))

#: 马走日：dr/dc 是落点偏移，lr/lc 是「马腿」（被别住则走不了）。
_HORSE = (
    (-2, -1, -1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (2, 1, 1, 0),
    (-1, -2, 0, -1), (1, -2, 0, -1), (-1, 2, 0, 1), (1, 2, 0, 1),
)

#: 60 回合（120 个半回合）无吃子判和——这条规则同时保证了棋局必然终止，
#: 所以不再单独做「三次重复局面」检测。
DRAW_PLIES = 120


# ── 棋盘与棋子 ────────────────────────────────────────

def create_board() -> list[list[str | None]]:
    """标准开局棋盘（红方在下）。"""
    return [[None if ch == "." else ch for ch in row] for row in _START_ROWS]


def board_from_rows(rows: list[str]) -> list[list[str | None]]:
    """从 10 行字符串建棋盘（单测/摆残局用）。"""
    if len(rows) != ROWS:
        raise ValueError(f"需要 {ROWS} 行")
    return [[None if ch == "." else ch for ch in row] for row in rows]


def rows_of(board: list[list[str | None]]) -> list[str]:
    """棋盘 → 10 行字符串（快照/断言用）。"""
    return ["".join(ch or "." for ch in row) for row in board]


def color_of(piece: str) -> str:
    return RED if piece.isupper() else BLACK


def type_of(piece: str) -> str:
    return piece.upper()


def other(color: str) -> str:
    return BLACK if color == RED else RED


def in_board(r: int, c: int) -> bool:
    return 0 <= r < ROWS and 0 <= c < COLS


def in_palace(r: int, c: int, color: str) -> bool:
    """九宫：列 3..5，红方 7..9 行、黑方 0..2 行。"""
    if not 3 <= c <= 5:
        return False
    return 7 <= r <= 9 if color == RED else 0 <= r <= 2


def on_own_side(r: int, color: str) -> bool:
    """是否还在自己这半边（相/象不过河）。"""
    return r >= 5 if color == RED else r <= 4


def find_king(board: list[list[str | None]], color: str) -> tuple[int, int] | None:
    target = "K" if color == RED else "k"
    for r in range(ROWS):
        for c in range(COLS):
            if board[r][c] == target:
                return (r, c)
    return None


# ── 走法生成（伪合法：不管走完是否自己被将军）────────────────

def _can_land(board, r, c, color) -> bool:
    if not in_board(r, c):
        return False
    piece = board[r][c]
    return piece is None or color_of(piece) != color


def _king_moves(board, r, c, color):
    return [(r + dr, c + dc) for dr, dc in _ORTHO
            if in_palace(r + dr, c + dc, color) and _can_land(board, r + dr, c + dc, color)]


def _advisor_moves(board, r, c, color):
    return [(r + dr, c + dc) for dr, dc in _DIAG
            if in_palace(r + dr, c + dc, color) and _can_land(board, r + dr, c + dc, color)]


def _elephant_moves(board, r, c, color):
    out = []
    for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):
        nr, nc = r + dr, c + dc
        if not on_own_side(nr, color) or not _can_land(board, nr, nc, color):
            continue
        if board[r + dr // 2][c + dc // 2] is not None:   # 塞象眼
            continue
        out.append((nr, nc))
    return out


def _horse_moves(board, r, c, color):
    out = []
    for dr, dc, lr, lc in _HORSE:
        nr, nc = r + dr, c + dc
        if not _can_land(board, nr, nc, color):
            continue
        if board[r + lr][c + lc] is not None:             # 蹩马腿
            continue
        out.append((nr, nc))
    return out


def _ray_moves(board, r, c, color):
    """车：沿四个正向滑到被挡住为止。"""
    out = []
    for dr, dc in _ORTHO:
        nr, nc = r + dr, c + dc
        while in_board(nr, nc) and board[nr][nc] is None:
            out.append((nr, nc))
            nr += dr
            nc += dc
        if in_board(nr, nc) and color_of(board[nr][nc]) != color:
            out.append((nr, nc))
    return out


def _cannon_moves(board, r, c, color):
    """炮：不吃子时同车；吃子必须隔且只隔一个「炮架」。"""
    out = []
    for dr, dc in _ORTHO:
        nr, nc = r + dr, c + dc
        while in_board(nr, nc) and board[nr][nc] is None:
            out.append((nr, nc))
            nr += dr
            nc += dc
        if not in_board(nr, nc):
            continue
        nr += dr                    # 跳过炮架
        nc += dc
        while in_board(nr, nc) and board[nr][nc] is None:
            nr += dr
            nc += dc
        if in_board(nr, nc) and color_of(board[nr][nc]) != color:
            out.append((nr, nc))
    return out


def _pawn_moves(board, r, c, color):
    """兵/卒：只能向前；过河后可以横走，永不后退。"""
    out = []
    nr = r - 1 if color == RED else r + 1
    if _can_land(board, nr, c, color):
        out.append((nr, c))
    crossed = r <= 4 if color == RED else r >= 5
    if crossed:
        for dc in (-1, 1):
            if _can_land(board, r, c + dc, color):
                out.append((r, c + dc))
    return out


_MOVERS = {
    "K": _king_moves,
    "A": _advisor_moves,
    "B": _elephant_moves,
    "N": _horse_moves,
    "R": _ray_moves,
    "C": _cannon_moves,
    "P": _pawn_moves,
}


def pseudo_moves(board, r, c) -> list[tuple[int, int]]:
    """(r,c) 上棋子的伪合法走法（未过滤「自己被将军」）。"""
    if not in_board(r, c):
        return []
    piece = board[r][c]
    if piece is None:
        return []
    return _MOVERS[type_of(piece)](board, r, c, color_of(piece))


# ── 将军判定 ──────────────────────────────────────────
# 从「王所在的格」反向扫，比枚举对方全部走法快一个量级——AI 搜索里
# is_in_check 是热路径，慢一点整棵搜索树就塌了。

def _attacked_by_ray(board, r, c, by_color) -> bool:
    """车（无遮挡）/ 炮（隔一子）/ 将帅照面（同列且中间无子）。"""
    for dr, dc in _ORTHO:
        nr, nc = r + dr, c + dc
        while in_board(nr, nc) and board[nr][nc] is None:
            nr += dr
            nc += dc
        if not in_board(nr, nc):
            continue
        first = board[nr][nc]
        if color_of(first) == by_color:
            kind = type_of(first)
            if kind == "R":
                return True
            if kind == "K" and dc == 0:
                return True          # 将帅照面：同列、中间无子
        nr += dr                     # 第一个子当炮架
        nc += dc
        while in_board(nr, nc) and board[nr][nc] is None:
            nr += dr
            nc += dc
        if in_board(nr, nc):
            second = board[nr][nc]
            if color_of(second) == by_color and type_of(second) == "C":
                return True
    return False


def _attacked_by_horse(board, r, c, by_color) -> bool:
    for dr, dc, _, _ in _HORSE:
        hr, hc = r + dr, c + dc
        if not in_board(hr, hc):
            continue
        piece = board[hr][hc]
        if piece is None or color_of(piece) != by_color or type_of(piece) != "N":
            continue
        if (r, c) in _horse_moves(board, hr, hc, by_color):
            return True
    return False


def _attacked_by_short(board, r, c, by_color) -> bool:
    """兵/卒、仕/士、相/象——都是短距离，直接看候选来源格。"""
    forward = -1 if by_color == RED else 1
    pr = r - forward                                   # 兵/卒正前方来的那一格
    if in_board(pr, c):
        piece = board[pr][c]
        if piece is not None and color_of(piece) == by_color and type_of(piece) == "P":
            return True
    crossed = r <= 4 if by_color == RED else r >= 5     # 兵/卒横走需已过河
    if crossed:
        for dc in (-1, 1):
            if not in_board(r, c + dc):
                continue
            piece = board[r][c + dc]
            if piece is not None and color_of(piece) == by_color and type_of(piece) == "P":
                return True
    for dr, dc in _DIAG:                               # 仕/士
        ar, ac = r + dr, c + dc
        if not in_board(ar, ac):
            continue
        piece = board[ar][ac]
        if piece is not None and color_of(piece) == by_color and type_of(piece) == "A":
            return True
    for dr, dc in ((-2, -2), (-2, 2), (2, -2), (2, 2)):   # 相/象（塞象眼则不算）
        br, bc = r + dr, c + dc
        if not in_board(br, bc):
            continue
        piece = board[br][bc]
        if piece is None or color_of(piece) != by_color or type_of(piece) != "B":
            continue
        if board[r + dr // 2][c + dc // 2] is None:
            return True
    return False


def in_check_at(board, kr: int, kc: int, color: str) -> bool:
    """假定 color 方的王在 (kr, kc)，判断它是否正被攻击。

    单独拆出来是因为 `find_king` 要扫 90 格，而 AI 搜索里这是最热的路径——
    调用方已经知道王在哪时就不该再找一遍。
    """
    enemy = other(color)
    return (_attacked_by_ray(board, kr, kc, enemy)
            or _attacked_by_horse(board, kr, kc, enemy)
            or _attacked_by_short(board, kr, kc, enemy))


def is_in_check(board: list[list[str | None]], color: str) -> bool:
    """color 方是否被将军（含「将帅照面」这一象棋特有的非法局面）。"""
    king = find_king(board, color)
    if king is None:
        return True                    # 王不在盘上（已被吃）——按最坏情况处理
    return in_check_at(board, king[0], king[1], color)


# ── 合法走法 ──────────────────────────────────────────

def _legal_moves_from(board, r: int, c: int, color: str,
                      kr: int, kc: int) -> list[tuple[int, int]]:
    """已知 color 方的王在 (kr,kc) 时，(r,c) 上棋子的合法走法。"""
    piece = board[r][c]
    king_moving = (r == kr and c == kc)
    out = []
    for nr, nc in pseudo_moves(board, r, c):
        captured = board[nr][nc]
        board[nr][nc] = piece
        board[r][c] = None
        # 动的不是王 → 王位不变，只需复查原来那一格
        safe = not (in_check_at(board, nr, nc, color) if king_moving
                    else in_check_at(board, kr, kc, color))
        board[r][c] = piece
        board[nr][nc] = captured
        if safe:
            out.append((nr, nc))
    return out


def legal_moves(board, r: int, c: int) -> list[tuple[int, int]]:
    """过滤掉「走完自己被将军（含照面）」的伪合法走法。"""
    if not in_board(r, c):
        return []
    piece = board[r][c]
    if piece is None:
        return []
    color = color_of(piece)
    king = find_king(board, color)
    if king is None:
        return []
    return _legal_moves_from(board, r, c, color, king[0], king[1])


def all_legal_moves(board, color: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """color 方全部合法走法，元素为 ((fr,fc),(tr,tc))。"""
    king = find_king(board, color)
    if king is None:
        return []
    kr, kc = king
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            piece = board[r][c]
            if piece is None or color_of(piece) != color:
                continue
            out.extend(((r, c), dst) for dst in _legal_moves_from(board, r, c, color, kr, kc))
    return out


def apply_move(board, frm: tuple[int, int], to: tuple[int, int]) -> str | None:
    """原地走子，返回被吃的棋子（可能为 None）。"""
    fr, fc = frm
    tr, tc = to
    captured = board[tr][tc]
    board[tr][tc] = board[fr][fc]
    board[fr][fc] = None
    return captured


def undo_move(board, frm, to, captured) -> None:
    """撤销 `apply_move`。"""
    fr, fc = frm
    tr, tc = to
    board[fr][fc] = board[tr][tc]
    board[tr][tc] = captured


def game_status(board, color: str) -> str:
    """轮到 color 走时的局面：``playing`` / ``checkmate`` / ``stalemate``。

    **困毙（stalemate）在象棋里判负**，不是和棋——所以调用方对这两个
    返回值都要判 color 输，只是理由不同。
    """
    if all_legal_moves(board, color):
        return "playing"
    return "checkmate" if is_in_check(board, color) else "stalemate"


# ── 对局状态 ──────────────────────────────────────────
# gs 的字段形状与 reversi 对齐（board / moves / next / winner / reason /
# player_colors），room_manager 的通用路径（认输、离开、断线判负、战绩落库）
# 才能直接复用。player_colors 由 room_manager 在开局时填。

def create_game() -> dict:
    """新开一局（红先）。返回的状态 dict 可直接 JSON 落盘。"""
    board = create_board()
    return {
        "board": board,
        "moves": [],               # [{from:[r,c], to:[r,c], piece, captured}]
        "next": RED,               # 红先
        "winner": None,            # None / RED / BLACK / "draw"
        "reason": None,            # checkmate / stalemate / no_capture / surrender / disconnect
        "player_colors": {},       # {user_id: "red"/"black"}
        "finished": False,
        "last_move": None,
        "no_capture": 0,           # 距上次吃子的半回合数（60 回合无吃子判和）
        "check": False,            # 轮到走的一方是否正被将军
    }


def _finish(gs: dict, winner: str, reason: str) -> None:
    gs["winner"] = winner
    gs["reason"] = reason
    gs["finished"] = True
    # **不清 check**：将死那一步对方确实正被将军，客户端要靠它亮「将军」提示；
    # 清成 False 反而丢信息（对局结束后没人再读它，留着不会有副作用）。


def _settle(gs: dict) -> None:
    """走完一步后判定终局：将死 / 困毙 / 60 回合无吃子判和。

    重复局面（长将）不单独判——没有吃子的来回走子会先把 `no_capture` 顶到上限
    判和，棋局必然终止，所以不做三次重复检测。**代价是长将赖和**（标准规则里
    长将判负），属已知简化。
    """
    turn = gs["next"]
    status = game_status(gs["board"], turn)
    gs["check"] = is_in_check(gs["board"], turn)
    if status == "checkmate":
        _finish(gs, other(turn), "checkmate")
        return
    if status == "stalemate":
        _finish(gs, other(turn), "stalemate")
        return
    if gs["no_capture"] >= DRAW_PLIES:
        _finish(gs, "draw", "no_capture")


def make_move(gs: dict, frm, to) -> dict:
    """走一步棋（就地改 gs）。返回 ``{"ok", "move", "game_over"}`` 或 ``{"ok": False, "error"}``。

    形状与 ``room_manager._reversi_make_move`` 一致，方便照搬那套「失败回 error、
    成功广播 move、有 game_over 就广播」的 WS 处理。
    """
    if gs.get("finished") or gs.get("winner"):
        return {"ok": False, "error": "对局已结束"}
    fr, fc = frm
    tr, tc = to
    if not (in_board(fr, fc) and in_board(tr, tc)):
        return {"ok": False, "error": "落点超出棋盘"}
    board = gs["board"]
    piece = board[fr][fc]
    if piece is None:
        return {"ok": False, "error": "起点没有棋子"}
    if color_of(piece) != gs["next"]:
        return {"ok": False, "error": "还没轮到你走"}
    if (tr, tc) not in legal_moves(board, fr, fc):
        return {"ok": False, "error": "这步棋不合规则"}

    captured = apply_move(board, (fr, fc), (tr, tc))
    move = {"from": [fr, fc], "to": [tr, tc], "piece": piece, "captured": captured}
    gs["moves"].append(move)
    gs["last_move"] = move
    gs["no_capture"] = 0 if captured else gs["no_capture"] + 1
    gs["next"] = other(gs["next"])
    _settle(gs)

    msg = {**move, "next": gs["next"], "check": gs["check"], "legal": legal_payload(gs)}
    game_over = None
    if gs["winner"]:
        game_over = {"winner": gs["winner"], "reason": gs["reason"]}
    return {"ok": True, "move": msg, "game_over": game_over}


def surrender(gs: dict, color: str) -> dict:
    """认输（room_manager 的通用路径会直接写 winner/reason，这里供单测/直接调用）。"""
    if gs.get("finished") or gs.get("winner"):
        return {"ok": False, "error": "对局已结束"}
    _finish(gs, other(color), "surrender")
    return {"ok": True, "game_over": {"winner": gs["winner"], "reason": gs["reason"]}}


def legal_payload(gs: dict) -> list[list[int]]:
    """轮到走的一方全部合法着法，扁平 ``[fr, fc, tr, tc]`` 列表。

    **客户端不实现象棋规则**——走法合法性只在服务端有一份实现，客户端拿这份
    列表做「可动子 / 可落点」高亮。象棋是完备信息游戏，这个列表本来就是公开的。
    （飞行棋踩过「客户端自己推路径画错」的坑，同一条教训。）
    """
    if gs.get("finished") or gs.get("winner"):
        return []
    return [[fr, fc, tr, tc]
            for (fr, fc), (tr, tc) in all_legal_moves(gs["board"], gs["next"])]


def game_to_sync(gs: dict, user_id: int) -> dict:
    """sync / game_start 里的对局视图（照 reversi.game_to_sync 的形状）。"""
    return {
        "rows": ROWS,
        "cols": COLS,
        "board": gs["board"],
        "moves": gs["moves"],
        "next": gs["next"],
        "your_color": gs["player_colors"].get(user_id, ""),
        "winner": gs["winner"],
        "reason": gs["reason"],
        "check": gs.get("check", False),
        "last_move": gs.get("last_move"),
        "legal": legal_payload(gs),
    }


def snapshot(gs: dict) -> dict:
    """终局快照（存进战绩，供历史页回放）。"""
    return {
        "game": "xiangqi",
        "rows": ROWS,
        "cols": COLS,
        "board": gs.get("board", []),
        "moves": gs.get("moves", []),
        "winner": gs.get("winner"),
        "reason": gs.get("reason"),
    }
