"""Asynchronous host jobs; stage and network work never block WS dispatch."""

import asyncio
import logging

import httpx

from app import ai_credits, room_manager as rm, turtle_soup_agent, turtle_soup_catalog
from app.game import turtle_soup

_tasks = {}
_closing = False
_publish = None
_log = logging.getLogger(__name__)


def configure(publish):
    global _publish
    _publish = publish


def _current(room, state, request):
    return (rm.get_room(room['code']) is room and room['status'] == 'playing'
            and room.get('game_state') is state and turtle_soup.pending(state) is request)


def _can_call(room, state, request):
    return (not _closing and _current(room, state, request)
            and any(p['connected'] and p['id'] in state['active'] for p in room['players']))


def submit(room, uid, data):
    if room['game'] != 'turtle_soup' or room['status'] != 'playing' or not rm.user_in_game(room, uid):
        return '你不在本局海龟汤游戏中'
    state = room['game_state']
    if data['type'] == 'soup_reveal':
        if (data.get('match_id') != state['match_id'] or type(data.get('revision')) is not int
                or data['revision'] != state['revision']):
            return '对局已更新，请查看最新状态'
        if uid != room['host_id']:
            return '只有房主可以结束并揭晓'
        turtle_soup.finish(state, 'revealed')
        rm.commit_turtle_soup(room)
        _publish(room)
        return None
    if ai_credits.balance_units(state['ai_owner_id']) < 0:
        return '房主 AI 积分不足，暂时无法提问；可请管理员设置积分或由房主结束并揭晓'
    error, changed = turtle_soup.submit(state, uid, data)
    if changed:
        rm._touch_activity(room)
        _publish(room)
        request = turtle_soup.pending(state)
        key = (state['match_id'], uid, request['id'])
        _tasks[key] = asyncio.create_task(_answer(room, state, request, key))
    return error


async def _answer(room, state, request, key):
    try:
        try:
            response = await turtle_soup_agent.answer(state, request, lambda: _can_call(room, state, request))
            if not _current(room, state, request):
                return
            if response is None:
                turtle_soup.fail_pending(state, '主持请求未执行，请重新提交')
            else:
                turtle_soup.apply_response(state, response)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            if not _current(room, state, request):
                return
            _log.warning('Soup host request failed: %s:%s', state['match_id'], request['id'])
            message = ('房主 AI 积分不足，请补充积分后重试' if ai_credits.balance_units(state['ai_owner_id']) < 0
                       else 'AI 主持暂未回答，本次不自动重试，请重新提交')
            turtle_soup.fail_pending(state, message)
        rm.commit_turtle_soup(room)
        _publish(room)
    except asyncio.CancelledError:
        if _current(room, state, request):
            turtle_soup.fail_pending(state, '主持请求中断，请重新提交')
            rm.commit_turtle_soup(room)
        raise
    finally:
        if _tasks.get(key) is asyncio.current_task():
            _tasks.pop(key, None)


def recover(room):
    state = room['game_state']
    if room['status'] == 'playing' and 'hints' not in state:
        puzzle = next((p for p in turtle_soup_catalog.all_puzzles() if p['id'] == state.get('puzzle_id')), None)
        if puzzle:
            state['hints'] = list(puzzle['hints'])
            rm.commit_turtle_soup(room)
    if room['status'] == 'playing' and turtle_soup.fail_pending(
        room['game_state'], '上次主持请求因服务重启中断，请重新提交',
    ):
        rm.commit_turtle_soup(room)


async def shutdown():
    global _closing
    _closing = True
    if not _tasks:
        return
    _, pending = await asyncio.wait(list(_tasks.values()), timeout=25)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def startup():
    global _closing
    _closing = False
