"""WebSocket handler for room connections."""

import json
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.security import decode_token_ws
from app.database import get_conn, row_to_user
from app import room_manager as rm
from app.game.minesweeper import game_to_sync as mine_game_to_sync
from app.game.reversi import game_to_sync as reversi_game_to_sync
from app.game.battleship import game_to_sync as bs_game_to_sync
from app.game.battleship import random_placement as bs_random_placement
from app.game.battleship import ship_afloat as bs_ship_afloat
from app.game.dots import game_to_sync as dots_game_to_sync
from app.game.dots import PLAYER1 as DOTS_P1, PLAYER2 as DOTS_P2
from app.game.ludo import game_to_sync as ludo_game_to_sync
from app.game.game2048 import game_to_sync as g2048_game_to_sync
from app.game.game2048 import leaderboard as g2048_leaderboard
from app.game.xiangqi import game_to_sync as xiangqi_game_to_sync
from app.game import luzhanqi as lzq
from app.game.luzhanqi import view_for as luzhanqi_view_for
from app.game.luzhanqi import mask_battle as luzhanqi_mask_battle
from app.game.tetris import (
    game_to_sync as tetris_game_to_sync,
    leaderboard as tetris_leaderboard,
)
from app.game.uno import (
    game_to_sync as uno_game_to_sync,
    snapshot as uno_snapshot,
    can_call_uno as uno_can_call_uno,
    jumpable_cards as uno_jumpable_cards,
)
from app.game.connect4 import (
    COLS as C4_COLS, ROWS as C4_ROWS,
    BLACK as C4_BLACK, WHITE as C4_WHITE,
)
from app.game import stasis as stasis_engine
from app.game import (battleship_ai, gomoku_ai, reversi_ai, dots_ai, ludo_ai, xiangqi_ai,
                 connect4_ai, uno_ai, luzhanqi_ai)
from app.game.gomoku import BLACK as GOMOKU_BLACK, WHITE as GOMOKU_WHITE
from app.game.reversi import BLACK as REVERSI_BLACK, WHITE as REVERSI_WHITE

router = APIRouter()

# room_code -> {user_id: WebSocket}
_connections: dict[str, dict[int, WebSocket]] = {}


def _register_dc_timeout_callback():
    """Register the callback for disconnect timeout events."""

    async def _on_timeout(code: str, winner: str):
        room = rm.get_room(code)
        if room and room["game"] == "uno":
            # UNO 是回合制多人：一个人掉线判负之后**牌局常常还在跑**，不能发一条
            # 空洞的 game_over 了事——先同步权威状态（出局名单、当前回合），
            # 真终局了再补一条带排名的结算。
            await _send_uno_state(code, room)
            if room["game_state"].get("finished"):
                await _broadcast_uno_game_over(code, room)
            else:
                await _maybe_autopass_uno(code, room)
            return
        await _broadcast(code, {
            "type": "game_over",
            "winner": winner,
            "reason": "disconnect",
        })

    rm.set_dc_timeout_callback(_on_timeout)


@router.websocket("/ws/room/{code}")
async def ws_room(websocket: WebSocket, code: str):
    token = websocket.query_params.get("token", "")
    user_id = decode_token_ws(token)
    if not user_id:
        await websocket.close(code=4001, reason="未授权")
        return
    room = rm.get_room(code)
    if not room:
        await websocket.close(code=4004, reason="房间不存在")
        return
    # Check user is a player in the room
    player_ids = {p["id"] for p in room["players"]}
    if user_id not in player_ids:
        await websocket.close(code=4003, reason="你不在房间中")
        return
    await websocket.accept()
    # Register connection
    if code not in _connections:
        _connections[code] = {}
    _connections[code][user_id] = websocket
    # Mark player connected
    for p in room["players"]:
        if p["id"] == user_id:
            p["connected"] = True
            break
    # Cancel any disconnect timer (reconnect case)
    rm.handle_player_reconnect(code, user_id)
    # Send sync
    await websocket.send_json(_build_sync(room, user_id))
    # Broadcast lobby so every client sees the fresh connected flag
    await _broadcast(code, {
        "type": "lobby", "room": rm.room_to_dict(room)
    }, exclude={user_id})
    # Broadcast player_reconnected if game is playing
    if room["status"] == "playing":
        await _broadcast(code, {
            "type": "player_reconnected", "user_id": user_id
        }, exclude={user_id})
    try:
        while True:
            data = await websocket.receive_json()
            # 僵尸连接自愈：本页连接被同用户新连接替换后又被弹出（如对局结束返回房间页），
            # 旧连接再发消息时若表位空缺则重新注册；表位有新连接时不抢占（防旧连接 ping 夺位）
            conns = _connections.setdefault(code, {})
            if user_id not in conns:
                conns[user_id] = websocket
            await _handle_message(code, user_id, data)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        # Remove connection — only if it's still THIS websocket instance
        # (a newer connection from the same user may have replaced it)
        conns = _connections.get(code, {})
        current = conns.get(user_id)
        superseded = current is not None and current is not websocket
        if current is websocket:
            conns.pop(user_id, None)
        if not conns:
            _connections.pop(code, None)
        # 被新连接顶替的旧连接关闭时**不能**走掉线流程：它的关闭事件往往在新连接
        # 注册之后才到达，无条件标记会把刚置上的在线态覆盖回掉线。症状就是「对局
        # 结束点返回房间后概率性显示『断线』」（房间里还会误启 120s 判负计时）。
        room = rm.get_room(code) if not superseded else None
        if room:
            status_before = room["status"]
            loop = asyncio.get_event_loop()
            rm.handle_player_disconnect(code, user_id, loop)
            if status_before == "playing":
                await _broadcast(code, {
                    "type": "player_disconnected", "user_id": user_id,
                    # 重连倒计时的起点（测试会把超时调小，客户端别写死 120）
                    "timeout": rm.get_disconnect_timeout(),
                })
            else:
                # 等待中：玩家可能已被移出房间，广播最新大厅
                room = rm.get_room(code)
                if room:
                    await _broadcast(code, {
                        "type": "lobby", "room": rm.room_to_dict(room)
                    })


async def _handle_message(code: str, user_id: int, data: dict):
    """Route a client message to the appropriate handler."""
    msg_type = data.get("type")
    room = rm.get_room(code)
    if not room:
        return
    if msg_type == "ping":
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "pong"})
    elif msg_type == "ready":
        await _handle_ready(code, user_id)
    elif msg_type == "unready":
        await _handle_unready(code, user_id)
    elif msg_type == "start":
        await _handle_start(code, user_id)
    elif msg_type == "move":
        await _handle_move(code, user_id, data)
    elif msg_type == "xiangqi_move":
        await _handle_xiangqi_move(code, user_id, data)
    elif msg_type == "placement":
        await _handle_placement(code, user_id, data)
    elif msg_type == "fire":
        await _handle_fire(code, user_id, data)
    elif msg_type == "use_weapon":
        await _handle_use_weapon(code, user_id, data)
    elif msg_type == "reveal":
        await _handle_reveal(code, user_id, data)
    elif msg_type == "flag":
        await _handle_flag(code, user_id, data)
    elif msg_type == "chord":
        await _handle_chord(code, user_id, data)
    elif msg_type == "roll":
        await _handle_roll(code, user_id)
    elif msg_type == "move_piece":
        await _handle_move_piece(code, user_id, data)
    elif msg_type == "add_bot":
        await _handle_add_bot(code, user_id, data)
    elif msg_type == "remove_bot":
        await _handle_remove_bot(code, user_id, data)
    elif msg_type == "swap_request":
        await _handle_swap_request(code, user_id, data)
    elif msg_type == "swap_respond":
        await _handle_swap_respond(code, user_id, data)
    elif msg_type == "move2048":
        await _handle_move2048(code, user_id, data)
    elif msg_type == "undo2048":
        await _handle_undo2048(code, user_id, data)
    elif msg_type == "tetris_actions":
        await _handle_tetris_actions(code, user_id, data)
    elif msg_type == "uno_play":
        await _handle_uno_play(code, user_id, data)
    elif msg_type == "uno_draw":
        await _handle_uno_draw(code, user_id)
    elif msg_type == "uno_pass":
        await _handle_uno_pass(code, user_id)
    elif msg_type == "uno_call":
        await _handle_uno_call(code, user_id, data)
    elif msg_type == "uno_roulette":
        await _handle_uno_roulette(code, user_id, data)
    elif msg_type == "room_setting":
        await _handle_room_setting(code, user_id, data)
    elif msg_type in RETIRED_SETTING_MSGS:
        # v1.15 起房间级设置统一走 `room_setting`。**老客户端不会被自动拦下**
        # （应用内更新是用户手动点的），静默丢弃在界面上就是「点了没反应」——
        # 那正是本项目最忌讳的失败模式，所以这里回一句能看懂的话。
        # 这一支不解析载荷、不写状态，下一版连常量一起删。
        await _send_error(code, user_id, "客户端版本过旧，请更新后再改玩法设置")
    elif msg_type == "stasis_build":
        await _handle_stasis_build(code, user_id, data)
    elif msg_type == "stasis_lie":
        await _stasis_do(code, user_id, lambda: rm.stasis_lie(code, user_id))
    elif msg_type == "stasis_upgrade":
        await _handle_stasis_upgrade(code, user_id, data)
    elif msg_type == "stasis_demolish":
        await _handle_stasis_demolish(code, user_id, data)
    elif msg_type == "stasis_patch":
        await _handle_stasis_patch(code, user_id, data)
    elif msg_type == "stasis_move":
        await _handle_stasis_move(code, user_id, data)
    elif msg_type == "luzhanqi_setup":
        await _handle_luzhanqi_setup(code, user_id, data)
    elif msg_type == "luzhanqi_move":
        await _handle_luzhanqi_move(code, user_id, data)
    elif msg_type == "surrender":
        await _handle_surrender(code, user_id)
    elif msg_type == "leave":
        await _handle_leave(code, user_id)


async def _handle_ready(code: str, user_id: int):
    room = rm.get_room(code)
    if not room or room["status"] == "playing":
        return
    for p in room["players"]:
        if p["id"] == user_id:
            p["ready"] = True
            break
    rm._touch_activity(room)
    await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})


async def _handle_unready(code: str, user_id: int):
    room = rm.get_room(code)
    if not room or room["status"] == "playing":
        return
    for p in room["players"]:
        if p["id"] == user_id:
            p["ready"] = False
            break
    rm._touch_activity(room)
    await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})


