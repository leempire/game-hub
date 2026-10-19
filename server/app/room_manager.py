"""In-memory room state management (singleton)."""

import random
import string
import asyncio
import logging
import time as _time
from datetime import datetime, timezone

from app import persistence
from app import room_settings
from app import stasis as stasis_engine
from app import stasis_ai

# Room idle TTL in seconds. Sweeper deletes rooms exceeding this.
# Monkeypatch this constant in tests to avoid real 30-minute waits.
ROOM_IDLE_TTL: float = 1800.0

# 等待房「没人在线」的宽限期（秒）。客户端从对局页返回房间时会先关对局页连接
# 再重连房间页，两步之间有窗口期——宽限期就是给这个重连留的时间，重连即取消。
# 超过这个时间仍无人在线，说明是真离开了，回收房间。
WAITING_EMPTY_TTL: float = 60.0

# ── 深空休眠（stasis）的 tick ────────────────────────────────
#
# **本项目第一个、也是目前唯一一个服务端 tick。** tetris.py 的注释专门写了
# 「不做服务端 tick」及三条理由（任务泄漏 / SQLite 高频写盘 / 热重启恢复），
# 这个游戏绕不开时间，所以三条要逐条解掉 —— 完整论述见
# docs/游戏设计/深空休眠/设计.md §2，这里只放落地参数。
STASIS_TICK_MS = 200               # 5 Hz，与 stasis.TICK_MS 必须一致
STASIS_PERSIST_EVERY = 25          # 每 25 tick（5 秒）才落一次关键帧
STASIS_BOT_INTERVAL_MS = 1000      # 人机队友的决策间隔

# 「舱室归属变了」的两条事件 —— 它们改了 `rooms[].owner`，而归属只在离散层
# 下发，所以 tick 循环见到它们要补一份 `stasis_state`（见 `_stasis_tick_loop`）。
_ROOM_OWNER_EVENTS = ("room_claimed", "room_released")

# code -> asyncio.Task。**不做「创建时启动、销毁时停止」的精确配对** ——
# 那条路只要漏掉任何一条销毁路径（sweeper 回收 / 房主离开 / 全员掉线 / 热重启）
# 就泄漏一个永不退出的协程。改成两条腿：任务自己在循环里发现 status 不对就退出
# （自净）+ sweeper 每轮对账补一个（兜底）。与 _bot_tasks 同构。
_tick_tasks: dict[str, asyncio.Task] = {}

from app.gomoku import (
    create_board,
    place_stone,
    check_win,
    is_board_full,
    BOARD_SIZE,
    BLACK,
    WHITE,
)
from app.minesweeper import (
    create_game as mine_create_game,
    reveal as mine_reveal,
    flag as mine_flag,
    chord as mine_chord,
    game_to_sync as mine_game_to_sync,
    compute_ranking as mine_compute_ranking,
)
from app.reversi import (
    create_board as reversi_create_board,
    apply_move as reversi_apply_move,
    get_legal_moves as reversi_get_legal_moves,
    count_stones as reversi_count_stones,
    game_to_sync as reversi_game_to_sync,
    BOARD_SIZE as REVERSI_BOARD_SIZE,
    BLACK as REVERSI_BLACK,
    WHITE as REVERSI_WHITE,
)
from app.xiangqi import (
    create_game as xiangqi_create_game,
    make_move as xiangqi_engine_move,
    RED as XIANGQI_RED,
    BLACK as XIANGQI_BLACK,
)
from app import luzhanqi as lzq
from app.connect4 import (
    create_board as c4_create_board,
    drop as c4_drop,
    check_win as c4_check_win,
    is_board_full as c4_is_board_full,
    is_valid_move as c4_is_valid_move,
    legal_columns as c4_legal_columns,
    BLACK as C4_BLACK,
    WHITE as C4_WHITE,
)
from app.battleship import (
    create_board as bs_create_board,
    validate_placement,
    apply_placement,
    fire as bs_fire,
    random_placement as bs_random_placement,
    all_ships_sunk,
    game_to_sync as bs_game_to_sync,
    snapshot as bs_snapshot,
    normalize_weapon_assignment,
    ship_afloat as bs_ship_afloat,
    radar_scan as bs_radar_scan,
    area_cells as bs_area_cells,
    WEAPON_DEFS as BS_WEAPON_DEFS,
    SALVO_SHOTS as BS_SALVO_SHOTS,
    BOARD_SIZE as BS_BOARD_SIZE,
    DEFAULT_SIZE as BS_DEFAULT_SIZE,
)
from app.battleship_ai import normalize_difficulty as bs_normalize_difficulty
from app.dots import (
    create_game as dots_create_game,
    apply_move as dots_apply_move,
    is_valid_edge as dots_is_valid_edge,
    game_to_sync as dots_game_to_sync,
    snapshot as dots_snapshot,
    PLAYER1 as DOTS_P1,
    PLAYER2 as DOTS_P2,
    DEFAULT_DOTS,
)
from app.ludo import (
    create_game as ludo_create_game,
    roll_dice as ludo_roll_dice,
    move_piece as ludo_move_piece,
    surrender as ludo_surrender,
    game_to_sync as ludo_game_to_sync,
    compute_ranking as ludo_compute_ranking,
    _advance_turn as ludo_advance_turn,
    _check_game_over as ludo_check_game_over,
)
from app.tetris import (
    create_game as tetris_create_game,
    spawn_next as tetris_spawn_next,
    apply_action as tetris_apply_action,
    finish_player as tetris_finish_player,
    ranking as tetris_ranking,
    leaderboard as tetris_leaderboard,
    game_to_sync as tetris_game_to_sync,
    snapshot as tetris_snapshot,
)
from app.game2048 import (
    create_board as g2048_create_board,
    spawn_tile as g2048_spawn_tile,
    move_with_trace as g2048_move_with_trace,
    has_moves as g2048_has_moves,
    has_won as g2048_has_won,
    max_tile as g2048_max_tile,
    create_seeded_rng as g2048_create_seeded_rng,
)
from app.uno import (
    create_game as uno_create_game,
    play as uno_play,
    draw as uno_draw,
    pass_turn as uno_pass,
    auto_pass as uno_autopass,
    call_uno as uno_call_uno,
    resolve_roulette as uno_resolve_roulette,
    surrender as uno_surrender,
    compute_ranking as uno_compute_ranking,
)
from app import uno_rules

# Bot turn callback: registered by ws.py to schedule AI moves.
# Signature: async callback(code, bot_user_id)
_bot_turn_callback = None


def set_bot_turn_callback(callback):
    """Register async callback invoked when it is a bot's turn."""
    global _bot_turn_callback
    _bot_turn_callback = callback


# Bot placement callback for battleship: registered by ws.py.
# Signature: async callback(code, bot_user_id)
_bot_placement_callback = None


def set_bot_placement_callback(callback):
    """Register async callback invoked when battleship bot needs to place."""
    global _bot_placement_callback
    _bot_placement_callback = callback

# Module-level singleton
_rooms: dict[str, dict] = {}
_dc_tasks: dict[tuple[str, int], asyncio.Task] = {}
_bot_tasks: dict[tuple[str, int], asyncio.Task] = {}
_disconnect_timeout: int = 120
_bot_id_counter: int = -1  # bot user IDs are negative

# Called when a game ends (game result known). Receives the room dict.
#
# 分两个槽位：`set_game_end_callback` 是**唯一**的落库回调（main.py 注册），
# `add_game_end_listener` 是额外监听（ws.py 用来在对局结束后广播一次 lobby）。
# 之所以要分两槽而不是一个列表：v1.5.1 之前只有一个槽，谁后注册谁生效——
# 再加第二个监听就会把落库挤掉，战绩静默停止记录（那条坑在
# `save_game_record` 的守卫上已经踩过一次）。
_game_end_callback = None
_game_end_listeners: list = []


def set_game_end_callback(callback):
    """Register the primary (sync) game-end callback. Pass None to clear."""
    global _game_end_callback
    _game_end_callback = callback


def add_game_end_listener(callback):
    """Register an extra game-end listener; doesn't displace the primary one."""
    _game_end_listeners.append(callback)


def _notify_game_end(room: dict):
    """Fire every registered game-end callback; one raising doesn't block the rest."""
    callbacks = ([_game_end_callback] if _game_end_callback else []) + list(_game_end_listeners)
    for callback in callbacks:
        try:
            callback(room)
        except Exception:
            pass


def set_disconnect_timeout(seconds: int):
    global _disconnect_timeout
    _disconnect_timeout = seconds


