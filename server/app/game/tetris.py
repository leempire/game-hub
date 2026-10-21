"""Tetris (俄罗斯方块对战) pure game logic — no I/O, easy to unit-test.

10 列 × 20 行，棋盘的 row 0 在最上面、落满的行在下面。每人一份自己的盘
（各玩各的，与 2048 同构），方块序列用**同种子 7-bag**保证公平。

**为什么不做服务端 tick**：这个项目的所有游戏都是「请求—响应」模型，没有任何
周期性推进状态的先例。重力交给客户端按自己的节奏发（客户端逐动累积动作，锁定
时一次性把动作序列发上来），服务端用同一套引擎**重放**这些动作——于是服务端
仍然是盘面的唯一权威（零信任），而每块方块只需要一次往返，也不需要每房间一个
后台任务（那会带来任务泄漏、SQLite 高频写盘、热重启要恢复 tick 状态三件事）。

对战部分是**攻击行**：一次消掉多行会给对手从底部顶上来若干带空洞的行。
"""

import random

COLS = 10
ROWS = 20

# 棋盘格子：0 = 空，1..7 = 对应方块的 id（客户端按 id 上色），8 = 攻击行填充块
EMPTY = 0
GARBAGE_ID = 8

# 七种方块。每个形状用「相对坐标列表」描述，SRS 的四态旋转表直接写出来
# （比运行时旋转更省事，也避免旋转中心争议）。
# 坐标朝下为 y 正方向，与 board[row][col] 一致。
PIECES: dict[str, list[list[tuple[int, int]]]] = {
    "I": [
        [(0, 0), (0, 1), (0, 2), (0, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 1), (1, 1), (2, 1), (3, 1)],
    ],
    "O": [
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (1, 2)],
    ],
    "T": [
        [(0, 1), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (1, 2), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 1)],
        [(0, 1), (1, 0), (1, 1), (2, 1)],
    ],
    "S": [
        [(0, 1), (0, 2), (1, 0), (1, 1)],
        [(0, 1), (1, 1), (1, 2), (2, 2)],
        [(1, 1), (1, 2), (2, 0), (2, 1)],
        [(0, 0), (1, 0), (1, 1), (2, 1)],
    ],
    "Z": [
        [(0, 0), (0, 1), (1, 1), (1, 2)],
        [(0, 2), (1, 1), (1, 2), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(0, 1), (1, 0), (1, 1), (2, 0)],
    ],
    "J": [
        [(0, 0), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (0, 2), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 0), (2, 1)],
    ],
    "L": [
        [(0, 2), (1, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (1, 2), (2, 0)],
        [(0, 0), (0, 1), (1, 1), (2, 1)],
    ],
}

PIECE_IDS: dict[str, int] = {name: i + 1 for i, name in enumerate("IOT SZJL".replace(" ", ""))}

# 旋转踢墙偏移：先试原位，再左右挪 1~2 格（不做完整 SRS，够用且好懂）
KICK_OFFSETS = [0, -1, 1, -2, 2]

# 消行得分（× 等级）
LINE_SCORES = {1: 100, 2: 300, 3: 500, 4: 800}

# 消行 → 送给对手的攻击行数（标准 Tetris 的简化版：单行不攻击）
GARBAGE_TABLE = {1: 0, 2: 1, 3: 2, 4: 4}

# 每消 10 行升 1 级；等级既用于算分（倍率）也用于客户端重力间隔
LINES_PER_LEVEL = 10

# 预览队列长度（客户端显示「下一块」+ 再往后两块）
PREVIEW_COUNT = 3


def create_board() -> list[list[int]]:
    """空盘：20 行 × 10 列。"""
    return [[EMPTY] * COLS for _ in range(ROWS)]


def create_seeded_rng(seed: int) -> random.Random:
    """同种子 RNG —— 所有玩家共用同一个 seed，方块序列完全一致（公平）。"""
    return random.Random(seed)


def _refill_bag(rng: random.Random) -> list[str]:
    """一袋七个方块（7-bag）：洗牌后依次发，保证不会长时间不来某个形状。"""
    bag = list(PIECE_IDS.keys())
    rng.shuffle(bag)
    return bag