async def _handle_start(code: str, user_id: int):
    room = rm.get_room(code)
    if not room:
        return
    try:
        gs = rm.start_game(code, user_id)
    except ValueError as e:
        # 引擎级的前置校验（如 UNO 组队档「需要恰好 4 名玩家」）。**必须把原话发出去**：
        # 只说「无法开始游戏」等于让房主自己猜哪儿不对，而这条恰恰是「人数不够」
        # 这种看一眼界面就能自己解决的问题。
        await _send_error(code, user_id, str(e))
        return
    if not gs:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": "无法开始游戏"})
        return
    if room["game"] == "minesweeper":
        players_info = rm._get_players_info(room)
        sync_game = mine_game_to_sync(gs, players_info)
        await _broadcast(code, {
            "type": "game_start",
            "game": sync_game,
        })
        return
    if room["game"] == "ludo":
        await _send_ludo_start(code, room, gs)
        return
    if room["game"] == "battleship":
        await _broadcast_battleship_start(code, room, gs)
        _schedule_battleship_bots(code, room, gs)
        return
    if room["game"] == "2048":
        await _broadcast_2048_start(code, room, gs)
        return
    if room["game"] == "tetris":
        await _broadcast_tetris_start(code, room, gs)
        return
    if room["game"] == "uno":
        await _broadcast_uno_start(code, room, gs)
        return
    if room["game"] == "stasis":
        await _broadcast_stasis_start(code, room, gs)
        return
    if room["game"] == "luzhanqi":
        # 军棋先进**布阵阶段**：逐人下发（暗棋下对手半场必须是扣着的）。
        # 人机由服务端直接确认自己的默认阵型，真人摆完点「确认布阵」。
        for pid, color in gs["player_colors"].items():
            if _is_bot(room, pid):
                rm.luzhanqi_setup(code, pid, "confirm")
        await _luzhanqi_send_view(code, room, gs, "game_start")
        await _broadcast(code, {
            "type": "luzhanqi_ready",
            "ready": {c: bool(v) for c, v in gs.get("ready", {}).items()},
            "phase": gs.get("phase"),
        })
        if gs.get("phase") == "playing":
            await _luzhanqi_broadcast_begin(code, room, gs)
        return
    # Gomoku, reversi, dots, xiangqi: send individual game_start with correct your_color
    first_color = "red" if room["game"] == "xiangqi" else "black"
    for pid in gs["player_colors"]:
        conns = _connections.get(code, {})
        ws = conns.get(pid)
        if ws:
            if room["game"] == "reversi":
                sync_game = reversi_game_to_sync(gs, pid)
            elif room["game"] == "dots":
                sync_game = dots_game_to_sync(gs, pid)
            elif room["game"] == "xiangqi":
                sync_game = xiangqi_game_to_sync(gs, pid)
            elif room["game"] == "connect4":
                sync_game = _connect4_state_for_sync(gs, pid)
            else:
                sync_game = _game_state_for_sync(gs, pid)
            await ws.send_json({
                "type": "game_start",
                "game": sync_game,
                "first": first_color,
            })
    # Schedule bot first move if needed
    _maybe_schedule_bot_turn(code, room, gs)


async def _handle_move(code: str, user_id: int, data: dict):
    x = data.get("x")
    if x is None:
        return
    room = rm.get_room(code)
    # 重力四子棋只发列号（落点行由服务端按重力算），所以放在 y 校验之前
    if room and room["game"] == "connect4":
        await _handle_connect4_move(code, user_id, int(x))
        return
    y = data.get("y")
    if y is None:
        return
    if room and room["game"] == "minesweeper":
        await _handle_reveal(code, user_id, data)
        return
    if room and room["game"] == "reversi":
        await _handle_reversi_move(code, user_id, int(x), int(y))
        return
    if room and room["game"] == "dots":
        orient = data.get("orient", "h")
        await _handle_dots_move(code, user_id, orient, int(y), int(x))
        return
    result = rm.make_move(code, user_id, int(x), int(y))
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_connect4_move(code: str, user_id: int, col: int):
    """Handle a Connect Four drop: broadcast the authoritative landing row."""
    result = rm.connect4_make_move(code, user_id, col)
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        room = rm.get_room(code)
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_reversi_move(code: str, user_id: int, x: int, y: int):
    """Handle a reversi move: broadcast move with flips, pass, and game_over."""
    result = rm.reversi_make_move(code, user_id, x, y)
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    room = rm.get_room(code)
    # Broadcast the move to all players (includes flipped positions)
    await _broadcast(code, {"type": "move", **result["move"]})
    # Broadcast pass if opponent was auto-passed
    if result.get("pass"):
        await _broadcast(code, {"type": "pass", **result["pass"]})
    # Broadcast game_over if neither player can move
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    elif room:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_xiangqi_move(code: str, user_id: int, data: dict):
    """Handle a xiangqi move（起点 → 终点四个坐标）。

    走子消息里带 `legal`（新的合法着法列表），客户端据此更新「可动子/可落点」
    高亮——**客户端不实现象棋规则**，规则只在服务端有一份。
    """
    from_x = data.get("from_x")
    from_y = data.get("from_y")
    to_x = data.get("to_x")
    to_y = data.get("to_y")
    if from_x is None or from_y is None or to_x is None or to_y is None:
        return
    result = rm.xiangqi_make_move(
        code, user_id,
        (int(from_y), int(from_x)),      # 引擎按 (行, 列)，客户端按 (x=列, y=行)
        (int(to_y), int(to_x)),
    )
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    room = rm.get_room(code)
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    elif room:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_dots_move(
    code: str, user_id: int, orient: str, y: int, x: int,
):
    """Handle a dots and boxes move: broadcast edge, completions, game_over."""
    result = rm.dots_make_move(code, user_id, orient, y, x)
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    room = rm.get_room(code)
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    elif room:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _broadcast_mine_reveal(
    code: str, seat: int, player: dict | None, result: dict, chord: bool = False,
):
    """Broadcast a minesweeper reveal/chord outcome + elimination + game_over.

    Shared by `_handle_reveal` and `_handle_chord` so both paths stay in sync
    (same message shape, `chord: true` marks a multi-cell chord opening).
    """
    payload = {
        "type": "revealed",
        "seat": seat,
        "username": player["username"] if player else "",
        "cells": result["cells"],
        "hit_mine": result["hit_mine"],
        "lost_life": result.get("lost_life", False),
        "lives_left": result.get("lives_left", 1),
        "eliminated": result["eliminated"],
        "revealed_count": result["revealed_count"],
    }
    if chord:
        payload["chord"] = True
    await _broadcast(code, payload)
    # 踩雷：命没扣完是「掉一条命」，扣完才是淘汰——两种事件分开播，
    # 否则客户端只能把「踩雷」一律当成淘汰（多命下界面就错了）。
    if result["eliminated"]:
        nickname = player.get("nickname") or player["username"] if player else ""
        await _broadcast(code, {
            "type": "player_eliminated",
            "seat": seat,
            "username": player["username"] if player else "",
            "message": f"{nickname} 踩雷，生命耗尽淘汰",
        })
    elif result.get("lost_life"):
        nickname = player.get("nickname") or player["username"] if player else ""
        await _broadcast(code, {
            "type": "life_lost",
            "seat": seat,
            "username": player["username"] if player else "",
            "lives_left": result.get("lives_left", 0),
            "message": f"{nickname} 踩雷，还剩 {result.get('lives_left', 0)} 条命",
        })
    if result["game_over"]:
        await _broadcast(code, {
            "type": "game_over",
            "reason": result["game_over"]["reason"],
            "ranking": result["game_over"]["ranking"],
        })


async def _handle_reveal(code: str, user_id: int, data: dict):
    """Handle minesweeper reveal action."""
    x = data.get("x")
    y = data.get("y")
    if x is None or y is None:
        return
    room = rm.get_room(code)
    if not room:
        return
    seat = rm._get_player_seat(room, user_id)
    player = room["players"][seat] if seat is not None else None
    result = rm.mine_reveal_action(code, user_id, int(x), int(y))
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    await _broadcast_mine_reveal(code, seat, player, result)


async def _handle_chord(code: str, user_id: int, data: dict):
    """Handle minesweeper chord (tap a revealed number to open its neighbors).

    Precondition unmet (cell not revealed / flag count mismatch / nothing to
    open) is a normal no-op — stay silent, no error popup.
    """
    x = data.get("x")
    y = data.get("y")
    if x is None or y is None:
        return
    room = rm.get_room(code)
    if not room:
        return
    seat = rm._get_player_seat(room, user_id)
    player = room["players"][seat] if seat is not None else None
    result = rm.mine_chord_action(code, user_id, int(x), int(y))
    if not result["ok"]:
        return
    await _broadcast_mine_reveal(code, seat, player, result, chord=True)


async def _handle_flag(code: str, user_id: int, data: dict):
    """Handle minesweeper flag toggle action."""
    x = data.get("x")
    y = data.get("y")
    if x is None or y is None:
        return
    room = rm.get_room(code)
    if not room:
        return
    seat = rm._get_player_seat(room, user_id)
    result = rm.mine_flag_action(code, user_id, int(x), int(y))
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    await _broadcast(code, {
        "type": "flagged",
        "x": result["x"],
        "y": result["y"],
        "flagged": result["flagged"],
        # 三态：flag / question / none（问号是纯笔记，不参与裁决）
        "questioned": result.get("questioned", False),
        "state": result.get("state", "flag" if result["flagged"] else "none"),
        "seat": result["seat"],
    })


async def _send_ludo_start(code: str, room: dict, gs: dict):
    """飞行棋开局：逐人下发 game_start，附各自座位号（seat = room.players 索引）。"""
    players_info = rm._get_players_info(room)
    base_sync = ludo_game_to_sync(gs, players_info)
    conns = _connections.get(code, {})
    for p in room["players"]:
        ws = conns.get(p["id"])
        if ws:
            await ws.send_json({
                "type": "game_start",
                "game": {**base_sync, "your_seat": rm._get_player_seat(room, p["id"])},
            })