def _generate_code() -> str:
    """Generate a unique 6-char room code (uppercase letters + digits)."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        code = "".join(random.choices(chars, k=6))
        if code not in _rooms:
            return code
    raise RuntimeError("无法生成唯一房间号")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _touch_activity(room: dict):
    """Refresh last_activity timestamp on any state change.

    同时是落盘收口：所有房间状态变更（含每步棋）都经由这里写盘，
    支撑热重启（persistence 未 init 时 no-op，单测不受影响）。
    """
    room["last_activity"] = _time.time()
    persistence.save_room(room)


def _touch_memory_only(room: dict):
    """只刷内存里的 last_activity，**不落盘** —— 深空休眠的 tick 专用。

    tick 是 5 Hz 的，每次都写盘就是每 200ms 一次整房 JSON 序列化 + SQLite 写，
    会阻塞事件循环（正是 tetris.py 点名的「SQLite 高频写盘」）。而 last_activity
    又必须刷 —— sweeper 靠它判断房间是否空闲。好在那个判断只在「没有真人在线」
    时才动手，所以内存里刷就够了，不需要写盘。
    """
    room["last_activity"] = _time.time()


def _finish_action(code: str, result: dict):
    """动作函数收尾落盘：保证写盘的是「全部变更已完成」的状态。

    动作函数中途调 _touch_activity 会把半更新状态写盘——五子棋/翻转棋/海战棋
    的回合切换（gs["next"]）都排在原落盘点之后，热重启恢复后同一人会连走两步。
    因此走子类动作统一改为：内部实现 + 本函数收尾（仅成功时落盘）。
    """
    if not result.get("ok"):
        return
    room = _rooms.get(code)
    if room:
        _touch_activity(room)


def _destroy_room(code: str):
    """彻底销毁房间：取消任务 + 移出内存 + 删除落盘行。"""
    _cancel_all_dc_tasks(code)
    _stop_stasis_tick(code)
    _rooms.pop(code, None)
    persistence.delete_room(code)


def all_rooms() -> list[dict]:
    """快照当前全部内存房间（启动恢复 bot 调度等场景用）。"""
    return list(_rooms.values())


def _normalize_restored_rules(room: dict) -> None:
    """热重启恢复老房间时，把对局里的房规补全成**当前这一版**的完整一份。

    ⚠️ **每加一条房规，老房间的 `gs["rules"]` 就少一个键**，而引擎是直接
    `game["rules"]["teams"]` 这样读的 —— 少一个键不是「退回默认值」，是
    `KeyError`，而且炸在某个 tick / 走子路径上（不是恢复的时候，排查起来很远）。
    v1.16 加 `teams` 时正好撞上这条：v1.14/v1.15 落盘的进行中对局只有 7 个键。

    `normalize` 只补缺、不覆盖已有值，所以对当前版本的房间是恒等变换。
    """
    if room.get("game") != "uno":
        return
    gs = room.get("game_state")
    if isinstance(gs, dict) and gs.get("rules") is not None:
        gs["rules"] = uno_rules.normalize(gs["rules"])


def restore_rooms() -> int:
    """热重启：从磁盘恢复全部房间。startup 时 persistence.init() 后调用。

    - 所有真人玩家标记离线（客户端 WS 自动重连后由 handle_player_reconnect 恢复）
    - playing 房启动空房计时（无人回来则 30 分钟后被 sweeper 清理，
      绝不因重启直接判负）
    - 恢复 bot id 计数器，避免新 bot 与恢复的 bot 撞 id
    """
    global _bot_id_counter
    rooms = persistence.load_all_rooms()
    restored = 0
    for room in rooms:
        code = room.get("code")
        if not code or code in _rooms:
            continue
        if room.get("status") not in ("waiting", "playing"):
            # v1.5.1 之前落盘的 finished 房：状态已经不存在，sweeper 只认
            # waiting/playing 两个分支，恢复进来就是**永远回收不掉的僵尸房**
            # （占内存、还留在落盘表里）。直接丢掉并删行。
            persistence.delete_room(code)
            continue
        room.pop("rematch_votes", None)  # 旧数据兼容：rematch 已删除
        _normalize_restored_rules(room)
        for p in room.get("players", []):
            if p.get("is_bot"):
                _bot_id_counter = min(_bot_id_counter, p["id"] - 1)
            else:
                p["connected"] = False
        if room.get("status") == "playing":
            if room.get("empty_since") is None:
                room["empty_since"] = _time.time()
        _rooms[code] = room
        restored += 1
    return restored


def _has_human_player(room: dict) -> bool:
    """Return True if room has at least one non-bot player."""
    return any(not p.get("is_bot") for p in room["players"])


def _has_human_online(room: dict) -> bool:
    """Return True if at least one non-bot player is connected."""
    return any(
        not p.get("is_bot") and p.get("connected", False)
        for p in room["players"]
    )


def _transfer_host(room: dict):
    """Transfer host to the next human player by seat order."""
    if any(p["id"] == room["host_id"] for p in room["players"]):
        return  # current host still in room
    for p in room["players"]:
        if not p.get("is_bot"):
            room["host_id"] = p["id"]
            return
    # No humans left — host_id stays stale; sweeper will clean up


def _maybe_destroy_or_mark(room: dict):
    """Decide whether to destroy a room or mark empty_since.

    Rules:
    - waiting + 连真人成员都没有了（显式退出/被移出）→ 立即销毁
    - 没有真人在线 → 记 empty_since（等待房由 WAITING_EMPTY_TTL、游戏中由
      ROOM_IDLE_TTL 回收）
    - 有真人在线 → 清 empty_since
    """
    code = room["code"]
    if room["status"] == "waiting" and not _has_human_player(room):
        _destroy_room(code)
        return
    if not _has_human_online(room):
        if room.get("empty_since") is None:
            room["empty_since"] = _time.time()
    else:
        room["empty_since"] = None
    persistence.save_room(room)


def get_room(code: str) -> dict | None:
    return _rooms.get(code)


def get_all_rooms() -> list[dict]:
    """Return lobby-visible rooms sorted by creation time descending.

    房间状态只有 waiting / playing 两种。等待房要**有真人在线**才展示——
     * 玩家掉线后不会立即被移出（见 handle_player_disconnect），会有一小段
       empty_since 宽限期，这期间不展示，避免列表里出现「幽灵房」。
    * 刚创建、WS 还没连上的房间也会短暂不展示（毫秒级，无感）。
    """
    rooms = [
        r for r in _rooms.values()
        if r["status"] == "playing"
        or (r["status"] == "waiting" and _has_human_online(r))
    ]
    rooms.sort(key=lambda r: r["created_at"], reverse=True)
    return rooms


# 注：「一个人同时只能在一个等待房」的限制已去掉（v1.7）。
# 它只在 create_room / join_room 里拦一道「你已在房间中」，却反复惹麻烦：
# 等待房掉线有 60s 宽限期，人早退回大厅了、座位还挂着，这 60 秒里他建不了也
# 进不了任何房（用户实测报过）；v1.6.1 为此打的 drop_stale_waiting_room 补丁
# 又引入「有活连接才算在房里」的第二套判据。现在直接允许一个人待在多个房间——
# 房间本身按 (房间, 玩家) 维度管理连接与回收，多房并存不需要额外处理；
# 没人管的等待房由 sweeper 按 WAITING_EMPTY_TTL 正常回收。


def _make_player(user: dict, is_host: bool) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "nickname": user["nickname"],
        "ready": False,
        "is_host": is_host,
        "connected": False,
        "is_bot": False,
    }


def _next_bot_id() -> int:
    """Generate a unique negative ID for bot players."""
    global _bot_id_counter
    _bot_id_counter -= 1
    return _bot_id_counter


def _make_bot_player(code: str, seq: int = 1) -> dict:
    """Create a synthetic bot player dict.

    seq: 本房第几个人机（1 起）。飞行棋可加多个人机，昵称带序号区分。
    """
    bot_id = _next_bot_id()
    return {
        "id": bot_id,
        "username": f"bot_{code}",
        "nickname": "人机·棋手" if seq <= 1 else f"人机·棋手{seq}",
        "ready": True,
        "is_host": False,
        "connected": True,
        "is_bot": True,
    }


# 扫雷难度的缺省档。难度是**房间级设置、由房主在等待大厅里选**（与军棋的玩法、
# 深空休眠的地图同一套），这里只是房间刚建出来时的初值——房主不选也能直接开局。
DEFAULT_MINE_DIFFICULTY = "intermediate"


def create_room(
    user: dict,
    name: str | None,
    password: str | None,
    game: str = "gomoku",
) -> dict:
    """Create a new room."""
    if game not in ("gomoku", "reversi", "minesweeper", "battleship", "dots", "ludo",
                    "2048", "xiangqi", "connect4", "tetris", "uno", "stasis", "luzhanqi"):
        raise ValueError("不支持的游戏类型")
    code = _generate_code()
    room_name = name or f"{user['username']}的房间"
    # 深空休眠 6 人（12 间舱室 / 12 个座位），其余多人游戏 4 人上限
    max_players = 6 if game == "stasis" else (
        4 if game in ("minesweeper", "ludo", "2048", "tetris", "uno") else 2)
    room = {
        "code": code,
        "name": room_name,
        "game": game,
        "host_id": user["id"],
        "status": "waiting",
        "password": password,
        "max_players": max_players,
        "players": [_make_player(user, is_host=True)],
        "game_state": None,
        "created_at": _now_iso(),
        "last_activity": _time.time(),
        "empty_since": None,  # set when no human online/in-room
        # 待答的换位请求（等待大厅专用，见 `request_swap`）。开局即清空。
        "swap_requests": [],
    }
    if game == "minesweeper":
        room["difficulty"] = DEFAULT_MINE_DIFFICULTY
    if game == "stasis":
        # 地图由房主在等待大厅里选（v9）。**默认不写这个键**：`create_game` 收到
        # `None` 会退回 `stasis_map.DEFAULT_MAP_ID`，不必在这里复制一份默认值。
        room["stasis_map_id"] = None
    if game == "luzhanqi":
        # 明棋 / 暗棋，以及暗棋下「阵亡是否公开身份」—— 都由房主在等待大厅里选。
        room["luzhanqi_mode"] = "open"
        room["luzhanqi_reveal"] = True
    if game == "battleship":
        # **棋盘尺寸 / 舰队配置**（v13）：房主在等待大厅里选，默认标准 10×10。
        room["bs_size"] = BS_DEFAULT_SIZE
    _rooms[code] = room
    _touch_activity(room)
    return room


def join_room(code: str, user: dict, password: str | None) -> dict:
    """Join a room. Raises ValueError with appropriate message on failure."""
    room = _rooms.get(code)
    if not room:
        raise ValueError("__NOT_FOUND__")
    # 原玩家放行：游戏中重连、结束后重进、等待房重进都走这一条。
    # 必须先判「是否本房成员」再查 find_user_waiting_room——后者不区分房间，
    # 会把「凭房号回到自己所在的房」误判成「你已在房间中」。
    for p in room["players"]:
        if p["id"] == user["id"]:
            room["empty_since"] = None
            # 房主已不在房里时把房主转给回来的这位
            if not any(q["id"] == room["host_id"] for q in room["players"]):
                room["host_id"] = user["id"]
            _touch_activity(room)
            return room
    if room["status"] == "playing":
        raise ValueError("游戏已开始")
    if room["password"] is not None and room["password"] != password:
        raise ValueError("房间密码错误")
    if len(room["players"]) >= room["max_players"]:
        raise ValueError("房间已满")
    room["players"].append(_make_player(user, is_host=False))
    room["empty_since"] = None
    _touch_activity(room)
    return room


def add_bot(code: str, host_id: int, difficulty: str | None = None) -> dict:
    """Add a bot player to a waiting room. Returns updated room.

    difficulty only applies to battleship (三档 AI)；其余游戏忽略。
    Stored per-bot so one room can host bots of mixed skill.

    Raises ValueError if conditions are not met.
    """
    room = _rooms.get(code)
    if not room:
        raise ValueError("房间不存在")
    if room["host_id"] != host_id:
        raise ValueError("只有房主可以添加人机")
    if room["status"] == "playing":
        raise ValueError("只能在等待大厅添加人机")
    if room["game"] not in ("gomoku", "reversi", "battleship", "dots", "ludo", "xiangqi",
                            "connect4", "uno", "stasis", "luzhanqi"):
        raise ValueError("该游戏不支持人机")
    # 满员即止：双人游戏 max_players=2，加一个 bot 后自然无法再加；
    # 飞行棋/UNO max_players=4，可连加 3 个凑满 4 人局
    if len(room["players"]) >= room["max_players"]:
        raise ValueError("房间已满")
    seq = sum(1 for p in room["players"] if p.get("is_bot")) + 1
    bot = _make_bot_player(code, seq)
    if room["game"] == "battleship":
        bot["ai_difficulty"] = bs_normalize_difficulty(difficulty)
    room["players"].append(bot)
    _touch_activity(room)
    return room


def remove_bot(code: str, host_id: int, bot_id: int | None = None) -> dict:
    """Remove a bot player from a waiting room. Returns updated room.

    Raises ValueError if conditions are not met.
    """
    room = _rooms.get(code)
    if not room:
        raise ValueError("房间不存在")
    if room["host_id"] != host_id:
        raise ValueError("只有房主可以移除人机")
    if room["status"] == "playing":
        raise ValueError("只能在等待大厅移除人机")
    # Find the bot
    bot_idx = None
    for idx, p in enumerate(room["players"]):
        if p.get("is_bot"):
            if bot_id is None or p["id"] == bot_id:
                bot_idx = idx
                break
    if bot_idx is None:
        raise ValueError("没有人机可移除")
    room["players"].pop(bot_idx)
    _touch_activity(room)
    return room


def remove_player(code: str, user_id: int) -> dict | None:
    """Remove a player from a waiting room. Returns room or None if destroyed."""
    room = _rooms.get(code)
    if not room:
        return None
    is_host = user_id == room["host_id"]
    room["players"] = [p for p in room["players"] if p["id"] != user_id]
    if is_host:
        _transfer_host(room)
    if not _has_human_player(room):
        _destroy_room(code)
        return None
    _prune_swap_requests(room)
    _touch_activity(room)
    return room


# ── 等待大厅里互换座位（v1.16）──────────────────────────────
#
# **座位就是 `room["players"]` 里的下标** —— 开局时各 `_start_*` 直接读它决定
# 先手 / 出牌顺序 / 颜色。所以这条功能**只能在大厅里用**：开局之后再换，等于把
# 已经发出去的座位号（`your_seat` / `your_color` / UNO 的座位下标）全部打乱。
#
# 请求**必须双方同意**：人机直接算同意（它没有「不同意」这个语义），真人之间由
# 服务端把请求转给对方，对方在弹窗里点同意才真的换。用户要这条的理由很体面
# （「棋类第一个位置是先手、牌类决定出牌顺序」），但没有理由让任何人被单方面
# 挪走座位。
#
# 待答请求**不落进 `room_to_dict`**：它只是「谁在等谁点头」，不是房间的公开状态；
# 而且超时/对方离开之后它就该自己失效（`_prune_swap_requests` + 应答时再校验一遍）。


def player_name(p: dict) -> str:
    """玩家的显示名（昵称优先）。换位提示里要写对方是谁，别再抄一份取值口径。"""
    return p.get("nickname") or p["username"]


def _swap_seats(room: dict, a_id: int, b_id: int) -> None:
    """交换两名玩家在 `players` 里的位置（＝交换座位号）。"""
    idx_a = _get_player_seat(room, a_id)
    idx_b = _get_player_seat(room, b_id)
    if idx_a is None or idx_b is None:
        raise ValueError("对方已经不在房间里了")
    players = room["players"]
    players[idx_a], players[idx_b] = players[idx_b], players[idx_a]


def _prune_swap_requests(room: dict) -> None:
    """丢掉牵扯到「已经不在房里的人」的待答请求。

    `room.get(...)` 而不是 `room[...]`：热重启恢复进来的老房间没有这个键
    （`create_room` 之后的房间才有），不该因此抛异常。
    """
    ids = {p["id"] for p in room["players"]}
    room["swap_requests"] = [
        r for r in room.get("swap_requests") or []
        if r["from"] in ids and r["to"] in ids
    ]


def request_swap(code: str, user_id: int, target_id: int) -> dict:
    """请求与 `target_id` 互换座位。

    返回 `{"auto": bool, "target": <对方名字>}`：`auto=True` 表示对方是人机、
    **已经换好了**；`False` 表示请求已转给对方，等他点头。
    """
    room = _rooms.get(code)
    if not room:
        raise ValueError("房间不存在")
    if room["status"] == "playing":
        raise ValueError("对局中不能换位置")
    if user_id == target_id:
        raise ValueError("不能和自己换位置")
    me = next((p for p in room["players"] if p["id"] == user_id), None)
    if me is None:
        raise ValueError("你不在这个房间里")
    target = next((p for p in room["players"] if p["id"] == target_id), None)
    if target is None:
        raise ValueError("对方已经不在房间里了")
    _prune_swap_requests(room)
    if target.get("is_bot"):
        _swap_seats(room, user_id, target_id)
        _touch_activity(room)
        return {"auto": True, "target": player_name(target)}
    pending = room.setdefault("swap_requests", [])
    if not any(r["from"] == user_id and r["to"] == target_id for r in pending):
        pending.append({"from": user_id, "to": target_id})
    _touch_activity(room)
    return {"auto": False, "target": player_name(target)}


def respond_swap(code: str, user_id: int, from_id: int, accept: bool) -> dict:
    """答复一条换位请求。**只有被请求方本人能答**（`from`/`to` 都要对上）。"""
    room = _rooms.get(code)
    if not room:
        raise ValueError("房间不存在")
    if room["status"] == "playing":
        raise ValueError("对局中不能换位置")
    _prune_swap_requests(room)
    pending = room["swap_requests"]
    idx = next((i for i, r in enumerate(pending)
                if r["from"] == from_id and r["to"] == user_id), None)
    if idx is None:
        raise ValueError("这条换位请求已经失效了")
    pending.pop(idx)
    if accept:
        _swap_seats(room, user_id, from_id)
    _touch_activity(room)
    return {"accepted": accept, "from": from_id}


def leave_room(code: str, user_id: int) -> tuple[str, dict | None]:
    """
    Handle player leaving.
    Returns (action, room) where action is 'closed', 'game_over', or 'removed'.
    room is None when action is 'closed'.

    New lifecycle: host leaving does NOT unconditionally delete the room.
    Host is transferred to next human player by seat order.
    Playing host leaving = surrender (opponent wins, room → waiting).
    """
    room = _rooms.get(code)
    if not room:
        return ("closed", None)
    is_host = user_id == room["host_id"]
    if room["status"] == "playing":
        if room["game"] == "stasis":
            # 主动退出 = 弃守（舱室失压），遗产平分给队友。**队友还在守就继续**
            # ——与扫雷「本人淘汰、其他人继续」同一套语义，人留在 players 里算排名。
            gs = room["game_state"]
            if user_id in gs["players"] and gs["players"][user_id]["alive"]:
                stasis_engine.surrender(gs, user_id)
            if is_host:
                _transfer_host(room)
            if gs.get("finished"):
                _settle_stasis(room)
                _maybe_destroy_or_mark(room)
                return ("game_over", room)
            _touch_activity(room)
            _maybe_destroy_or_mark(room)
            return ("removed", room)
        if room["game"] == "minesweeper":
            # Competitive: mark player as eliminated, keep in room for ranking
            seat = _get_player_seat(room, user_id)
            if seat is not None:
                gs = room["game_state"]
                if gs["players"][seat]["alive"]:
                    gs["players"][seat]["alive"] = False
                    gs["players"][seat]["lives_left"] = 0
                    gs["players"][seat]["eliminated_at"] = _now_iso()
            gs = room["game_state"]
            all_eliminated = all(not p["alive"] for p in gs["players"].values())
            if all_eliminated:
                gs["finished"] = True
                gs["reason"] = "eliminated"
                from app.minesweeper import _reveal_all_mines
                _reveal_all_mines(gs)
                players_info = _get_players_info(room)
                ranking = mine_compute_ranking(gs, players_info)
                _set_mine_game_over(room, ranking)
                if is_host:
                    _transfer_host(room)
                _maybe_destroy_or_mark(room)
                return ("game_over", room)
            if is_host:
                _transfer_host(room)
            _touch_activity(room)
            _maybe_destroy_or_mark(room)
            return ("removed", room)
        if room["game"] == "ludo":
            # Ludo: player leaving = surrender (mark finished, rank last)
            seat = _get_player_seat(room, user_id)
            gs = room["game_state"]
            if seat is not None and not gs["players"][seat]["finished"]:
                was_current = gs["current_seat"] == seat
                ludo_surrender(gs, seat)
                if was_current and not gs["finished"]:
                    ludo_advance_turn(gs)  # 否则回合停在已弃权座位上，整桌卡死
            go = _check_ludo_game_over(gs)
            if go:
                _finalize_ludo_game(room, go)
                if is_host:
                    _transfer_host(room)
                _maybe_destroy_or_mark(room)
                return ("game_over", room)
            if is_host:
                _transfer_host(room)
            _touch_activity(room)
            _maybe_destroy_or_mark(room)
            return ("removed", room)
        if room["game"] == "2048":
            # 2048：各玩各的盘。中途退出只结束本人（排名垫底），其他人继续；
            # 全员结束才结算。座位保留（盘面以 user_id 为键）
            gs = room["game_state"]
            if not gs["game_over_flags"].get(user_id):
                _finish_2048_player(gs, user_id, "surrender")
            go = _maybe_finish_2048(room)
            if is_host:
                _transfer_host(room)
            _touch_activity(room)
            _maybe_destroy_or_mark(room)
            return ("game_over", room) if go else ("removed", room)
        if room["game"] == "tetris":
            # 俄罗斯方块同理：各玩各的盘，中途退出只结束本人、其他人继续
            gs = room["game_state"]
            if not gs["game_over_flags"].get(user_id):
                tetris_finish_player(gs, user_id, "surrender")
            go = _maybe_finish_tetris(room)
            if is_host:
                _transfer_host(room)
            _touch_activity(room)
            _maybe_destroy_or_mark(room)
            return ("game_over", room) if go else ("removed", room)
        if room["game"] == "uno":
            # UNO 认输/中途退出：只让本人出局，牌局继续（与扫雷/飞行棋同类）。
            # 玩家**留在 players 名单里**（要算排名、座位号是列表下标），
            # 房间仍是 playing —— 所以 user_in_game 必须一起改，否则他在房间页
            # 会被 sync 立刻弹回对局页。
            go = _uno_surrender(room, user_id)
            if is_host:
                _transfer_host(room)
            _maybe_destroy_or_mark(room)
            return ("game_over", room) if go else ("removed", room)
        # Non-minesweeper/ludo/2048: leaving during play = surrender
        room["players"] = [p for p in room["players"] if p["id"] != user_id]
        winner_color = _opponent_color(_user_color(room, user_id), room["game"])
        _set_game_over(room, winner_color, "surrender")
        if is_host:
            _transfer_host(room)
        _maybe_destroy_or_mark(room)
        return ("game_over", room)
    # 等待中：移出玩家，必要时转移房主
    room["players"] = [p for p in room["players"] if p["id"] != user_id]
    if is_host:
        _transfer_host(room)
    _prune_swap_requests(room)
    _touch_activity(room)
    if room["status"] == "waiting" and not _has_human_player(room):
        _destroy_room(code)
        return ("closed", None)
    _maybe_destroy_or_mark(room)
    return ("removed", room)


def start_game(code: str, user_id: int) -> dict | None:
    """
    Start the game. Returns game_state dict or None on failure.
    Sets room status to 'playing'. Works from waiting status.
    """
    room = _rooms.get(code)
    if not room:
        return None
    if user_id != room["host_id"]:
        return None
    if room["status"] == "playing":
        return None
    # 房主点击开始即视为就绪，不要求房主 ready（与前端 UX 一致）
    if not all(p["ready"] for p in room["players"] if p["id"] != room["host_id"]):
        return None
    # 开局后座位就锁死了：还挂着的换位请求一律作废（否则下一局回到大厅时
    # 会有一条「上一局之前」的请求突然生效）
    room["swap_requests"] = []
    if room["game"] == "minesweeper":
        return _start_minesweeper(room)
    if room["game"] == "reversi":
        return _start_reversi(room)
    if room["game"] == "battleship":
        return _start_battleship(room)
    if room["game"] == "dots":
        return _start_dots(room)
    if room["game"] == "ludo":
        return _start_ludo(room)
    if room["game"] == "2048":
        return _start_2048(room)
    if room["game"] == "xiangqi":
        return _start_xiangqi(room)
    if room["game"] == "connect4":
        return _start_connect4(room)
    if room["game"] == "tetris":
        return _start_tetris(room)
    if room["game"] == "uno":
        return _start_uno(room)
    if room["game"] == "stasis":
        return _start_stasis(room)
    if room["game"] == "luzhanqi":
        return _start_luzhanqi(room)
    # Gomoku requires exactly 2 players
    if len(room["players"]) != room["max_players"]:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["empty_since"] = None
    game_state = {
        "board": create_board(),
        "moves": [],
        "next": "black",
        "winner": None,
        "reason": None,
        "player_colors": {},
    }
    host_player = next(p for p in room["players"] if p["id"] == room["host_id"])
    other_player = next(p for p in room["players"] if p["id"] != room["host_id"])
    game_state["player_colors"][host_player["id"]] = "black"
    game_state["player_colors"][other_player["id"]] = "white"
    room["game_state"] = game_state
    _touch_activity(room)
    return game_state


def _start_minesweeper(room: dict) -> dict:
    """Start a minesweeper game (1-4 players, competitive)."""
    diff = room.get("difficulty", "intermediate")
    num_players = len(room["players"])
    game_state = mine_create_game(diff, num_players)
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = game_state
    room["empty_since"] = None
    _touch_activity(room)
    return game_state


def _start_reversi(room: dict) -> dict | None:
    """Start a reversi game (2 players, competitive)."""
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    gs = {
        "board": reversi_create_board(),
        "moves": [],
        "next": "black",
        "winner": None,
        "reason": None,
        "pass_count": 0,
        "player_colors": {},
    }
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other = next(p for p in room["players"] if p["id"] != room["host_id"])
    gs["player_colors"][host["id"]] = "black"
    gs["player_colors"][other["id"]] = "white"
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_xiangqi(room: dict) -> dict | None:
    """Start a xiangqi game (2 players, 红先)."""
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    gs = xiangqi_create_game()
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other = next(p for p in room["players"] if p["id"] != room["host_id"])
    gs["player_colors"][host["id"]] = XIANGQI_RED      # 房主执红先行
    gs["player_colors"][other["id"]] = XIANGQI_BLACK
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_luzhanqi(room: dict) -> dict | None:
    """开始军棋（2 人，红先）。

    开局先进入**布阵阶段**（`phase == "setup"`）：服务端给双方各发一套合法默认阵型，
    玩家点两子交换、点「随机」重掷、点「确认布阵」；双方都确认后才进 `playing`。
    默认阵型是随机生成的（不是固定套路），免得每局都被同一套强阵型锁死。
    """
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    gs = lzq.create_game(room.get("luzhanqi_mode", "open"),
                         room.get("luzhanqi_reveal", True))
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other_player = next(p for p in room["players"] if p["id"] != room["host_id"])
    gs["player_colors"][host["id"]] = lzq.RED          # 房主执红先行
    gs["player_colors"][other_player["id"]] = lzq.BLACK
    rng = random.Random()
    for color in (lzq.RED, lzq.BLACK):
        lzq.fill_layout(gs, color, lzq.random_layout(color, rng))
    gs["ready"] = {lzq.RED: False, lzq.BLACK: False}
    gs["flag_revealed"] = {lzq.RED: False, lzq.BLACK: False}
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_connect4(room: dict) -> dict | None:
    """Start a Connect Four game (2 players, 黑先)."""
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    gs = {
        "board": c4_create_board(),
        "moves": [],
        "next": "black",
        "winner": None,
        "reason": None,
        "player_colors": {},
    }
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other = next(p for p in room["players"] if p["id"] != room["host_id"])
    gs["player_colors"][host["id"]] = "black"
    gs["player_colors"][other["id"]] = "white"
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _bs_in_bounds(gs: dict, x: int, y: int) -> bool:
    """坐标在不在**这一局那张棋盘**上（v13：尺寸是房间级设置，不再写死 10）。"""
    n = gs.get("size") or BS_BOARD_SIZE
    return 0 <= x < n and 0 <= y < n


def _start_battleship(room: dict) -> dict | None:
    """Start a battleship game (2 players, competitive)."""
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other = next(p for p in room["players"] if p["id"] != room["host_id"])
    n = room.get("bs_size") or BS_DEFAULT_SIZE
    gs = {
        "phase": "placing",
        # **棋盘尺寸是这一局的参数**（v13）：引擎里凡是「有多大」都问 `gs["size"]`
        "size": n,
        "boards": {host["id"]: bs_create_board(n), other["id"]: bs_create_board(n)},
        "attack_boards": {host["id"]: bs_create_board(n),
                          other["id"]: bs_create_board(n)},
        "placements": {host["id"]: [], other["id"]: []},
        "shots": {host["id"]: [], other["id"]: []},
        "player_colors": {host["id"]: "blue", other["id"]: "red"},
        "placement_ready": {},
        "next": "blue",
        "winner": None,
        "reason": None,
        # ── 特殊武器（v1.10）── 挂靠关系在布阵时写入
        "weapons": {host["id"]: {}, other["id"]: {}},
        "weapons_used": {host["id"]: [], other["id"]: []},
        "radar_results": {host["id"]: [], other["id"]: []},
        # 命中续射的「先手第一发」补偿标记，只有真实开炮会置位
        "first_shot_done": False,
    }
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_dots(room: dict) -> dict | None:
    """Start a dots and boxes game (2 players)."""
    if len(room["players"]) != 2:
        return None
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    gs = dots_create_game(DEFAULT_DOTS)
    host = next(p for p in room["players"] if p["id"] == room["host_id"])
    other = next(p for p in room["players"] if p["id"] != room["host_id"])
    gs["player_colors"] = {host["id"]: "black", other["id"]: "white"}
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_tetris(room: dict) -> dict | None:
    """开始俄罗斯方块对战（1-4 人，各玩各的盘 + 同种子方块序列）。

    1 人 = 单人刷分；2-4 人 = 各玩各的、同时进行，消多行会给别人顶攻击行。
    所有人都结束（顶死/认输）后按排名结算——与 2048 同一套「各玩各的 + 实时排行」
    模板，区别只在于方块序列与攻击行。
    """
    players = room["players"]
    if not 1 <= len(players) <= 4:
        return None
    seed = random.randint(0, 2**31 - 1)
    colors = ["blue", "red", "green", "yellow"]
    player_ids = [p["id"] for p in players]
    # 所有人同一个 seed → 方块序列完全一致（公平，与 2048 同种子出块同理）
    gs = tetris_create_game({pid: seed for pid in player_ids}, player_ids)
    gs["player_colors"] = {
        p["id"]: colors[i % len(colors)] for i, p in enumerate(players)
    }
    gs["seed"] = seed
    gs["ranking"] = []
    for pid in player_ids:
        tetris_spawn_next(gs, pid)
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_2048(room: dict) -> dict | None:
    """开始 2048 竞速（1-4 人，各自独立盘面 + 同种子公平出块）。

    1 人 = 单人刷分；2-4 人 = 各玩各的盘，实时共享分数排名，
    所有人都结束（达成 2048 或卡死）后按排名结算。
    """
    players = room["players"]
    if not 1 <= len(players) <= 4:
        return None
    seed = random.randint(0, 2**31 - 1)
    colors = ["blue", "red", "green", "yellow"]
    gs = {
        "boards": {p["id"]: g2048_create_board() for p in players},
        "scores": {p["id"]: 0 for p in players},
        "rngs": {p["id"]: g2048_create_seeded_rng(seed) for p in players},
        "game_over_flags": {p["id"]: False for p in players},  # 该玩家是否已结束
        "finish_reasons": {p["id"]: None for p in players},    # reach_2048 | stuck
        "finish_order": [],                                    # 结束先后（玩家 id）
        "move_counts": {p["id"]: 0 for p in players},
        "player_colors": {
            p["id"]: colors[i % len(colors)] for i, p in enumerate(players)
        },
        "seed": seed,
        "winner": None,
        "reason": None,
        "ranking": [],
    }
    # Spawn initial 2 tiles for each player
    for pid in gs["boards"]:
        g2048_spawn_tile(gs["boards"][pid], gs["rngs"][pid])
        g2048_spawn_tile(gs["boards"][pid], gs["rngs"][pid])
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = gs
    room["empty_since"] = None
    _touch_activity(room)
    return gs


def _start_ludo(room: dict) -> dict:
    """Start a ludo game (2-4 players)."""
    num_players = len(room["players"])
    if num_players < 2:
        return None
    game_state = ludo_create_game(num_players)
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = game_state
    room["empty_since"] = None
    _touch_activity(room)
    return game_state


def _start_uno(room: dict) -> dict | None:
    """开始 UNO（2-4 人回合制牌类）。

    座位顺序 = room.players 顺序（房主先手），与飞行棋同源 —— `_get_player_seat`
    与 gs["turn"] 都按这个下标对齐。
    """
    players = room["players"]
    if not 2 <= len(players) <= 4:
        return None
    # 房规由房主在等待大厅里选（`set_room_setting` 的 `preset` / 各条房规 key），
    # 这里只把它交给引擎 —— 引擎是唯一权威，`uno_rules.normalize` 会把残缺/脏值
    # 补全或抛错。
    gs = uno_create_game([p["id"] for p in players],
                         rules=room.get("uno_rules"))
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = gs
    room["empty_since"] = None
    room["_uno_ranking"] = []
    _touch_activity(room)
    return gs


def set_room_setting(code: str, user_id: int, key: str, value) -> dict:
    """**房主在等待大厅里改一条房间级设置**（v1.15 统一入口）。

    在这之前是五个各写各的 setter（`set_mine_difficulty` / `set_stasis_map` /
    `set_battleship_size` / `luzhanqi_pick_mode` / `set_uno_rules`），校验与文案各写
    一遍 —— 其中 `set_battleship_size` 还漏了「这个房间的游戏对不对」那一条
    （在 UNO 房里也能改海战棋尺寸）。现在只有这一处。

    五步校验各给一句能看懂的中文错误，**一步都不静默跳过**：被静默拒绝的动作在
    界面上就是「点了没反应」，那是本项目反复栽过的坑。

    「游戏对不对」由 `spec_for(game, key)` **结构性保证** —— 在 UNO 房里查
    `difficulty` 必然查不到，回到「这个房间没有这项设置」。
    """
    room = _rooms.get(code)
    if room is None:
        raise ValueError("房间不存在")
    if room["host_id"] != user_id:
        raise ValueError("只有房主能改玩法设置")
    if room["status"] != "waiting":
        raise ValueError("对局已经开始，改不了玩法设置了")
    spec = room_settings.spec_for(room["game"], key)
    if spec is None:
        raise ValueError(f"这个房间没有「{key}」这项设置")
    room_settings.apply(room, spec, room_settings.coerce(spec, value))
    _touch_activity(room)
    return room


def _start_stasis(room: dict) -> dict | None:
    """开始深空休眠（2-6 人实时合作塔防）。

    建完局**立刻起 tick 任务**（懒启动，正常路径零延迟）；异常路径（任务意外死掉、
    热重启后漏起）由 `_sweeper_loop` 的对账兜底补上。
    """
    players = room["players"]
    if not stasis_engine.PLAYER_MIN <= len(players) <= stasis_engine.PLAYER_MAX:
        return None
    seed = random.randint(0, 2**31 - 1)
    gs = stasis_engine.create_game([p["id"] for p in players], seed=seed,
                                   map_id=room.get("stasis_map_id"))
    # 引擎的随机源跟着对局状态一起走：热重启后从落盘的 rng 状态继续，
    # 保证「同种子 + 同输入 ⇒ 同结果」这条不变量跨重启成立。
    gs["rng"] = random.Random(seed ^ 0x5EED)
    gs["_bot_ms"] = 0
    # **给每个人机发一个互不相同的性格**（v9）。用户报的问题是「每个人机造的
    # 建筑都一模一样」—— 同一局里发到同一套流程，结果就是三个复制品。
    # 放在建局时定下来（而不是每次决策现掷）：性格要**一局之内稳定**，
    # 否则它会在两种打法之间反复横跳，比单调还难看懂。
    bots = [p["id"] for p in players if p.get("is_bot")]
    if bots:
        profs = stasis_ai.assign_profiles(random.Random(seed ^ 0xA1), bots)
        for uid, key in profs.items():
            gs["players"][uid]["ai_profile"] = key
    room["status"] = "playing"
    room["game_started_at"] = _now_iso()
    room["game_state"] = gs
    room["empty_since"] = None
    room["_stasis_settled"] = False
    room["_stasis_ranking"] = []
    _touch_activity(room)
    _ensure_stasis_tick(room["code"])
    return gs


def _ensure_stasis_tick(code: str):
    """确保这个房间有一个活着的 tick 任务（幂等）。"""
    old = _tick_tasks.get(code)
    if old is not None and not old.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    _tick_tasks[code] = loop.create_task(_stasis_tick_loop(code))


def _stop_stasis_tick(code: str):
    task = _tick_tasks.pop(code, None)
    if task is not None and not task.done():
        task.cancel()


async def _stasis_tick_loop(code: str):
    """深空休眠的推进循环 —— **本项目唯一的服务端 tick**。

    三条 tick 风险的落地（完整论述见 docs/游戏设计/深空休眠/设计.md §2）：

    - **任务泄漏** → 循环里每轮检查 status/game，不对就 `break`（自净）；
      `finally` 里无论如何都把自己从 `_tick_tasks` 摘掉；`_sweeper_loop` 每轮
      对账补一个。**不依赖「创建时启动、销毁时停止」的精确配对。**
    - **SQLite 高频写盘** → tick 内一次盘都不写，只 `_touch_memory_only`；
      每 `STASIS_PERSIST_EVERY` 个 tick 才落一次关键帧。
    - **热重启** → 状态全在 `game_state` 里（引擎是纯函数 + 显式 dt），恢复后
      重起任务即可；因为用的是**游戏内时钟**而不是墙钟，时间不会跳变。
    """
    # 延迟导入：ws 模块 import 了 room_manager，模块级导入会成环。
    from app import ws as _ws
    ticks = 0
    # **按截止时间排 tick，不是「干完活再睡 200ms」**。原来每轮固定
    # `sleep(0.2)`，处理耗时与定时器误差全部累加：Windows 上实测平均间隔
    # 205ms（定时器粒度 ~15.6ms），而引擎每轮按 200ms 推进 —— 游戏时间比真实
    # 时间慢 2.6%，客户端按真实时间预演，于是每个 tick 都被往回拽一截。
    # 落后超过一个 tick（进程卡顿、热重启）就**重新对齐、不补帧**：补帧等于让
    # 静默体在一瞬间连走好几步。
    period = STASIS_TICK_MS / 1000
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    try:
        while True:
            next_at += period
            delay = next_at - loop.time()
            if delay < -period:
                next_at = loop.time()
                delay = 0
            await asyncio.sleep(max(0.0, delay))
            room = _rooms.get(code)
            if room is None or room["status"] != "playing" \
                    or room["game"] != "stasis":
                break
            gs = room.get("game_state")
            if not gs or gs.get("finished"):
                break

            # **全员离线时暂停**：只睡不推进，游戏时钟因此冻结，重连后从暂停点
            # 继续（这正是「用游戏时钟而不是墙钟」的收益）。注意与「断线保座
            # 120 秒」不冲突 —— 那个用墙钟，一直在走。
            if not _has_human_online(room):
                if room.get("empty_since") is None:
                    room["empty_since"] = _time.time()
                continue
            room["empty_since"] = None
            _touch_memory_only(room)

            events = stasis_engine.advance(gs, STASIS_TICK_MS, gs["rng"])
            _stasis_bot_think(room, gs)

            await _ws.broadcast_stasis_tick(code, room)
            if events:
                await _ws.broadcast_stasis_events(code, events)
                # 占房 / 让房改的是 `rooms[].owner`，而**归属只在离散层
                # （`stasis_state`）里下发**（tick 只带「谁站在哪」）。不补这一份，
                # 跑动阶段抢到的舱室在客户端**不会亮**，要等到躺下（走
                # `_stasis_do`）才亮起来 —— 而「舱室跟着人走」这条规则的全部
                # 反馈就是那盏灯。一次跑动阶段最多十来次，不是高频路径。
                #
                # **`auto` 那一批不算**：它们是 `_start_playing` 给没占到房的人
                # 补的，属于转场本身，客户端看同一条 tick 的 `phase` 就知道开局了；
                # 而且转场第一件事就是躺下（`_stasis_do` 自己会推一份全量）。
                if any(e["type"] in _ROOM_OWNER_EVENTS and not e.get("auto")
                       for e in events):
                    await _ws._broadcast_stasis_state(code, room)

            ticks += 1
            if ticks % STASIS_PERSIST_EVERY == 0:
                persistence.save_room(room)      # 关键帧

            if gs.get("finished"):
                await _finish_stasis(room)
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        # tick 绝不能把事件循环带崩（同 _sweeper_loop 的约定）。但**必须留下痕迹**：
        # 静默吞掉的话，tick 死了只表现为「游戏整个冻住」，而 sweeper 要 60 秒后
        # 才把它对账补回来 —— 现场只剩一个卡住的界面，无从判断是网络、前端还是
        # 服务端。排查时先看这一行。
        logging.exception("深空休眠 tick 循环异常退出 code=%s", code)
    finally:
        _tick_tasks.pop(code, None)


def _stasis_bot_think(room: dict, gs: dict):
    """人机队友按**固定间隔**决策（不是回合制那种一次性延迟任务）。

    它在合作游戏里扮演的是「和你一起守站的船员」，所以决策失败（钱不够等）
    直接跳过即可，不需要报错。
    """
    # **跑动阶段必须每 tick 都决策**：那时人机的动作是「移动方向」，而方向是
    # 每 tick 都要重新发的输入（引擎按方向积分位置）。按间隔发的话人机会一顿一顿地走。
    if gs["phase"] != "run":
        gs["_bot_ms"] = gs.get("_bot_ms", 0) + STASIS_TICK_MS
        if gs["_bot_ms"] < STASIS_BOT_INTERVAL_MS:
            return
        gs["_bot_ms"] = 0
    for p in room["players"]:
        if not p.get("is_bot"):
            continue
        try:
            action = stasis_ai.decide(gs, p["id"])
        except Exception:
            continue
        if not action:
            continue
        try:
            _apply_stasis_action(gs, p["id"], action)
        except (ValueError, KeyError):
            pass


def _apply_stasis_action(gs: dict, uid: int, action: dict) -> dict:
    """把「动作描述」翻译成引擎调用（人机与真人共用同一套翻译）。"""
    kind = action.get("type")
    if kind == "idle":
        # 「随性」性格的一支：**故意什么都不做**。人机不需要每一轮都有产出，
        # 一个总在忙的人机反而显得像脚本（见 `stasis_ai._random_action`）。
        return {}
    if kind == "move":
        return stasis_engine.set_move(gs, uid, action["dx"], action["dy"])
    if kind == "lie":
        return stasis_engine.lie(gs, uid)
    if kind == "build":
        return stasis_engine.build(gs, uid, action["kind"], action["cell"])
    if kind == "upgrade":
        return stasis_engine.upgrade(gs, uid, action["target"])
    if kind == "demolish":
        return stasis_engine.demolish(gs, uid, action["cell"])
    if kind == "patch":
        return stasis_engine.patch(gs, uid, action.get("target_uid"),
                                   gate=action.get("gate"))
    raise ValueError(f"未知动作 {kind}")


def _stasis_action(code: str, user_id: int, fn) -> dict:
    """真人动作的统一包装：校验 → 调引擎 → 落盘 → 检查终局。

    **离散动作照旧立即落盘**（走 `_finish_action`）—— tick 不落盘，但玩家的
    每一次建造/升级都是「关键状态变更」，不能丢。
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "stasis":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    if user_id not in gs["players"]:
        return {"ok": False, "error": "你不是游戏玩家"}
    try:
        result = fn(gs, user_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    _finish_action(code, result)
    return result


def stasis_move(code: str, user_id: int, dx: float, dy: float) -> dict:
    """真人移动输入（5 Hz，每 tick 一条）。

    **刻意不走 `_stasis_action`**：那条路径每次都 `_finish_action` 落盘，而移动
    输入一局要发上千条 —— 那是「tick 内零写盘」这条不变量最容易被破坏的地方
    （见设计文档 §2 的风险二）。移动方向是**瞬态输入**不是状态：热重启后重置为
    静止，玩家松手重按即可，丢了没有任何后果。
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "stasis":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    if user_id not in gs["players"]:
        return {"ok": False, "error": "你不是游戏玩家"}
    try:
        return stasis_engine.set_move(gs, user_id, dx, dy)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def stasis_lie(code: str, user_id: int) -> dict:
    return _stasis_action(
        code, user_id, lambda gs, uid: stasis_engine.lie(gs, uid))


def stasis_build(code: str, user_id: int, kind: str, cell: int) -> dict:
    return _stasis_action(
        code, user_id, lambda gs, uid: stasis_engine.build(gs, uid, kind, cell))


def stasis_upgrade(code: str, user_id: int, target) -> dict:
    return _stasis_action(
        code, user_id, lambda gs, uid: stasis_engine.upgrade(gs, uid, target))


def stasis_demolish(code: str, user_id: int, cell: int) -> dict:
    return _stasis_action(
        code, user_id, lambda gs, uid: stasis_engine.demolish(gs, uid, cell))


def stasis_patch(code: str, user_id: int, target_uid: int | None,
                 gate: str | None = None) -> dict:
    return _stasis_action(
        code, user_id,
        lambda gs, uid: stasis_engine.patch(gs, uid, target_uid, gate=gate))


def stasis_surrender(code: str, user_id: int) -> dict:
    """弃守＝舱室失压。**只结束本人，队友继续守**（同扫雷/UNO 的语义）。

    走 `_stasis_action` 而不是通用的 `surrender` —— 通用那条是「对手获胜」的
    2 人对战语义，用在合作塔防上会直接把整局判给静默体。
    """
    return _stasis_action(
        code, user_id, lambda gs, uid: stasis_engine.surrender(gs, uid))


def _settle_stasis(room: dict) -> list[dict] | None:
    """终局结算的**同步部分**（幂等，已结算过返回 None）。

    拆出同步版是因为 `leave_room` 是同步函数（WS 的 leave 路径要用），
    而广播是异步的。
    """
    if room.get("_stasis_settled"):
        return None
    room["_stasis_settled"] = True
    gs = room["game_state"]
    players_info = [
        {"seat": i, "uid": p["id"], "username": p["username"],
         "nickname": p.get("nickname")}
        for i, p in enumerate(room["players"])
    ]
    ranking = stasis_engine.compute_ranking(gs, players_info)
    gs["ranking"] = ranking
    room["_stasis_ranking"] = ranking
    _stop_stasis_tick(room["code"])
    _back_to_lobby(room)
    gs["finished"] = True
    _touch_activity(room)
    room["empty_since"] = None if _has_human_online(room) else _time.time()
    _notify_game_end(room)
    return ranking


async def _finish_stasis(room: dict):
    """终局：结算 + 广播 `game_over`（tick 路径与动作路径共用）。"""
    ranking = _settle_stasis(room)
    if ranking is None:
        return
    from app import ws as _ws
    await _ws.broadcast_stasis_game_over(room, ranking)


# 一段动作序列最多多少个动作：客户端把「一块方块的整个生命周期」攒起来一次发，
# 正常几十个动作（左右平移 + 旋转 + 下落）。上限只是防滥用。
TETRIS_MAX_ACTIONS = 200


def tetris_apply_actions(code: str, user_id: int, actions: list) -> dict:
    """重放客户端攒下的动作序列。**服务端盘面的唯一权威入口**。

    客户端为了手感会本地预演，但每一次动作都要在这里由服务端用自己的引擎再走
    一遍——所以「盘面在哪儿」永远由服务端说了算，客户端只是把它渲染出来。
    每块方块一次往返（不是每个动作一次），手感与流量都够用；也正因如此不需要
    服务端 tick：没有任何「按时间推进」的状态。
    """
    result = _tetris_apply_actions(code, user_id, actions)
    _finish_action(code, result)
    return result


def _tetris_apply_actions(code: str, user_id: int, actions: list) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "tetris":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    if user_id not in gs["boards"]:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["game_over_flags"].get(user_id, False):
        return {"ok": False, "error": "你已结束本局"}
    if gs["winner"]:
        return {"ok": False, "error": "游戏已结束"}
    if not isinstance(actions, list) or not actions:
        # 空序列是正常情况（点了一下但什么都没发生），静默丢弃
        return {"ok": False, "silent": True, "error": "没有动作"}
    if len(actions) > TETRIS_MAX_ACTIONS:
        return {"ok": False, "error": "动作序列过长"}

    locked = False
    cleared = score_gained = garbage_sent = 0
    victims: list[int] = []
    for action in actions:
        if not isinstance(action, str):
            continue
        res = tetris_apply_action(gs, user_id, action)
        if not res.get("ok") or not res.get("locked"):
            continue
        locked = True
        cleared += res.get("cleared", 0)
        score_gained += res.get("score_gained", 0)
        garbage_sent += res.get("garbage_sent", 0)
        victims.extend(res.get("garbage_victims") or [])
        # 一次序列里通常只锁一块；万一锁了多块（超长序列）也照常累计
        if gs["game_over_flags"].get(user_id):
            break

    result = {
        "ok": True, "locked": locked, "cleared": cleared,
        "score_gained": score_gained, "garbage_sent": garbage_sent,
        # 收到攻击行的玩家（含被顶死的）：WS 层要给这些人也推一次新盘面，
        # 他们的盘变了但不是他们自己动的
        "garbage_victims": victims, "game_over": None,
        # 这次顶过去的行数（受害者弹「被顶了 N 行」提示用；本局累计在 gs 里）。
        # 一次动作序列通常只锁一块，所以「本次发送量」就是每个受害者收到的量。
        "garbage_count": garbage_sent if victims else 0,
    }
    if gs["game_over_flags"].get(user_id):
        result["player_finished"] = gs["finish_reasons"].get(user_id)
    go = _maybe_finish_tetris(room)
    if go:
        result["game_over"] = go
    return result


def _tetris_player_reason(gs: dict, pid: int) -> str | None:
    return gs["finish_reasons"].get(pid)


def _compute_tetris_ranking(room: dict) -> list[dict]:
    """俄罗斯方块排名：还在玩的排最前，其余按「撑得久 → 分高」排。"""
    gs = room["game_state"]
    order = gs.get("finish_order", [])
    entries = []
    for seat, p in enumerate(room["players"]):
        pid = p["id"]
        entries.append({
            "seat": seat,
            "user_id": pid,
            "username": p["username"],
            "nickname": p.get("nickname"),
            "is_bot": p.get("is_bot", False),
            "color": gs["player_colors"].get(pid, ""),
            "score": gs["scores"].get(pid, 0),
            "lines": gs["lines"].get(pid, 0),
            "level": gs["level"].get(pid, 1),
            "garbage_received": gs["garbage_received"].get(pid, 0),
            "finish_reason": _tetris_player_reason(gs, pid),
            "_order": order.index(pid) if pid in order else len(order),
        })
    entries.sort(key=lambda e: (
        0 if not gs["game_over_flags"].get(e["user_id"]) else 1,
        -e["_order"],
        -e["score"],
    ))
    for i, e in enumerate(entries):
        e["rank"] = i + 1
        e.pop("_order")
    return entries


def _maybe_finish_tetris(room: dict) -> dict | None:
    """全员结束才结算；否则返回 None（其他人继续玩自己的盘）。"""
    gs = room["game_state"]
    if not all(gs["game_over_flags"].values()):
        return None
    ranking = _compute_tetris_ranking(room)
    # 排名必须在 _set_game_over **之前**写回 gs（它内部触发战绩落库，快照读的
    # 就是 gs["ranking"]；后写会让存下来的快照排名永远是空的——2048 踩过）
    room["game_state"]["ranking"] = ranking
    winner_color = ranking[0]["color"] if ranking else "draw"
    _set_game_over(room, winner_color, "ranking")
    return {"winner": winner_color, "reason": "ranking", "ranking": ranking}


def game2048_make_move(code: str, user_id: int, direction: str) -> dict:
    """Apply a 2048 move for a player. Returns result dict for WS broadcast."""
    result = _game2048_make_move(code, user_id, direction)
    _finish_action(code, result)
    return result


def _game2048_make_move(code: str, user_id: int, direction: str) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "2048":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    pid = user_id
    if pid not in gs["boards"]:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["game_over_flags"].get(pid, False):
        return {"ok": False, "error": "你已结束本局"}
    if gs["winner"]:
        return {"ok": False, "error": "游戏已结束"}
    board = gs["boards"][pid]
    new_board, score_gained, moved, trace = g2048_move_with_trace(board, direction)
    if not moved:
        # silent：往「走不动」的方向滑/点是 2048 的正常操作，不该弹错误提示
        return {"ok": False, "silent": True, "error": "无效移动"}
    gs["boards"][pid] = new_board
    gs["scores"][pid] += score_gained
    gs["move_counts"][pid] = gs["move_counts"].get(pid, 0) + 1
    # Spawn new tile
    spawned = g2048_spawn_tile(new_board, gs["rngs"][pid])
    result = {
        "ok": True, "board": new_board, "score": gs["scores"][pid],
        "score_gained": score_gained, "spawned": spawned,
        # 逐块走子轨迹：客户端据此播放滑动/合并动画（盘面私有，只发给走子者）
        "trace": trace,
        "game_over": None,
    }
    # 达成 2048 → 本人结束（其他人继续玩自己的盘）
    if g2048_has_won(new_board):
        _finish_2048_player(gs, pid, "reach_2048")
        result["player_finished"] = "reach_2048"
    # 卡死 → 本人结束
    elif not g2048_has_moves(new_board):
        _finish_2048_player(gs, pid, "stuck")
        result["player_finished"] = "stuck"
    else:
        return result
    # 全员结束 → 排名结算
    go = _maybe_finish_2048(room)
    if go:
        result["game_over"] = go
    return result


def _finish_2048_player(gs: dict, pid: int, reason: str):
    """标记某玩家本局结束（达成 2048 或卡死），记录结束先后。"""
    if gs["game_over_flags"].get(pid):
        return
    gs["game_over_flags"][pid] = True
    gs["finish_reasons"][pid] = reason
    gs["finish_order"].append(pid)


def _compute_2048_ranking(room: dict) -> list[dict]:
    """2048 排名：达成 2048 的按达成先后排前，其余按分数降序。"""
    gs = room["game_state"]
    order = gs.get("finish_order", [])
    entries = []
    for seat, p in enumerate(room["players"]):
        pid = p["id"]
        reason = gs["finish_reasons"].get(pid)
        entries.append({
            "seat": seat,
            "user_id": pid,
            "username": p["username"],
            "nickname": p.get("nickname"),
            "is_bot": p.get("is_bot", False),
            "color": gs["player_colors"].get(pid, ""),
            "score": gs["scores"].get(pid, 0),
            "max_tile": g2048_max_tile(gs["boards"][pid]),
            "finish_reason": reason,
            "_order": order.index(pid) if pid in order else len(order),
        })
    entries.sort(key=lambda e: (
        0 if e["finish_reason"] == "reach_2048" else 1,
        e["_order"],
        -e["score"],
    ))
    for i, e in enumerate(entries):
        e["rank"] = i + 1
        e.pop("_order")
    return entries


def _maybe_finish_2048(room: dict) -> dict | None:
    """全员结束才结算；否则返回 None（其他人继续）。"""
    gs = room["game_state"]
    if not all(gs["game_over_flags"].values()):
        return None
    ranking = _compute_2048_ranking(room)
    # 排名必须在 _set_game_over **之前**写回 gs：_set_game_over 会触发战绩落库，
    # 而快照读的正是 gs["ranking"]，后写会让存下来的快照排名永远是空的。
    room["game_state"]["ranking"] = ranking
    # 注意：2048 的 winner 记的是「排名第一的颜色」，不是「胜利者」。
    # 只有合出 2048（ranking 里 finish_reason == reach_2048）才算赢，
    # 客户端据此文案区分「胜利」与「游戏结束」，不要拿 winner 直接当胜负。
    winner_color = ranking[0]["color"] if ranking else "draw"
    _set_game_over(room, winner_color, "ranking")
    return {"winner": winner_color, "reason": "ranking", "ranking": ranking}


def _user_color(room: dict, user_id: int) -> str:
    return room["game_state"]["player_colors"].get(user_id, "")


def _opponent_color(color: str, game: str | None = None) -> str:
    """翻转颜色（认输 / 中途离开 / 断线判负都靠它定赢家）。

    **必须带上 game**：象棋与军棋都用红/黑，`black` 在那里对手是红、在五子棋/翻转棋里
    对手是白——只看颜色本身分不出来。旧调用方不传 game 时行为逐字不变。
    """
    if game in ("xiangqi", "luzhanqi"):
        return XIANGQI_BLACK if color == XIANGQI_RED else XIANGQI_RED
    if color == "black":
        return "white"
    if color == "white":
        return "black"
    if color == "blue":
        return "red"
    if color == "red":
        return "blue"
    return "white"


def _get_player_seat(room: dict, user_id: int) -> int | None:
    """Return the seat index for a user_id in a room, or None."""
    for idx, p in enumerate(room["players"]):
        if p["id"] == user_id:
            return idx
    return None


def get_disconnect_timeout() -> int:
    """断线保座时长（秒）。测试会调小，所以下发给客户端而不是让它写死。"""
    return _disconnect_timeout


def reconnect_seconds_left(room: dict, player: dict) -> int | None:
    """该玩家重连的剩余秒数；不在游戏中或仍在线时为 None。

    客户端拿它做「等待重连（N 秒）」的倒计时起点——重连后 sync 给的是**剩余**
    秒数而不是总时长，所以刷新页面/换设备回来看到的倒计时也是准的。
    """
    if room.get("status") != "playing" or player.get("connected", True):
        return None
    started = player.get("disconnected_at")
    if not started:
        return _disconnect_timeout
    return max(0, int(round(_disconnect_timeout - (_time.time() - started))))


def user_in_game(room: dict, user_id: int) -> bool:
    """该用户是否仍是这局的「在局玩家」——房间页据此决定要不要把他送回对局页。

    **扫雷 / 飞行棋 / 2048 允许「本人已出局、其他人继续」**：这三种情况下
    `leave_room` 会把该玩家留在 `players` 名单里（扫雷要算排名、2048 的盘面以
    user_id 为键、飞行棋要排座次），房间仍是 `playing`。若房间页只看
    「status=playing 且有 game」就自动跳对局页，这类玩家一点「退出/返回房间」
    就会被立刻弹回去——表现为**点了没反应、页面闪一下还在原处**。

    其余游戏（2 人对战）中途离开必然终局、房间回到 waiting，走不到这条判断。
    """
    if room.get("status") != "playing":
        return False
    if not any(p["id"] == user_id for p in room["players"]):
        return False
    gs = room.get("game_state") or {}
    game = room["game"]
    if game == "minesweeper":
        seat = _get_player_seat(room, user_id)
        return bool(gs.get("players", {}).get(seat, {}).get("alive", True))
    if game == "ludo":
        seat = _get_player_seat(room, user_id)
        return not gs.get("players", {}).get(seat, {}).get("finished", False)
    if game == "2048":
        return not gs.get("game_over_flags", {}).get(user_id, False)
    if game == "tetris":
        # 顶死之后本人还在房里（要算排名），房间仍是 playing——不加这条，
        # 回房间页会被立刻弹回对局页（点退出没反应）
        return not gs.get("game_over_flags", {}).get(user_id, False)
    if game == "uno":
        # 认输/断线判负之后本人已出局但牌局还在跑（要算排名、座位号是下标），
        # 同上：漏了这条就会「点了返回房间立刻被弹回对局页」。
        seat = _get_player_seat(room, user_id)
        return seat is not None and seat not in gs.get("out", [])
    if game == "stasis":
        # 舱室失压之后本人已出局但队友还在守（要算排名、遗产已转移），同上。
        return bool(gs.get("players", {}).get(user_id, {}).get("alive", False))
    return True


def _get_players_info(room: dict) -> list[dict]:
    """Build players_info list for ranking computation."""
    return [
        {
            "seat": idx,
            "username": p["username"],
            "nickname": p.get("nickname"),
        }
        for idx, p in enumerate(room["players"])
    ]


def _back_to_lobby(room: dict):
    """对局结束：房间回到等待态。

    房间状态只有 waiting / playing 两种——「已结束」等同于等待中：还能被加入、
    房主可以直接续开下一局。`round` 记录已打完的局数，供客户端显示「上一局已结束」
    提示。

    **真人非房主要复位为未准备**：v1.5.1 曾经故意不复位（「刚一起打完的人显然还
    想继续」），结果人机房之外的对局打完一局后，房主点「开始游戏」会直接把还没
    表态的人拖进下一局——续开一局应该重新表态。房主本来就不需要准备（前端
    `allReady` 里房主恒真、服务端开局校验也跳过房主），人机是自动准备的，两者都
    不参与复位。
    """
    room["status"] = "waiting"
    room["round"] = room.get("round", 0) + 1
    for p in room["players"]:
        if p["id"] != room["host_id"] and not p.get("is_bot"):
            p["ready"] = False


def _set_mine_game_over(room: dict, ranking: list[dict]):
    """Finalize a minesweeper game with ranking."""
    _back_to_lobby(room)
    room["game_state"]["finished"] = True
    room["_mine_ranking"] = ranking  # stored for snapshot building
    _touch_activity(room)
    # Mark empty_since if no humans online
    if not _has_human_online(room):
        room["empty_since"] = _time.time()
    else:
        room["empty_since"] = None
    _notify_game_end(room)


def make_move(code: str, user_id: int, x: int, y: int) -> dict:
    """
    Attempt a move. Returns result dict:
    {'ok': True, 'move': {...}, 'game_over': {...}|None}
    or {'ok': False, 'error': str}
    """
    result = _apply_move(code, user_id, x, y)
    _finish_action(code, result)
    return result


def _apply_move(code: str, user_id: int, x: int, y: int) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你"}
    if x < 0 or x >= BOARD_SIZE or y < 0 or y >= BOARD_SIZE:
        return {"ok": False, "error": "坐标超出范围"}
    if gs["board"][y][x] != 0:
        return {"ok": False, "error": "该位置已有棋子"}
    # Place stone
    stone = BLACK if color == "black" else WHITE
    place_stone(gs["board"], x, y, stone)
    gs["moves"].append({"x": x, "y": y, "color": color})
    # Check win
    if check_win(gs["board"], x, y):
        _set_game_over(room, color, "five")
        move_msg = {"x": x, "y": y, "color": color, "next": gs["next"]}
        go_msg = {"winner": color, "reason": "five"}
        return {"ok": True, "move": move_msg, "game_over": go_msg}
    # Check draw
    if is_board_full(len(gs["moves"])):
        _set_game_over(room, "draw", "draw")
        move_msg = {"x": x, "y": y, "color": color, "next": gs["next"]}
        go_msg = {"winner": "draw", "reason": "draw"}
        return {"ok": True, "move": move_msg, "game_over": go_msg}
    # Switch turn
    gs["next"] = "white" if color == "black" else "black"
    move_msg = {"x": x, "y": y, "color": color, "next": gs["next"]}
    return {"ok": True, "move": move_msg, "game_over": None}


def reversi_make_move(code: str, user_id: int, x: int, y: int) -> dict:
    """Attempt a reversi move. Returns result with flips, pass, game_over."""
    result = _reversi_make_move(code, user_id, x, y)
    _finish_action(code, result)
    return result


def _reversi_make_move(code: str, user_id: int, x: int, y: int) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "reversi":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你"}
    stone = REVERSI_BLACK if color == "black" else REVERSI_WHITE
    try:
        flips = reversi_apply_move(gs["board"], x, y, stone)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    # Record move
    gs["moves"].append({
        "x": x, "y": y, "color": color,
        "flipped": [[fx, fy] for fx, fy in flips],
    })
    gs["pass_count"] = 0
    # Switch turn
    gs["next"] = _opponent_color(color)
    next_stone = REVERSI_WHITE if gs["next"] == "white" else REVERSI_BLACK
    next_legal = reversi_get_legal_moves(gs["board"], next_stone)
    # Next player has moves → normal advance
    if next_legal:
        move_msg = {
            "x": x, "y": y, "color": color, "next": gs["next"],
            "flipped": [[fx, fy] for fx, fy in flips],
        }
        return {"ok": True, "move": move_msg, "pass": None, "game_over": None}
    # Next player has no legal moves → auto-pass
    gs["pass_count"] = 1
    passed_color = gs["next"]
    gs["next"] = color  # turn stays with current player
    # Check if current player also has no moves → game over
    cur_stone = REVERSI_BLACK if color == "black" else REVERSI_WHITE
    cur_legal = reversi_get_legal_moves(gs["board"], cur_stone)
    if not cur_legal:
        score = reversi_count_stones(gs["board"])
        if score["black"] > score["white"]:
            winner = "black"
        elif score["white"] > score["black"]:
            winner = "white"
        else:
            winner = "draw"
        _set_game_over(room, winner, "score")
        move_msg = {
            "x": x, "y": y, "color": color, "next": gs["next"],
            "flipped": [[fx, fy] for fx, fy in flips],
        }
        pass_msg = {"color": passed_color}
        go_msg = {"winner": winner, "reason": "score", "score": score}
        return {
            "ok": True, "move": move_msg,
            "pass": pass_msg, "game_over": go_msg,
        }
    # Only opponent passed, current player goes again
    move_msg = {
        "x": x, "y": y, "color": color, "next": gs["next"],
        "flipped": [[fx, fy] for fx, fy in flips],
    }
    pass_msg = {"color": passed_color}
    return {"ok": True, "move": move_msg, "pass": pass_msg, "game_over": None}


def xiangqi_make_move(code: str, user_id: int, frm, to) -> dict:
    """Attempt a xiangqi move. Returns result with the move and game_over."""
    result = _xiangqi_make_move(code, user_id, frm, to)
    _finish_action(code, result)
    return result


def _xiangqi_make_move(code: str, user_id: int, frm, to) -> dict:
    """象棋走子。走法合法性、将死/困毙/判和全在引擎里（`xiangqi.make_move`）。"""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "xiangqi":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你走"}
    result = xiangqi_engine_move(gs, frm, to)
    if not result["ok"]:
        return result
    if result["game_over"]:
        _set_game_over(room, result["game_over"]["winner"], result["game_over"]["reason"])
    return result


def luzhanqi_make_move(code: str, user_id: int, frm, to) -> dict:
    """军棋走子。合法性、碰子、无棋可走判负、吃军旗判胜全在引擎里。"""
    result = _luzhanqi_make_move(code, user_id, frm, to)
    _finish_action(code, result)
    return result


def _luzhanqi_make_move(code: str, user_id: int, frm, to) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "luzhanqi":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    result = lzq.make_move(gs, frm, to)
    if not result["ok"]:
        return result
    if result["game_over"]:
        _set_game_over(room, result["game_over"]["winner"], result["game_over"]["reason"])
    return result


def luzhanqi_setup(code: str, user_id: int, action: str, a=None, b=None, name=None) -> dict:
    """布阵阶段的四个动作：``swap`` / ``random`` / ``preset`` / ``confirm``。

    **交换合法性只在服务端判定**（地雷只能在后两排、军旗必须在大本营、炸弹不能在第一排），
    客户端不做任何校验，只把请求发上来 —— 同象棋「合法着法由服务端下发」那条约定。
    `preset` 摆的是公开阵库里的经典阵型，名单由服务端随视图下发（`lzq.PRESETS`）。
    """
    room = _rooms.get(code)
    if not room or room["game"] != "luzhanqi":
        return {"ok": False, "error": "这个房间不是军棋"}
    if room["status"] != "playing" or not room.get("game_state"):
        return {"ok": False, "error": "对局还没开始"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs.get("phase") != "setup":
        return {"ok": False, "error": "已经开打了，改不了阵型"}
    if gs.get("ready", {}).get(color):
        return {"ok": False, "error": "已经确认布阵，不能再改"}
    if action == "swap":
        if not a or not b:
            return {"ok": False, "error": "参数不全"}
        err = lzq.swap_layout(gs, color, a, b)
        if err:
            return {"ok": False, "error": err}
    elif action == "random":
        for r in lzq.own_rows(color):
            for c in range(lzq.COLS):
                gs["board"][r][c] = None
        lzq.fill_layout(gs, color, lzq.random_layout(color, random.Random()))
    elif action == "preset":
        layout = lzq.preset_layout(color, str(name or ""))
        if layout is None:
            return {"ok": False, "error": "没有这套阵型"}
        # 阵型表是常量，理论上必然合法 —— 仍然过一遍校验：它是「布阵合法性」的
        # 唯一判据，将来往表里加阵型时手滑写错一个字，这里会当场拦住。
        err = lzq.validate_layout(color, layout)
        if err:
            return {"ok": False, "error": err}
        for r in lzq.own_rows(color):
            for c in range(lzq.COLS):
                gs["board"][r][c] = None
        lzq.fill_layout(gs, color, layout)
    elif action == "confirm":
        lzq.confirm_setup(gs, color)
    else:
        return {"ok": False, "error": "未知的布阵操作"}
    _touch_activity(room)
    return {"ok": True, "color": color}


def connect4_make_move(code: str, user_id: int, col: int) -> dict:
    """重力四子棋落子。返回结果带 move / game_over，与五子棋同形。"""
    result = _connect4_make_move(code, user_id, col)
    _finish_action(code, result)
    return result


def _connect4_make_move(code: str, user_id: int, col: int) -> dict:
    """重力落子 + 四连检测 + 和棋判定。

    **客户端只给列号**：落点行由重力决定，服务端是唯一权威——客户端自己按
    「列内还有几个空位」推落点，一旦和棋盘状态差一格就会画错（飞行棋客户端
    自己推路径画错是同一类教训）。落点行随 move 消息回给所有人。
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "connect4":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你走"}
    if not c4_is_valid_move(gs["board"], col):
        return {"ok": False, "error": "这一列已经满了"}
    row = c4_drop(gs["board"], col, C4_BLACK if color == "black" else C4_WHITE)
    gs["moves"].append({"x": col, "y": row, "color": color})
    if c4_check_win(gs["board"], row, col):
        _set_game_over(room, color, "four")
        return {
            "ok": True,
            "move": {"x": col, "y": row, "color": color, "next": gs["next"]},
            "game_over": {"winner": color, "reason": "four"},
        }
    if c4_is_board_full(gs["board"]):
        _set_game_over(room, "draw", "draw")
        return {
            "ok": True,
            "move": {"x": col, "y": row, "color": color, "next": gs["next"]},
            "game_over": {"winner": "draw", "reason": "draw"},
        }
    gs["next"] = "white" if color == "black" else "black"
    return {
        "ok": True,
        "move": {"x": col, "y": row, "color": color, "next": gs["next"]},
        "game_over": None,
    }


def dots_make_move(
    code: str, user_id: int, orient: str, y: int, x: int,
) -> dict:
    """Attempt a dots and boxes move. Returns result with completions, game_over."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "dots":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    player_num = DOTS_P1 if color == "black" else DOTS_P2
    if gs["next"] != player_num:
        return {"ok": False, "error": "还没轮到你"}
    if not dots_is_valid_edge(gs, orient, y, x):
        return {"ok": False, "error": "非法边"}
    result = dots_apply_move(gs, orient, y, x)
    _touch_activity(room)
    completed = result["completed"]
    extra_turn = result["extra_turn"]
    game_over = result["game_over"]
    move_msg = {
        "orient": orient, "y": y, "x": x,
        "color": color,
        "next": "black" if gs["next"] == DOTS_P1 else "white",
        "completed": [[by, bx] for by, bx in completed],
        "extra_turn": extra_turn,
        "scores": gs["scores"],
    }
    if game_over:
        # 必须落 finished：否则房间停在 playing，玩家返回大厅时 RoomScreen 收到
        # sync（status=playing + game）会立刻把人弹回对局页，表现为「返回房间无效」
        _set_game_over(room, gs["winner"], gs["reason"])
        go_msg = {
            "winner": gs["winner"],
            "reason": gs["reason"],
            "score": {
                "black": gs["scores"][DOTS_P1],
                "white": gs["scores"][DOTS_P2],
            },
        }
        return {"ok": True, "move": move_msg, "game_over": go_msg}
    return {"ok": True, "move": move_msg, "game_over": None}


def battleship_set_placement(
    code: str, user_id: int, ships: list[dict], weapons: dict | None = None,
) -> dict:
    """Set battleship placement for a player.

    weapons: {weapon_key: ship_id} — 三件特殊武器挂靠到三条**不同**的舰船上。
    字段缺失或不合法时用默认分配兜底（旧客户端不发这个字段）。

    Returns result dict with 'ok', 'ready' (bool), and optionally 'battle_start' (bool).
    """
    result = _battleship_set_placement(code, user_id, ships, weapons)
    _finish_action(code, result)
    return result


def _battleship_set_placement(
    code: str, user_id: int, ships: list[dict], weapons: dict | None = None,
) -> dict:
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "battleship":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    if gs["phase"] != "placing":
        return {"ok": False, "error": "已过布阵阶段"}
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    n = gs.get("size") or BS_DEFAULT_SIZE
    if not validate_placement(ships, n):
        return {"ok": False, "error": "布阵不合法"}
    # Apply placement
    board = gs["boards"][user_id]
    apply_placement(board, ships, n)
    gs["placements"][user_id] = ships
    gs["weapons"][user_id] = normalize_weapon_assignment(weapons, n)
    gs["placement_ready"][user_id] = True
    # Check if both ready
    all_ready = all(
        gs["placement_ready"].get(p["id"], False)
        for p in room["players"]
    )
    if all_ready:
        gs["phase"] = "battle"
        return {"ok": True, "ready": True, "battle_start": True}
    return {"ok": True, "ready": True, "battle_start": False}


def battleship_fire(code: str, user_id: int, x: int, y: int) -> dict:
    result = _battleship_fire(code, user_id, x, y)
    _finish_action(code, result)
    return result


def _battleship_fire(code: str, user_id: int, x: int, y: int) -> dict:
    """Fire a shot in battleship. Returns result dict.

    On success: {'ok': True, 'shot': {...}, 'game_over': {...}|None}
    On failure: {'ok': False, 'error': str}
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "battleship":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    if gs["phase"] != "battle":
        return {"ok": False, "error": "当前不在战斗阶段"}
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你"}
    # Find opponent
    opp_id = None
    for p in room["players"]:
        if p["id"] != user_id:
            opp_id = p["id"]
            break
    if opp_id is None:
        return {"ok": False, "error": "找不到对手"}
    opp_board = gs["boards"][opp_id]
    attack_board = gs["attack_boards"][opp_id]
    if not _bs_in_bounds(gs, x, y):
        return {"ok": False, "error": "坐标超出范围"}
    if attack_board[y][x] != 0:
        return {"ok": False, "error": "该位置已经开过炮"}
    shot_record = {"x": x, "y": y, **bs_fire(opp_board, attack_board, x, y)}
    gs["shots"][user_id].append(shot_record)
    if all_ships_sunk(opp_board, attack_board):
        _set_game_over(room, color, "all_sunk")
        return {"ok": True, "shot": shot_record, "game_over": {
            "winner": color, "reason": "all_sunk",
        }, "keeps_turn": False}
    return {
        "ok": True, "shot": shot_record, "game_over": None,
        "keeps_turn": _advance_after_shot(gs, color, shot_record["result"]),
    }


def _advance_after_shot(gs: dict, color: str, result: str) -> bool:
    """开炮后的回合推进，返回是否保留回合（命中续射，v1.10）。

    命中/击沉后保留回合再打一发；**先手方的第一发不触发**——先手天然多一个回合，
    续射会把先手优势进一步放大，这是给后手的补偿。
    `first_shot_done` 只由真实开炮置位，**用武器不置位**（武器有自己的回合语义）。
    """
    is_opening_shot = not gs.get("first_shot_done", False)
    gs["first_shot_done"] = True
    keeps_turn = result in ("hit", "sunk") and not is_opening_shot
    if not keeps_turn:
        gs["next"] = "red" if color == "blue" else "blue"
    return keeps_turn


def battleship_use_weapon(
    code: str, user_id: int, weapon: str,
    x: int | None = None, y: int | None = None,
    cells: list | None = None,
) -> dict:
    """Use a battleship special weapon (雷达 / 齐射 / 空袭).

    On success: {'ok': True, 'weapon', 'x', 'y', 'cells', 'shots',
                 'radar', 'game_over'}
    On failure: {'ok': False, 'error': str}
    """
    result = _battleship_use_weapon(code, user_id, weapon, x, y, cells)
    _finish_action(code, result)
    return result


def _battleship_use_weapon(
    code: str, user_id: int, weapon: str,
    x: int | None = None, y: int | None = None,
    cells: list | None = None,
) -> dict:
    """使用武器即本回合的全部行动——用完就换手，**不触发命中续射**。

    这是 Hasbro「一回合要么报一个坐标、要么启动一次特殊武器」的语义：
    武器是回合的替代品而非附加品，否则一次空袭命中多格会立刻续射出多回合。
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "battleship":
        return {"ok": False, "error": "游戏未在进行中"}
    gs = room["game_state"]
    err = _weapon_guard(gs, room, user_id, weapon)
    if err:
        return err
    color = _user_color(room, user_id)
    opp_id = next((p["id"] for p in room["players"] if p["id"] != user_id), None)
    if opp_id is None:
        return {"ok": False, "error": "找不到对手"}
    opp_board = gs["boards"][opp_id]
    attack_board = gs["attack_boards"][opp_id]
    used = gs["weapons_used"].setdefault(user_id, [])

    if weapon == "radar":
        return _use_radar(gs, user_id, color, opp_board, x, y, used)

    targets, err = _weapon_targets(gs, weapon, attack_board, x, y, cells)
    if err:
        return err
    return _apply_weapon_shots(
        room, gs, user_id, color, weapon, x, y, targets,
        opp_board, attack_board, used,
    )


def _weapon_guard(gs: dict, room: dict, user_id: int, weapon: str) -> dict | None:
    """武器使用的共同前置校验。通过返回 None，否则返回可直接回给玩家的错误。

    三道判据各自对应一句玩家能看懂的话：**有没有这件武器 / 是不是你的回合 /
    携带它的船还在不在水面**——最后一条是「挂靠舰船」机制的落点。
    """
    if gs["phase"] != "battle":
        return {"ok": False, "error": "当前不在战斗阶段"}
    if weapon not in BS_WEAPON_DEFS:
        return {"ok": False, "error": "没有这件武器"}
    color = _user_color(room, user_id)
    if not color:
        return {"ok": False, "error": "你不是游戏玩家"}
    if gs["next"] != color:
        return {"ok": False, "error": "还没轮到你"}
    ship_id = (gs["weapons"].get(user_id) or {}).get(weapon)
    if not ship_id:
        return {"ok": False, "error": "没有这件武器"}
    if weapon in gs["weapons_used"].get(user_id, []):
        return {"ok": False, "error": "这件武器已经用过了"}
    if not bs_ship_afloat(
        gs["boards"][user_id], gs["attack_boards"][user_id], ship_id,
    ):
        wname = BS_WEAPON_DEFS[weapon]["name"]
        return {"ok": False, "error": f"携带{wname}的舰船已被击沉，武器失效"}
    return None


def _use_radar(
    gs: dict, user_id: int, color: str,
    opp_board: list[list[int]], x, y, used: list,
) -> dict:
    """雷达：只报选定格周围 3×3「有没有舰」，不报位置，然后交出回合。"""
    if x is None or y is None:
        return {"ok": False, "error": "请选择探测中心"}
    x, y = int(x), int(y)
    if not _bs_in_bounds(gs, x, y):
        return {"ok": False, "error": "坐标超出范围"}
    has_ship = bs_radar_scan(opp_board, x, y)
    gs["radar_results"].setdefault(user_id, []).append(
        {"x": x, "y": y, "has_ship": has_ship},
    )
    used.append("radar")
    gs["next"] = "red" if color == "blue" else "blue"
    return {
        "ok": True, "weapon": "radar", "x": x, "y": y,
        "cells": [], "shots": [], "radar": {"has_ship": has_ship},
        "game_over": None,
    }


def _weapon_targets(
    gs: dict, weapon: str, attack_board: list[list[int]], x, y, cells,
) -> tuple[list[tuple[int, int]] | None, dict | None]:
    """算出这次武器实际作用在哪些格子上。返回 (targets, error)，二选一为 None。

    齐射是玩家点名 3 格（有非法就整发拒绝）；空袭是 3×3 区域扫描，
    已开过火的格子跳过即可，不必报错。
    """
    if weapon == "salvo":
        if not isinstance(cells, list) or len(cells) != BS_SALVO_SHOTS:
            return None, {
                "ok": False, "error": f"齐射需要指定 {BS_SALVO_SHOTS} 个格子",
            }
        targets: list[tuple[int, int]] = []
        for cell in cells:
            if not isinstance(cell, (list, tuple)) or len(cell) != 2:
                return None, {"ok": False, "error": "齐射坐标格式错误"}
            cx, cy = int(cell[0]), int(cell[1])
            if not _bs_in_bounds(gs, cx, cy):
                return None, {"ok": False, "error": "坐标超出范围"}
            if attack_board[cy][cx] != 0:
                return None, {"ok": False, "error": f"({cx}, {cy}) 已经开过炮"}
            if (cx, cy) in targets:
                return None, {"ok": False, "error": "齐射的格子不能重复"}
            targets.append((cx, cy))
        return targets, None

    if x is None or y is None:
        return None, {"ok": False, "error": "请选择空袭中心"}
    x, y = int(x), int(y)
    if not _bs_in_bounds(gs, x, y):
        return None, {"ok": False, "error": "坐标超出范围"}
    targets = [
        (cx, cy) for cx, cy in bs_area_cells(x, y, size=gs.get("size") or BS_BOARD_SIZE)
        if attack_board[cy][cx] == 0
    ]
    if not targets:
        return None, {"ok": False, "error": "该区域已经全部开过炮"}
    return targets, None


def _apply_weapon_shots(
    room: dict, gs: dict, user_id: int, color: str, weapon: str,
    x, y, targets: list[tuple[int, int]],
    opp_board: list[list[int]], attack_board: list[list[int]], used: list,
) -> dict:
    """逐格结算武器伤害，然后交出回合（武器不触发续射）。"""
    shots = []
    for cx, cy in targets:
        shot = bs_fire(opp_board, attack_board, cx, cy)
        shots.append({"x": cx, "y": cy, **shot})
        gs["shots"][user_id].append({"x": cx, "y": cy, **shot})
    used.append(weapon)
    payload = {
        "ok": True, "weapon": weapon, "x": x, "y": y,
        "cells": [list(t) for t in targets], "shots": shots,
        "radar": None,
    }
    if all_ships_sunk(opp_board, attack_board):
        _set_game_over(room, color, "all_sunk")
        return {**payload, "game_over": {"winner": color, "reason": "all_sunk"}}
    gs["next"] = "red" if color == "blue" else "blue"
    return {**payload, "game_over": None}


def mine_reveal_action(code: str, user_id: int, x: int, y: int) -> dict:
    """Reveal a minesweeper cell. Returns result dict for WS broadcast."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "minesweeper":
        return {"ok": False, "error": "游戏未在进行中"}
    # Find player seat
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    gs = room["game_state"]
    result = mine_reveal(gs, x, y, seat)
    if result["ok"]:
        _touch_activity(room)
    if result["ok"] and result["game_over"]:
        # Compute ranking and finalize game
        players_info = _get_players_info(room)
        ranking = mine_compute_ranking(gs, players_info)
        result["game_over"]["ranking"] = ranking
        _set_mine_game_over(room, ranking)
    return result


def mine_chord_action(code: str, user_id: int, x: int, y: int) -> dict:
    """Chord a minesweeper number cell: open all remaining unflagged neighbors.

    Returns result dict for WS broadcast; ok=False (precondition unmet) is
    silently ignored by the WS layer.
    """
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "minesweeper":
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    gs = room["game_state"]
    result = mine_chord(gs, x, y, seat)
    if result["ok"]:
        _touch_activity(room)
    if result["ok"] and result["game_over"]:
        players_info = _get_players_info(room)
        ranking = mine_compute_ranking(gs, players_info)
        result["game_over"]["ranking"] = ranking
        _set_mine_game_over(room, ranking)
    return result


def mine_flag_action(code: str, user_id: int, x: int, y: int) -> dict:
    """Toggle flag on a minesweeper cell. Returns result dict."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "minesweeper":
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    gs = room["game_state"]
    result = mine_flag(gs, x, y, seat)
    if result.get("ok"):
        _touch_activity(room)
    return result


def ludo_roll_action(code: str, user_id: int) -> dict:
    """Roll dice for ludo. Returns result dict for WS broadcast."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "ludo":
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    gs = room["game_state"]
    if gs["current_seat"] != seat:
        return {"ok": False, "error": "还没轮到你"}
    result = ludo_roll_dice(gs)
    if result["ok"]:
        _touch_activity(room)
    if result["ok"] and result.get("game_over"):
        _finalize_ludo_game(room, result["game_over"])
        result["game_over"]["ranking"] = room.get("_ludo_ranking", [])
    return result


def ludo_move_action(code: str, user_id: int, piece_id: int) -> dict:
    """Move a ludo piece. Returns result dict for WS broadcast."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "ludo":
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    gs = room["game_state"]
    if gs["current_seat"] != seat:
        return {"ok": False, "error": "还没轮到你"}
    result = ludo_move_piece(gs, piece_id)
    if result["ok"]:
        _touch_activity(room)
    if result["ok"] and result.get("game_over"):
        _finalize_ludo_game(room, result["game_over"])
        result["game_over"]["ranking"] = room.get("_ludo_ranking", [])
    return result


def _ludo_surrender(room: dict, user_id: int) -> dict | None:
    """Handle ludo surrender: eliminate the player.

    认输者若正轮到出手，必须把回合交出去——否则 current_seat 停在已 finished
    的座位上，其余玩家谁也动不了（整桌卡死，人机局尤其明显）。
    """
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return None
    gs = room["game_state"]
    if gs["players"][seat]["finished"]:
        return None
    was_current = gs["current_seat"] == seat
    go = ludo_surrender(gs, seat)
    if not go and was_current:
        ludo_advance_turn(gs)
    _touch_activity(room)
    if go:
        _finalize_ludo_game(room, go)
        go["ranking"] = room.get("_ludo_ranking", [])
    return go


def _check_ludo_game_over(gs: dict) -> dict | None:
    """Check if ludo game is over (for leave/disconnect scenarios)."""
    return ludo_check_game_over(gs)


def _finalize_ludo_game(room: dict, game_over: dict):
    """Finalize a ludo game with ranking."""
    players_info = _get_players_info(room)
    ranking = ludo_compute_ranking(room["game_state"], players_info)
    _back_to_lobby(room)
    room["game_state"]["finished"] = True
    room["game_state"]["ranking"] = ranking
    room["_ludo_ranking"] = ranking
    _touch_activity(room)
    if not _has_human_online(room):
        room["empty_since"] = _time.time()
    else:
        room["empty_since"] = None
    _notify_game_end(room)


# ── UNO ──────────────────────────────────────────────────────


def _uno_room(code: str) -> dict | None:
    """取一个正在进行的 UNO 房间；不满足返回 None。"""
    room = _rooms.get(code)
    if not room or room["status"] != "playing" or room["game"] != "uno":
        return None
    return room


def _uno_after_action(room: dict, result: dict) -> dict:
    """UNO 动作的统一收尾：落盘 + 终局结算。

    四个动作（出牌/摸牌/过牌/喊 UNO）都是「引擎结果 → 落盘 → 若终局则算排名」，
    收在一处免得四份实现漂移（漏一次落盘 = 热重启后丢一步）。
    """
    if not result.get("ok"):
        return result
    _touch_activity(room)
    if result.get("game_over"):
        _finalize_uno_game(room)
        result["game_over"]["ranking"] = room.get("_uno_ranking", [])
    return result


def _finalize_uno_game(room: dict) -> list[dict]:
    """UNO 终局：算排名 → 房间回等待态 → 存排名供战绩快照。"""
    gs = room["game_state"]
    ranking = uno_compute_ranking(gs, _get_players_info(room))
    _back_to_lobby(room)
    gs["ranking"] = ranking
    room["_uno_ranking"] = ranking
    _touch_activity(room)
    if not _has_human_online(room):
        room["empty_since"] = _time.time()
    else:
        room["empty_since"] = None
    _notify_game_end(room)
    return ranking


def _uno_surrender(room: dict, user_id: int) -> dict | None:
    """UNO 认输 / 断线判负：只让本人出局，牌局继续。返回 game_over 或 None。"""
    gs = room["game_state"]
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return None
    go = uno_surrender(gs, seat)
    if go:
        _finalize_uno_game(room)
        go["ranking"] = room.get("_uno_ranking", [])
    else:
        _touch_activity(room)
    return go


def _uno_winner_uid(room: dict, game_over: dict) -> int | None:
    """终局载荷里的 winner 是**座位号**，掉线回调要的是 user_id。"""
    seat = game_over.get("winner")
    if seat is None:
        return None
    gs = room["game_state"]
    uids = gs.get("seat_uids", [])
    return uids[seat] if 0 <= seat < len(uids) else None


def uno_should_autopass(room: dict) -> bool:
    """轮到的人不在线时，是否该替他自动摸牌过牌。

    **全员断线时必须返回 False**：否则回合会在几个断线座位之间无限自转（每圈都
    自动摸牌过牌 → 忙循环，还会把牌堆摸空）。这种情况下退回等 120s 保座超时判负，
    由 `_on_dc_timeout` 逐个把人判出局。

    人机不算「不在线」—— 它有自己的调度路径，不走这条。
    """
    gs = room.get("game_state") or {}
    players = room["players"]
    seat = gs.get("turn")
    if seat is None or not (0 <= seat < len(players)):
        return False
    me = players[seat]
    if me.get("is_bot") or me.get("connected", True):
        return False
    # 除他之外，还有没有能推进牌局的人（在线真人 / 人机）？
    for s, p in enumerate(players):
        if s == seat or s in gs.get("out", []):
            continue
        if p.get("is_bot") or p.get("connected", True):
            return True
    return False


def uno_play_action(code: str, user_id: int, card: str,
                    chosen_color: str | None = None,
                    jump: bool = False, target: int | None = None) -> dict:
    """出牌。返回结果 dict 供 WS 广播。

    - `jump=True` 是**抢出**（非本回合、与牌堆顶完全相同的牌）。
    - `target` 是 7 换手的对象座位。
    两者都由引擎校验（`uno.can_jump` / `uno._can_swap_with`），这里不做判断。
    """
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room, uno_play(room["game_state"], seat, card,
                                            chosen_color, jump=jump, target=target))


def uno_draw_action(code: str, user_id: int) -> dict:
    """摸牌。"""
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room, uno_draw(room["game_state"], seat))


def uno_pass_action(code: str, user_id: int) -> dict:
    """摸到牌之后选择不出，移交回合。"""
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room, uno_pass(room["game_state"], seat))


def uno_roulette_action(code: str, user_id: int, color: str) -> dict:
    """颜色轮盘点名（`W+RO`）：翻牌直到出现该色，翻出来的全归点名者并停一轮。"""
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room,
                             uno_resolve_roulette(room["game_state"], seat, color))


def uno_call_action(code: str, user_id: int, target: int | None = None) -> dict:
    """喊 UNO（自己或队友手牌 1 张时）。本版不抓罚，纯广播。

    `target` 省略时由引擎决定替谁喊（自己有就喊自己，否则喊队友）。
    """
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room, uno_call_uno(room["game_state"], seat, target))


def uno_autopass_action(code: str, user_id: int) -> dict:
    """替断线玩家自动摸牌过牌。调用方必须先过 `uno_should_autopass`。"""
    room = _uno_room(code)
    if not room:
        return {"ok": False, "error": "游戏未在进行中"}
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return {"ok": False, "error": "你不是游戏玩家"}
    return _uno_after_action(room, uno_autopass(room["game_state"], seat))


def surrender(code: str, user_id: int) -> dict | None:
    """Handle surrender. Returns game_over dict or None."""
    room = _rooms.get(code)
    if not room or room["status"] != "playing":
        return None
    if room["game"] == "minesweeper":
        return _mine_surrender(room, user_id)
    if room["game"] == "ludo":
        return _ludo_surrender(room, user_id)
    if room["game"] == "2048":
        # 2048 各玩各的盘：认输只结束本人（排名垫底），其他人继续
        gs = room["game_state"]
        if gs["game_over_flags"].get(user_id):
            return None
        _finish_2048_player(gs, user_id, "surrender")
        _touch_activity(room)
        return _maybe_finish_2048(room)
    if room["game"] == "tetris":
        # 同理：认输只结束本人（排名垫底），其他人继续玩
        gs = room["game_state"]
        if gs["game_over_flags"].get(user_id):
            return None
        tetris_finish_player(gs, user_id, "surrender")
        _touch_activity(room)
        return _maybe_finish_tetris(room)
    if room["game"] == "uno":
        # 认输只出局，牌局继续；只剩一个人还在局中时才终局
        return _uno_surrender(room, user_id)
    color = _user_color(room, user_id)
    if not color:
        return None
    winner = _opponent_color(color, room["game"])
    _set_game_over(room, winner, "surrender")
    return {"winner": winner, "reason": "surrender"}


def _mine_surrender(room: dict, user_id: int) -> dict | None:
    """Handle minesweeper surrender: eliminate the player."""
    seat = _get_player_seat(room, user_id)
    if seat is None:
        return None
    gs = room["game_state"]
    if not gs["players"][seat]["alive"]:
        return None  # Already eliminated
    gs["players"][seat]["alive"] = False
    gs["players"][seat]["lives_left"] = 0
    gs["players"][seat]["eliminated_at"] = _now_iso()
    _touch_activity(room)
    # Check if all players eliminated
    all_eliminated = all(not p["alive"] for p in gs["players"].values())
    if all_eliminated:
        gs["finished"] = True
        gs["reason"] = "eliminated"
        from app.minesweeper import _reveal_all_mines
        _reveal_all_mines(gs)
        players_info = _get_players_info(room)
        ranking = mine_compute_ranking(gs, players_info)
        _set_mine_game_over(room, ranking)
        return {"reason": "eliminated", "ranking": ranking, "eliminated_seat": seat}
    return {"eliminated_seat": seat, "reason": None}


def _set_game_over(room: dict, winner: str, reason: str):
    _back_to_lobby(room)
    room["game_state"]["winner"] = winner
    room["game_state"]["reason"] = reason
    _touch_activity(room)
    if not _has_human_online(room):
        room["empty_since"] = _time.time()
    else:
        room["empty_since"] = None
    _notify_game_end(room)


def handle_player_disconnect(code: str, user_id: int, loop: asyncio.AbstractEventLoop):
    """Called when a player's WebSocket disconnects."""
    room = _rooms.get(code)
    if not room:
        return
    # Bots don't have WS connections, skip
    for p in room["players"]:
        if p["id"] == user_id:
            if p.get("is_bot"):
                return
            p["connected"] = False
            p["disconnected_at"] = _time.time()   # 供重连倒计时算剩余秒数
            break
    persistence.save_room(room)
    # 等待中（含刚打完一局回到等待态的房间）：**不立即移出玩家**。
    # 客户端从对局页返回房间时是「先关对局页连接、再重连房间页」，两步之间有
    # 窗口期；立即移出会让房间瞬间没人而被销毁（返回房间 → 房间不存在）。
    # 改为只标记离线并记 empty_since，由 sweeper 在 WAITING_EMPTY_TTL 后回收；
    # 玩家重连即取消。界面上等待房不显示「断线」（等待房没有掉线状态）。
    if room["status"] == "waiting":
        _maybe_destroy_or_mark(room)
    elif room["status"] == "playing":
        _start_dc_timer(code, user_id, loop)


def handle_player_reconnect(code: str, user_id: int):
    """Called when a disconnected player rejoins. Returns True if reconnected."""
    room = _rooms.get(code)
    if not room:
        return False
    key = (code, user_id)
    task = _dc_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()
    for p in room["players"]:
        if p["id"] == user_id:
            p["connected"] = True
            room["empty_since"] = None
            _touch_activity(room)
            return True
    return False


def _start_dc_timer(code: str, user_id: int, loop: asyncio.AbstractEventLoop):
    """Start a 120s disconnect timer for a player."""
    key = (code, user_id)
    old = _dc_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()

    async def _timeout():
        try:
            await asyncio.sleep(_disconnect_timeout)
            _on_dc_timeout(code, user_id)
        except asyncio.CancelledError:
            pass

    task = loop.create_task(_timeout())
    _dc_tasks[key] = task


# Callback set by ws.py to broadcast game_over on timeout
_dc_timeout_callback = None


def set_dc_timeout_callback(callback):
    """Set async callback for when disconnect timeout fires."""
    global _dc_timeout_callback
    _dc_timeout_callback = callback


def _on_dc_timeout(code: str, user_id: int):
    """Called when 120s disconnect timer expires."""
    _dc_tasks.pop((code, user_id), None)
    room = _rooms.get(code)
    if not room or room["status"] != "playing":
        return
    if room["game"] == "minesweeper":
        # Competitive: disconnect timeout = player eliminated
        seat = _get_player_seat(room, user_id)
        if seat is not None:
            gs = room["game_state"]
            if gs["players"][seat]["alive"]:
                gs["players"][seat]["alive"] = False
                gs["players"][seat]["lives_left"] = 0
                gs["players"][seat]["eliminated_at"] = _now_iso()
            # Check if all players eliminated
            all_eliminated = all(
                not p["alive"] for p in gs["players"].values()
            )
            if all_eliminated:
                gs["finished"] = True
                gs["reason"] = "eliminated"
                from app.minesweeper import _reveal_all_mines
                _reveal_all_mines(gs)
                players_info = _get_players_info(room)
                ranking = mine_compute_ranking(gs, players_info)
                _set_mine_game_over(room, ranking)
                if _dc_timeout_callback:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(
                                _dc_timeout_callback(code, "eliminated")
                            )
                    except RuntimeError:
                        pass
                return
        _touch_activity(room)
        return
    if room["game"] == "ludo":
        seat = _get_player_seat(room, user_id)
        gs = room["game_state"]
        if seat is not None and not gs["players"][seat]["finished"]:
            was_current = gs["current_seat"] == seat
            ludo_surrender(gs, seat)
            if was_current and not gs["finished"]:
                ludo_advance_turn(gs)  # 否则回合停在已判负座位上，整桌卡死
        go = _check_ludo_game_over(gs)
        if go:
            _finalize_ludo_game(room, go)
            if _dc_timeout_callback:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(
                            _dc_timeout_callback(code, "eliminated")
                        )
                except RuntimeError:
                    pass
        _touch_activity(room)
        return
    if room["game"] == "tetris":
        # 各玩各的盘：一人掉线超时只结束他自己，其余人继续玩。
        # （2048 在这里走的是下面的通用分支 = 一人掉线整局结束，那条与其
        #   「各玩各的」语义不符，属于已知的历史遗留；tetris 不跟着抄。）
        gs = room["game_state"]
        if not gs["game_over_flags"].get(user_id):
            tetris_finish_player(gs, user_id, "disconnect")
        go = _maybe_finish_tetris(room)
        if go and _dc_timeout_callback:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_dc_timeout_callback(code, go["winner"]))
            except RuntimeError:
                pass
        _touch_activity(room)
        return
    if room["game"] == "uno":
        # 断线超时 = 该玩家出局（与认输同一条路径）。牌局若还没结束就继续打，
        # 别人照常玩；只剩一个人时才终局。
        go = _uno_surrender(room, user_id)
        if go and _dc_timeout_callback:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(
                        _dc_timeout_callback(code, _uno_winner_uid(room, go))
                    )
            except RuntimeError:
                pass
        return
    color = _user_color(room, user_id)
    if not color:
        return
    winner = _opponent_color(color, room["game"])
    _set_game_over(room, winner, "disconnect")
    if _dc_timeout_callback:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_dc_timeout_callback(code, winner))
        except RuntimeError:
            pass