def draw_piece(queue: list[str], rng: random.Random) -> str:
    """从队列取下一个方块；队列空了就补一袋。"""
    if not queue:
        queue.extend(_refill_bag(rng))
    return queue.pop(0)


def _refill_preview(gs: dict, uid: int) -> None:
    """把该玩家的预览队列补到 `PREVIEW_COUNT` 块。

    **预览只是「提前抽出来放着」**，抽取顺序与逐块 `draw_piece` 完全一致，所以
    同种子的公平性不变。预览不消费方块，消费发生在 `spawn_next` / 换暂存时。
    """
    preview = gs["previews"][uid]
    while len(preview) < PREVIEW_COUNT:
        preview.append(draw_piece(gs["queues"][uid], gs["rngs"][uid]))
    gs["next_piece"][uid] = preview[0] if preview else None


def cells_of(piece: str, rotation: int, x: int, y: int) -> list[tuple[int, int]]:
    """方块在盘面上的绝对坐标列表。x 是左上角列、y 是左上角行。"""
    return [(y + dy, x + dx) for dy, dx in PIECES[piece][rotation % 4]]


def is_valid(board: list[list[int]], piece: str, rotation: int, x: int, y: int) -> bool:
    """该摆放是否合法：不越界、不压到已有方块。"""
    for r, c in cells_of(piece, rotation, x, y):
        if c < 0 or c >= COLS or r < 0 or r >= ROWS:
            return False
        if board[r][c] != EMPTY:
            return False
    return True


def spawn(piece: str) -> dict:
    """新方块从顶部中间出现。"""
    # 各形状的宽度不同，居中摆放：I 是 4 宽、O 是 4 宽（含空列），其余 3 宽
    start_x = 3 if piece != "O" else 3
    return {"piece": piece, "rotation": 0, "x": start_x, "y": 0}


def move(board: list[list[int]], cur: dict, dx: int, dy: int) -> bool:
    """平移/下落一格；失败返回 False（不动）。"""
    nx, ny = cur["x"] + dx, cur["y"] + dy
    if not is_valid(board, cur["piece"], cur["rotation"], nx, ny):
        return False
    cur["x"], cur["y"] = nx, ny
    return True


def rotate(board: list[list[int]], cur: dict, direction: int) -> bool:
    """旋转（direction: 1 顺时针 / -1 逆时针），带简单踢墙。"""
    target = (cur["rotation"] + direction) % 4
    for dx in KICK_OFFSETS:
        if is_valid(board, cur["piece"], target, cur["x"] + dx, cur["y"]):
            cur["rotation"] = target
            cur["x"] += dx
            return True
    return False


def drop_distance(board: list[list[int]], cur: dict) -> int:
    """当前方块还能往下落几格（硬降用）。"""
    d = 0
    while is_valid(board, cur["piece"], cur["rotation"], cur["x"], cur["y"] + d + 1):
        d += 1
    return d


def lock(board: list[list[int]], cur: dict) -> None:
    """把方块固定到盘面上（原地修改）。

    要求摆放合法——调用方（`apply_action`）保证这一点（只会在 move/drop_distance
    成功之后调）。这里显式校验一次：非法摆放直接写越界会让整个 WS 处理 500，
    而「静默写坏盘面」更难查，宁可在这里响亮地炸。
    """
    if not is_valid(board, cur["piece"], cur["rotation"], cur["x"], cur["y"]):
        raise ValueError(
            f"非法摆放：{cur['piece']} rot={cur['rotation']} x={cur['x']} y={cur['y']}"
        )
    stone = PIECE_IDS[cur["piece"]]
    for r, c in cells_of(cur["piece"], cur["rotation"], cur["x"], cur["y"]):
        board[r][c] = stone


def full_rows(board: list[list[int]]) -> list[int]:
    """已填满的行号（从下往上）。"""
    return [r for r in range(ROWS - 1, -1, -1)
            if all(cell != EMPTY for cell in board[r])]