async def _handle_roll(code: str, user_id: int):
    """Handle ludo dice roll."""
    room = rm.get_room(code)
    if not room or room["game"] != "ludo":
        return
    seat = rm._get_player_seat(room, user_id)
    result = rm.ludo_roll_action(code, user_id)
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    payload = {
        "type": "dice_rolled",
        "seat": seat,
        "dice": result["dice"],
        "movable": result["movable"],
        "extra_turn": result["extra_turn"],
        "penalty": result["penalty"],
        # 掷到无可动飞机时回合会自动移交，客户端必须知道轮到谁
        "current_seat": room["game_state"]["current_seat"],
    }
    if result["penalty"]:
        # 三连六惩罚已把该玩家所有飞机打回基地，必须带上最新位置，
        # 否则客户端棋盘上飞机还留在原地，跟服务端彻底脱节
        payload["pieces"] = list(room["game_state"]["players"][seat]["pieces"])
    await _broadcast(code, payload)
    if result.get("game_over"):
        await _broadcast(code, {
            "type": "game_over",
            "reason": result["game_over"]["reason"],
            "ranking": result["game_over"].get("ranking", []),
        })
    else:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_move_piece(code: str, user_id: int, data: dict):
    """Handle ludo piece move."""
    piece_id = data.get("piece_id")
    if piece_id is None:
        return
    room = rm.get_room(code)
    if not room or room["game"] != "ludo":
        return
    seat = rm._get_player_seat(room, user_id)
    result = rm.ludo_move_action(code, user_id, int(piece_id))
    if not result["ok"]:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    await _broadcast(code, {
        "type": "piece_moved",
        "seat": result["seat"],
        "piece_id": result["piece_id"],
        "old_progress": result["old_progress"],
        "new_progress": result["new_progress"],
        "dice": result["dice"],
        # 逐格动画的完整路径 + 分段（走/跳/飞）。跳格与虚线飞行的形状只有
        # 服务端知道，客户端不能按 old+dice 自己推
        "path": result["path"],
        "segments": result["segments"],
        "captures": result["captures"],
        "finished": result["finished"],
        "extra_turn": result["extra_turn"],
        "current_seat": room["game_state"]["current_seat"],
    })
    if result.get("game_over"):
        await _broadcast(code, {
            "type": "game_over",
            "reason": result["game_over"]["reason"],
            "ranking": result["game_over"].get("ranking", []),
        })
    else:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_surrender(code: str, user_id: int):
    room = rm.get_room(code)
    if not room:
        return
    if room["game"] == "stasis":
        # 合作塔防：弃守只是本人舱室失压、遗产平分给队友，**队友继续守**。
        # 通用的 rm.surrender 是「对手获胜」的 2 人对战语义，用在这里会把
        # 整局直接判给静默体。
        await _stasis_do(code, user_id, lambda: rm.stasis_surrender(code, user_id))
        return
    go = rm.surrender(code, user_id)
    if room["game"] == "2048":
        # 认输只是本人结束，局可能继续——两种情况都要广播
        gs = room["game_state"]
        await _broadcast(code, {
            "type": "move2048",
            "user_id": user_id,
            "color": gs["player_colors"].get(user_id, ""),
            "score": gs["scores"].get(user_id, 0),
            "score_gained": 0,
            "leaderboard": g2048_leaderboard(gs),
            "player_finished": gs["finish_reasons"].get(user_id),
        })
        if go:
            await _broadcast(code, {"type": "game_over", **go})
        return
    if room["game"] == "tetris":
        # 认输只结束本人（其他人继续玩自己的盘），所以 go 常常是 None——
        # 但**本人必须收到自己已结束的权威状态**，否则客户端还在本地跑，
        # 下一个动作才被服务端一句「你已结束本局」顶回来（界面像卡死）。
        gs = room["game_state"]
        leaderboard = tetris_leaderboard(gs)
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({
                "type": "tetris_state",
                **tetris_game_to_sync(gs, user_id),
                "user_id": user_id,
                "leaderboard": leaderboard,
                "last_action": "surrender",
                "player_finished": gs["finish_reasons"].get(user_id),
            })
        for pid in gs["player_colors"]:
            if pid == user_id:
                continue
            other = conns.get(pid)
            if other:
                await other.send_json({
                    "type": "tetris_leaderboard",
                    "leaderboard": leaderboard,
                })
        if go:
            await _broadcast(code, {"type": "game_over", **go})
        return
    if room["game"] == "ludo":
        # 飞行棋认输只是弃权，局可能继续（go 为 None）——两种情况都要广播，
        # 并把回合交给下家（可能是人机）
        seat = rm._get_player_seat(room, user_id)
        player = room["players"][seat] if seat is not None else None
        nickname = (player.get("nickname") or player["username"]) if player else ""
        await _broadcast(code, {
            "type": "player_surrendered",
            "seat": seat,
            "username": player["username"] if player else "",
            "message": f"{nickname} 主动弃权",
            # 弃权已把该座位飞机清回基地，带上位置让客户端同步清掉棋盘上的残留
            "pieces": (
                list(room["game_state"]["players"][seat]["pieces"])
                if seat is not None and room.get("game_state") else None
            ),
        })
        if go and go.get("reason"):
            await _broadcast(code, {
                "type": "game_over",
                "reason": go["reason"],
                "ranking": go.get("ranking", []),
            })
        else:
            _maybe_schedule_bot_turn(code, room, room["game_state"])
        return
    if room["game"] == "uno":
        # 认输只是出局，牌局常常继续（go 为 None）——两种情况都要广播：
        # 一条公开事件（给提示条/动效）+ 逐人权威状态；只有真终局才再发 game_over。
        seat = rm._get_player_seat(room, user_id)
        await _uno_event(code, "surrender", seat)
        await _send_uno_state(code, room)
        if go:
            await _broadcast_uno_game_over(code, room)
        else:
            await _maybe_autopass_uno(code, room)
            _maybe_schedule_bot_turn(code, room, room["game_state"])
        return
    if not go:
        return
    if room["game"] == "minesweeper":
        # Minesweeper surrender = self-elimination
        seat = go.get("eliminated_seat")
        player = None
        if seat is not None and seat < len(room["players"]):
            player = room["players"][seat]
        nickname = player.get("nickname") or player["username"] if player else ""
        await _broadcast(code, {
            "type": "player_eliminated",
            "seat": seat,
            "username": player["username"] if player else "",
            "message": f"{nickname} 主动弃权淘汰",
        })
        if go.get("reason") == "eliminated":
            await _broadcast(code, {
                "type": "game_over",
                "reason": go["reason"],
                "ranking": go["ranking"],
            })
    else:
        await _broadcast(code, {"type": "game_over", **go})




async def _handle_leave(code: str, user_id: int):
    room = rm.get_room(code)
    if not room:
        return
    action, updated_room = rm.leave_room(code, user_id)
    if action == "closed":
        # Room was destroyed (waiting room with no humans left)
        await _broadcast(code, {"type": "room_closed"})
    elif action == "game_over" and updated_room:
        if updated_room["game"] == "minesweeper":
            ranking = updated_room.get("_mine_ranking", [])
            await _broadcast(code, {
                "type": "game_over",
                "reason": updated_room["game_state"].get("reason", "eliminated"),
                "ranking": ranking,
            })
        elif updated_room["game"] == "ludo":
            ranking = updated_room.get("_ludo_ranking", [])
            await _broadcast(code, {
                "type": "game_over",
                "reason": updated_room["game_state"].get("reason", "all_finished"),
                "ranking": ranking,
            })
        elif updated_room["game"] == "2048":
            gs = updated_room["game_state"]
            await _broadcast(code, {
                "type": "game_over",
                "winner": gs.get("winner"),
                "reason": gs.get("reason", "ranking"),
                "ranking": gs.get("ranking", []),
            })
        elif updated_room["game"] == "uno":
            seat = rm._get_player_seat(updated_room, user_id)
            await _uno_event(code, "leave", seat)
            await _send_uno_state(code, updated_room)
            await _broadcast_uno_game_over(code, updated_room)
        elif updated_room["game"] == "stasis":
            await broadcast_stasis_game_over(
                updated_room, updated_room.get("_stasis_ranking", []))
        else:
            gs = updated_room["game_state"]
            winner = gs["winner"]
            reason = gs.get("reason", "surrender")
            await _broadcast(code, {
                "type": "game_over", "winner": winner, "reason": reason,
            })
        # Also broadcast updated lobby (host may have transferred)
        await _broadcast(code, {
            "type": "lobby", "room": rm.room_to_dict(updated_room),
        })
    elif action == "removed" and updated_room:
        if updated_room["game"] in ("minesweeper", "ludo", "uno", "stasis"):
            seat = rm._get_player_seat(updated_room, user_id)
            await _broadcast(code, {
                "type": "player_eliminated",
                "seat": seat,
                "username": "",
                "message": "玩家离开游戏",
            })
            if updated_room["game"] == "uno":
                # 牌局还在跑：把新的回合/出局名单同步给留下的人
                await _send_uno_state(code, updated_room)
                await _maybe_autopass_uno(code, updated_room)
        await _broadcast(code, {
            "type": "lobby", "room": rm.room_to_dict(updated_room),
        })
    # Close this user's websocket
    conns = _connections.get(code, {})
    ws = conns.pop(user_id, None)
    if ws:
        try:
            await ws.close()
        except Exception:
            pass


async def _broadcast(code: str, message: dict, exclude: set[int] | None = None):
    """Send a message to all connected players in a room."""
    conns = _connections.get(code, {})
    exclude = exclude or set()
    for uid, ws in list(conns.items()):
        if uid in exclude:
            continue
        try:
            await ws.send_json(message)
        except Exception:
            pass


def _build_sync(room: dict, user_id: int) -> dict:
    """Build the sync message for a user joining/reconnecting."""
    room_dict = rm.room_to_dict(room)
    game_info = None
    # 只有游戏中才下发对局数据：房间回到等待态后若继续下发 game，
    # 玩家点「返回房间」会被 sync 立刻弹回对局页（v1.5.0 点格棋踩过）。
    if room["status"] == "playing" and room.get("game_state"):
        if room["game"] == "minesweeper":
            players_info = rm._get_players_info(room)
            game_info = mine_game_to_sync(room["game_state"], players_info)
        elif room["game"] == "ludo":
            players_info = rm._get_players_info(room)
            game_info = {
                **ludo_game_to_sync(room["game_state"], players_info),
                "your_seat": rm._get_player_seat(room, user_id),
            }
        elif room["game"] == "reversi":
            game_info = reversi_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "battleship":
            game_info = bs_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "dots":
            game_info = dots_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "2048":
            game_info = g2048_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "xiangqi":
            game_info = xiangqi_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "connect4":
            game_info = _connect4_state_for_sync(room["game_state"], user_id)
        elif room["game"] == "tetris":
            game_info = tetris_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "uno":
            game_info = uno_game_to_sync(room["game_state"], user_id)
        elif room["game"] == "stasis":
            game_info = stasis_engine.game_to_sync(room["game_state"], user_id)
        elif room["game"] == "luzhanqi":
            # 逐人裁剪：`phase` 一起下发，客户端据此决定渲染布阵界面还是对局界面
            # （布阵阶段重连回来不能直接跳到对局界面）。
            game_info = lzq.game_to_sync(room["game_state"], user_id)
        else:
            game_info = _game_state_for_sync(room["game_state"], user_id)
    # in_game：这个人在本局里还算不算「在局玩家」。房间页只对在局玩家自动跳对局页——
    # 扫雷/飞行棋/2048 的玩家出局后仍留在房间里，不加这个判断他们一点「退出」
    # 就会被 sync 弹回对局页（点了没反应、页面闪一下还在原处）。
    return {
        "type": "sync", "room": room_dict, "game": game_info,
        "in_game": rm.user_in_game(room, user_id),
    }


def _game_state_for_sync(gs: dict, user_id: int) -> dict:
    """Build the game state portion of a sync message (gomoku)."""
    return {
        "board_size": 15,
        "board": gs["board"],
        "moves": gs["moves"],
        "next": gs["next"],
        "your_color": gs["player_colors"].get(user_id, ""),
        "winner": gs["winner"],
        "reason": gs["reason"],
    }


def _connect4_state_for_sync(gs: dict, user_id: int) -> dict:
    """重力四子棋的 sync / game_start 载荷。

    board 是 6×7 的 `board[row][col]`（row 0 在最上面）；带 cols/rows 让客户端
    不必写死尺寸——五子棋那套写死 `board_size: 15` 的载荷不能复用。
    """
    return {
        "cols": C4_COLS,
        "rows": C4_ROWS,
        "board": gs["board"],
        "moves": gs["moves"],
        "next": gs["next"],
        "your_color": gs["player_colors"].get(user_id, ""),
        "winner": gs["winner"],
        "reason": gs["reason"],
    }


