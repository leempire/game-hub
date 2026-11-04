"""Separate read-only spectator grants/connections and explicitly public game views."""

import asyncio
import json
import logging
import secrets
import time
from copy import deepcopy

from fastapi import WebSocket, WebSocketDisconnect

from app import room_manager as rm
from app.game import uno, stasis
from app.security import decode_token_ws

GAMES = ('uno', 'stasis')
SAMPLE_SECONDS = 0.5
SEND_TIMEOUT = 2.0
AUTH_CHECK_SECONDS = 5.0
_grants: dict[str, dict] = {}
_watchers: dict[str, dict[int, tuple]] = {}
_UNO_PUBLIC = (
    'game', 'num_players', 'seat_uids', 'seat_colors', 'rules', 'hand_counts',
    'discard_top', 'current_color', 'turn', 'direction', 'pending_draw',
    'pending_kind', 'uno_called', 'out', 'seat_teams', 'emptied', 'winner_team',
    'draw_pile_count', 'winner', 'reason', 'finished', 'series',
)


class WatchError(ValueError):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def _allowed(room):
    return bool(room and room['game'] in GAMES and room.get('allow_spectators')
                and not room.get('practice'))


def grant_access(code: str, user_id: int, password: str | None = None) -> dict:
    room = rm.get_room(code)
    if room is None:
        raise WatchError('房间不存在', 404)
    if not _allowed(room):
        raise WatchError('房主没有开放观战')
    member = any(p['id'] == user_id for p in room['players'])
    expected = room.get('password')
    if expected is not None and not member and not secrets.compare_digest(
            expected.encode(), (password or '').encode()):
        raise WatchError('房间密码错误')
    for token, saved in list(_grants.items()):
        if not valid_grant(token, saved['user_id'], saved['code']):
            _grants.pop(token, None)
        elif saved['code'] == code and saved['user_id'] == user_id:
            return {'grant': token, 'ws_path': f'/ws/watch/{code}', 'game': room['game']}
    token = secrets.token_urlsafe(32)
    _grants[token] = {'code': code, 'user_id': user_id,
                      'generation': room.get('watch_generation', 0),
                      'instance': (id(room), room.get('created_at'))}
    return {'grant': token, 'ws_path': f'/ws/watch/{code}', 'game': room['game']}


def valid_grant(token: str, user_id: int, code: str) -> bool:
    saved = _grants.get(token)
    room = rm.get_room(code)
    return bool(saved and saved['user_id'] == user_id and saved['code'] == code
                and _allowed(room) and saved['generation'] == room.get('watch_generation', 0)
                and saved['instance'] == (id(room), room.get('created_at')))


def public_game(room: dict) -> dict | None:
    gs = room.get('game_state')
    if not isinstance(gs, dict) or not gs:
        return None
    if room['game'] == 'uno':
        view = uno.game_to_sync(gs, -999999999)
        # An allowlist ensures future action flags/private cards cannot leak.
        return deepcopy({key: view[key] for key in _UNO_PUBLIC if key in view})
    view = stasis.game_to_sync(gs, -999999999)
    view.pop('your_seat', None)
    view.pop('your_uid', None)
    return view


def state_payload(room: dict, kind='watch_state') -> dict:
    return {'type': kind, 'read_only': True, 'room': rm.room_to_dict(room), 'game': public_game(room)}


async def _send(socket, lock, payload, authorize=None) -> bool:
    async def ordered():
        async with lock:
            if authorize is not None and not authorize():
                return False
            await socket.send_json(payload)
            return True
    try:
        return await asyncio.wait_for(ordered(), timeout=SEND_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
        return False


async def _close(socket, code, reason):
    try:
        await asyncio.wait_for(socket.close(code=code, reason=reason), timeout=1.0)
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
        pass


async def broadcast_public(code: str, message: dict):
    async def deliver(socket, lock, grant, user_id):
        authorized = lambda: valid_grant(grant, user_id, code)
        if not authorized():
            await _close(socket, 4003, '观战已关闭')
        elif not await _send(socket, lock, message, authorize=authorized):
            await _close(socket, 4000, '连接不可用')
    connections = list(_watchers.get(code, {}).values())
    if connections:
        await asyncio.gather(*(deliver(*connection) for connection in connections))


async def kick_user(user_id: int, reason: str):
    """Revoke grants and disconnect all watching sessions immediately."""
    for grant, saved in list(_grants.items()):
        if saved['user_id'] == user_id:
            _grants.pop(grant, None)
    connections = [connection for group in list(_watchers.values())
                   for connection in list(group.values()) if connection[3] == user_id]
    if connections:
        await asyncio.gather(*(_close(connection[0], 4001, reason) for connection in connections))


def _unregister(code, socket):
    group = _watchers.get(code, {})
    group.pop(id(socket), None)
    if not group:
        _watchers.pop(code, None)


async def watch_socket(socket: WebSocket, code: str):
    token, grant = socket.query_params.get('token', ''), socket.query_params.get('grant', '')
    user_id = await asyncio.to_thread(decode_token_ws, token)
    if not user_id:
        await _close(socket, 4001, '未授权')
        return
    if not valid_grant(grant, user_id, code):
        await _close(socket, 4003, '观战许可无效或已关闭')
        return
    await socket.accept()
    lock = asyncio.Lock()
    _watchers.setdefault(code, {})[id(socket)] = (socket, lock, grant, user_id)
    room = rm.get_room(code)
    authorized = lambda: valid_grant(grant, user_id, code)
    initial = state_payload(room, 'watch_sync')
    baseline = json.dumps({**initial, 'type': 'watch_state'}, sort_keys=True, ensure_ascii=False)
    if not await _send(socket, lock, initial, authorize=authorized):
        await _close(socket, 4000, '连接不可用')
        _unregister(code, socket)
        return

    async def sample():
        previous = baseline
        verified = time.monotonic()
        while True:
            await asyncio.sleep(SAMPLE_SECONDS)
            if not valid_grant(grant, user_id, code):
                _grants.pop(grant, None)
                await _close(socket, 4003, '房间关闭或观战已关闭')
                return
            if time.monotonic() - verified >= AUTH_CHECK_SECONDS:
                if await asyncio.to_thread(decode_token_ws, token) != user_id:
                    await _close(socket, 4001, '登录已失效')
                    return
                verified = time.monotonic()
            current_room = rm.get_room(code)
            if not current_room or not valid_grant(grant, user_id, code):
                await _close(socket, 4003, "房间关闭或观战已关闭")
                return
            payload = state_payload(current_room)
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if encoded != previous:
                previous = encoded
                if not await _send(socket, lock, payload, authorize=authorized):
                    await _close(socket, 4000, '连接不可用')
                    return

    async def receive():
        try:
            while True:
                message = await socket.receive_json()
                if not valid_grant(grant, user_id, code):
                    await _close(socket, 4003, '观战已关闭')
                    return
                response = ({'type': 'pong'} if isinstance(message, dict) and message.get('type') == 'ping'
                            else {'type': 'error', 'message': '观战为只读，不能发送对局动作'})
                if not await _send(socket, lock, response, authorize=authorized):
                    await _close(socket, 4000, '连接不可用')
                    return
        except (WebSocketDisconnect, RuntimeError):
            return
        except ValueError:
            await _close(socket, 4003, '观战消息格式错误')
            return

    tasks = [asyncio.create_task(sample()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            error = task.exception() if not task.cancelled() else None
            if error is not None:
                logging.error("观战连接异常 code=%s", code, exc_info=(type(error), error, error.__traceback__))
                await _close(socket, 1011, "观战连接异常，请重新连接")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _unregister(code, socket)