def clear_rows(board: list[list[int]], rows: list[int]) -> None:
    """删掉这些行，上面的整体下移（原地修改）。

    **不要写成「逐行 del + insert(0)」的循环**：每插一行到顶部，其余所有行的
    下标都会 +1，于是第二行起就删错位置了——不管从下往上还是从上往下删都错
    （两行以上一起消时盘面立刻错乱，表现为「悬空方块」，自检里抓到过）。
    一次重建最省心：留下没被消的行，在前面补等量的空行。

    用 `board[:] = ...` 原地改，不能 `board = ...`——调用方持有的是同一个列表对象。
    """
    cleared = set(rows)
    kept = [row for i, row in enumerate(board) if i not in cleared]
    board[:] = [[EMPTY] * COLS for _ in range(len(cleared))] + kept


def add_garbage(board: list[list[int]], count: int, hole_cols: list[int]) -> bool:
    """从底部顶上来 `count` 行攻击行（每行一个空洞），返回是否因此顶出局。

    空洞位置由调用方给（服务端用发起方的 rng 抽取，保证可落盘可复现）。
    注意：盘面整体上移，所以**下落中的方块也必须跟着上移**——见
    `deliver_garbage`，否则刚顶上来的行会直接盖在方块身上（方块陷进墙里）。
    """
    for i in range(count):
        hole = hole_cols[i % len(hole_cols)] if hole_cols else 0
        # 顶到天花板说明被攻击行压死了
        if any(cell != EMPTY for cell in board[0]):
            return True
        board.pop(0)
        board.append([EMPTY if c == hole else GARBAGE_ID for c in range(COLS)])
    return False


def deliver_garbage(gs: dict, from_uid: int, count: int) -> list[int]:
    """把 count 行攻击行顶给其他**还在玩**的玩家。

    返回**所有收到攻击行的 uid**（含因此被顶死的）——调用方既要用它知道
    「哪些人的盘面变了、得给他们推新状态」，也要用它发「你被攻击了」的提示。
    是否因此出局读 `gs["game_over_flags"]`，不必在这里再分一个列表。

    盘面上移时**方块必须跟着上移**（只做盘面的话方块会陷进新行里，越界/重叠，
    自检里被「攻击行顶上来后方块应还在堆叠上方」抓到）。

    **顶死判据（v1.9.1 修）**：方块最多被顶到第 0 行（`max(0, ...)`），只有
    「堆叠自己顶到天花板」或「顶上去之后方块在该位置放不下」才算出局。

    早先的判据是 `cur["y"] - count < 0` 即判死，看似与真实俄罗斯方块一致，
    实际上是个**致命 bug**：真实俄罗斯方块的方块是从可视区**上方**进入的
    （有缓冲区，被顶上去时先退到缓冲区里），而本引擎的 20 行棋盘就是整个
    可视区、`spawn()` 把方块放在第 0 行——**没有任何可退的空间**，于是
    「刚出生的方块」被任何一次攻击直接顶死。更糟的是判死分支 `continue`
    掉了 `add_garbage`，受害者的盘面根本没被顶起来：玩家看到的是「方块凭空
    消失、盘面毫无变化、之后重力与按键全部无效」——就是「被顶之后卡住、
    再也不出方块」。回归见 `test_tetris.py::TestGarbage::test_garbage_*`。
    """
    if count <= 0:
        return []
    rng = gs["rngs"][from_uid]
    holes = [rng.randrange(COLS) for _ in range(count)]
    victims: list[int] = []
    for uid in gs["boards"]:
        if uid == from_uid or gs["game_over_flags"].get(uid):
            continue
        victims.append(uid)
        cur = gs["current"].get(uid)
        if cur is not None:
            cur["y"] = max(0, cur["y"] - count)
        board = gs["boards"][uid]
        if add_garbage(board, count, holes):
            # 堆叠自己顶到天花板
            finish_player(gs, uid, "garbage")
            continue
        if cur is not None and not is_valid(
                board, cur["piece"], cur["rotation"], cur["x"], cur["y"]):
            # 方块被顶上来之后放不下（堆叠已经压到天花板）→ 顶死
            finish_player(gs, uid, "garbage")
            continue
        gs["garbage_received"][uid] = gs["garbage_received"].get(uid, 0) + count
    return victims