def _cancel_all_dc_tasks(code: str):
    """Cancel all disconnect timers and bot tasks for a room."""
    keys = [k for k in _dc_tasks if k[0] == code]
    for key in keys:
        task = _dc_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
    bot_keys = [k for k in _bot_tasks if k[0] == code]
    for key in bot_keys:
        task = _bot_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()


def schedule_bot_turn(code: str, bot_id: int):
    """Schedule a delayed AI move for a bot player.

    The actual AI computation is delegated to the registered callback
    (_bot_turn_callback) after a human-like delay of 0.8-1.2 seconds.
    """
    if not _bot_turn_callback:
        return
    key = (code, bot_id)
    old = _bot_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()

    async def _delayed_move():
        import random as _rng
        try:
            await asyncio.sleep(_rng.uniform(0.8, 1.2))
            await _bot_turn_callback(code, bot_id)
        except asyncio.CancelledError:
            pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    task = loop.create_task(_delayed_move())
    _bot_tasks[key] = task


def schedule_bot_placement(code: str, bot_id: int):
    """Schedule automatic battleship placement for a bot.

    Uses random_placement() immediately (no AI needed for placement).
    """
    if not _bot_placement_callback:
        return
    key = (code, bot_id)
    old = _bot_tasks.pop(key, None)
    if old and not old.done():
        old.cancel()

    async def _delayed_placement():
        try:
            await asyncio.sleep(0.5)
            await _bot_placement_callback(code, bot_id)
        except asyncio.CancelledError:
            pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    task = loop.create_task(_delayed_placement())
    _bot_tasks[key] = task