# ── Battleship handlers ───────────────────────────────────────


async def _handle_placement(code: str, user_id: int, data: dict):
    """Handle battleship ship placement."""
    ships = data.get("ships", [])
    if not isinstance(ships, list) or len(ships) != 5:
        await _send_error(code, user_id, "布阵格式错误")
        return
    # weapons 可选：旧客户端不发，服务端用默认分配兜底
    weapons = data.get("weapons")
    result = rm.battleship_set_placement(code, user_id, ships, weapons)
    if not result["ok"]:
        await _send_error(code, user_id, result["error"])
        return
    room = rm.get_room(code)
    if not room:
        return
    gs = room["game_state"]
    # Broadcast placement_ready
    await _broadcast(code, {
        "type": "placement_ready",
        "user_id": user_id,
    })
    if result.get("battle_start"):
        await _broadcast_battleship_start(code, room, gs)
        _schedule_battleship_bots(code, room, gs)


async def _handle_fire(code: str, user_id: int, data: dict):
    """Handle battleship fire shot."""
    x = data.get("x")
    y = data.get("y")
    if x is None or y is None:
        return
    result = rm.battleship_fire(code, user_id, int(x), int(y))
    if not result["ok"]:
        await _send_error(code, user_id, result["error"])
        return
    room = rm.get_room(code)
    if not room:
        return
    await _broadcast_battleship_action(code, room, user_id, result)


async def _send_error(code: str, user_id: int, message: str):
    """Send an error back to just the acting player."""
    ws = _connections.get(code, {}).get(user_id)
    if ws:
        await ws.send_json({"type": "error", "message": message})


async def _send_to(code: str, user_id: int, message: dict):
    """给房里某一个人单独发一条（他没在线就算了）。

    逐人下发在换位这条路上是必需的：请求只该给被请求的那个人看，
    `swap_result` 也只该回给发起人。
    """
    ws = _connections.get(code, {}).get(user_id)
    if ws:
        try:
            await ws.send_json(message)
        except Exception:
            pass


async def _send_battleship_to_all(code: str, gs: dict, msg: dict):
    """Send a battleship message to every player, each with their own game sync.

    海战棋的盘面是私有的（对手未沉舰的位置不下发），所以不能整条广播。
    """
    for pid in gs["player_colors"]:
        ws = _connections.get(code, {}).get(pid)
        if ws:
            await ws.send_json({**msg, "game": bs_game_to_sync(gs, pid)})


def _public_weapon_shots(shots: list, hide_type: bool) -> list:
    """击沉不报舰种时，连**协议里的 ship_id** 也一并抹掉。

    只让客户端「不显示」是假隐藏——消息线路上仍带着舰种，等于没做这条房规。
    位置（x/y/cells）与是否击沉保留，这些是公开信息。
    """
    if not hide_type:
        return shots
    return [
        {k: v for k, v in shot.items() if k != "ship_id"} if isinstance(shot, dict) else shot
        for shot in shots
    ]


async def _broadcast_battleship_action(
    code: str, room: dict, user_id: int, result: dict,
):
    """Broadcast the outcome of a battleship action (开炮 or 武器) and advance.

    三条路径（真人开炮 / 真人用武器 / 人机）共用一份广播，
    避免三处各写一遍导致协议漂移。
    """
    gs = room["game_state"]
    hide_type = bool(gs.get("hide_sunk_ship_type"))
    if result.get("weapon"):
        await _send_battleship_to_all(code, gs, {
            "type": "weapon_used",
            "weapon": result["weapon"],
            "user_id": user_id,
            "shooter_color": gs["player_colors"].get(user_id, ""),
            "x": result["x"],
            "y": result["y"],
            "cells": result["cells"],
            "shots": _public_weapon_shots(result["shots"], hide_type),
        })
        # 雷达结果只回给使用者——对手只看到他放了一次雷达，看不到探测结果
        if result.get("radar") is not None:
            ws = _connections.get(code, {}).get(user_id)
            if ws:
                await ws.send_json({
                    "type": "radar_result",
                    "x": result["x"],
                    "y": result["y"],
                    "has_ship": result["radar"]["has_ship"],
                })
    else:
        shot = result["shot"]
        await _send_battleship_to_all(code, gs, {
            "type": "shot",
            "x": shot["x"],
            "y": shot["y"],
            "result": shot["result"],
            "ship_id": None if hide_type else shot.get("ship_id"),
            "sunk_cells": shot.get("sunk_cells"),
            "shooter_color": gs["player_colors"].get(user_id, ""),
            # 命中续射：连着打时客户端要保持「轮到你」的高亮
            "keeps_turn": result.get("keeps_turn", False),
        })
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _handle_use_weapon(code: str, user_id: int, data: dict):
    """Handle a battleship special-weapon use (雷达 / 齐射 / 空袭)."""
    weapon = data.get("weapon")
    if not isinstance(weapon, str):
        return
    cells = data.get("cells")
    x = data.get("x")
    y = data.get("y")
    result = rm.battleship_use_weapon(
        code, user_id, weapon,
        int(x) if x is not None else None,
        int(y) if y is not None else None,
        cells,
    )
    if not result["ok"]:
        await _send_error(code, user_id, result["error"])
        return
    room = rm.get_room(code)
    if not room:
        return
    await _broadcast_battleship_action(code, room, user_id, result)


async def _handle_move2048(code: str, user_id: int, data: dict):
    """Handle a 2048 move (direction: up/down/left/right)."""
    direction = data.get("direction")
    if direction not in ("up", "down", "left", "right"):
        return
    result = rm.game2048_make_move(code, user_id, direction)
    if not result["ok"]:
        # silent 的失败（往走不动的方向滑）是正常操作，不回错误
        if result.get("silent"):
            return
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    room = rm.get_room(code)
    if not room:
        return
    gs = room["game_state"]
    color = gs["player_colors"].get(user_id, "")
    # 盘面是私有的：只把走子者自己的盘面发给本人，其余人只收到排行榜
    base_msg = {
        "type": "move2048",
        "user_id": user_id,
        "color": color,
        "direction": direction,
        "score": result["score"],
        "score_gained": result["score_gained"],
        "leaderboard": g2048_leaderboard(gs),
    }
    if result.get("player_finished"):
        base_msg["player_finished"] = result["player_finished"]
    conns = _connections.get(code, {})
    for pid in gs["player_colors"]:
        ws = conns.get(pid)
        if not ws:
            continue
        payload = dict(base_msg)
        if pid == user_id:
            payload["board"] = result["board"]
            payload["spawned"] = result["spawned"]
            payload["trace"] = result["trace"]
        await ws.send_json(payload)
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})


async def _handle_undo2048(code: str, user_id: int, data: dict):
    """单人 2048 撤销：只回给本人（盘面私有；多人局由服务端拒绝）。"""
    result = rm.game2048_undo(code, user_id)
    conns = _connections.get(code, {})
    ws = conns.get(user_id)
    if not result["ok"]:
        if result.get("silent"):
            return
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    if ws:
        await ws.send_json({
            "type": "undo2048",
            "user_id": user_id,
            "board": result["board"],
            "score": result["score"],
            "move_count": result["move_count"],
            "undo_available": result["undo_available"],
            "leaderboard": result["leaderboard"],
        })


async def _handle_tetris_actions(code: str, user_id: int, data: dict):
    """俄罗斯方块：重放客户端攒下的一段动作序列，然后**逐人下发**新状态。

    盘面是私有的（各玩各的），所以不能 `_broadcast` 一份了事：
    - 走子者本人：拿到自己的新盘面 + 新方块 + 分数
    - **收到攻击行的对手**：他们的盘也被顶了，同样要推一份新状态
    - 其余人：只更新排行榜
    """
    actions = data.get("actions")
    if not isinstance(actions, list):
        return
    result = rm.tetris_apply_actions(code, user_id, actions)
    if not result["ok"]:
        if result.get("silent"):
            return
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": result["error"]})
        return
    room = rm.get_room(code)
    if not room:
        return
    gs = room["game_state"]
    leaderboard = tetris_leaderboard(gs)
    conns = _connections.get(code, {})

    # ① 本人：完整新状态（锁定 / 暂存换块 / 兜底重同步三种情况都要推）
    if result["locked"]:
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({
                "type": "tetris_state",
                **tetris_game_to_sync(gs, user_id),
                "user_id": user_id,
                "cleared": result["cleared"],
                "score_gained": result["score_gained"],
                "garbage_sent": result["garbage_sent"],
                "garbage_killed": [
                    v for v in result["garbage_victims"]
                    if gs["game_over_flags"].get(v)
                ],
                "player_finished": result.get("player_finished"),
                "leaderboard": leaderboard,
                "last_action": "lock",
            })
    elif result.get("held"):
        # 暂存换了当前块（没有锁定）：盘面不变，但 current / next / held 都变了
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({
                "type": "tetris_state",
                **tetris_game_to_sync(gs, user_id),
                "user_id": user_id,
                "leaderboard": leaderboard,
                "last_action": "hold",
                "player_finished": result.get("player_finished"),
            })
    else:
        # **分叉兜底**：客户端只在「方块下不动了」时才把动作序列发上来（或点了硬降），
        # 所以服务端重放却**没锁定**只有一种解释——两边盘面已经不一样了（漏收过一条
        # 广播、本地预演跑偏等）。这时**必须回一份权威盘面**：不回的话客户端
        # 的 `awaiting` 永远等不到回包，重力与按键会全部失效——表现就是
        # 「盘面冻住、再也不出新的方块」。正常对局走不到这里。
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({
                "type": "tetris_state",
                **tetris_game_to_sync(gs, user_id),
                "user_id": user_id,
                "leaderboard": leaderboard,
                "last_action": "resync",
            })

    # ② 被攻击的对手：盘面被顶上去了，必须同步（不是他们自己动的）
    for vid in result["garbage_victims"]:
        ws = conns.get(vid)
        if not ws:
            continue
        await ws.send_json({
            "type": "tetris_state",
            **tetris_game_to_sync(gs, vid),
            "user_id": vid,
            "garbage_from": user_id,
            # 这次被顶了几行（garbage_received 是本局累计值，弹提示要用单次增量）
            "garbage_count": result.get("garbage_count", 0),
            "leaderboard": leaderboard,
            "last_action": "garbage",
            "player_finished": (
                gs["finish_reasons"].get(vid)
                if gs["game_over_flags"].get(vid) else None
            ),
        })

    # ③ 其余人：只更新排行榜（分数/行数变了）
    touched = {user_id, *result["garbage_victims"]}
    for pid in gs["player_colors"]:
        if pid in touched:
            continue
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "tetris_leaderboard",
                "leaderboard": leaderboard,
            })

    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})


async def _broadcast_tetris_start(code: str, room: dict, gs: dict):
    """逐人下发开局状态（各玩各的盘，只给自己的盘面与方块）。"""
    conns = _connections.get(code, {})
    for pid in gs["player_colors"]:
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "game_start",
                "game": tetris_game_to_sync(gs, pid),
            })