def top_out(board: list[list[int]]) -> bool:
    """顶部两行有方块就算顶死（新方块没地方出现）。"""
    return any(cell != EMPTY for row in board[:2] for cell in row)


def level_of(lines: int, start_level: int = 1) -> int:
    """起始档 + 每 10 行升 1 级。

    起始档同时抬高**分数倍率**与**重力速度**（标准俄罗斯方块的做法）：三档之间
    分差因此不是同一条计分线，但同一房间里所有人同档，排名仍然公平。
    """
    return max(1, start_level) + lines // LINES_PER_LEVEL


def create_game(
    seeds: dict[int, int],
    player_ids: list[int],
    start_level: int = 1,
) -> dict:
    """新建一局：每人一份空盘 + 一份同种子的方块队列。

    `seeds[uid]` 由调用方给（同种子公平）；`start_level` 是房间设置里的起始速度档。
    返回的 gs 直接可被 persistence 落盘（rng 走 `__rng__` 编码器）。
    """
    start_level = max(1, int(start_level))
    return {
        "boards": {uid: create_board() for uid in player_ids},
        "rngs": {uid: create_seeded_rng(seeds[uid]) for uid in player_ids},
        "queues": {uid: [] for uid in player_ids},
        "current": {},          # uid -> 当前方块 {piece, rotation, x, y}
        "next_piece": {},       # uid -> 下一个方块名（客户端预览用）
        "previews": {uid: [] for uid in player_ids},   # uid -> [接下来 3 块]
        "held": {uid: None for uid in player_ids},     # uid -> 暂存的方块名
        "hold_used": {uid: False for uid in player_ids},  # 本块是否已用过暂存
        "start_level": start_level,
        "scores": {uid: 0 for uid in player_ids},
        "lines": {uid: 0 for uid in player_ids},
        "level": {uid: start_level for uid in player_ids},
        "garbage_received": {uid: 0 for uid in player_ids},   # 本局被顶了多少攻击行（展示用）
        "game_over_flags": {uid: False for uid in player_ids},
        "finish_reasons": {uid: None for uid in player_ids},
        "finish_order": [],
        "player_colors": {},
        "winner": None,
        "reason": None,
    }


def spawn_next(gs: dict, uid: int) -> bool:
    """给 uid 出下一个方块；顶死返回 False（并标记该玩家结束）。"""
    _refill_preview(gs, uid)
    piece = gs["previews"][uid].pop(0)
    _refill_preview(gs, uid)
    cur = spawn(piece)
    if not is_valid(gs["boards"][uid], piece, cur["rotation"], cur["x"], cur["y"]):
        gs["game_over_flags"][uid] = True
        gs["finish_reasons"][uid] = "top_out"
        gs["current"][uid] = None
        return False
    gs["current"][uid] = cur
    return True


def hold_piece(gs: dict, uid: int) -> bool:
    """暂存 / 换出当前方块。每块只能用一次（锁定后重置）。

    规则：没有暂存块时，把当前块存起来、从预览队首取一块；已有暂存块时两块互换。
    **换出来的方块从顶部重新出生**（与真实俄罗斯方块一致），所以它可能直接顶死。
    返回是否真的执行了（已用过 / 当前没有方块时返回 False，调用方静默处理）。
    """
    if gs["hold_used"].get(uid):
        return False
    cur = gs["current"].get(uid)
    if cur is None:
        return False
    held = gs["held"].get(uid)
    gs["held"][uid] = cur["piece"]
    gs["hold_used"][uid] = True
    if held is None:
        _refill_preview(gs, uid)
        piece = gs["previews"][uid].pop(0)
        _refill_preview(gs, uid)
    else:
        piece = held
    new_cur = spawn(piece)
    if not is_valid(gs["boards"][uid], piece, new_cur["rotation"],
                    new_cur["x"], new_cur["y"]):
        gs["game_over_flags"][uid] = True
        gs["finish_reasons"][uid] = "top_out"
        gs["current"][uid] = None
        return True
    gs["current"][uid] = new_cur
    return True


