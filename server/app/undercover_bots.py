"""One serial AI worker per room; account locking is shared across rooms."""

import asyncio
import logging

import httpx

from app import ai_credits, persistence, room_manager as rm, room_timing, undercover_agent
from app.game import undercover

_tasks = {}
_closing = False
_log = logging.getLogger(__name__)


def _current(room, state, phase_id):
    return (rm.get_room(room['code']) is room and room['status'] == 'playing'
            and room.get('game_state') is state and state.get('phase_id') == phase_id
            and room_timing.undercover_online(room))


def _actor(room):
    state = room['game_state']
    attempted = state.get('_ai_attempted', [])
    for player in room['players']:
        uid = player['id']
        if not player.get('is_bot') or uid not in state['alive']:
            continue
        view = undercover.view_for(state, uid)
        view["your_id"] = uid
        operation = f"undercover:{state['match_id']}:{state['phase_id']}:{uid}"
        if operation in attempted:
            continue
        if view['can_clue'] or (state['phase'] in ('vote', 'runoff')
                                and view['your_vote'] is None and view['candidate_ids']):
            return uid, view, operation
    return None


async def _run(room, publish, report):
    state = room['game_state']
    try:
        while not _closing and room['status'] == 'playing' and room.get('game_state') is state:
            if not room_timing.undercover_online(room) or not (actor := _actor(room)):
                return
            uid, view, operation = actor
            owner = state['ai_owner_id']
            if ai_credits.balance_units(owner) < 0:
                if not state.get('_ai_disabled_notice'):
                    state['_ai_disabled_notice'] = True
                    report(owner, '房主 AI 积分已低于 0，已禁用 AI；人机本回合将超时跳过或弃票')
                return
            phase_id = state['phase_id']
            # Persist before sending: hot restart must not replay this paid request.
            state.setdefault('_ai_attempted', []).append(operation)
            persistence.save_room(room)
            try:
                action = await undercover_agent.decide(
                    view, owner, operation, lambda: not _closing and _current(room, state, phase_id))
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                # Do not log response bodies, headers, words or the credential.
                _log.warning('Undercover AI request failed: %s', operation)
                report(owner, 'AI 调用失败，本次不自动重试；人机将超时跳过或弃票')
                continue
            if action is None or not _current(room, state, phase_id):
                continue
            data = {'type': 'undercover_clue' if view['can_clue'] else 'undercover_vote',
                    'match_id': view['match_id'], 'phase_id': phase_id,
                    'text': action.get('text'), 'target_id': action.get('target_id')}
            error, changed = rm.undercover_action(room, uid, data)
            if changed:
                publish(room)
            elif error:
                report(owner, 'AI 操作未通过游戏规则校验，本回合将超时跳过或弃票')
    finally:
        if _tasks.get(room['code']) is asyncio.current_task():
            _tasks.pop(room['code'], None)


def schedule(room, publish, report):
    if (_closing or room['status'] != 'playing' or not room['game_state'].get('ai_owner_id')
            or not room_timing.undercover_online(room)):
        return
    task = _tasks.get(room['code'])
    if task is None or task.done():
        _tasks[room['code']] = asyncio.create_task(_run(room, publish, report))


async def shutdown():
    """Finish in-flight billing, without starting another paid action."""
    global _closing
    _closing = True
    tasks = list(_tasks.values())
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=25)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def startup():
    global _closing
    _closing = False