async def _broadcast_2048_start(code: str, room: dict, gs: dict):
    """Send individual game_start for 2048 (each player sees own board)."""
    for pid in gs["player_colors"]:
        conns = _connections.get(code, {})
        ws = conns.get(pid)
        if ws:
            sync_game = g2048_game_to_sync(gs, pid)
            await ws.send_json({
                "type": "game_start",
                "game": sync_game,
            })


#: v1.15 之前那五条「选设置」的消息。留着只为给老客户端回一句人话，下一版删除。
RETIRED_SETTING_MSGS = frozenset({
    "mine_pick", "stasis_pick_map", "battleship_pick_size",
    "luzhanqi_pick", "uno_pick",
})


async def _handle_room_setting(code: str, user_id: int, data: dict):
    """**房主在等待大厅里改一条房间级设置**（v1.15 统一入口）。

    在这之前是五个 handler，每个都重复「类型预检 → try/except → 广播 lobby」三段式。
    现在校验全在 `rm.set_room_setting` 里（房间在不在、是不是房主、是不是等待中、
    这个房间有没有这项设置、取值合不合法），这里只把 `ValueError` 变成一条 error。

    改完**必须广播一次 `lobby` 给全房** —— 房间级设置只回给房主的话，别人看到的
    还是旧值。

    `value` **刻意不做类型预检**：`None` / 字符串 / 布尔冒充数字，都会在
    `room_settings.coerce` 里变成一句能看懂的中文错误，不会静默写成默认值。
    """
    key = data.get("key")
    if not isinstance(key, str):
        await _send_error(code, user_id, "缺少设置项名称")
        return
    try:
        rm.set_room_setting(code, user_id, key, data.get("value"))
    except ValueError as e:
        await _send_error(code, user_id, str(e))
        return
    room = rm.get_room(code)
    if room:
        await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})


async def _luzhanqi_send_view(code: str, room: dict, gs: dict,
                              msg_type: str, only_user: int | None = None):
    """把（逐人裁剪过的）对局视图发给房里的真人。

    **布阵阶段对手的棋子身份也不能下发** —— 暗棋自不必说，明棋也是「各自摆好、
    摆完同时亮出」，边摆边被人看着反而怪。

    `msg_type` 必须区分：进对局屏那一次是 `game_start`（客户端**靠它导航到对局页**，
    发成别的消息，加入方根本不会跳过去），之后改阵型才是 `luzhanqi_setup`。
    """
    for pid, color in gs["player_colors"].items():
        if only_user is not None and pid != only_user:
            continue
        ws = _connections.get(code, {}).get(pid)
        if not ws:
            continue
        await ws.send_json({"type": msg_type, "game": luzhanqi_view_for(gs, color)})


async def _luzhanqi_broadcast_begin(code: str, room: dict, gs: dict):
    """双方都确认布阵 → 开打。**逐人下发**：暗棋下两份视图不一样。"""
    for pid, color in gs["player_colors"].items():
        ws = _connections.get(code, {}).get(pid)
        if not ws:
            continue
        await ws.send_json({"type": "luzhanqi_begin", "game": luzhanqi_view_for(gs, color)})
    _maybe_schedule_bot_turn(code, room, gs)


async def _handle_luzhanqi_setup(code: str, user_id: int, data: dict):
    """布阵阶段的四个动作：swap / random / preset / confirm（校验全在 room_manager 里）。"""
    result = rm.luzhanqi_setup(code, user_id, str(data.get("action") or ""),
                               data.get("a"), data.get("b"), data.get("name"))
    if not result["ok"]:
        await _send_error(code, user_id, result["error"])
        return
    room = rm.get_room(code)
    if not room or not room.get("game_state"):
        return
    gs = room["game_state"]
    # 布局只回给本人（对手看不到你摆了什么）
    await _luzhanqi_send_view(code, room, gs, "luzhanqi_setup", only_user=user_id)
    # 「我已确认」要让对手看见（双方都确认才开打）
    await _broadcast(code, {
        "type": "luzhanqi_ready",
        "ready": {c: bool(v) for c, v in gs.get("ready", {}).items()},
        "phase": gs.get("phase"),
    })
    if gs.get("phase") == "playing":
        await _luzhanqi_broadcast_begin(code, room, gs)


async def _broadcast_luzhanqi_move(code: str, room: dict, move: dict):
    """走子广播 —— **必须逐人下发**：暗棋下战报里的身份按收件人裁剪，
    合法着法也只算收件人自己的（「某位置有子可动」本身就是情报）。"""
    gs = room["game_state"]
    for pid, color in gs["player_colors"].items():
        ws = _connections.get(code, {}).get(pid)
        if not ws:
            continue
        await ws.send_json({
            "type": "move",
            "from": move["from"],
            "to": move["to"],
            "by": move["by"],
            "next": move["next"],
            "flag_revealed": move["flag_revealed"],
            "newly_revealed": move.get("newly_revealed") or [],
            "battle": luzhanqi_mask_battle(move["battle"], color,
                                           gs.get("mode", "open"), gs.get("reveal", True)),
            "legal": lzq.legal_payload(gs, color),
        })


async def _handle_luzhanqi_move(code: str, user_id: int, data: dict):
    from_x, from_y = data.get("from_x"), data.get("from_y")
    to_x, to_y = data.get("to_x"), data.get("to_y")
    if None in (from_x, from_y, to_x, to_y):
        return                                  # 缺字段：静默丢弃（同象棋）
    result = rm.luzhanqi_make_move(code, user_id,
                                   (int(from_y), int(from_x)), (int(to_y), int(to_x)))
    if not result["ok"]:
        await _send_error(code, user_id, result["error"])
        return
    room = rm.get_room(code)
    if not room:
        return
    await _broadcast_luzhanqi_move(code, room, result["move"])
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, room["game_state"])


async def _handle_add_bot(code: str, user_id: int, data: dict | None = None):
    """Handle add_bot request from host.

    data.difficulty 只对海战棋有意义（三档 AI），其余游戏忽略。
    """
    difficulty = (data or {}).get("difficulty")
    try:
        rm.add_bot(code, user_id, difficulty)
    except ValueError as e:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": str(e)})
        return
    room = rm.get_room(code)
    if room:
        await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})


async def _handle_remove_bot(code: str, user_id: int, data: dict):
    """Handle remove_bot request from host."""
    try:
        rm.remove_bot(code, user_id, data.get("bot_id"))
    except ValueError as e:
        conns = _connections.get(code, {})
        ws = conns.get(user_id)
        if ws:
            await ws.send_json({"type": "error", "message": str(e)})
        return
    room = rm.get_room(code)
    if room:
        await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})


# ── 等待大厅里互换座位（v1.16）────────────────────────────────


async def _handle_swap_request(code: str, user_id: int, data: dict):
    """`swap_request {target_id}`：请求与某人换座位。

    人机直接同意 → 立刻换好并广播 `lobby`，同时回一句 `swap_result`（`auto=True`）；
    真人 → 把 `swap_request` 转给对方（带发起人名字，界面据此弹窗），发起人收到
    一句「已发出请求」。**两条路都要回话** —— 不回的话界面上就是「点了没反应」。
    """
    target_id = data.get("target_id")
    if not isinstance(target_id, int):
        await _send_error(code, user_id, "换位请求缺少目标")
        return
    try:
        result = rm.request_swap(code, user_id, target_id)
    except ValueError as e:
        await _send_error(code, user_id, str(e))
        return
    if result["auto"]:
        room = rm.get_room(code)
        if room:
            await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})
        await _send_to(code, user_id, {
            "type": "swap_result", "ok": True, "accepted": True, "auto": True,
            "message": f"已和人机「{result['target']}」交换位置",
        })
        return
    room = rm.get_room(code)
    name = next((rm.player_name(p) for p in (room or {}).get("players", [])
                 if p["id"] == user_id), "")
    await _send_to(code, target_id, {
        "type": "swap_request", "from_id": user_id, "from_name": name,
    })
    await _send_to(code, user_id, {
        "type": "swap_result", "ok": True, "accepted": None, "auto": False,
        "message": f"已向「{result['target']}」发出换位请求，等对方同意",
    })


async def _handle_swap_respond(code: str, user_id: int, data: dict):
    """`swap_respond {from_id, accept}`：被请求方答复。"""
    from_id = data.get("from_id")
    if not isinstance(from_id, int):
        await _send_error(code, user_id, "换位答复缺少对象")
        return
    accept = bool(data.get("accept"))
    try:
        rm.respond_swap(code, user_id, from_id, accept)
    except ValueError as e:
        await _send_error(code, user_id, str(e))
        return
    room = rm.get_room(code)
    if accept and room:
        await _broadcast(code, {"type": "lobby", "room": rm.room_to_dict(room)})
    # 答复只回给发起人：他自己那边要看到「同意/拒绝」的结果（同意的话 `lobby`
    # 已经把新座位顺序带过去了，这句是给人看的确认）
    await _send_to(code, from_id, {
        "type": "swap_result", "ok": True, "accepted": accept, "auto": False,
        "message": "对方同意了换位，位置已经调换" if accept else "对方拒绝了换位请求",
    })


# ── Bot helpers ───────────────────────────────────────────────


def _is_bot(room: dict, user_id: int) -> bool:
    """Check if a player is a bot."""
    for p in room["players"]:
        if p["id"] == user_id:
            return p.get("is_bot", False)
    return False


def _find_bot_in_room(room: dict) -> dict | None:
    """Find the bot player in a room, if any."""
    for p in room["players"]:
        if p.get("is_bot"):
            return p
    return None


async def _broadcast_battleship_start(code: str, room: dict, gs: dict):
    """Send individual game_start for battleship (each player sees own view)."""
    for pid in gs["player_colors"]:
        conns = _connections.get(code, {})
        ws = conns.get(pid)
        if ws:
            sync_game = bs_game_to_sync(gs, pid)
            await ws.send_json({
                "type": "game_start",
                "game": sync_game,
            })


# ── UNO ──────────────────────────────────────────────────────


async def _broadcast_uno_start(code: str, room: dict, gs: dict):
    """开局：逐人下发（**本人拿到自己的手牌**，别人的一张都不给）。"""
    conns = _connections.get(code, {})
    for pid in gs["player_colors"]:
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "game_start",
                "game": uno_game_to_sync(gs, pid),
            })
    _maybe_schedule_bot_turn(code, room, gs)


async def _send_uno_state(code: str, room: dict):
    """逐人下发权威状态。

    **手牌私有是这局的核心不变量**：`uno_game_to_sync` 只把本人的手牌发出来，
    其余人只有张数（真实牌局里你也只看得到对手手里几张牌）。客户端渲染一律以
    这份状态为准——本地不做任何规则推算。
    """
    gs = room["game_state"]
    conns = _connections.get(code, {})
    for pid in gs["player_colors"]:
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "uno_state",
                **uno_game_to_sync(gs, pid),
            })