def get_user_color(room: dict, user_id: int) -> str:
    """Public accessor for a user's color in a room."""
    if not room.get("game_state"):
        return ""
    return room["game_state"]["player_colors"].get(user_id, "")


def room_to_dict(room: dict) -> dict:
    """Convert room to API-serializable dict (without password/internal state)."""
    players = []
    for p in room["players"]:
        players.append({
            "id": p["id"],
            "username": p["username"],
            "nickname": p["nickname"],
            "ready": p["ready"],
            "is_host": p["id"] == room["host_id"],
            "connected": p["connected"],
            "is_bot": p.get("is_bot", False),
            # 人机的 AI 难度档（仅海战棋，见 add_bot）
            "ai_difficulty": p.get("ai_difficulty"),
            # 断线重连剩余秒数（仅游戏中且已断线时有值）——客户端据此倒计时，
            # 不要在界面上写死 120：测试会把超时改成 5 秒，写死就对不上了。
            "reconnect_left": reconnect_seconds_left(room, p),
        })
    result = {
        "code": room["code"],
        "name": room["name"],
        "game": room["game"],
        "host_id": room["host_id"],
        "status": room["status"],
        "has_password": room["password"] is not None,
        "max_players": room["max_players"],
        "players": players,
        # 已打完的局数：>0 说明这房打过至少一局，客户端据此提示「可续开新一局」
        "round": room.get("round", 0),
    }
    # **房间级设置**（v1.15）：五处设置原先各下发一套字段、可选项列表还有四种形状，
    # 扫雷与军棋的档位表干脆不下发（客户端各抄一份）。现在只有这一份统一结构 ——
    # 客户端按 `kind` 通用渲染，一行规则都不写。见 `room_settings.py`。
    settings = room_settings.items_for(room["game"], room)
    if settings:
        result["settings"] = settings
    return result