def apply_action(gs: dict, uid: int, action: str) -> dict:
    """在 uid 的盘面上执行一个动作。**服务端重放客户端动作序列的入口**。

    返回 {ok, locked, cleared, score_gained, garbage_sent}；非法动作是静默 no-op
    （与 2048 的无效方向一致，不弹错）。
    """
    cur = gs["current"].get(uid)
    if cur is None or gs["game_over_flags"].get(uid):
        return {"ok": False}

    board = gs["boards"][uid]
    locked = False

    if action == "left":
        move(board, cur, -1, 0)
    elif action == "right":
        move(board, cur, 1, 0)
    elif action == "rotate_cw":
        rotate(board, cur, 1)
    elif action == "rotate_ccw":
        rotate(board, cur, -1)
    elif action == "soft_drop":
        # 软降到底部就锁定
        if not move(board, cur, 0, 1):
            locked = True
    elif action == "hard_drop":
        cur["y"] += drop_distance(board, cur)
        locked = True
    elif action == "down":
        # 重力推进一格；落不下去就锁定
        if not move(board, cur, 0, 1):
            locked = True
    elif action == "hold":
        # 暂存是**即时换块**，不锁定当前块：客户端要的是「换出来的是什么」，
        # 所以调用方收到 `held: True` 要立刻回一份权威状态（见 room_manager）。
        done = hold_piece(gs, uid)
        return {
            "ok": done, "locked": False, "held": done, "cleared": 0,
            "score_gained": 0, "garbage_sent": 0,
            "player_finished": (
                gs["finish_reasons"].get(uid)
                if gs["game_over_flags"].get(uid) else None
            ),
        }
    else:
        return {"ok": False, "error": f"未知动作 {action}"}

    if not locked:
        return {"ok": True, "locked": False, "cleared": 0,
                "score_gained": 0, "garbage_sent": 0}

    return _lock_and_settle(gs, uid)


def _lock_and_settle(gs: dict, uid: int) -> dict:
    """锁定当前方块 → 消行 → 算分 → 出下一个。"""
    board = gs["boards"][uid]
    cur = gs["current"][uid]
    lock(board, cur)

    rows = full_rows(board)
    cleared = len(rows)
    if rows:
        clear_rows(board, rows)

    gained = LINE_SCORES.get(cleared, 0) * gs["level"].get(uid, 1) if cleared else 0
    gs["scores"][uid] = gs["scores"].get(uid, 0) + gained
    gs["lines"][uid] = gs["lines"].get(uid, 0) + cleared
    gs["level"][uid] = level_of(gs["lines"][uid], gs.get("start_level", 1))
    # 暂存每块限一次：锁定后重置
    gs["hold_used"][uid] = False

    garbage = GARBAGE_TABLE.get(cleared, 0)
    victims = deliver_garbage(gs, uid, garbage) if garbage else []

    gs["current"][uid] = None
    spawn_next(gs, uid)
    return {
        "ok": True,
        "locked": True,
        "cleared": cleared,
        "score_gained": gained,
        "garbage_sent": garbage,
        "garbage_victims": victims,
        "rows": rows,
    }


def empty_column(board: list[list[int]], col: int) -> bool:
    return all(board[r][col] == EMPTY for r in range(ROWS))


def finish_player(gs: dict, uid: int, reason: str) -> None:
    """把一个玩家标记为结束（认输/中途离开/顶死）。"""
    if gs["game_over_flags"].get(uid):
        return
    gs["game_over_flags"][uid] = True
    gs["finish_reasons"][uid] = reason
    gs["finish_order"].append(uid)
    gs["current"][uid] = None


def all_finished(gs: dict) -> bool:
    return bool(gs["game_over_flags"]) and all(gs["game_over_flags"].values())