async def _uno_event(code: str, kind: str, seat: int | None = None, **fields):
    """公开事件广播：谁做了什么。

    拆成「公开事件 + 逐人状态」两条消息，是因为客户端需要两样东西：动效/提示条
    要知道**刚刚发生了什么**（出的是哪张牌、罚了几张、谁被跳过），而手牌只有本人
    能看。事件里一律不含别人的手牌。
    """
    await _broadcast(code, {"type": "uno_event", "kind": kind, "seat": seat, **fields})


async def _broadcast_uno_game_over(code: str, room: dict):
    """终局广播：带排名与每人剩余手牌点数（结算弹窗要用点数解释名次）。"""
    gs = room["game_state"]
    snap = uno_snapshot(gs)
    await _broadcast(code, {
        "type": "game_over",
        "game": "uno",
        "reason": snap["reason"],
        "winner": snap["winner"],
        # 组队档的胜负是**队伍**：结算文案要说「你和 XX 赢了」，只给一个座位号
        # 会让队友看起来像输家（同 `compute_ranking` 那条）
        "winner_team": snap.get("winner_team"),
        "seat_teams": snap.get("seat_teams"),
        "emptied": snap.get("emptied"),
        "ranking": room.get("_uno_ranking", []),
        "points": snap["points"],
        "seat_uids": snap["seat_uids"],
    })


async def _maybe_autopass_uno(code: str, room: dict) -> bool:
    """轮到已断线的玩家时，替他自动摸牌过牌。

    不这么做的话，回合制牌局会被一个掉线的人卡满 120s 保座超时。
    `rm.uno_should_autopass` 里带着**全员断线就停手**的护栏——否则回合会在几个
    断线座位之间无限自转（每圈自动摸牌过牌 → 忙循环，还会把牌堆摸空）。
    """
    gs = room.get("game_state") or {}
    acted: list[int] = []
    # 最多转一圈：每个座位至多被代打一次
    for _ in range(len(room["players"])):
        if gs.get("finished") or not rm.uno_should_autopass(room):
            break
        seat = gs["turn"]
        uid = gs["seat_uids"][seat]
        result = rm.uno_autopass_action(code, uid)
        if not result.get("ok"):
            break
        acted.append(seat)
        await _uno_event(code, "autopass", seat,
                         drawn=len(result.get("drawn") or []),
                         penalty=result.get("penalty"),
                         takeover=result.get("takeover"))
        if result.get("game_over"):
            break
    if not acted:
        return False
    await _send_uno_state(code, room)
    if gs.get("finished"):
        await _broadcast_uno_game_over(code, room)
    return True


async def _send_uno_error(code: str, user_id: int, message: str | None):
    """只回给动作发起者（非法出牌不该打扰整桌）。"""
    if not message:
        return
    ws = _connections.get(code, {}).get(user_id)
    if ws:
        await ws.send_json({"type": "error", "message": message})


async def _handle_uno_play(code: str, user_id: int, data: dict):
    """出牌。`jump` 是抢出、`target` 是 7 换手的对象 —— 校验全在引擎里。

    **抢输的人必须收到一条能看懂的 error**（`_send_uno_error`），不能静默：
    抢出是竞态的，同一张牌可能好几个人都有，先到先得。
    """
    card = data.get("card")
    if not isinstance(card, str):
        return
    color = data.get("color")
    result = rm.uno_play_action(code, user_id, card, color,
                                jump=bool(data.get("jump")),
                                target=data.get("target"))
    if not result.get("ok"):
        await _send_uno_error(code, user_id, result.get("error"))
        return
    room = rm.get_room(code)
    await _uno_event(code, "play", result["seat"],
                     card=result["card"], color=result["color"],
                     effect=result["effect"], skipped=result["skipped"],
                     pending_draw=result["pending_draw"],
                     target=result.get("target"), jump=result.get("jump"),
                     takeover=result.get("takeover"))
    await _send_uno_state(code, room)
    if result.get("game_over"):
        await _broadcast_uno_game_over(code, room)
        return
    await _after_uno_turn(code, room)


async def _handle_uno_draw(code: str, user_id: int):
    result = rm.uno_draw_action(code, user_id)
    if not result.get("ok"):
        await _send_uno_error(code, user_id, result.get("error"))
        return
    room = rm.get_room(code)
    seat = rm._get_player_seat(room, user_id)
    await _uno_event(code, "draw", seat,
                     drawn=len(result.get("drawn") or []),
                     penalty=result.get("penalty"),
                     kept_turn=result.get("kept_turn"),
                     # 摸牌是 TAGGING BACK IN 最常触发的时机（「队友只要摸到牌就
                     # 分他一半」）—— 不带上它，界面上就会看到张数莫名跳变
                     takeover=result.get("takeover"))
    await _send_uno_state(code, room)
    if result.get("game_over"):
        await _broadcast_uno_game_over(code, room)
        return
    # 摸到能出的牌时本人还握着回合，等他自己出或过牌——不能替他推进
    if result.get("kept_turn"):
        return
    await _after_uno_turn(code, room)


async def _handle_uno_pass(code: str, user_id: int):
    result = rm.uno_pass_action(code, user_id)
    if not result.get("ok"):
        await _send_uno_error(code, user_id, result.get("error"))
        return
    room = rm.get_room(code)
    await _uno_event(code, "pass", rm._get_player_seat(room, user_id))
    await _send_uno_state(code, room)
    await _after_uno_turn(code, room)


async def _handle_uno_roulette(code: str, user_id: int, data: dict):
    """颜色轮盘点名。校验（是不是该你点、颜色合不合法）全在引擎里。"""
    color = data.get("color")
    if not isinstance(color, str):
        return
    result = rm.uno_roulette_action(code, user_id, color)
    if not result.get("ok"):
        await _send_uno_error(code, user_id, result.get("error"))
        return
    room = rm.get_room(code)
    await _uno_event(code, "roulette", result["seat"], color=result["color"],
                     drawn=len(result.get("drawn") or []))
    await _send_uno_state(code, room)
    if result.get("game_over"):
        await _broadcast_uno_game_over(code, room)
        return
    await _after_uno_turn(code, room)


async def _handle_uno_call(code: str, user_id: int, data: dict):
    """`target` 可选：组队档里替队友喊 UNO（不传则由引擎决定替谁喊）。"""
    target = data.get("target")
    result = rm.uno_call_action(code, user_id, None if target is None else int(target))
    if not result.get("ok"):
        await _send_uno_error(code, user_id, result.get("error"))
        return
    room = rm.get_room(code)
    # 事件里带的是**被喊的那个座位**（可能是队友），不是发起人
    await _uno_event(code, "uno", result.get("seat"))
    await _send_uno_state(code, room)


async def _after_uno_turn(code: str, room: dict):
    """一次回合结束后的公共收尾：替断线的人过牌、轮到人机就调度。

    **顺序不能反**：先补掉断线的座位，再看新回合是不是人机的——反过来的话，
    断线座位后刚好跟着一个人机，人机那一手会被漏掉。（v1.14 修：原来这里在
    代打成功时直接 `return`，把后面的人机调度整个跳过了，与上面那句注释正好相反。
    代打之后回合往往正落在一个**人机**身上，那一步就一直没人走。）
    """
    await _maybe_autopass_uno(code, room)
    gs = room.get("game_state") or {}
    if gs.get("finished"):
        return
    _maybe_schedule_bot_turn(code, room, gs)
    # 抢出房规：不轮到他的人机也可能能抢一手，单独排（见函数注释）
    _maybe_schedule_bot_jump_uno(code, room, gs)


def _maybe_schedule_bot_jump_uno(code: str, room: dict, gs: dict):
    """抢出房规下，给「手里有与牌堆顶完全相同的牌」的人机排一手抢出。

    **人机走和真人同一条延迟通道（0.8~1.2 秒）**：真人看着屏幕、需要一个反应窗口，
    人机要是零延迟地抢，这条房规就变成「只有人机在玩」。延迟一到，`_bot_uno_jump`
    会**重新校验**一次 —— 这中间牌堆顶可能已经被别人抢掉了。

    一次把**所有**够格的人机都排上：各自的 task key 是 `(code, bot_id)`，互不干扰，
    先到的抢到，后到的在 `can_jump` 上落空、自己退出。
    """
    if not (gs.get("rules") or {}).get("jump_in"):
        return
    players = room["players"]
    turn = gs.get("turn")
    for seat, uid in enumerate(gs.get("seat_uids", [])):
        if seat == turn or not (0 <= seat < len(players)):
            continue
        if not players[seat].get("is_bot"):
            continue
        if uno_jumpable_cards(gs, seat):
            rm.schedule_bot_turn(code, uid)


async def _bot_uno_turn(code: str, bot_id: int, room: dict, gs: dict):
    """UNO 人机一个回合：出牌（黑牌自己选色）/ 摸牌 / 过牌，然后喊 UNO。

    AI 只负责决策，裁决仍在引擎里（走 `rm.uno_*_action`），与象棋/飞行棋一致。
    UNO 的 AI 是贪心、无搜索，所以**不需要 `asyncio.to_thread`**。

    **不是他的回合**也照样进来（`_maybe_schedule_bot_jump_uno` 走的是同一条延迟
    通道）—— 那时只可能是抢出。
    """
    seat = rm._get_player_seat(room, bot_id)
    if seat is None or gs.get("finished"):
        return
    if gs.get("turn") != seat:
        await _bot_uno_jump(code, bot_id, room, gs, seat)
        return
    act = uno_ai.best_action(gs, seat)
    if not act:
        return
    if act["action"] == "play":
        result = rm.uno_play_action(code, bot_id, act["card"], act.get("color"),
                                    target=act.get("target"))
    elif act["action"] == "roulette":
        result = rm.uno_roulette_action(code, bot_id, act["color"])
    elif act["action"] == "draw":
        result = rm.uno_draw_action(code, bot_id)
    else:
        result = rm.uno_pass_action(code, bot_id)
    if not result.get("ok"):
        return
    await _uno_event(code, act["action"], seat,
                     card=result.get("card"), color=result.get("color"),
                     effect=result.get("effect"), skipped=result.get("skipped"),
                     drawn=len(result.get("drawn") or []),
                     penalty=result.get("penalty"),
                     target=result.get("target"))
    await _uno_bot_maybe_call(code, room, gs, bot_id, seat)
    await _send_uno_state(code, room)
    if result.get("game_over"):
        await _broadcast_uno_game_over(code, room)
        return
    await _after_uno_turn(code, room)


async def _bot_uno_jump(code: str, bot_id: int, room: dict, gs: dict, seat: int):
    """人机抢出一张（`jump_in` 房规）。

    **必须在落地的这一刻重新校验** —— 从「排一手抢出」到「真的抢」之间隔着
    0.8~1.2 秒，牌堆顶可能已经被别人抢掉了。`uno_ai.best_jump` 只回答「想不想抢」，
    「抢不抢得到」由引擎的 `can_jump` 说了算（AI 不给裁决，同其它游戏）。
    """
    act = uno_ai.best_jump(gs, seat)
    if not act:
        return
    result = rm.uno_play_action(code, bot_id, act["card"], act.get("color"),
                                jump=True, target=act.get("target"))
    if not result.get("ok"):
        return
    await _uno_event(code, "play", seat,
                     card=result.get("card"), color=result.get("color"),
                     effect=result.get("effect"), skipped=result.get("skipped"),
                     pending_draw=result.get("pending_draw"),
                     target=result.get("target"), jump=True)
    await _uno_bot_maybe_call(code, room, gs, bot_id, seat)
    await _send_uno_state(code, room)
    if result.get("game_over"):
        await _broadcast_uno_game_over(code, room)
        return
    await _after_uno_turn(code, room)