def sweep_idle_rooms():
    """Scan all rooms and delete those exceeding idle TTL.

    Called periodically by the background sweeper task.
    Returns list of deleted room codes (for testing/logging).

    Deletion rules:
    - waiting: 无人在线超过 WAITING_EMPTY_TTL → delete（重连即取消）；
               否则 now - last_activity > TTL → delete
    - playing: empty_since is set AND now - empty_since > TTL → delete
    """
    now = _time.time()
    deleted = []
    # **tick 对账兜底**：playing 的 stasis 房必须有一个活着的 tick 任务。
    # 懒启动覆盖了正常路径，这里兜住异常路径（任务意外死掉 / 热重启漏起）。
    # 反过来「非 playing 房还挂着任务」不需要在这里处理 —— 任务自己每轮检查
    # status 后会退出（自净）。
    for room in list(_rooms.values()):
        if room.get("game") == "stasis" and room["status"] == "playing":
            _ensure_stasis_tick(room["code"])
    codes_to_check = list(_rooms.keys())
    for code in codes_to_check:
        room = _rooms.get(code)
        if not room:
            continue
        if room["status"] == "waiting":
            es = room.get("empty_since")
            if es is not None and (now - es) > WAITING_EMPTY_TTL:
                _destroy_room(code)
                deleted.append(code)
                continue
            idle = now - room.get("last_activity", now)
            if idle > ROOM_IDLE_TTL:
                _destroy_room(code)
                deleted.append(code)
        elif room["status"] == "playing":
            es = room.get("empty_since")
            if es is not None and (now - es) > ROOM_IDLE_TTL:
                _destroy_room(code)
                deleted.append(code)
    return deleted


async def _sweeper_loop():
    """Background task: sweep idle rooms every 60 seconds."""
    while True:
        try:
            await asyncio.sleep(60)
            sweep_idle_rooms()
        except asyncio.CancelledError:
            return
        except Exception:
            pass  # sweeper must never crash the event loop


_sweeper_task: asyncio.Task | None = None


def start_sweeper():
    """Start the background sweeper task. Called from main.py startup."""
    global _sweeper_task
    if _sweeper_task is not None and not _sweeper_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    _sweeper_task = loop.create_task(_sweeper_loop())


def stop_sweeper():
    """Cancel the sweeper task (for testing/shutdown)."""
    global _sweeper_task
    if _sweeper_task and not _sweeper_task.done():
        _sweeper_task.cancel()
        _sweeper_task = None