def ranking(gs: dict) -> list[dict]:
    """排名：先活到最后的（按结束顺序倒序 → 晚结束的更靠前），再比分数。

    顶死/认输的人比的是「撑了多久 + 拿多少分」——用 finish_order 的先后当
    存活长度（越晚被记进 finish_order 说明撑得越久）。
    """
    order_index = {uid: i for i, uid in enumerate(gs["finish_order"])}
    rows = [
        {
            "user_id": uid,
            "color": gs["player_colors"].get(uid, ""),
            "score": gs["scores"].get(uid, 0),
            "lines": gs["lines"].get(uid, 0),
            "level": gs["level"].get(uid, 1),
            "finish_reason": gs["finish_reasons"].get(uid),
            "finished": gs["game_over_flags"].get(uid, False),
        }
        for uid in gs["boards"]
    ]
    # 先按「是否结束」分两组：没结束的（还在玩）排前面；都结束了就比分数。
    rows.sort(key=lambda r: (
        0 if not r["finished"] else 1,
        -order_index.get(r["user_id"], -1),
        -r["score"],
    ))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def leaderboard(gs: dict) -> list[dict]:
    """实时排行榜（每次锁定后广播给所有人）。"""
    rows = [
        {
            "user_id": uid,
            "color": gs["player_colors"].get(uid, ""),
            "score": gs["scores"].get(uid, 0),
            "lines": gs["lines"].get(uid, 0),
            "level": gs["level"].get(uid, 1),
            "finished": gs["game_over_flags"].get(uid, False),
            "finish_reason": gs["finish_reasons"].get(uid),
        }
        for uid in gs["boards"]
    ]
    rows.sort(key=lambda r: -r["score"])
    return rows


def active_board(gs: dict, uid: int) -> list[list[int]]:
    return gs["boards"][uid]


def game_to_sync(gs: dict, user_id: int) -> dict:
    """逐人下发：只给自己的盘面、当前方块与下一个方块（**别人的盘不公开**）。

    **方块形状也由服务端给**（`shapes` = 七种方块 × 四态的完整旋转表）：客户端为了
    手感必须在本地预演（左右/旋转/硬降），预演就要知道「转一下变成什么形状」——
    让它自己再写一份旋转表必然漂移（象棋的合法着法、飞行棋的走子路径都是同一条
    教训）。整表只有 7×4×4 个坐标，跟着每次状态一起下发，省掉客户端保存状态的坑。
    """
    uid = user_id
    return {
        "game": "tetris",
        "cols": COLS,
        "rows": ROWS,
        "board": gs["boards"].get(uid, []),
        "current": gs["current"].get(uid),
        "next_piece": gs["next_piece"].get(uid),
        # 预览队列（接下来 3 块）与暂存块；`hold_used` 决定暂存入口还能不能点
        "next_pieces": list(gs["previews"].get(uid, [])),
        "held": gs["held"].get(uid),
        "hold_used": bool(gs["hold_used"].get(uid)),
        "start_level": gs.get("start_level", 1),
        "shapes": {
            name: [[list(c) for c in state] for state in states]
            for name, states in PIECES.items()
        },
        # 旋转踢墙的尝试顺序也下发：客户端预演旋转时必须和服务端试同一个顺序，
        # 否则「贴墙转一下」两边结论不同，预演出来的盘面就和重放结果对不上
        "kicks": list(KICK_OFFSETS),
        "your_color": gs["player_colors"].get(uid, ""),
        "score": gs["scores"].get(uid, 0),
        "lines": gs["lines"].get(uid, 0),
        "level": gs["level"].get(uid, 1),
        "garbage_received": gs["garbage_received"].get(uid, 0),
        "finished": gs["game_over_flags"].get(uid, False),
        "finish_reason": gs["finish_reasons"].get(uid),
        "winner": gs["winner"],
        "reason": gs["reason"],
        "leaderboard": leaderboard(gs),
    }


def snapshot(gs: dict) -> dict:
    """战绩快照：保住每人的终局盘面与统计。"""
    return {
        "game": "tetris",
        "boards": gs["boards"],
        "scores": gs["scores"],
        "lines": gs["lines"],
        "levels": gs["level"],
        "garbage_received": gs["garbage_received"],
        "winner": gs["winner"],
        "reason": gs["reason"],
        "ranking": gs.get("ranking", []),
    }