async def _uno_bot_maybe_call(code: str, room: dict, gs: dict,
                              bot_id: int, seat: int):
    """人机只剩一张时自动喊 UNO（真人是自己点按钮）。"""
    if gs.get("finished") or not uno_can_call_uno(gs, seat):
        return
    if rm.uno_call_action(code, bot_id).get("ok"):
        await _uno_event(code, "uno", seat)


def _schedule_battleship_bots(code: str, room: dict, gs: dict):
    """Auto-place ships for any bot in a battleship game."""
    for pid, color in gs["player_colors"].items():
        if _is_bot(room, pid):
            rm.schedule_bot_placement(code, pid)


def _maybe_schedule_bot_turn(code: str, room: dict, gs: dict):
    """If it's a bot's turn, schedule the AI move."""
    if room["game"] == "battleship":
        next_color = gs["next"]
        for pid, color in gs["player_colors"].items():
            if color == next_color and _is_bot(room, pid):
                rm.schedule_bot_turn(code, pid)
                return
    elif room["game"] in ("gomoku", "reversi", "xiangqi", "connect4", "luzhanqi"):
        next_color = gs["next"]
        for pid, color in gs["player_colors"].items():
            if color == next_color and _is_bot(room, pid):
                rm.schedule_bot_turn(code, pid)
                return
    elif room["game"] == "dots":
        next_player = gs["next"]
        next_color = "black" if next_player == DOTS_P1 else "white"
        for pid, color in gs["player_colors"].items():
            if color == next_color and _is_bot(room, pid):
                rm.schedule_bot_turn(code, pid)
                return
    elif room["game"] == "ludo":
        # 飞行棋座位号即 room.players 下标，与 gs["current_seat"] 同源
        seat = gs["current_seat"]
        players = room["players"]
        if 0 <= seat < len(players) and players[seat].get("is_bot"):
            rm.schedule_bot_turn(code, players[seat]["id"])
    elif room["game"] == "uno":
        # UNO 同理：座位号 = room.players 下标 = gs["turn"]
        seat = gs.get("turn")
        players = room["players"]
        if seat is not None and 0 <= seat < len(players) and players[seat].get("is_bot"):
            rm.schedule_bot_turn(code, players[seat]["id"])


# ── Bot AI callbacks ─────────────────────────────────────────


async def _bot_do_turn(code: str, bot_id: int):
    """AI callback: compute and execute a bot move."""
    room = rm.get_room(code)
    if not room or room["status"] != "playing":
        return
    gs = room["game_state"]
    if gs.get("winner") or gs.get("finished"):
        return
    if room["game"] == "gomoku":
        await _bot_gomoku_move(code, bot_id, room, gs)
    elif room["game"] == "reversi":
        await _bot_reversi_move(code, bot_id, room, gs)
    elif room["game"] == "battleship":
        await _bot_battleship_fire(code, bot_id, room, gs)
    elif room["game"] == "dots":
        await _bot_dots_move(code, bot_id, room, gs)
    elif room["game"] == "ludo":
        await _bot_ludo_turn(code, bot_id, room, gs)
    elif room["game"] == "xiangqi":
        await _bot_xiangqi_move(code, bot_id, room, gs)
    elif room["game"] == "connect4":
        await _bot_connect4_move(code, bot_id, room, gs)
    elif room["game"] == "luzhanqi":
        await _bot_luzhanqi_move(code, bot_id, room, gs)
    elif room["game"] == "uno":
        await _bot_uno_turn(code, bot_id, room, gs)


async def _bot_gomoku_move(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a gomoku AI move for the bot."""
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color:
        return
    stone = GOMOKU_BLACK if color == "black" else GOMOKU_WHITE
    move = gomoku_ai.best_move(gs["board"], stone)
    if move is None:
        return
    x, y = move
    result = rm.make_move(code, bot_id, x, y)
    if not result["ok"]:
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_reversi_move(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a reversi AI move for the bot."""
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color:
        return
    stone = REVERSI_BLACK if color == "black" else REVERSI_WHITE
    move = reversi_ai.best_move(gs["board"], stone)
    if move is None:
        # No legal moves: auto-pass (handled by reversi_make_move)
        # Just advance turn to opponent
        from app.game.reversi import get_legal_moves
        legal = get_legal_moves(gs["board"], stone)
        if not legal:
            gs["pass_count"] = gs.get("pass_count", 0) + 1
            opp_color = "white" if color == "black" else "black"
            gs["next"] = opp_color
            rm._touch_activity(room)
            await _broadcast(code, {"type": "pass", "color": color})
            _maybe_schedule_bot_turn(code, room, gs)
        return
    x, y = move
    result = rm.reversi_make_move(code, bot_id, x, y)
    if not result["ok"]:
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result.get("pass"):
        await _broadcast(code, {"type": "pass", **result["pass"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_xiangqi_move(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a xiangqi AI move for the bot.

    搜索要跑 0.5~1 秒，**必须丢进线程池**——直接 await 会把整个事件循环（这个进程
    里所有房间的 WS）卡住。盘面也复制一份再进去：AI 搜索途中会临时改写棋盘
    （走子 → 评估 → 撤销），和事件循环里正在读同一份 board 的其它协程会打架。
    """
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color:
        return
    board_copy = [row[:] for row in gs["board"]]
    move = await asyncio.to_thread(xiangqi_ai.best_move, board_copy, color)
    if move is None:
        return
    frm, to = move
    result = rm.xiangqi_make_move(code, bot_id, frm, to)
    if not result["ok"]:
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_luzhanqi_move(code: str, bot_id: int, room: dict, gs: dict):
    """军棋人机走子。

    与象棋同一条纪律：**必须丢进线程池**（评估 + 威胁检查是同步计算，直接 await
    会卡住整个事件循环），而且**只把逐人裁剪过的视图交给 AI** —— 暗棋下它同样
    看不见对手的棋子身份，和真人拿到的信息一模一样。
    """
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color or gs.get("phase") != "playing":
        return
    view = luzhanqi_view_for(gs, color)
    move = await asyncio.to_thread(luzhanqi_ai.best_move, view)
    if move is None:
        return
    result = rm.luzhanqi_make_move(code, bot_id, move[0], move[1])
    if not result["ok"]:
        return
    await _broadcast_luzhanqi_move(code, room, result["move"])
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_connect4_move(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a Connect Four AI move for the bot.

    和象棋 AI 同理：搜索最长 0.5 秒，**必须丢进线程池**——直接 await 会把这个
    进程里所有房间的事件循环卡住。盘面复制一份再进去（搜索途中会原地落子/撤销）。
    """
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color:
        return
    stone = C4_BLACK if color == "black" else C4_WHITE
    board_copy = [row[:] for row in gs["board"]]
    col = await asyncio.to_thread(connect4_ai.best_move, board_copy, stone)
    if col is None:
        return
    result = rm.connect4_make_move(code, bot_id, col)
    if not result["ok"]:
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_dots_move(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a dots AI move for the bot."""
    color = gs["player_colors"].get(bot_id)
    if not color:
        return
    player_num = DOTS_P1 if color == "black" else DOTS_P2
    if gs["next"] != player_num:
        return
    move = dots_ai.best_move(gs)
    if move is None:
        return
    orient, y, x = move
    result = rm.dots_make_move(code, bot_id, orient, y, x)
    if not result["ok"]:
        return
    await _broadcast(code, {"type": "move", **result["move"]})
    if result["game_over"]:
        await _broadcast(code, {"type": "game_over", **result["game_over"]})
    else:
        _maybe_schedule_bot_turn(code, room, gs)


async def _bot_ludo_turn(code: str, bot_id: int, room: dict, gs: dict):
    """飞行棋人机一个完整回合：掷骰 → 选子走 → （掷 6 加手则继续）。"""
    seat = rm._get_player_seat(room, bot_id)
    if seat is None or gs["current_seat"] != seat:
        return
    if gs["phase"] == "roll":
        result = rm.ludo_roll_action(code, bot_id)
        if not result["ok"]:
            return
        bot_roll = {
            "type": "dice_rolled",
            "seat": seat,
            "dice": result["dice"],
            "movable": result["movable"],
            "extra_turn": result["extra_turn"],
            "penalty": result["penalty"],
            "current_seat": gs["current_seat"],
        }
        if result["penalty"]:
            # 与真人路径一致：惩罚把飞机清回基地，要带上最新位置
            bot_roll["pieces"] = list(gs["players"][seat]["pieces"])
        await _broadcast(code, bot_roll)
        if result.get("game_over"):
            await _broadcast(code, {
                "type": "game_over",
                "reason": result["game_over"]["reason"],
                "ranking": result["game_over"].get("ranking", []),
            })
            return
        if gs["phase"] != "move":
            # 无可动飞机 / 三连六惩罚 → 回合已移交（或仍在自己手里但只能重掷）
            _maybe_schedule_bot_turn(code, room, gs)
            return
    piece_id = ludo_ai.best_move(gs)
    if piece_id is None:
        return
    result = rm.ludo_move_action(code, bot_id, piece_id)
    if not result["ok"]:
        return
    await _broadcast(code, {
        "type": "piece_moved",
        "seat": result["seat"],
        "piece_id": result["piece_id"],
        "old_progress": result["old_progress"],
        "new_progress": result["new_progress"],
        "dice": result["dice"],
        # 与真人路径保持同一份载荷（人机走子要让所有人看到同样的跳/飞动画）
        "path": result["path"],
        "segments": result["segments"],
        "captures": result["captures"],
        "finished": result["finished"],
        "extra_turn": result["extra_turn"],
        "current_seat": gs["current_seat"],
    })
    if result.get("game_over"):
        await _broadcast(code, {
            "type": "game_over",
            "reason": result["game_over"]["reason"],
            "ranking": result["game_over"].get("ranking", []),
        })
    else:
        # 掷 6 加手时 current_seat 未变，会再次调度到自己
        _maybe_schedule_bot_turn(code, room, gs)


def _bot_difficulty(room: dict, bot_id: int) -> str:
    """Bot's AI tier, stored per-player so a room can host bots of mixed skill."""
    for p in room["players"]:
        if p["id"] == bot_id:
            return battleship_ai.normalize_difficulty(p.get("ai_difficulty"))
    return battleship_ai.DEFAULT_DIFFICULTY


async def _bot_battleship_fire(code: str, bot_id: int, room: dict, gs: dict):
    """Execute a battleship AI action for the bot (先看要不要用武器，否则开炮)."""
    color = gs["player_colors"].get(bot_id)
    if not color or gs["next"] != color or gs["phase"] != "battle":
        return
    opp_id = next((p["id"] for p in room["players"] if p["id"] != bot_id), None)
    if opp_id is None:
        return
    attack_board = gs["attack_boards"][opp_id]
    opp_board = gs["boards"][opp_id]
    # 自己还有哪些武器可用（未用过 + 携带舰还在水面）
    used = gs["weapons_used"].get(bot_id, [])
    own_board = gs["boards"][bot_id]
    own_attack = gs["attack_boards"][bot_id]
    available = {
        w: bool(sid) and w not in used
        and bs_ship_afloat(own_board, own_attack, sid)
        for w, sid in (gs["weapons"].get(bot_id) or {}).items()
    }
    plan = battleship_ai.weapon_plan(
        attack_board, opp_board, available, len(gs["shots"].get(bot_id, [])),
    )
    if plan:
        weapon, kwargs = plan
        result = rm.battleship_use_weapon(
            code, bot_id, weapon,
            kwargs.get("x"), kwargs.get("y"), kwargs.get("cells"),
        )
    else:
        x, y = battleship_ai.best_move(
            attack_board, opp_board, gs["placements"][opp_id],
            _bot_difficulty(room, bot_id),
        )
        result = rm.battleship_fire(code, bot_id, x, y)
    if not result["ok"]:
        return
    await _broadcast_battleship_action(code, room, bot_id, result)


async def _bot_do_placement(code: str, bot_id: int):
    """Bot auto-placement callback for battleship."""
    room = rm.get_room(code)
    if not room or room["status"] != "playing":
        return
    gs = room["game_state"]
    if gs["phase"] != "placing":
        return
    if gs["placement_ready"].get(bot_id):
        return  # 已布阵（热重启重复调度时幂等）
    # 人机布阵按**这一局那张棋盘**的舰队与尺寸（v13：尺寸是房间级设置）
    ships = bs_random_placement(
        size=gs.get("size"), no_touch=bool(gs.get("adjacent_placement")),
    )
    result = rm.battleship_set_placement(code, bot_id, ships)
    if not result["ok"]:
        return
    # Broadcast placement_ready
    await _broadcast(code, {
        "type": "placement_ready",
        "user_id": bot_id,
    })
    if result.get("battle_start"):
        await _broadcast_battleship_start(code, room, gs)


# ── 深空休眠（stasis）────────────────────────────────────────
#
# 广播分三层（频率不同，见 docs/游戏设计/深空休眠/协议.md）：
#   stasis_tick   5 Hz   会动的东西（时钟/静默体/门血/资源）—— 由 tick 循环调
#   stasis_state  离散   全量（含建筑布局），只在建造/升级/选舱室后发
#   stasis_events 立即   门破/撤退/进化/EMP 等离散事件
#
# `game_start` 与 `stasis_state` 都逐人下发：`game_to_sync` 带 your_seat/your_uid，
# 全员同一份会让客户端认不出自己（同飞行棋 your_seat、2048 your_color 的教训）。


async def _broadcast_stasis_start(code: str, room: dict, gs: dict):
    conns = _connections.get(code, {})
    for pid in gs["players"]:
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "game_start",
                "game": stasis_engine.game_to_sync(gs, pid),
            })


async def _broadcast_stasis_state(code: str, room: dict):
    """全量状态逐人下发。**布局（建筑列表与等级）走这一层，不进 5 Hz 的 tick**。

    载荷**扁平展开**（不套 `game` 键）—— 与 `stasis_tick` 一致；只有 `game_start`
    沿用了全项目的 `msg.game` 惯例（客户端两条路都认，见协议文档）。
    """
    gs = room.get("game_state")
    if not gs:
        return
    conns = _connections.get(code, {})
    for pid in gs["players"]:
        ws = conns.get(pid)
        if ws:
            await ws.send_json({
                "type": "stasis_state",
                **stasis_engine.game_to_sync(gs, pid),
            })


async def broadcast_stasis_tick(code: str, room: dict):
    """每 tick 广播动态快照（5 Hz）。由 room_manager 的 tick 循环调用。"""
    gs = room.get("game_state")
    if not gs:
        return
    await _broadcast(code, {
        "type": "stasis_tick",
        **stasis_engine.dynamic_snapshot(gs),
    })


async def broadcast_stasis_events(code: str, events: list[dict]):
    """离散事件立即广播。由 tick 循环调用。"""
    if not events:
        return
    await _broadcast(code, {"type": "stasis_events", "events": events})


async def broadcast_stasis_game_over(room: dict, ranking: list[dict]):
    """终局广播（tick 路径与动作/离开路径共用）。"""
    gs = room.get("game_state") or {}
    await _broadcast(room["code"], {
        "type": "game_over",
        "winner": gs.get("winner"),
        "reason": gs.get("reason"),
        "ranking": ranking,
    })


async def _stasis_do(code: str, user_id: int, fn) -> bool:
    """动作统一收尾：失败回 error，成功推一份全量状态（布局变了）。"""
    room = rm.get_room(code)
    if not room or room["game"] != "stasis":
        return False
    result = fn()
    if not result.get("ok"):
        await _send_error(code, user_id, result.get("error", "操作失败"))
        return False
    # 这个动作可能直接终局（最后一个人弃守 → 全灭）。终局时由 `_finish_stasis`
    # 统一广播 `game_over`（它自己幂等），否则推一份全量状态。
    if room["game_state"].get("finished"):
        await rm._finish_stasis(room)
    else:
        await _broadcast_stasis_state(code, room)
    return True


async def _handle_stasis_move(code: str, user_id: int, data: dict):
    """移动输入（5 Hz）。**回包静默**：位置真值走 `stasis_tick` 广播，
    逐条回 ack 只会把上行带宽翻倍，而客户端本来就做本地预演、不等 ack。"""
    dx, dy = data.get("dx", 0.0), data.get("dy", 0.0)
    try:
        dx, dy = float(dx), float(dy)
    except (TypeError, ValueError):
        return
    rm.stasis_move(code, user_id, dx, dy)


async def _handle_stasis_build(code: str, user_id: int, data: dict):
    # `cell` 是 **`"x,y"` 字符串**（v2 把格子从抽象下标改成坐标，见 stasis_map.py）。
    # 这里曾漏改成字符串、仍校验 `isinstance(cell, int)`，于是每一次建造都被静默
    # 拒掉、只回一条 error toast —— 界面上「点了没反应」（E2E 抓到）。
    kind, cell = data.get("kind"), data.get("cell")
    if not isinstance(kind, str) or not isinstance(cell, str):
        await _send_error(code, user_id, "建造参数不对")
        return
    await _stasis_do(code, user_id,
                     lambda: rm.stasis_build(code, user_id, kind, cell))


async def _handle_stasis_upgrade(code: str, user_id: int, data: dict):
    # `"bed"` / `"door"` / `"x,y"` —— 三种都是字符串
    target = data.get("target")
    if not isinstance(target, str):
        await _send_error(code, user_id, "升级参数不对")
        return
    await _stasis_do(code, user_id,
                     lambda: rm.stasis_upgrade(code, user_id, target))


async def _handle_stasis_demolish(code: str, user_id: int, data: dict):
    cell = data.get("cell")
    if not isinstance(cell, str):
        await _send_error(code, user_id, "拆除参数不对")
        return
    await _stasis_do(code, user_id,
                     lambda: rm.stasis_demolish(code, user_id, cell))


async def _handle_stasis_patch(code: str, user_id: int, data: dict):
    # 两种目标二选一：`target_uid`（房间门，含自己）或 `gate`（**共用闸门**，v9）。
    # 两个都给的话以 `gate` 为准 —— 客户端不会这么发，但校验得有个确定的答案。
    gate = data.get("gate")
    if gate is not None:
        if not isinstance(gate, str):
            await _send_error(code, user_id, "修补参数不对")
            return
        await _stasis_do(code, user_id,
                         lambda: rm.stasis_patch(code, user_id, None, gate))
        return
    target = data.get("target_uid")
    if target is not None and (not isinstance(target, int) or isinstance(target, bool)):
        await _send_error(code, user_id, "修补参数不对")
        return
    await _stasis_do(code, user_id,
                     lambda: rm.stasis_patch(code, user_id, target))


# ── 热重启支持 ────────────────────────────────────────────────


async def reschedule_after_restore():
    """startup 恢复房间后调用（在事件循环内跑）。

    - playing 房：轮到 bot 时重新调度 AI（海战棋按布阵进度补调度）
    - waiting 房：60s 宽限期后移除仍未重连的真人玩家（与「等待中断线即
      移出」语义一致；宽限期留给客户端 WS 自动重连），并广播大厅刷新
    """
    for room in rm.all_rooms():
        if room["status"] != "playing" or not room.get("game_state"):
            continue
        gs = room["game_state"]
        if gs.get("winner") or gs.get("finished"):
            continue
        try:
            if room["game"] == "stasis":
                # 深空休眠：tick 状态全在 game_state 里（引擎是纯函数 + 显式 dt），
                # 恢复只需要重起任务。因为用的是**游戏内时钟**而不是墙钟，
                # 恢复后时间从最后一次关键帧继续，不会跳变（最多丢 5 秒游戏时间）。
                rm._ensure_stasis_tick(room["code"])
            elif room["game"] == "battleship" and gs.get("phase") == "placing":
                for pid in gs["player_colors"]:
                    if _is_bot(room, pid) and not gs["placement_ready"].get(pid):
                        rm.schedule_bot_placement(room["code"], pid)
            else:
                _maybe_schedule_bot_turn(room["code"], room, gs)
        except Exception:
            pass
    await asyncio.sleep(60)
    for room in rm.all_rooms():
        if room["status"] != "waiting":
            continue
        code = room["code"]
        stale = [
            p["id"] for p in room["players"]
            if not p.get("is_bot") and not p.get("connected")
        ]
        for pid in stale:
            rm.remove_player(code, pid)
        if stale and rm.get_room(code):
            await _broadcast(code, {
                "type": "lobby", "room": rm.room_to_dict(rm.get_room(code))
            })


# ── Register callbacks ────────────────────────────────────────


def _broadcast_lobby_after_game_end(room: dict):
    """对局结束：把房间最新状态（含刚被复位为未准备的 ready）广播给房间页。

    对局屏认 `game_over`，房间页不认——**已出局的观战者**（扫雷/飞行棋/2048 允许
    「本人出局、其他人继续」）会一直停在房间页，手里还是开局那一刻的 ready 快照：
    界面显示「已准备」、房主看到「开始游戏」可点，点下去服务端却拒绝，症状又是
    「点了没反应」。终局后补一条 lobby，房间页就永远是权威数据。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 单测里直接调 room_manager，没有事件循环，跳过广播
    loop.create_task(_broadcast(room["code"], {
        "type": "lobby", "room": rm.room_to_dict(room),
    }))


rm.set_bot_turn_callback(_bot_do_turn)
rm.set_bot_placement_callback(_bot_do_placement)
rm.add_game_end_listener(_broadcast_lobby_after_game_end)
